from __future__ import annotations

import pytest

from dirty_data_to_olap.application.distributed import LargeJoinFanoutError, PartitionMergeError, ScaleAuthorizationError, ScaleError
from dirty_data_to_olap.domain.contracts.distributed import (
    EquivalenceStatus,
    PartitionResultStatus,
    ScaleExecutionMode,
    ScaleExecutionStatus,
    ScaleInputDataset,
    ScaleInputRow,
    ScalePolicy,
    StageScaleSupport,
)
from dirty_data_to_olap.domain.contracts.platform import ResourceBudget
from tests.step24_support import make_dataset, make_gate, make_policy, service


def test_legacy_g6_receipts_cannot_authorize_scale() -> None:
    gate = make_gate().model_copy(update={"validation_report_id": "legacy:old-report", "provenance_refs": ("legacy:gate-evidence-unverified",)})
    with pytest.raises(ScaleAuthorizationError):
        service().authorize_g6(make_dataset(), gate)


def test_plan_and_partition_ids_are_stable_under_input_reordering() -> None:
    first = make_dataset()
    reordered = make_dataset(row_order=list(reversed(range(12))))
    policy = make_policy(partitions=3)
    first_plan = service().plan(first, policy, partition_count=3)
    second_plan = service().plan(reordered, policy, partition_count=3)
    assert first_plan.partition_plan_id == second_plan.partition_plan_id
    assert first_plan.partition_plan_hash == second_plan.partition_plan_hash
    assert first_plan.expected_partition_ids == second_plan.expected_partition_ids
    assert [item.expected_row_digest for item in first_plan.partitions] == [item.expected_row_digest for item in second_plan.partitions]


def test_partition_count_and_worker_count_do_not_change_semantic_output() -> None:
    data = make_dataset()
    s = service()
    reference = s.reference_reduce(data, make_policy(workers=4, partitions=7))
    outputs = []
    for count in (1, 2, 3, 7):
        policy = make_policy(workers=2, partitions=count)
        plan = s.plan(data, policy, partition_count=count)
        result = s.execute(data, plan, policy)
        assert result.status is ScaleExecutionStatus.SUCCEEDED
        outputs.append(result.merged_output.semantic_hash)
    assert len(set(outputs)) == 1
    assert outputs[0] == reference.semantic_hash

    one_worker_policy = make_policy(workers=1, partitions=3)
    many_worker_policy = make_policy(workers=2, partitions=3)
    one_plan = s.plan(data, one_worker_policy, partition_count=3)
    many_plan = s.plan(data, many_worker_policy, partition_count=3)
    assert one_plan.expected_partition_ids == many_plan.expected_partition_ids
    assert s.execute(data, one_plan, one_worker_policy).merged_output.semantic_hash == s.execute(data, many_plan, many_worker_policy).merged_output.semantic_hash


def test_global_sampling_is_partition_invariant() -> None:
    data = make_dataset(count=40)
    s = service()
    policy = make_policy(workers=2, partitions=7, sample_ratio=0.5, sample_limit=8)
    expected = s.sample_record_refs(data, policy)
    for count in (1, 2, 3, 7):
        plan = s.plan(data, policy, partition_count=count)
        result = s.execute(data, plan, policy)
        assert result.merged_output.sampled_record_refs == expected


def test_empty_and_all_null_profile_partitions_merge_exactly() -> None:
    data = make_dataset(count=2)
    data = ScaleInputDataset(descriptor=data.descriptor, rows=tuple(row.model_copy(update={"measure": None}) for row in data.rows))
    policy = make_policy(partitions=7)
    result = service().execute(data, service().plan(data, policy, partition_count=7), policy)
    assert result.status is ScaleExecutionStatus.SUCCEEDED
    assert result.merged_output.profile.row_count == 2
    assert result.merged_output.profile.null_count == 2
    assert result.merged_output.profile.minimum is None


