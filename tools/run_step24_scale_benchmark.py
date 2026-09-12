"""Run the bounded Step24 scale-semantics reference benchmark.

This is a correctness/equivalence fixture, not a capacity or cluster test.
"""

from __future__ import annotations

import json
import hashlib
import os
import platform as host_platform
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.distributed import PartitionMergeError, ScaleService
from dirty_data_to_olap.domain.contracts.distributed import (
    ScaleEquivalenceReport,
    ScaleExecutionMode,
    ScaleExecutionStatus,
    ScaleInputDataset,
    ScaleInputDescriptor,
    ScaleInputRow,
    ScalePolicy,
    StageScaleSupport,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactDependency,
    ArtifactManifest,
    ArtifactStorageMode,
    GateEvidenceStatus,
    LocalPlatformConfig,
    ResourceBudget,
    RetentionClass,
    RunRecord,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.platform import LocalPlatform


RUN_ID = "step24-scale-benchmark-run"
INPUT_ID = "step24-order-lines-input-v2"
POLICY_VERSION = "step24-benchmark-policy-v2"
OUTPUT_ROOT = ROOT / "workspace" / "runs" / RUN_ID / "scale"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _publish_json(platform: LocalPlatform, *, artifact_id: str, kind: str, payload: object, dependencies: tuple[str, ...] = ()):
    payload_bytes = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    existing = platform.control_store.get_artifact(artifact_id)
    if existing is not None:
        if existing.content_hash != hashlib.sha256(payload_bytes).hexdigest() or existing.byte_size != len(payload_bytes):
            raise RuntimeError(f"immutable benchmark artifact {artifact_id} already has different content")
        if platform.artifact_store.verify(existing).state.value != "VERIFIED":
            raise RuntimeError(f"existing benchmark artifact {artifact_id} failed integrity verification")
        return existing
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        run_id=RUN_ID,
        stage_id="STEP24_SCALE",
        attempt_id="step24-benchmark-attempt",
        artifact_kind=kind,
        media_type="application/json",
        producer="tools.run_step24_scale_benchmark",
        storage_mode=ArtifactStorageMode.MANAGED,
        logical_key=f"runs/{RUN_ID}/step24/{artifact_id}.json",
        retention_class=RetentionClass.RUN_SCOPED,
    )
    ref = platform.artifact_store.publish(manifest, payload_bytes)
    dependency_models = []
    for upstream_id in dependencies:
        upstream = platform.control_store.get_artifact(upstream_id)
        if upstream is not None:
            dependency_models.append(ArtifactDependency(artifact_id=ref.artifact_id, upstream_artifact_id=upstream.artifact_id, expected_content_hash=upstream.content_hash, relationship_kind="DERIVED_FROM"))
    if dependency_models:
        platform.control_store.register_artifact_with_dependencies(ref, dependency_models)
    else:
        platform.control_store.register_artifact(ref)
    return ref


def _dataset(gate) -> ScaleInputDataset:
    rows = []
    for index in range(12_000):
        # The hot key is intentional: it makes skew visible without claiming
        # that this small synthetic fixture measures production capacity.
        key = "hot-customer" if index < 4_875 else f"customer-{index % 95:03d}"
        rows.append(
            ScaleInputRow(
                row_ref=f"order-line-{index:06d}",
                table_id="order_lines",
                schema_fingerprint="schema-order-lines-v2",
                partition_key=key,
                measure=Decimal((index % 17) + 1),
                profile_values={"status": "settled" if index % 3 else None},
                source_record_refs=(f"retail-source/order-lines/{index:06d}",),
                fact_grain_key=f"order-line-grain-{index:06d}",
                warehouse_key=f"order-line-key-{index:06d}",
                dependency_lhs=f"order-{index:04d}",
                dependency_rhs="customer",
            )
        )
    input_hash = stable_digest(sorted(stable_digest(row.model_dump(mode="json")) for row in rows))
    descriptor = ScaleInputDescriptor(
        input_id=INPUT_ID,
        run_id=RUN_ID,
        source_id="retail-source",
        snapshot_id="retail-snapshot-v2",
        table_id="order_lines",
        dataset_id="retail-order-lines",
        dataset_version="v2",
        stage_id="PROFILING",
        schema_fingerprint="schema-order-lines-v2",
        content_hash=input_hash,
        row_count=len(rows),
        estimated_bytes=sum(len(json.dumps(row.model_dump(mode="json"), separators=(",", ":"))) for row in rows),
        reference_gate_run_id=gate.run_id,
        reference_validation_report_artifact_id=gate.validation_report_artifact_id,
        reference_validation_report_run_id=gate.validation_report_run_id,
        reference_validation_report_id=gate.validation_report_id,
        reference_validation_report_hash=gate.validation_report_content_hash,
        provenance_refs=("step22:exact-validation-report", "step24:synthetic-reference-fixture"),
    )
    return ScaleInputDataset(descriptor=descriptor, rows=tuple(rows))


