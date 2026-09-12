"""Bounded local partition execution over project-owned semantic contracts.

The service owns routing, reduction and equivalence.  It is intentionally a
synchronous data-work boundary; scheduling, cancellation and job retries are
outside Step24 and remain Step28 responsibilities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform as host_platform
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort
from dirty_data_to_olap.domain.contracts.distributed import (
    EquivalenceStatus,
    PartitionDescriptor,
    PartitionMergeManifest,
    PartitionPlan,
    PartitionResult,
    PartitionResultStatus,
    PartitionStrategy,
    ScaleDecision,
    ScaleEquivalenceReport,
    ScaleExecutionMode,
    ScaleExecutionResult,
    ScaleExecutionStatus,
    ScaleInputDataset,
    ScaleInputRow,
    ScaleMergedOutput,
    ScalePartitionOutput,
    ScalePolicy,
    ScaleProfileStats,
    SkewAction,
    SkewAssessment,
    SkewStatus,
    StageScaleSupport,
    pair_id_for,
    partition_id_for,
    partition_plan_id_for,
    partition_result_id_for,
    record_digest,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactDependency,
    ArtifactManifest,
    ArtifactPublicationState,
    ArtifactStorageMode,
    CacheEntry,
    CacheInputRef,
    CacheKey,
    GateEvidence,
    GateEvidenceStatus,
    RetentionClass,
    ResourceBudget,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id


class ScaleError(RuntimeError):
    """Base error for fail-closed scale decisions and data work."""


class ScaleAuthorizationError(ScaleError):
    """The supplied G6 receipt or input binding is not authoritative."""


class PartitionMergeError(ScaleError):
    """A complete, exact partition barrier cannot be formed."""


class LargeJoinFanoutError(ScaleError):
    """A comparison request exceeds its explicit bounded candidate policy."""


@runtime_checkable
class PartitionExecutorPort(Protocol):
    """Synchronous typed data-work boundary, not a job-control interface."""

    def execute(self, dataset: ScaleInputDataset, plan: Any, policy: ScalePolicy, *, failure_partition_ids: Iterable[str] = ()) -> ScaleExecutionResult:
        ...


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _row_bytes(row: ScaleInputRow) -> int:
    return len(_json_bytes(row.model_dump(mode="json")))


def _scope_key(dataset: ScaleInputDataset) -> dict[str, str]:
    descriptor = dataset.descriptor
    return {
        "run_id": descriptor.run_id,
        "source_id": descriptor.source_id,
        "snapshot_id": descriptor.snapshot_id,
        "table_id": descriptor.table_id,
        "dataset_id": descriptor.dataset_id,
        "dataset_version": descriptor.dataset_version,
        "input_id": descriptor.input_id,
        "stage_id": descriptor.stage_id,
        "schema_fingerprint": descriptor.schema_fingerprint,
    }


class ScaleService(PartitionExecutorPort):
    """Reference implementation of deterministic partitioned data semantics."""

    OPERATION_VERSION = "step24-scale-v1"
    MERGE_VERSION = "step24-deterministic-reduction-v1"

    def __init__(self, *, artifact_store: ArtifactStorePort | None = None, control_store: ControlStorePort | None = None) -> None:
        self.artifact_store = artifact_store
        self.control_store = control_store
        self.max_observed_concurrency = 0
        self.peak_observed_memory_mb: int | None = None
        self._active_workers = 0
        self._observation_lock = threading.Lock()

    def authorize_g6(self, dataset: ScaleInputDataset, gate: GateEvidence) -> GateEvidence:
        """Require the exact typed Step22 receipt; legacy receipts are rejected."""

        descriptor = dataset.descriptor
        if gate.gate_id != "G6_DATA_CORRECTNESS" or gate.status is not GateEvidenceStatus.PASS or not gate.eligible:
            raise ScaleAuthorizationError("G6_DATA_CORRECTNESS is not an eligible PASS")
        if gate.validation_report_id.startswith("legacy:") or "legacy:gate-evidence-unverified" in gate.provenance_refs:
            raise ScaleAuthorizationError("legacy or unverified G6 evidence cannot authorize scale")
        expected = (
            (gate.run_id, descriptor.reference_gate_run_id, "gate run"),
            (gate.validation_report_artifact_id, descriptor.reference_validation_report_artifact_id, "report artifact"),
            (gate.validation_report_run_id, descriptor.reference_validation_report_run_id, "report run"),
            (gate.validation_report_id, descriptor.reference_validation_report_id, "report id"),
            (gate.validation_report_content_hash, descriptor.reference_validation_report_hash, "report hash"),
        )
        for actual, required, label in expected:
            if actual != required:
                raise ScaleAuthorizationError(f"G6 {label} does not match the input binding")
        return gate

    def _support_for(self, dataset: ScaleInputDataset, policy: ScalePolicy) -> StageScaleSupport:
        stage = dataset.descriptor.stage_id
        value = policy.stage_support.get(stage) or policy.stage_support.get(stage.lower()) or policy.stage_support.get(stage.upper())
        return value or StageScaleSupport.NOT_EVALUATED

    def assess(self, dataset: ScaleInputDataset, policy: ScalePolicy, *, requested_parallelism: int | None = None) -> ScaleDecision:
        if policy.requested_mode is ScaleExecutionMode.EXTERNAL_DISTRIBUTED:
            raise ScaleAuthorizationError("EXTERNAL_DISTRIBUTED is not an available Step24 V1 execution mode")
        descriptor = dataset.descriptor
        rows = descriptor.row_count
        byte_count = descriptor.estimated_bytes
        support = self._support_for(dataset, policy)
        below_rows = rows <= policy.local_row_threshold
        below_bytes = policy.local_byte_threshold is None or byte_count is None or byte_count <= policy.local_byte_threshold
        safe_to_partition = support in {
            StageScaleSupport.PARTITION_SAFE,
            StageScaleSupport.SHUFFLE_REQUIRED,
            StageScaleSupport.GLOBAL_REDUCTION,
            StageScaleSupport.GLOBAL_CANDIDATE_GENERATION,
        }
        requested = max(1, requested_parallelism or policy.max_worker_slots)
        if below_rows and below_bytes:
            mode = ScaleExecutionMode.LOCAL_REFERENCE
            reason = "workload is below the explicitly configured local thresholds"
        elif safe_to_partition:
            mode = ScaleExecutionMode.PARTITIONED_LOCAL
            reason = f"stage support={support.value} and configured workload thresholds require bounded partitioning"
        elif policy.fallback_policy.value == "FALLBACK_TO_LOCAL":
            mode = ScaleExecutionMode.LOCAL_REFERENCE
            reason = f"stage support={support.value} is not proven partition-safe; explicit local fallback selected"
        else:
            raise ScaleAuthorizationError(f"stage support={support.value} cannot satisfy the configured scale policy")
        decision_id = stable_id(
            "scale-decision",
            {
                "scope": _scope_key(dataset),
                "content_hash": descriptor.content_hash,
                "rows": rows,
                "bytes": byte_count,
                "support": support.value,
                "mode": mode.value,
                "requested_parallelism": requested,
                "policy": policy.configuration_fingerprint,
            },
        )
        return ScaleDecision(
            decision_id=decision_id,
            stage_id=descriptor.stage_id,
            input_id=descriptor.input_id,
            input_content_hash=descriptor.content_hash,
            estimated_row_count=rows,
            estimated_byte_count=byte_count,
            measured_row_count=len(dataset.rows),
            measured_byte_count=sum(_row_bytes(row) for row in dataset.rows),
            resource_budget=policy.resource_budget,
            requested_parallelism=requested,
            selected_mode=mode,
            partitioning_required=mode is ScaleExecutionMode.PARTITIONED_LOCAL,
            reason=reason,
            capability_evidence=(f"stage-support:{support.value}", "partitioned_local:REFERENCE_TESTED"),
            fallback_mode=ScaleExecutionMode.LOCAL_REFERENCE if mode is ScaleExecutionMode.PARTITIONED_LOCAL else None,
            policy_version=policy.policy_version,
            provenance_refs=descriptor.provenance_refs,
        )

    @staticmethod
    def _route_key(row: ScaleInputRow, strategy: PartitionStrategy) -> str:
        if strategy is PartitionStrategy.BLOCK_KEY:
            return row.block_key or row.group_key or row.partition_key
        if strategy is PartitionStrategy.EXISTING_STAGED_PART:
            raise ScaleError("EXISTING_STAGED_PART is a storage strategy and has no generic row router")
        return row.partition_key or row.fact_grain_key or row.row_ref

    @staticmethod
    def _bucket(key: str, seed: int, count: int) -> int:
        digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()
        return int(digest[:16], 16) % count

    def _strategy_for(self, dataset: ScaleInputDataset, policy: ScalePolicy, decision: ScaleDecision) -> PartitionStrategy:
        if decision.selected_mode is ScaleExecutionMode.LOCAL_REFERENCE:
            return PartitionStrategy.SINGLE_NODE
        if policy.partition_strategy is not None:
            if policy.partition_strategy is PartitionStrategy.EXISTING_STAGED_PART:
                raise ScaleError("EXISTING_STAGED_PART requires an existing typed staging manifest")
            return policy.partition_strategy
        support = self._support_for(dataset, policy)
        if support is StageScaleSupport.GLOBAL_CANDIDATE_GENERATION:
            return PartitionStrategy.BLOCK_KEY
        return PartitionStrategy.HASH

    @staticmethod
    def _partition_budget_fingerprint(budget: ResourceBudget) -> str:
        """Bind per-partition limits while keeping worker scheduling orthogonal."""

        value = budget.model_dump(mode="json")
        value["max_worker_slots"] = 1
        return stable_digest(value)

    def _partition_assignments(self, dataset: ScaleInputDataset, strategy: PartitionStrategy, count: int, policy: ScalePolicy) -> dict[int, list[ScaleInputRow]]:
        assignments: dict[int, list[ScaleInputRow]] = {index: [] for index in range(count)}
        for row in dataset.rows:
            if strategy is PartitionStrategy.SINGLE_NODE:
                bucket = 0
            elif strategy is PartitionStrategy.RANGE:
                bucket = len(tuple(boundary for boundary in policy.range_boundaries if row.partition_key >= boundary))
                bucket = min(bucket, count - 1)
            else:
                bucket = self._bucket(self._route_key(row, strategy), policy.deterministic_seed, count)
            assignments[bucket].append(row)
        return assignments

    def _skew(self, assignments: dict[int, list[ScaleInputRow]], policy: ScalePolicy) -> SkewAssessment:
        counts = tuple(len(assignments[index]) for index in sorted(assignments))
        bytes_by_partition = tuple(sum(_row_bytes(row) for row in assignments[index]) for index in sorted(assignments))
        minimum = min(counts) if counts else 0
        maximum = max(counts) if counts else 0
        mean = sum(counts) / len(counts) if counts else 0.0
        ratio = maximum / mean if mean else 0.0
        if maximum == 0:
            status = SkewStatus.EMPTY
        elif ratio > policy.skew_ratio_threshold:
            status = SkewStatus.SKEWED
        else:
            status = SkewStatus.BALANCED
        action = policy.skew_action if status is SkewStatus.SKEWED else SkewAction.ALLOW
        return SkewAssessment(
            partition_count=len(counts),
            record_counts=counts,
            byte_counts=bytes_by_partition,
            minimum=minimum,
            maximum=maximum,
            mean=mean,
            max_mean_ratio=ratio,
            empty_partition_count=sum(count == 0 for count in counts),
            status=status,
            action=action,
            detail="partition distribution is within policy" if status is SkewStatus.BALANCED else "configured policy exposes skew for review or rejection",
        )

    def plan(self, dataset: ScaleInputDataset, policy: ScalePolicy, *, partition_count: int | None = None, requested_parallelism: int | None = None) -> Any:
        decision = self.assess(dataset, policy, requested_parallelism=requested_parallelism)
        strategy = self._strategy_for(dataset, policy, decision)
        count = 1 if strategy is PartitionStrategy.SINGLE_NODE else max(1, min(partition_count or max(1, decision.requested_parallelism), policy.max_partitions))
        assignments = self._partition_assignments(dataset, strategy, count, policy)
        for index, rows in assignments.items():
            if policy.max_partition_rows is not None and len(rows) > policy.max_partition_rows:
                raise ScaleError(f"RESOURCE_BUDGET_EXCEEDED: partition {index} has {len(rows)} rows")
            if policy.max_partition_bytes is not None and sum(_row_bytes(row) for row in rows) > policy.max_partition_bytes:
                raise ScaleError(f"RESOURCE_BUDGET_EXCEEDED: partition {index} exceeds configured byte envelope")
        descriptor = dataset.descriptor
        scope = _scope_key(dataset)
        plan_id = partition_plan_id_for(
            scope=scope,
            input_content_hash=descriptor.content_hash,
            strategy=strategy.value,
            partition_count=count,
            deterministic_seed=policy.deterministic_seed,
            policy_version=policy.policy_version,
            resource_budget_fingerprint=self._partition_budget_fingerprint(policy.resource_budget),
            range_boundaries=policy.range_boundaries,
        )
        partition_ids = tuple(
            partition_id_for(
                run_id=descriptor.run_id,
                source_id=descriptor.source_id,
                snapshot_id=descriptor.snapshot_id,
                table_id=descriptor.table_id,
                dataset_id=descriptor.dataset_id,
                dataset_version=descriptor.dataset_version,
                input_id=descriptor.input_id,
                stage_id=descriptor.stage_id,
                schema_fingerprint=descriptor.schema_fingerprint,
                strategy=strategy,
                partition_index=index,
                routing_key=f"bucket:{index}",
                deterministic_seed=policy.deterministic_seed,
                policy_version=policy.policy_version,
            )
            for index in range(count)
        )
        semantic_partition_content = [
            {
                "partition_id": partition_ids[index],
                "logical_partition_index": index,
                "strategy": strategy.value,
                "routing_key": f"bucket:{index}",
                "range_start": policy.range_boundaries[index - 1] if strategy is PartitionStrategy.RANGE and index > 0 and index - 1 < len(policy.range_boundaries) else None,
                "range_end": policy.range_boundaries[index] if strategy is PartitionStrategy.RANGE and index < len(policy.range_boundaries) else None,
                "expected_row_count": len(assignments[index]),
                "expected_row_digest": stable_digest(sorted(record_digest(row) for row in assignments[index])),
            }
            for index in range(count)
        ]
        plan_semantic_content = {
            "schema_version": "1.0",
            "operation_id": descriptor.stage_id,
            "operation_version": self.OPERATION_VERSION,
            "run_id": descriptor.run_id,
            "input_id": descriptor.input_id,
            "input_content_hash": descriptor.content_hash,
            "input_artifact_refs": sorted(descriptor.artifact_refs),
            "source_id": descriptor.source_id,
            "snapshot_id": descriptor.snapshot_id,
            "table_id": descriptor.table_id,
            "dataset_id": descriptor.dataset_id,
            "dataset_version": descriptor.dataset_version,
            "schema_fingerprint": descriptor.schema_fingerprint,
            "strategy": strategy.value,
            "partition_key_specification": "explicit-range-boundaries" if strategy is PartitionStrategy.RANGE else ("blocking-key" if strategy is PartitionStrategy.BLOCK_KEY else "stable-sha256-routing-key"),
            "partition_count": count,
            "deterministic_seed": policy.deterministic_seed,
            "resource_budget_fingerprint": self._partition_budget_fingerprint(policy.resource_budget),
            "policy_version": policy.policy_version,
            "expected_partition_ids": sorted(partition_ids),
            "partitions": sorted(semantic_partition_content, key=lambda item: item["partition_id"]),
            "expected_merge_strategy": "single-node" if strategy is PartitionStrategy.SINGLE_NODE else "sorted-partition-id-exact-reduction",
        }
        plan_hash = stable_digest(plan_semantic_content)
        partitions = tuple(
            PartitionDescriptor(
                partition_id=partition_ids[index],
                partition_plan_id=plan_id,
                partition_plan_hash=plan_hash,
                logical_partition_index=index,
                strategy=strategy,
                routing_key=f"bucket:{index}",
                range_start=item["range_start"],
                range_end=item["range_end"],
                input_id=descriptor.input_id,
                input_content_hash=descriptor.content_hash,
                run_id=descriptor.run_id,
                source_id=descriptor.source_id,
                snapshot_id=descriptor.snapshot_id,
                table_id=descriptor.table_id,
                dataset_id=descriptor.dataset_id,
                dataset_version=descriptor.dataset_version,
                schema_fingerprint=descriptor.schema_fingerprint,
                deterministic_seed=policy.deterministic_seed,
                expected_row_count=len(assignments[index]),
                expected_row_digest=item["expected_row_digest"],
                expected_resource=policy.resource_budget,
                provenance_refs=descriptor.provenance_refs,
            )
            for index, item in enumerate(semantic_partition_content)
        )
        return PartitionPlan(
            partition_plan_id=plan_id,
            partition_plan_hash=plan_hash,
            operation_id=descriptor.stage_id,
            operation_version=self.OPERATION_VERSION,
            run_id=descriptor.run_id,
            input_id=descriptor.input_id,
            input_content_hash=descriptor.content_hash,
            input_artifact_refs=descriptor.artifact_refs,
            source_id=descriptor.source_id,
            snapshot_id=descriptor.snapshot_id,
            table_id=descriptor.table_id,
            dataset_id=descriptor.dataset_id,
            dataset_version=descriptor.dataset_version,
            schema_fingerprint=descriptor.schema_fingerprint,
            strategy=strategy,
            partition_key_specification=plan_semantic_content["partition_key_specification"],
            partition_count=count,
            deterministic_seed=policy.deterministic_seed,
            resource_budget_fingerprint=self._partition_budget_fingerprint(policy.resource_budget),
            policy_version=policy.policy_version,
            partitions=partitions,
            expected_partition_ids=partition_ids,
            expected_merge_strategy=plan_semantic_content["expected_merge_strategy"],
            skew=self._skew(assignments, policy),
            provenance_refs=descriptor.provenance_refs,
        )

    @staticmethod
    def _sample_candidates(rows: Sequence[ScaleInputRow], policy: ScalePolicy) -> tuple[str, ...]:
        if policy.sample_ratio >= 1:
            selected = [row.row_ref for row in rows]
        else:
            limit = int(policy.sample_ratio * (1 << 64))
            selected = [row.row_ref for row in rows if int(stable_digest({"row_ref": row.row_ref, "seed": policy.deterministic_seed, "policy": policy.policy_version})[:16], 16) < limit]
        selected.sort(key=lambda ref: stable_digest({"row_ref": ref, "seed": policy.deterministic_seed, "policy": policy.policy_version}))
        return tuple(selected)

    def sample_record_refs(self, dataset: ScaleInputDataset, policy: ScalePolicy) -> tuple[str, ...]:
        selected = self._sample_candidates(dataset.rows, policy)
        return selected if policy.sample_limit is None else selected[: policy.sample_limit]

    def _candidate_pairs(self, rows: Sequence[ScaleInputRow], dataset: ScaleInputDataset, *, max_pairs: int) -> tuple[str, ...]:
        by_key: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            for key in row.blocking_keys:
                by_key[key].add(row.row_ref)
        unique_pairs: set[str] = set()
        for key in sorted(by_key):
            members = sorted(by_key[key])
            if len(members) * (len(members) - 1) // 2 > max_pairs:
                raise LargeJoinFanoutError(f"candidate fanout for blocking key exceeds {max_pairs}")
            for left, right in combinations(members, 2):
                unique_pairs.add(pair_id_for(run_id=dataset.descriptor.run_id, source_id=dataset.descriptor.source_id, snapshot_id=dataset.descriptor.snapshot_id, table_id=dataset.descriptor.table_id, left_row_ref=left, right_row_ref=right))
        if len(unique_pairs) > max_pairs:
            raise LargeJoinFanoutError(f"candidate set exceeds {max_pairs}")
        return tuple(sorted(unique_pairs))

    def reject_join_fanout(self, join_pairs: Sequence[tuple[str, str]], *, max_pairs: int | None = None) -> None:
        limit = max_pairs or 100_000
        grouped: dict[str, int] = defaultdict(int)
        for key, _ in join_pairs:
            grouped[key] += 1
        if any(count > 1 for count in grouped.values()) and len(join_pairs) > limit:
            raise LargeJoinFanoutError(f"join fanout {len(join_pairs)} exceeds {limit}")
        if any(count > 1 for count in grouped.values()) and limit <= 1:
            raise LargeJoinFanoutError("duplicate dimension key is unsafe for the bounded join policy")

    def _output_for_rows(self, rows: Sequence[ScaleInputRow], dataset: ScaleInputDataset, policy: ScalePolicy, *, strategy: PartitionStrategy) -> ScalePartitionOutput:
        measures = [row.measure for row in rows if row.measure is not None]
        null_count = len(rows) - len(measures)
        profile = ScaleProfileStats(
            row_count=len(rows),
            non_null_count=len(measures),
            null_count=null_count,
            minimum=min(measures) if measures else None,
            maximum=max(measures) if measures else None,
            exact_sum=sum(measures, Decimal("0")),
        )
        dependency_pairs = tuple(sorted({(row.dependency_lhs, row.dependency_rhs) for row in rows if row.dependency_lhs is not None and row.dependency_rhs is not None}))
        candidate_pairs = self._candidate_pairs(rows, dataset, max_pairs=policy.max_join_pairs) if strategy is PartitionStrategy.BLOCK_KEY else ()
        routed_instances = sum(max(1, len(row.blocking_keys)) for row in rows) if strategy is PartitionStrategy.BLOCK_KEY else len(rows)
        shuffle_bytes = sum(_row_bytes(row) * (max(1, len(row.blocking_keys)) if strategy is PartitionStrategy.BLOCK_KEY else 1) for row in rows)
        return ScalePartitionOutput(
            record_refs=tuple(sorted(row.row_ref for row in rows)),
            source_record_refs=tuple(sorted({ref for row in rows for ref in row.source_record_refs})),
            canonical_entity_ids=tuple(sorted({row.canonical_entity_id for row in rows if row.canonical_entity_id is not None})),
            fact_grain_keys=tuple(sorted(row.fact_grain_key for row in rows if row.fact_grain_key is not None)),
            warehouse_keys=tuple(sorted(row.warehouse_key for row in rows if row.warehouse_key is not None)),
            profile=profile,
            exact_measure_sum=profile.exact_sum,
            dependency_pairs=dependency_pairs,
            candidate_pairs=candidate_pairs,
            sampled_record_refs=self._sample_candidates(rows, policy),
            unique_input_records=len(rows),
            routed_record_instances=routed_instances,
            replication_count=max(0, routed_instances - len(rows)),
            logical_shuffle_records=routed_instances,
            logical_shuffle_bytes=shuffle_bytes,
        )

    def _observe_worker(self, entering: bool) -> None:
        with self._observation_lock:
            self._active_workers += 1 if entering else -1
            self.max_observed_concurrency = max(self.max_observed_concurrency, self._active_workers)

    def _publish_partition_result(self, result: PartitionResult, dataset: ScaleInputDataset, plan: Any, policy: ScalePolicy) -> PartitionResult:
        if self.artifact_store is None:
            return result
        payload = _json_bytes({"contract": "PartitionResult", "semantic_result_hash": result.semantic_result_hash, "result": result.model_dump(mode="json")})
        artifact_id = result.result_id
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            run_id=plan.run_id,
            stage_id="STEP24_SCALE",
            attempt_id=result.partition_id,
            artifact_kind="Step24PartitionResult",
            media_type="application/json",
            producer="application.distributed_scale",
            storage_mode=ArtifactStorageMode.MANAGED,
            logical_key=f"runs/{plan.run_id}/step24/partition-results/{result.partition_id}.json",
            retention_class=RetentionClass.RUN_SCOPED,
            provenance_refs=(plan.partition_plan_id, *dataset.descriptor.provenance_refs),
        )
        ref = self.artifact_store.publish(manifest, payload)
        dependencies: list[ArtifactDependency] = []
        if self.control_store is not None:
            for upstream_id in dataset.descriptor.artifact_refs:
                upstream = self.control_store.get_artifact(upstream_id)
                if upstream is not None:
                    dependencies.append(ArtifactDependency(artifact_id=ref.artifact_id, upstream_artifact_id=upstream.artifact_id, expected_content_hash=upstream.content_hash, relationship_kind="INPUT"))
            plan_artifact = self.control_store.get_artifact(plan.partition_plan_id)
            if plan_artifact is not None:
                dependencies.append(ArtifactDependency(artifact_id=ref.artifact_id, upstream_artifact_id=plan_artifact.artifact_id, expected_content_hash=plan_artifact.content_hash, relationship_kind="PARTITION_PLAN"))
            if dependencies:
                self.control_store.register_artifact_with_dependencies(ref, dependencies)
            else:
                self.control_store.register_artifact(ref)
            key = self.cache_key_for(dataset, plan, next(item for item in plan.partitions if item.partition_id == result.partition_id), policy)
            self.control_store.record_cache_entry(CacheEntry(cache_key_hash=key.key_hash, cache_key=key, output_artifact_id=ref.artifact_id, output_content_hash=ref.content_hash))
        return result.model_copy(update={"output_artifact_refs": (ref.artifact_id,), "output_content_hash": ref.content_hash})

    def cache_key_for(self, dataset: ScaleInputDataset, plan: Any, partition: PartitionDescriptor, policy: ScalePolicy) -> CacheKey:
        input_refs = []
        for ref in dataset.descriptor.artifact_refs:
            upstream = self.control_store.get_artifact(ref) if self.control_store is not None else None
            input_refs.append(CacheInputRef(artifact_id=ref, content_hash=upstream.content_hash if upstream is not None else dataset.descriptor.content_hash))
        inputs = tuple(input_refs)
        return CacheKey(
            stage_id=plan.operation_id,
            component_id="application.distributed_scale",
            input_artifacts=inputs,
            contract_versions={"distributed": "1.0", "platform": "1.0"},
            policy_version=policy.policy_version,
            configuration_fingerprint=stable_digest({"plan_hash": plan.partition_plan_hash, "partition_id": partition.partition_id, "budget": policy.resource_budget.model_dump(mode="json")}),
            engine_version="stdlib-threadpool-reference-v1",
            code_version=plan.operation_version,
            seed=policy.deterministic_seed,
            domain_scope_fingerprint=stable_digest({"run_id": plan.run_id, "source_id": plan.source_id, "snapshot_id": plan.snapshot_id, "table_id": plan.table_id}),
        )

    def _run_partition(self, dataset: ScaleInputDataset, plan: Any, partition: PartitionDescriptor, policy: ScalePolicy, *, should_fail: bool = False) -> PartitionResult:
        self._observe_worker(True)
        started = time.perf_counter()
        try:
            rows = [row for row in dataset.rows if self._bucket(self._route_key(row, plan.strategy), plan.deterministic_seed, plan.partition_count) == partition.logical_partition_index] if plan.strategy not in {PartitionStrategy.SINGLE_NODE, PartitionStrategy.RANGE} else (
                [row for row in dataset.rows if partition.logical_partition_index == 0] if plan.strategy is PartitionStrategy.SINGLE_NODE else [row for row in dataset.rows if min(len(tuple(boundary for boundary in policy.range_boundaries if row.partition_key >= boundary)), plan.partition_count - 1) == partition.logical_partition_index]
            )
            actual_digest = stable_digest(sorted(record_digest(row) for row in rows))
            if actual_digest != partition.expected_row_digest or len(rows) != partition.expected_row_count or dataset.descriptor.content_hash != plan.input_content_hash:
                return PartitionResult(result_id=partition_result_id_for(partition_plan_id=plan.partition_plan_id, partition_id=partition.partition_id, operation_id=plan.operation_id, operation_version=plan.operation_version, input_content_hash=plan.input_content_hash, policy_version=policy.policy_version, deterministic_seed=plan.deterministic_seed), partition_id=partition.partition_id, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, operation_id=plan.operation_id, operation_version=plan.operation_version, status=PartitionResultStatus.FAILED, input_id=plan.input_id, input_content_hash=dataset.descriptor.content_hash, schema_fingerprint=dataset.descriptor.schema_fingerprint, record_count=0, failure_code="STALE_INPUT", failure_reason="current dataset does not match the immutable partition plan", attempt_semantic_identity=stable_id("partition-attempt", {"plan": plan.partition_plan_id, "partition": partition.partition_id, "input": dataset.descriptor.content_hash}), provenance_refs=dataset.descriptor.provenance_refs)
            if should_fail:
                raise ScaleError("FAILURE_INJECTED")
            output = self._output_for_rows(rows, dataset, policy, strategy=plan.strategy)
            result = PartitionResult(result_id=partition_result_id_for(partition_plan_id=plan.partition_plan_id, partition_id=partition.partition_id, operation_id=plan.operation_id, operation_version=plan.operation_version, input_content_hash=plan.input_content_hash, policy_version=policy.policy_version, deterministic_seed=plan.deterministic_seed), partition_id=partition.partition_id, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, operation_id=plan.operation_id, operation_version=plan.operation_version, status=PartitionResultStatus.SUCCEEDED, input_id=plan.input_id, input_content_hash=plan.input_content_hash, schema_fingerprint=plan.schema_fingerprint, output=output, record_count=len(rows), byte_count=sum(_row_bytes(row) for row in rows), candidate_pair_count=len(output.candidate_pairs), shuffle_records=output.logical_shuffle_records, shuffle_bytes=output.logical_shuffle_bytes, peak_memory_mb=max(1, math.ceil(sum(_row_bytes(row) for row in rows) / 1_048_576)), attempt_semantic_identity=stable_id("partition-attempt", {"plan": plan.partition_plan_id, "partition": partition.partition_id, "input": plan.input_content_hash}), provenance_refs=dataset.descriptor.provenance_refs)
            return self._publish_partition_result(result, dataset, plan, policy)
        except LargeJoinFanoutError as exc:
            return PartitionResult(result_id=partition_result_id_for(partition_plan_id=plan.partition_plan_id, partition_id=partition.partition_id, operation_id=plan.operation_id, operation_version=plan.operation_version, input_content_hash=plan.input_content_hash, policy_version=policy.policy_version, deterministic_seed=plan.deterministic_seed), partition_id=partition.partition_id, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, operation_id=plan.operation_id, operation_version=plan.operation_version, status=PartitionResultStatus.REJECTED, input_id=plan.input_id, input_content_hash=plan.input_content_hash, schema_fingerprint=plan.schema_fingerprint, record_count=0, failure_code="LARGE_JOIN_FANOUT", failure_reason=str(exc), attempt_semantic_identity=stable_id("partition-attempt", {"plan": plan.partition_plan_id, "partition": partition.partition_id, "input": plan.input_content_hash}), provenance_refs=dataset.descriptor.provenance_refs)
        except Exception as exc:
            return PartitionResult(result_id=partition_result_id_for(partition_plan_id=plan.partition_plan_id, partition_id=partition.partition_id, operation_id=plan.operation_id, operation_version=plan.operation_version, input_content_hash=plan.input_content_hash, policy_version=policy.policy_version, deterministic_seed=plan.deterministic_seed), partition_id=partition.partition_id, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, operation_id=plan.operation_id, operation_version=plan.operation_version, status=PartitionResultStatus.FAILED, input_id=plan.input_id, input_content_hash=dataset.descriptor.content_hash, schema_fingerprint=dataset.descriptor.schema_fingerprint, record_count=0, failure_code=str(exc) if str(exc) else "PARTITION_FAILED", failure_reason=str(exc) or "partition worker failed", attempt_semantic_identity=stable_id("partition-attempt", {"plan": plan.partition_plan_id, "partition": partition.partition_id, "input": dataset.descriptor.content_hash}), provenance_refs=dataset.descriptor.provenance_refs)
        finally:
            elapsed = time.perf_counter() - started
            self._observe_worker(False)
            if elapsed < 0:
                raise AssertionError("monotonic timer moved backwards")

    def _incomplete_manifest(self, plan: Any, results: Sequence[PartitionResult], *, status: ScaleExecutionStatus, reason: str | None = None) -> PartitionMergeManifest:
        grouped: dict[str, list[PartitionResult]] = defaultdict(list)
        for result in results:
            grouped[result.partition_id].append(result)
        duplicates = tuple(sorted(pid for pid, values in grouped.items() if len(values) > 1))
        conflicts = tuple(sorted(pid for pid, values in grouped.items() if len({item.semantic_result_hash for item in values}) > 1))
        result_map = {pid: values[0].semantic_result_hash for pid, values in grouped.items() if len(values) == 1}
        missing = tuple(sorted(set(plan.expected_partition_ids) - set(grouped)))
        unexpected = tuple(sorted(set(grouped) - set(plan.expected_partition_ids)))
        return PartitionMergeManifest(merge_manifest_id=stable_id("merge-manifest", {"plan": plan.partition_plan_id, "results": sorted(result_map.items()), "status": status.value, "reason": reason}), partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, expected_partition_ids=plan.expected_partition_ids, result_hashes=result_map, merge_reduction_version=self.MERGE_VERSION, merge_ordering="partition_id_ascending", missing_partitions=missing, duplicate_partitions=duplicates, unexpected_partitions=unexpected, conflicting_results=conflicts, aggregate_record_count=sum(item.record_count for item in results if item.status is PartitionResultStatus.SUCCEEDED), status=status, failure_reason=reason)

    def execute(self, dataset: ScaleInputDataset, plan: Any, policy: ScalePolicy, *, failure_partition_ids: Iterable[str] = ()) -> ScaleExecutionResult:
        if dataset.descriptor.content_hash != plan.input_content_hash:
            raise ScaleAuthorizationError("STALE_INPUT: dataset hash differs from partition plan")
        if plan.run_id != dataset.descriptor.run_id or plan.schema_fingerprint != dataset.descriptor.schema_fingerprint:
            raise ScaleAuthorizationError("partition plan scope does not match the dataset")
        if plan.skew.status is SkewStatus.SKEWED and plan.skew.action is SkewAction.REJECT:
            manifest = self._incomplete_manifest(plan, (), status=ScaleExecutionStatus.NEEDS_REVIEW, reason="SKEW_REVIEW_REQUIRED")
            return ScaleExecutionResult(execution_id=stable_id("scale-execution", {"plan": plan.partition_plan_id, "status": "skew"}), run_id=plan.run_id, operation_id=plan.operation_id, mode=ScaleExecutionMode.PARTITIONED_LOCAL, input_id=plan.input_id, input_content_hash=plan.input_content_hash, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, status=ScaleExecutionStatus.NEEDS_REVIEW, merge_manifest=manifest, observed_worker_slots=0, failure_code="SKEW_REVIEW_REQUIRED", failure_reason="configured skew policy rejected execution")
        failures = set(failure_partition_ids)
        slots = min(policy.max_worker_slots, policy.resource_budget.max_worker_slots, max(1, len(plan.partitions)))
        results: list[PartitionResult] = []
        pending: dict[Future[PartitionResult], PartitionDescriptor] = {}
        iterator = iter(sorted(plan.partitions, key=lambda item: item.partition_id))
        with ThreadPoolExecutor(max_workers=slots, thread_name_prefix="step24-partition") as executor:
            for _ in range(slots):
                try:
                    part = next(iterator)
                except StopIteration:
                    break
                pending[executor.submit(self._run_partition, dataset, plan, part, policy, should_fail=part.partition_id in failures)] = part
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: pending[item].partition_id):
                    results.append(future.result())
                    pending.pop(future)
                    try:
                        part = next(iterator)
                    except StopIteration:
                        continue
                    pending[executor.submit(self._run_partition, dataset, plan, part, policy, should_fail=part.partition_id in failures)] = part
        failed = [item for item in results if item.status is not PartitionResultStatus.SUCCEEDED]
        if failed:
            manifest = self._incomplete_manifest(plan, results, status=ScaleExecutionStatus.INCOMPLETE, reason=failed[0].failure_code or "PARTITION_FAILED")
            return ScaleExecutionResult(execution_id=stable_id("scale-execution", {"plan": plan.partition_plan_id, "results": sorted(item.semantic_result_hash for item in results)}), run_id=plan.run_id, operation_id=plan.operation_id, mode=ScaleExecutionMode.PARTITIONED_LOCAL, input_id=plan.input_id, input_content_hash=plan.input_content_hash, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, status=ScaleExecutionStatus.INCOMPLETE, partition_results=tuple(sorted(results, key=lambda item: item.partition_id)), merge_manifest=manifest, observed_worker_slots=self.max_observed_concurrency, peak_memory_mb=max((item.peak_memory_mb or 0 for item in results), default=0), failure_code=failed[0].failure_code, failure_reason=failed[0].failure_reason)
        try:
            merged, manifest = self.merge(dataset, plan, policy, results)
        except PartitionMergeError as exc:
            manifest = self._incomplete_manifest(plan, results, status=ScaleExecutionStatus.FAILED, reason=str(exc))
            return ScaleExecutionResult(execution_id=stable_id("scale-execution", {"plan": plan.partition_plan_id, "results": sorted(item.semantic_result_hash for item in results)}), run_id=plan.run_id, operation_id=plan.operation_id, mode=ScaleExecutionMode.PARTITIONED_LOCAL, input_id=plan.input_id, input_content_hash=plan.input_content_hash, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, status=ScaleExecutionStatus.FAILED, partition_results=tuple(sorted(results, key=lambda item: item.partition_id)), merge_manifest=manifest, observed_worker_slots=self.max_observed_concurrency, peak_memory_mb=max((item.peak_memory_mb or 0 for item in results), default=0), failure_code="MERGE_FAILED", failure_reason=str(exc))
        return ScaleExecutionResult(execution_id=stable_id("scale-execution", {"plan": plan.partition_plan_id, "output": merged.semantic_hash}), run_id=plan.run_id, operation_id=plan.operation_id, mode=ScaleExecutionMode.PARTITIONED_LOCAL, input_id=plan.input_id, input_content_hash=plan.input_content_hash, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, status=ScaleExecutionStatus.SUCCEEDED, partition_results=tuple(sorted(results, key=lambda item: item.partition_id)), merge_manifest=manifest, merged_output=merged, observed_worker_slots=self.max_observed_concurrency, peak_memory_mb=max((item.peak_memory_mb or 0 for item in results), default=0), output_artifact_refs=tuple(sorted(ref for item in results for ref in item.output_artifact_refs)), provenance_refs=dataset.descriptor.provenance_refs)

    def reference_reduce(self, dataset: ScaleInputDataset, policy: ScalePolicy) -> ScaleMergedOutput:
        return self._merge_outputs(dataset, policy, [self._output_for_rows(dataset.rows, dataset, policy, strategy=PartitionStrategy.BLOCK_KEY)])

    def _merge_outputs(self, dataset: ScaleInputDataset, policy: ScalePolicy, outputs: Sequence[ScalePartitionOutput]) -> ScaleMergedOutput:
        all_refs = tuple(sorted({ref for output in outputs for ref in output.record_refs}))
        source_refs = tuple(sorted({ref for output in outputs for ref in output.source_record_refs}))
        canonical_ids = tuple(sorted({ref for output in outputs for ref in output.canonical_entity_ids}))
        fact_grain = tuple(sorted({ref for output in outputs for ref in output.fact_grain_keys}))
        warehouse = tuple(sorted({ref for output in outputs for ref in output.warehouse_keys}))
        if len(fact_grain) != sum(len(output.fact_grain_keys) for output in outputs):
            raise PartitionMergeError("duplicate fact grain key across partition outputs")
        if len(warehouse) != sum(len(output.warehouse_keys) for output in outputs):
            raise PartitionMergeError("duplicate warehouse key across partition outputs")
        row_count = sum(output.profile.row_count for output in outputs)
        null_count = sum(output.profile.null_count for output in outputs)
        non_null = sum(output.profile.non_null_count for output in outputs)
        mins = [output.profile.minimum for output in outputs if output.profile.minimum is not None]
        maxs = [output.profile.maximum for output in outputs if output.profile.maximum is not None]
        dependency_pairs = tuple(sorted({pair for output in outputs for pair in output.dependency_pairs}))
        lhs_values: dict[str, set[str]] = defaultdict(set)
        for lhs, rhs in dependency_pairs:
            lhs_values[lhs].add(rhs)
        sample_candidates = tuple(sorted({ref for output in outputs for ref in output.sampled_record_refs}, key=lambda ref: stable_digest({"row_ref": ref, "seed": policy.deterministic_seed, "policy": policy.policy_version})))
        sampled = sample_candidates if policy.sample_limit is None else sample_candidates[: policy.sample_limit]
        profile = ScaleProfileStats(row_count=row_count, non_null_count=non_null, null_count=null_count, minimum=min(mins) if mins else None, maximum=max(maxs) if maxs else None, exact_sum=sum((output.exact_measure_sum for output in outputs), Decimal("0")))
        return ScaleMergedOutput(input_id=dataset.descriptor.input_id, input_content_hash=dataset.descriptor.content_hash, record_refs=all_refs, source_record_refs=source_refs, canonical_entity_ids=canonical_ids, fact_grain_keys=fact_grain, warehouse_keys=warehouse, profile=profile, exact_measure_sum=profile.exact_sum, dependency_pairs=dependency_pairs, dependency_valid=all(len(values) <= 1 for values in lhs_values.values()) if lhs_values else None, candidate_pairs=tuple(sorted({pair for output in outputs for pair in output.candidate_pairs})), sampled_record_refs=sampled, input_record_count=dataset.descriptor.row_count, emitted_record_count=len(all_refs), unique_input_records=sum(output.unique_input_records for output in outputs), routed_record_instances=sum(output.routed_record_instances for output in outputs), replication_count=sum(output.replication_count for output in outputs), logical_shuffle_records=sum(output.logical_shuffle_records for output in outputs), logical_shuffle_bytes=sum((output.logical_shuffle_bytes or 0) for output in outputs))

    def _validate_result(self, dataset: ScaleInputDataset, plan: Any, policy: ScalePolicy, partition: PartitionDescriptor, result: PartitionResult) -> None:
        if result.status is not PartitionResultStatus.SUCCEEDED:
            raise PartitionMergeError(f"partition {partition.partition_id} is {result.status.value}")
        if result.failure_code is not None or result.failure_reason is not None:
            raise PartitionMergeError(f"successful partition {partition.partition_id} carries failure metadata")
        if result.partition_id != partition.partition_id or result.partition_plan_id != plan.partition_plan_id or result.partition_plan_hash != plan.partition_plan_hash:
            raise PartitionMergeError("partition result is bound to a different plan or partition")
        if result.input_id != plan.input_id or result.input_content_hash != plan.input_content_hash or result.schema_fingerprint != plan.schema_fingerprint:
            raise PartitionMergeError("partition result has stale input or schema scope")
        if result.output is None:
            raise PartitionMergeError("successful partition result has no semantic output")
        rows = [row for row in dataset.rows if self._bucket(self._route_key(row, plan.strategy), plan.deterministic_seed, plan.partition_count) == partition.logical_partition_index] if plan.strategy not in {PartitionStrategy.SINGLE_NODE, PartitionStrategy.RANGE} else ([row for row in dataset.rows if partition.logical_partition_index == 0] if plan.strategy is PartitionStrategy.SINGLE_NODE else [row for row in dataset.rows if min(len(tuple(boundary for boundary in policy.range_boundaries if row.partition_key >= boundary)), plan.partition_count - 1) == partition.logical_partition_index])
        expected = self._output_for_rows(rows, dataset, policy, strategy=plan.strategy)
        if result.output.semantic_hash != expected.semantic_hash:
            raise PartitionMergeError(f"partition {partition.partition_id} semantic output hash mismatch")
        if self.artifact_store is not None:
            for artifact_id in result.output_artifact_refs:
                artifact = self.control_store.get_artifact(artifact_id) if self.control_store is not None else None
                if artifact is None:
                    raise PartitionMergeError("partition result references an unregistered artifact")
                integrity = self.artifact_store.verify(artifact)
                if integrity.state.value != "VERIFIED" or result.output_content_hash != artifact.content_hash:
                    raise PartitionMergeError("partition result artifact failed integrity verification")

    def merge(self, dataset: ScaleInputDataset, plan: Any, policy: ScalePolicy, results: Sequence[PartitionResult]) -> tuple[ScaleMergedOutput, PartitionMergeManifest]:
        grouped: dict[str, list[PartitionResult]] = defaultdict(list)
        for result in results:
            grouped[result.partition_id].append(result)
        if set(grouped) != set(plan.expected_partition_ids):
            missing = sorted(set(plan.expected_partition_ids) - set(grouped))
            unexpected = sorted(set(grouped) - set(plan.expected_partition_ids))
            raise PartitionMergeError(f"INCOMPLETE barrier: missing={missing}, unexpected={unexpected}")
        selected: list[PartitionResult] = []
        for pid in sorted(plan.expected_partition_ids):
            values = grouped[pid]
            hashes = {item.semantic_result_hash for item in values}
            if len(hashes) > 1:
                raise PartitionMergeError(f"CONFLICTING_RESULT for {pid}")
            selected.append(values[0])
        for partition in plan.partitions:
            self._validate_result(dataset, plan, policy, partition, next(item for item in selected if item.partition_id == partition.partition_id))
        merged = self._merge_outputs(dataset, policy, [item.output for item in selected if item.output is not None])
        if merged.emitted_record_count != dataset.descriptor.row_count or set(merged.record_refs) != {row.row_ref for row in dataset.rows}:
            raise PartitionMergeError("record accounting does not close")
        if plan.strategy is PartitionStrategy.BLOCK_KEY:
            expected_pairs = set(self._candidate_pairs(dataset.rows, dataset, max_pairs=policy.max_join_pairs))
            if set(merged.candidate_pairs) != expected_pairs:
                raise PartitionMergeError("cross-partition candidate coverage is incomplete")
        result_hashes = {item.partition_id: item.semantic_result_hash for item in selected}
        manifest = PartitionMergeManifest(merge_manifest_id=stable_id("merge-manifest", {"plan": plan.partition_plan_id, "results": sorted(result_hashes.items()), "output": merged.semantic_hash}), partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, expected_partition_ids=plan.expected_partition_ids, result_hashes=result_hashes, merge_reduction_version=self.MERGE_VERSION, merge_ordering="partition_id_ascending", semantic_output_hash=merged.semantic_hash, aggregate_record_count=merged.emitted_record_count, status=ScaleExecutionStatus.SUCCEEDED, provenance_refs=dataset.descriptor.provenance_refs)
        return merged, manifest

    def equivalence_report(self, dataset: ScaleInputDataset, *, local_reference_execution_id: str, local_reference_output: ScaleMergedOutput, partitioned_execution: ScaleExecutionResult, plan: Any, failure_test_evidence: dict[str, bool] | None = None, materialization_equivalence: bool | None = None) -> ScaleEquivalenceReport:
        scaled = partitioned_execution.merged_output
        same = scaled is not None and partitioned_execution.status is ScaleExecutionStatus.SUCCEEDED
        record_ok = bool(same and scaled and scaled.input_record_count == dataset.descriptor.row_count and scaled.emitted_record_count == dataset.descriptor.row_count and scaled.unique_input_records == dataset.descriptor.row_count)
        key_ok = bool(same and scaled and set(scaled.record_refs) == {row.row_ref for row in dataset.rows} and len(scaled.fact_grain_keys) == len(set(scaled.fact_grain_keys)) and len(scaled.warehouse_keys) == len(set(scaled.warehouse_keys)))
        aggregate_ok = bool(same and scaled and scaled.exact_measure_sum == local_reference_output.exact_measure_sum and scaled.profile == local_reference_output.profile)
        lineage_ok = bool(same and scaled and set(scaled.source_record_refs) == set(local_reference_output.source_record_refs) and set(scaled.canonical_entity_ids) == set(local_reference_output.canonical_entity_ids) and set(scaled.candidate_pairs) == set(local_reference_output.candidate_pairs))
        deterministic = {"semantic_output": bool(same and scaled and scaled.semantic_hash == local_reference_output.semantic_hash), "partition_plan": plan.partition_plan_hash == plan.semantic_hash, "merge_complete": partitioned_execution.merge_manifest.status is ScaleExecutionStatus.SUCCEEDED}
        failure_evidence = failure_test_evidence or {}
        all_checks = record_ok and key_ok and aggregate_ok and lineage_ok and all(deterministic.values()) and all(failure_evidence.values() or [False]) and (materialization_equivalence is not False)
        status = EquivalenceStatus.PASS if all_checks else EquivalenceStatus.FAIL
        content = {"operation": plan.operation_id, "reference": local_reference_output.semantic_hash, "scaled": None if scaled is None else scaled.semantic_hash, "plan": plan.partition_plan_hash, "checks": {"record": record_ok, "key": key_ok, "aggregate": aggregate_ok, "lineage": lineage_ok, **deterministic}, "failure": failure_evidence, "materialization": materialization_equivalence}
        report_id = stable_id("scale-equivalence", content)
        return ScaleEquivalenceReport(report_id=report_id, operation_id=plan.operation_id, local_reference_execution_id=local_reference_execution_id, local_reference_output_hash=local_reference_output.semantic_hash, partitioned_execution_id=partitioned_execution.execution_id, partitioned_output_hash=None if scaled is None else scaled.semantic_hash, partition_plan_id=plan.partition_plan_id, partition_plan_hash=plan.partition_plan_hash, partition_count=plan.partition_count, comparison_policy="semantic set/multiset identity with exact Decimal aggregates", record_accounting={"input": dataset.descriptor.row_count, "emitted": 0 if scaled is None else scaled.emitted_record_count, "closed": record_ok}, key_topology_equivalence=key_ok, aggregate_equivalence=aggregate_ok, lineage_equivalence=lineage_ok, ordering_policy="partition IDs and stable IDs sorted; physical order ignored", determinism_evidence=deterministic, skew_evidence=plan.skew.model_dump(mode="json"), shuffle_evidence={"logical_records": 0 if scaled is None else scaled.logical_shuffle_records, "logical_bytes": 0 if scaled is None else scaled.logical_shuffle_bytes, "replication": 0 if scaled is None else scaled.replication_count}, failure_test_evidence=failure_evidence, materialization_equivalence=materialization_equivalence, status=status, g7a_eligible=status is EquivalenceStatus.PASS, provenance_refs=dataset.descriptor.provenance_refs + (plan.partition_plan_id,))


class LocalPartitionExecutor:
    """Thin local adapter; it executes data partitions, not jobs or stages."""

    def __init__(self, service: ScaleService) -> None:
        self.service = service

    def execute(self, dataset: ScaleInputDataset, plan: Any, policy: ScalePolicy, *, failure_partition_ids: Iterable[str] = ()) -> ScaleExecutionResult:
        return self.service.execute(dataset, plan, policy, failure_partition_ids=failure_partition_ids)


__all__ = [
    "LargeJoinFanoutError",
    "LocalPartitionExecutor",
    "PartitionExecutorPort",
    "PartitionMergeError",
    "ScaleAuthorizationError",
    "ScaleError",
    "ScaleService",
]