def test_out_of_order_merge_accepts_idempotent_duplicate_but_rejects_conflict() -> None:
    data = make_dataset()
    s = service()
    policy = make_policy(partitions=3)
    plan = s.plan(data, policy, partition_count=3)
    execution = s.execute(data, plan, policy)
    reversed_results = tuple(reversed(execution.partition_results)) + (execution.partition_results[0],)
    merged, _ = s.merge(data, plan, policy, reversed_results)
    assert merged.semantic_hash == execution.merged_output.semantic_hash
    conflicting = execution.partition_results[0].model_copy(update={"failure_code": "TAMPERED"})
    with pytest.raises(PartitionMergeError):
        s.merge(data, plan, policy, (conflicting, *execution.partition_results[1:]))


def test_missing_stale_and_foreign_partition_results_fail_closed() -> None:
    data = make_dataset()
    s = service()
    policy = make_policy(partitions=3)
    plan = s.plan(data, policy, partition_count=3)
    execution = s.execute(data, plan, policy)
    with pytest.raises(PartitionMergeError):
        s.merge(data, plan, policy, execution.partition_results[:-1])
    foreign_plan = s.plan(make_dataset(stage_id="profiling"), policy, partition_count=2)
    foreign = s.execute(data, plan, policy).partition_results[0].model_copy(update={"partition_plan_id": foreign_plan.partition_plan_id})
    with pytest.raises(PartitionMergeError):
        s.merge(data, plan, policy, (foreign, *execution.partition_results[1:]))
    stale = data.model_copy(update={"descriptor": data.descriptor.model_copy(update={"content_hash": "c" * 64})})
    with pytest.raises(ScaleAuthorizationError):
        s.execute(stale, plan, policy)


def test_global_dependency_reduction_does_not_launder_a_false_dependency() -> None:
    data = make_dataset(count=10)
    policy = make_policy(partitions=3)
    s = service()
    plan = s.plan(data, policy, partition_count=3)
    result = s.execute(data, plan, policy)
    assert result.merged_output.dependency_valid is False
    assert len(result.merged_output.dependency_pairs) > 2


def test_block_key_routing_retains_cross_partition_candidates_once() -> None:
    data = make_dataset(stage_id="entity_resolution", candidate_fixture=True, count=9)
    policy = make_policy(stage_id="entity_resolution", partitions=3)
    s = service()
    plan = s.plan(data, policy, partition_count=3)
    assert plan.strategy.value == "BLOCK_KEY"
    result = s.execute(data, plan, policy)
    assert result.status is ScaleExecutionStatus.SUCCEEDED
    assert len(result.merged_output.candidate_pairs) == len(set(result.merged_output.candidate_pairs))
    assert result.merged_output.routed_record_instances == result.merged_output.unique_input_records


def test_large_join_fanout_is_rejected_before_unbounded_all_pairs() -> None:
    with pytest.raises(LargeJoinFanoutError):
        service().reject_join_fanout((("same-dimension", "left"), ("same-dimension", "right")), max_pairs=1)


def test_materialization_grain_and_warehouse_keys_are_not_silently_deduplicated() -> None:
    data = make_dataset(count=8)
    duplicate = data.rows[-1].model_copy(update={"row_ref": "row-duplicate", "source_record_refs": ("source/orders/duplicate",)})
    bad = ScaleInputDataset(descriptor=data.descriptor.model_copy(update={"row_count": 9}), rows=(*data.rows, duplicate))
    policy = make_policy(partitions=3)
    s = service()
    plan = s.plan(bad, policy, partition_count=3)
    result = s.execute(bad, plan, policy)
    assert result.status is ScaleExecutionStatus.FAILED
    assert "fact grain" in (result.failure_reason or "")


def test_external_mode_is_not_reported_as_available() -> None:
    with pytest.raises(ValueError):
        ScalePolicy(requested_mode=ScaleExecutionMode.EXTERNAL_DISTRIBUTED, resource_budget=ResourceBudget(max_worker_slots=1))
