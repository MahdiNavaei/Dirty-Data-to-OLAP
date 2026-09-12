from __future__ import annotations

import json

import pytest

from dirty_data_to_olap.domain.contracts.distributed import (
    PartitionPlan,
    PartitionResult,
    PartitionStrategy,
    ScaleExecutionMode,
    ScaleInputDataset,
    ScaleInputDescriptor,
    ScalePolicy,
    record_digest,
)
from tests.step24_support import make_dataset, make_policy


def test_scale_contracts_are_json_serializable_and_provider_neutral() -> None:
    data = make_dataset()
    plan = __import__("dirty_data_to_olap.application.distributed", fromlist=["ScaleService"]).ScaleService().plan(data, make_policy(), partition_count=3)
    encoded = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
    assert "ThreadPoolExecutor" not in encoded
    assert "spark" not in encoded.lower()
    assert plan.schema_version == "1.0"
    assert all(type(item).__module__.startswith("dirty_data_to_olap") for item in plan.partitions)


def test_partition_plan_hash_and_result_identity_are_stable() -> None:
    data = make_dataset()
    policy = make_policy()
    service = __import__("dirty_data_to_olap.application.distributed", fromlist=["ScaleService"]).ScaleService()
    first = service.plan(data, policy, partition_count=3)
    second = service.plan(data, policy, partition_count=3)
    assert first.partition_plan_hash == second.partition_plan_hash == first.semantic_hash
    first_result = service.execute(data, first, policy).partition_results
    second_result = service.execute(data, second, policy).partition_results
    assert [item.result_id for item in first_result] == [item.result_id for item in second_result]
    assert [item.semantic_result_hash for item in first_result] == [item.semantic_result_hash for item in second_result]


def test_contract_rejects_duplicate_input_refs_and_schema_mismatch() -> None:
    data = make_dataset()
    with pytest.raises(ValueError):
        type(data.descriptor)(**{**data.descriptor.model_dump(), "artifact_refs": ("artifact", "artifact")})
    with pytest.raises(ValueError):
        ScaleInputDataset(descriptor=data.descriptor, rows=(data.rows[0].model_copy(update={"schema_fingerprint": "other"}), *data.rows[1:]))


def test_partition_scope_contains_run_source_snapshot_table_dataset_and_schema() -> None:
    data = make_dataset()
    plan = __import__("dirty_data_to_olap.application.distributed", fromlist=["ScaleService"]).ScaleService().plan(data, make_policy(), partition_count=3)
    for part in plan.partitions:
        assert (part.run_id, part.source_id, part.snapshot_id, part.table_id, part.dataset_id, part.dataset_version, part.schema_fingerprint) == ("step24-reference-run", "retail-source", "retail-snapshot-v1", "orders", "orders-lines", "v1", "schema-orders-v1")


def test_range_router_has_explicit_deterministic_boundaries() -> None:
    data = make_dataset(count=6)
    policy = make_policy(partitions=3).model_copy(update={"partition_strategy": PartitionStrategy.RANGE, "range_boundaries": ("customer-2", "customer-4")})
    plan = __import__("dirty_data_to_olap.application.distributed", fromlist=["ScaleService"]).ScaleService().plan(data, policy, partition_count=3)
    assert plan.strategy is PartitionStrategy.RANGE
    assert [part.range_end for part in plan.partitions] == ["customer-2", "customer-4", None]


def test_failed_result_is_explicit_and_does_not_serialize_executable_payload() -> None:
    data = make_dataset()
    policy = make_policy()
    service = __import__("dirty_data_to_olap.application.distributed", fromlist=["ScaleService"]).ScaleService()
    plan = service.plan(data, policy, partition_count=3)
    result = service.execute(data, plan, policy, failure_partition_ids={plan.expected_partition_ids[0]})
    assert result.status.value == "INCOMPLETE"
    failed = next(item for item in result.partition_results if item.status.value == "FAILED")
    payload = json.dumps(failed.model_dump(mode="json"))
    assert failed.failure_code == "FAILURE_INJECTED"
    assert "pickle" not in payload.lower()
