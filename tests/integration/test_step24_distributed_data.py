from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.domain.contracts.distributed import ScaleExecutionStatus
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, CacheInputRef, CacheKey, ResourceBudget, RunRecord
from dirty_data_to_olap.platform import LocalPlatform
from tests.step24_support import make_dataset, make_policy
from dirty_data_to_olap.application.distributed import ScaleService


def _platform(tmp_path: Path) -> LocalPlatform:
    platform = LocalPlatform.from_project_root(tmp_path, resource_budget=ResourceBudget(max_worker_slots=2, disk_budget_bytes=20_000_000, max_staged_bytes=20_000_000))
    platform.create_run(RunRecord(run_id="step24-reference-run", project_id="dirty-data-to-olap", configuration_fingerprint=platform.config.configuration_fingerprint))
    return platform


def test_local_reference_and_partitioned_aggregate_flow_are_equivalent(tmp_path: Path) -> None:
    platform = _platform(tmp_path)
    try:
        data = make_dataset()
        policy = make_policy(workers=2, partitions=3)
        service = ScaleService(artifact_store=platform.artifact_store, control_store=platform.control_store)
        plan = service.plan(data, policy, partition_count=3)
        execution = service.execute(data, plan, policy)
        report = service.equivalence_report(data, local_reference_execution_id="local-reference-1", local_reference_output=service.reference_reduce(data, policy), partitioned_execution=execution, plan=plan, failure_test_evidence={"missing_partition": True, "worker_failure": True, "conflicting_result": True})
        assert execution.status is ScaleExecutionStatus.SUCCEEDED
        assert report.g7a_eligible is True
        assert len(platform.control_store.list_artifacts(run_id="step24-reference-run", stage_id="STEP24_SCALE")) == 3
        assert all(platform.control_store.inspect_dependencies(ref.artifact_id).status == "RESOLVED" for ref in platform.control_store.list_artifacts(run_id="step24-reference-run", stage_id="STEP24_SCALE"))
    finally:
        platform.close()


def test_partition_artifacts_and_cache_recover_after_reopen(tmp_path: Path) -> None:
    platform = _platform(tmp_path)
    data = make_dataset()
    policy = make_policy(workers=2, partitions=3)
    service = ScaleService(artifact_store=platform.artifact_store, control_store=platform.control_store)
    plan = service.plan(data, policy, partition_count=3)
    first = service.execute(data, plan, policy)
    artifact_ids = [ref for result in first.partition_results for ref in result.output_artifact_refs]
    cache_key = service.cache_key_for(data, plan, plan.partitions[0], policy)
    platform.close()
    reopened = LocalPlatform.from_project_root(tmp_path, resource_budget=ResourceBudget(max_worker_slots=2, disk_budget_bytes=20_000_000, max_staged_bytes=20_000_000))
    try:
        assert all(reopened.control_store.get_artifact(artifact_id) is not None for artifact_id in artifact_ids)
        assert reopened.control_store.get_cache_entry(cache_key) is not None
        second = ScaleService(artifact_store=reopened.artifact_store, control_store=reopened.control_store).execute(data, plan, policy)
        assert second.merged_output.semantic_hash == first.merged_output.semantic_hash
    finally:
        reopened.close()


def test_worker_failure_preserves_successful_partition_artifacts_and_reruns_idempotently(tmp_path: Path) -> None:
    platform = _platform(tmp_path)
    try:
        data = make_dataset()
        policy = make_policy(workers=2, partitions=3)
        service = ScaleService(artifact_store=platform.artifact_store, control_store=platform.control_store)
        plan = service.plan(data, policy, partition_count=3)
        failed = service.execute(data, plan, policy, failure_partition_ids={plan.expected_partition_ids[0]})
        assert failed.status is ScaleExecutionStatus.INCOMPLETE
        assert any(item.status.value == "FAILED" for item in failed.partition_results)
        successful = {item.partition_id for item in failed.partition_results if item.status.value == "SUCCEEDED"}
        assert successful
        retry = service.execute(data, plan, policy)
        assert retry.status is ScaleExecutionStatus.SUCCEEDED
        assert retry.merged_output.semantic_hash == service.reference_reduce(data, policy).semantic_hash
        assert {item.partition_id for item in retry.partition_results} >= successful
    finally:
        platform.close()


def test_materialization_semantic_keys_are_preserved_without_writing_source(tmp_path: Path) -> None:
    platform = _platform(tmp_path)
    try:
        data = make_dataset(count=12)
        policy = make_policy(workers=2, partitions=3)
        service = ScaleService(artifact_store=platform.artifact_store, control_store=platform.control_store)
        plan = service.plan(data, policy, partition_count=3)
        result = service.execute(data, plan, policy)
        assert result.merged_output.fact_grain_keys == tuple(f"fact-{i:04d}" for i in range(12))
        assert result.merged_output.warehouse_keys == tuple(f"warehouse-{i:04d}" for i in range(12))
        assert not list(tmp_path.glob("*.sql"))
    finally:
        platform.close()
