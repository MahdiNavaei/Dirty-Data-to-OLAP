from __future__ import annotations

import pytest

from dirty_data_to_olap.application.distributed import PartitionMergeError, ScaleAuthorizationError, ScaleService
from dirty_data_to_olap.domain.contracts.distributed import ScaleInputDescriptor
from tests.step24_support import make_dataset, make_gate, make_policy


def test_unsafe_scope_tokens_are_rejected() -> None:
    data = make_dataset()
    with pytest.raises(ValueError):
        ScaleInputDescriptor(**{**data.descriptor.model_dump(), "run_id": "../foreign"})


def test_g6_scope_and_report_hash_tampering_is_rejected() -> None:
    data = make_dataset()
    with pytest.raises(ScaleAuthorizationError):
        ScaleService().authorize_g6(data, make_gate().model_copy(update={"validation_report_content_hash": "e" * 64}))
    with pytest.raises(ScaleAuthorizationError):
        ScaleService().authorize_g6(data, make_gate().model_copy(update={"run_id": "foreign-run"}))


def test_foreign_partition_result_cannot_satisfy_current_merge() -> None:
    data = make_dataset()
    service = ScaleService()
    policy = make_policy(partitions=3)
    plan = service.plan(data, policy, partition_count=3)
    execution = service.execute(data, plan, policy)
    foreign = execution.partition_results[0].model_copy(update={"input_id": "foreign-input"})
    with pytest.raises(PartitionMergeError):
        service.merge(data, plan, policy, (foreign, *execution.partition_results[1:]))


def test_artifact_contract_contains_no_raw_row_payload_or_executable_code() -> None:
    data = make_dataset()
    service = ScaleService()
    plan = service.plan(data, make_policy(), partition_count=3)
    result = service.execute(data, plan, make_policy()).partition_results[0]
    payload = result.model_dump(mode="json")
    assert "callable" not in payload
    assert "pickle" not in str(payload).lower()
    assert "source/orders/" in str(payload)


def test_protected_quality_artifact_path_is_not_in_step24_sources() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert "quality_unit_artifacts" not in (root / "src/dirty_data_to_olap/application/distributed.py").read_text(encoding="utf-8")
