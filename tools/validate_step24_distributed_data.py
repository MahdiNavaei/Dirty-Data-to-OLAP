"""Behavioral validator for Step24's meaning-preserving scale boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.adapters.platform import LocalCapabilityRegistry
from dirty_data_to_olap.application.distributed import LargeJoinFanoutError, PartitionMergeError, ScaleAuthorizationError, ScaleService
from dirty_data_to_olap.domain.contracts.distributed import ScaleExecutionStatus, ScaleInputDataset, StageScaleSupport
from dirty_data_to_olap.domain.contracts.platform import CapabilityQuery
from dirty_data_to_olap.platform import LocalPlatform

from tests.step24_support import make_dataset, make_gate, make_policy


def _check(checks: list[dict[str, object]], name: str, condition: bool, detail: str) -> None:
    checks.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})


def main() -> int:
    checks: list[dict[str, object]] = []
    service = ScaleService()
    data = make_dataset(count=12)
    policy = make_policy(workers=2, partitions=3)
    plan = service.plan(data, policy, partition_count=3)
    execution = service.execute(data, plan, policy)
    reference = service.reference_reduce(data, policy)

    platform = LocalPlatform.from_project_root(ROOT)
    try:
        gate = platform.control_store.get_gate_evidence("G6_DATA_CORRECTNESS", run_id="step23-platform-reference-run")
        _check(checks, "current typed G6 permit", gate is not None and gate.status.value == "PASS" and gate.eligible, "the validator reads the persisted Step23 G6 receipt")
        if gate is not None:
            try:
                service.authorize_g6(data, gate)
                g6_authorized = True
            except ScaleAuthorizationError:
                g6_authorized = False
        else:
            g6_authorized = False
        _check(checks, "exact G6 identity binding", g6_authorized, "run/report/artifact/hash identity matches the typed input descriptor")
    finally:
        platform.close()

    try:
        service.authorize_g6(data, make_gate().model_copy(update={"validation_report_id": "legacy:old"}))
        legacy_rejected = False
    except ScaleAuthorizationError:
        legacy_rejected = True
    _check(checks, "legacy G6 receipt rejected", legacy_rejected, "legacy or unverified receipt identity cannot authorize scale")
    _check(checks, "scale decision deterministic", service.assess(data, policy).decision_id == service.assess(data, policy).decision_id, "decision identity is derived from typed input, policy and measured scope")
    _check(checks, "partition plan deterministic", plan.partition_plan_hash == service.plan(data, policy, partition_count=3).partition_plan_hash == plan.semantic_hash, "plan hash and partition identities are stable")
    _check(checks, "local and partitioned semantics equivalent", execution.status is ScaleExecutionStatus.SUCCEEDED and execution.merged_output.semantic_hash == reference.semantic_hash, "exact profile, aggregate, identity, lineage and accounting meaning match")

    one_worker_policy = make_policy(workers=1, partitions=3)
    one_worker_plan = service.plan(data, one_worker_policy, partition_count=3)
    one_worker = service.execute(data, one_worker_plan, one_worker_policy)
    _check(checks, "worker-count invariance", one_worker_plan.partition_plan_hash == plan.partition_plan_hash and one_worker.merged_output.semantic_hash == execution.merged_output.semantic_hash, "worker scheduling does not alter partition identity or semantic output")
    _check(checks, "bounded worker slots observed", service.max_observed_concurrency <= policy.max_worker_slots, f"observed={service.max_observed_concurrency}, configured={policy.max_worker_slots}")

    samples = []
    for count in (1, 2, 3, 7):
        count_policy = make_policy(workers=2, partitions=count, sample_ratio=0.5, sample_limit=5)
        count_data = make_dataset(count=12)
        count_plan = service.plan(count_data, count_policy, partition_count=count)
        count_execution = service.execute(count_data, count_plan, count_policy)
        samples.append((service.sample_record_refs(count_data, count_policy), count_execution.merged_output.semantic_hash))
    _check(checks, "partition-count invariance", len({item[1] for item in samples}) == 1, "partition counts 1/2/3/7 preserve the merged semantic output")
    _check(checks, "global sampling invariance", len({item[0] for item in samples}) == 1, "stable hash sampling is independent of partition count and order")

    candidate_data = make_dataset(stage_id="entity_resolution", candidate_fixture=True, count=9)
    candidate_policy = make_policy(stage_id="entity_resolution", workers=2, partitions=3)
    candidate_plan = service.plan(candidate_data, candidate_policy, partition_count=3)
    candidate_execution = service.execute(candidate_data, candidate_plan, candidate_policy)
    _check(checks, "cross-partition candidate retained", candidate_execution.status is ScaleExecutionStatus.SUCCEEDED and len(candidate_execution.merged_output.candidate_pairs) == len(set(candidate_execution.merged_output.candidate_pairs)), "block-key routing retains each unordered candidate pair exactly once")
    try:
        service.reject_join_fanout((("dimension", "left"), ("dimension", "right")), max_pairs=1)
        fanout_rejected = False
    except LargeJoinFanoutError:
        fanout_rejected = True
    _check(checks, "large join fanout rejected", fanout_rejected, "bounded join policy fails closed before all-pairs expansion")

    out_of_order = tuple(reversed(execution.partition_results)) + (execution.partition_results[0],)
    _check(checks, "out-of-order merge and idempotent duplicate", service.merge(data, plan, policy, out_of_order)[0].semantic_hash == execution.merged_output.semantic_hash, "valid merge order is explicit and same-hash duplicate is harmless")
    try:
        service.merge(data, plan, policy, execution.partition_results[:-1])
        missing_rejected = False
    except PartitionMergeError:
        missing_rejected = True
    _check(checks, "missing partition blocks COMPLETE", missing_rejected, "the exact merge barrier rejects incomplete result sets")
    try:
        original = execution.partition_results[0]
        service.merge(data, plan, policy, (original, original.model_copy(update={"failure_code": "TAMPERED"}), *execution.partition_results[1:]))
        conflict_rejected = False
    except PartitionMergeError:
        conflict_rejected = True
    _check(checks, "conflicting duplicate rejected", conflict_rejected, "conflicting same-partition semantic hashes never use first/last writer wins")

    foreign = execution.partition_results[0].model_copy(update={"input_id": "foreign-input"})
    try:
        service.merge(data, plan, policy, (foreign, *execution.partition_results[1:]))
        foreign_rejected = False
    except PartitionMergeError:
        foreign_rejected = True
    _check(checks, "foreign result rejected", foreign_rejected, "wrong input/run scope cannot satisfy the merge barrier")
    stale = data.model_copy(update={"descriptor": data.descriptor.model_copy(update={"content_hash": "c" * 64})})
    try:
        service.execute(stale, plan, policy)
        stale_rejected = False
    except ScaleAuthorizationError:
        stale_rejected = True
    _check(checks, "stale input rejected", stale_rejected, "execution fails closed against a changed input hash")
    schema_rejected = False
    try:
        ScaleInputDataset(descriptor=data.descriptor, rows=(data.rows[0].model_copy(update={"schema_fingerprint": "other"}), *data.rows[1:]))
    except ValueError:
        schema_rejected = True
    _check(checks, "schema mismatch rejected", schema_rejected, "row schema cannot cross the typed dataset closure")

    skew_policy = make_policy(workers=2, partitions=3, skew_action="REVIEW_REQUIRED").model_copy(update={"skew_ratio_threshold": 1.1})
    hot_rows = tuple(row.model_copy(update={"partition_key": "one-hot-key"}) for row in data.rows)
    hot = ScaleInputDataset(descriptor=data.descriptor, rows=hot_rows)
    hot_plan = service.plan(hot, skew_policy, partition_count=3)
    _check(checks, "skew is measured and visible", hot_plan.skew.status.value == "SKEWED" and hot_plan.skew.action.value == "REVIEW_REQUIRED", "policy threshold exposes skew rather than hiding it")
    _check(checks, "global dependency reduction is not laundered", execution.merged_output.dependency_valid is False, "the union detects a dependency false globally even when local groups can look consistent")
    _check(checks, "materialization grain and warehouse keys close", len(execution.merged_output.fact_grain_keys) == len(set(execution.merged_output.fact_grain_keys)) == data.descriptor.row_count and len(execution.merged_output.warehouse_keys) == len(set(execution.merged_output.warehouse_keys)) == data.descriptor.row_count, "fact grain and warehouse keys are preserved without silent deduplication")
    _check(checks, "validation aggregate equivalence", execution.merged_output.profile == reference.profile and execution.merged_output.exact_measure_sum == reference.exact_measure_sum, "merge-safe profile fields and exact Decimal aggregate match the local reference")
    _check(checks, "record accounting closes", execution.merged_output.input_record_count == execution.merged_output.emitted_record_count == execution.merged_output.unique_input_records == data.descriptor.row_count and execution.merged_output.replication_count == 0, "no unexplained loss, duplication or replicated-record inflation")

    benchmark_report = ROOT / "workspace" / "runs" / "step24-scale-benchmark-run" / "scale" / "scale_equivalence_report.json"
    failure_artifact = ROOT / "workspace" / "runs" / "step24-scale-benchmark-run" / "scale" / "failure_injection_result.json"
    report_payload = json.loads(benchmark_report.read_text(encoding="utf-8")) if benchmark_report.exists() else {}
    failed_payload = json.loads(failure_artifact.read_text(encoding="utf-8")) if failure_artifact.exists() else {}
    _check(checks, "benchmark G7A report is inspected", report_payload.get("status") == "PASS" and report_payload.get("g7a_eligible") is True, "generated equivalence report is read from the ignored benchmark output")
    _check(checks, "failed partition evidence is inspected", failed_payload.get("status") == "INCOMPLETE" and failed_payload.get("failure_code"), "failure-injection output remains explicit and is not treated as a complete result")

    capabilities = LocalCapabilityRegistry()
    _check(checks, "partitioned-local capability is honest", capabilities.get(CapabilityQuery(capability_id="distributed.partitioned_local")).status == "REFERENCE_TESTED", "only the executable local reference is reported as tested")
    _check(checks, "external engines are not falsely executed", all(capabilities.get(CapabilityQuery(capability_id=f"distributed.{name}")).status == "FUTURE_NOT_EXECUTED" for name in ("spark", "ray", "multi_node")), "Spark, Ray and multi-node remain explicit future capability records")
    _check(checks, "Step25 remains unstarted", not (ROOT / "src/dirty_data_to_olap/application/ux.py").exists(), "no Step25 review/product surface was added")
    _check(checks, "Step28 job control remains unstarted", not (ROOT / "src/dirty_data_to_olap/application/stage_orchestrator.py").exists() and not (ROOT / "src/dirty_data_to_olap/application/job_control.py").exists(), "no scheduler, queue or job-control implementation was added")

    passed = all(item["status"] == "PASS" for item in checks)
    output = {"validator": "step24-distributed-data", "status": "PASS" if passed else "FAIL", "checks": checks, "check_count": len(checks), "inspected": {"benchmark_report": str(benchmark_report), "failure_artifact": str(failure_artifact), "partition_plan_hash": plan.partition_plan_hash, "merged_output_hash": execution.merged_output.semantic_hash, "max_observed_concurrency": service.max_observed_concurrency}}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