def _policy() -> ScalePolicy:
    return ScalePolicy(
        policy_version=POLICY_VERSION,
        local_row_threshold=1_000,
        local_byte_threshold=1_000_000,
        target_partition_rows=2_000,
        max_partition_rows=6_000,
        max_partitions=8,
        max_worker_slots=4,
        resource_budget=ResourceBudget(max_worker_slots=4, memory_budget_mb=512, temporary_space_budget_bytes=100_000_000, max_artifact_bytes=20_000_000),
        requested_mode=ScaleExecutionMode.PARTITIONED_LOCAL,
        stage_support={"PROFILING": StageScaleSupport.PARTITION_SAFE},
        deterministic_seed=24,
        skew_ratio_threshold=2.0,
        skew_action="REVIEW_REQUIRED",
        sample_ratio=0.25,
        sample_limit=100,
        max_join_pairs=50_000,
        provisional=True,
        provenance_refs=("step24:bounded-reference-policy",),
    )


def main() -> int:
    platform = LocalPlatform.from_project_root(ROOT, resource_budget=ResourceBudget(max_worker_slots=4, disk_budget_bytes=50_000_000, max_staged_bytes=20_000_000))
    try:
        gate = platform.control_store.get_gate_evidence("G6_DATA_CORRECTNESS", run_id="step23-platform-reference-run")
        if gate is None or gate.status is not GateEvidenceStatus.PASS or not gate.eligible:
            raise RuntimeError("the exact persisted Step23 G6 PASS receipt is unavailable")
        run = platform.control_store.get_run(RUN_ID)
        content_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        if run is None:
            run = platform.create_run(RunRecord(run_id=RUN_ID, project_id="dirty-data-to-olap", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=content_commit, metadata={"scope": "step24-reference-scale", "external_engine": "NONE"}))
        elif run.git_content_commit != content_commit:
            run = platform.control_store.update_run(run.model_copy(update={"git_content_commit": content_commit}), expected_revision=run.revision)
        dataset = _dataset(gate)
        service = ScaleService(artifact_store=platform.artifact_store, control_store=platform.control_store)
        service.authorize_g6(dataset, gate)
        policy = _policy()
        decision = service.assess(dataset, policy, requested_parallelism=4)
        plan = service.plan(dataset, policy, partition_count=8, requested_parallelism=4)

        input_payload = {"descriptor": dataset.descriptor.model_dump(mode="json"), "record_digests": [stable_digest(row.model_dump(mode="json")) for row in sorted(dataset.rows, key=lambda item: item.row_ref)]}
        input_ref = _publish_json(platform, artifact_id=INPUT_ID, kind="Step24ScaleInputManifest", payload=input_payload)
        dataset = dataset.model_copy(update={"descriptor": dataset.descriptor.model_copy(update={"artifact_refs": (input_ref.artifact_id,)})})
        decision = service.assess(dataset, policy, requested_parallelism=4)
        plan = service.plan(dataset, policy, partition_count=8, requested_parallelism=4)
        plan_ref = _publish_json(platform, artifact_id=plan.partition_plan_id, kind="Step24PartitionPlan", payload=plan.model_dump(mode="json"), dependencies=(input_ref.artifact_id,))

        baseline_started = time.perf_counter()
        reference = service.reference_reduce(dataset, policy)
        baseline_seconds = time.perf_counter() - baseline_started
        reference_ref = _publish_json(platform, artifact_id="step24-reference-output-v2", kind="Step24ReferenceOutput", payload=reference.model_dump(mode="json"), dependencies=(input_ref.artifact_id,))

        started = time.perf_counter()
        execution = service.execute(dataset, plan, policy)
        scale_seconds = time.perf_counter() - started
        if execution.status is not ScaleExecutionStatus.SUCCEEDED or execution.merged_output is None:
            raise RuntimeError(f"partitioned reference failed: {execution.failure_reason}")

        failure = service.execute(dataset, plan, policy, failure_partition_ids={plan.expected_partition_ids[0]})
        missing_detected = False
        try:
            service.merge(dataset, plan, policy, execution.partition_results[:-1])
        except PartitionMergeError:
            missing_detected = True
        conflicting_detected = False
        try:
            original = execution.partition_results[0]
            changed = original.model_copy(update={"failure_code": "TAMPERED"})
            service.merge(dataset, plan, policy, (original, changed, *execution.partition_results[1:]))
        except PartitionMergeError:
            conflicting_detected = True
        failure_evidence = {"worker_failure_incomplete": failure.status is ScaleExecutionStatus.INCOMPLETE, "missing_partition_rejected": missing_detected, "conflicting_result_rejected": conflicting_detected}

        invariant_outputs = {}
        for count in (1, 2, 3, 7):
            invariant_policy = policy.model_copy(update={"max_worker_slots": 4, "max_partition_rows": 12_000, "resource_budget": policy.resource_budget.model_copy(update={"max_worker_slots": 4})})
            invariant_plan = service.plan(dataset, invariant_policy, partition_count=count, requested_parallelism=4)
            invariant_run = service.execute(dataset, invariant_plan, invariant_policy)
            if invariant_run.status is not ScaleExecutionStatus.SUCCEEDED:
                raise RuntimeError(f"partition-count invariant run failed for {count}")
            invariant_outputs[str(count)] = {"plan_hash": invariant_plan.partition_plan_hash, "output_hash": invariant_run.merged_output.semantic_hash}
        one_worker_policy = policy.model_copy(update={"max_worker_slots": 1, "resource_budget": policy.resource_budget.model_copy(update={"max_worker_slots": 1})})
        one_worker_plan = service.plan(dataset, one_worker_policy, partition_count=8, requested_parallelism=1)
        one_worker = service.execute(dataset, one_worker_plan, one_worker_policy)
        worker_invariant = one_worker.status is ScaleExecutionStatus.SUCCEEDED and one_worker.merged_output.semantic_hash == execution.merged_output.semantic_hash and one_worker_plan.partition_plan_hash == plan.partition_plan_hash
        materialization_equivalence = execution.merged_output.fact_grain_keys == reference.fact_grain_keys and execution.merged_output.warehouse_keys == reference.warehouse_keys and execution.merged_output.exact_measure_sum == reference.exact_measure_sum
        report = service.equivalence_report(dataset, local_reference_execution_id="step24-local-reference", local_reference_output=reference, partitioned_execution=execution, plan=plan, failure_test_evidence={**failure_evidence, "partition_count_invariance": len({item["output_hash"] for item in invariant_outputs.values()}) == 1, "worker_count_invariance": worker_invariant}, materialization_equivalence=materialization_equivalence, verified_content_commit=content_commit)
        if report.status.value != "PASS":
            raise RuntimeError(f"G7A equivalence failed: {report.model_dump(mode='json')}")
        report_ref = _publish_json(platform, artifact_id=report.report_id, kind="Step24ScaleEquivalenceReport", payload=report.model_dump(mode="json"), dependencies=(reference_ref.artifact_id, plan_ref.artifact_id, *[item for result in execution.partition_results for item in result.output_artifact_refs]))

        artifact_bytes = sum(platform.control_store.get_artifact(item.output_artifact_refs[0]).byte_size for item in execution.partition_results if item.output_artifact_refs and platform.control_store.get_artifact(item.output_artifact_refs[0]) is not None)
        _write(OUTPUT_ROOT / "single_node_baseline.json", {"mode": "LOCAL_REFERENCE", "row_count": dataset.descriptor.row_count, "wall_seconds": baseline_seconds, "semantic_output_hash": reference.semantic_hash, "artifact_id": reference_ref.artifact_id, "artifact_bytes": reference_ref.byte_size})
        _write(OUTPUT_ROOT / "scale_policy.json", policy.model_dump(mode="json"))
        _write(OUTPUT_ROOT / "scale_decision.json", decision.model_dump(mode="json"))
        _write(OUTPUT_ROOT / "partition_plan.json", {**plan.model_dump(mode="json"), "semantic_hash": plan.semantic_hash})
        _write(OUTPUT_ROOT / "skew_assessment.json", plan.skew.model_dump(mode="json"))
        for result in execution.partition_results:
            _write(OUTPUT_ROOT / "partition_results" / f"{result.partition_id}.json", {**result.model_dump(mode="json"), "semantic_result_hash": result.semantic_result_hash})
        _write(OUTPUT_ROOT / "merge_manifest.json", {**execution.merge_manifest.model_dump(mode="json"), "semantic_hash": execution.merge_manifest.semantic_hash})
        _write(OUTPUT_ROOT / "scale_execution_result.json", {**execution.model_dump(mode="json"), "invariant_outputs": invariant_outputs})
        _write(OUTPUT_ROOT / "scale_equivalence_report.json", report.model_dump(mode="json"))
        _write(OUTPUT_ROOT / "failure_injection_result.json", failure.model_dump(mode="json"))
        _write(OUTPUT_ROOT / "benchmark_manifest.json", {"run_id": RUN_ID, "input_rows": dataset.descriptor.row_count, "input_estimated_bytes": dataset.descriptor.estimated_bytes, "partition_count": plan.partition_count, "worker_slots": policy.max_worker_slots, "observed_max_concurrency": service.max_observed_concurrency, "memory_budget_mb": policy.resource_budget.memory_budget_mb, "execution_mode": "PARTITIONED_LOCAL", "external_engine_selected": "NONE", "wall_seconds": {"local_reference": baseline_seconds, "partitioned_local": scale_seconds}, "logical_shuffle": report.shuffle_evidence, "partitioned_artifact_bytes": artifact_bytes, "equivalence_report_id": report.report_id, "equivalence_report_hash": stable_digest(report.model_dump(mode="json")), "g7a": report.status.value, "g7": "PENDING", "os": host_platform.system(), "python": host_platform.python_version(), "cpu_logical_count": os.cpu_count(), "source_write": False, "claims": ["semantic correctness fixture only", "not a production capacity benchmark"]})
        print(json.dumps({"status": "PASS", "run_id": RUN_ID, "rows": dataset.descriptor.row_count, "partitions": plan.partition_count, "workers": policy.max_worker_slots, "skew": plan.skew.model_dump(mode="json"), "reference_seconds": baseline_seconds, "partitioned_seconds": scale_seconds, "max_observed_concurrency": service.max_observed_concurrency, "semantic_hash": execution.merged_output.semantic_hash, "equivalence_report_id": report.report_id, "equivalence_report_artifact_id": report_ref.artifact_id, "g7a": report.status.value, "g7": "PENDING", "external_engine_selected": "NONE", "artifact_bytes": artifact_bytes}, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
