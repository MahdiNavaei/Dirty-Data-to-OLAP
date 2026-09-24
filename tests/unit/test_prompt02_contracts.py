from __future__ import annotations

from decimal import Decimal

import pytest

from dirty_data_to_olap.application.product_runtime import LocalProductStageHandlers
from dirty_data_to_olap.application.multi_source_runtime import MultiSourceStageHandlers
from dirty_data_to_olap.domain.contracts.jobs import StageExecutionRequest, StageResultStatus
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SourceSelection, SourceSetSelection, source_set_fingerprint
from dirty_data_to_olap.observability import TelemetryClient, safe_exception_detail


def _selection(registry_id: str) -> SourceSelection:
    return SourceSelection(registry_id=registry_id, execution_context_id="prompt02-test", extraction=ExtractionPolicy(chunk_size=2))


def test_source_set_fingerprint_is_order_independent_and_finalized() -> None:
    selections = (_selection("source-b"), _selection("source-a"))
    bound = SourceSetSelection(selections=selections, source_set_fingerprint=source_set_fingerprint(selections))
    assert tuple(item.registry_id for item in bound.ordered_selections) == ("source-a", "source-b")
    assert bound.finalized is True


def test_source_set_rejects_single_source_and_stale_fingerprint() -> None:
    with pytest.raises(ValueError):
        SourceSetSelection(selections=(_selection("source-a"),), source_set_fingerprint="stale")
    selections = (_selection("source-a"), _selection("source-b"))
    with pytest.raises(ValueError, match="fingerprint"):
        SourceSetSelection(selections=selections, source_set_fingerprint="stale")


def test_multi_source_null_marker_does_not_enter_decimal_conversion() -> None:
    assert MultiSourceStageHandlers._typed_value("", "DECIMAL") is None
    assert MultiSourceStageHandlers._typed_value("23.0000", "DECIMAL") == Decimal("23.0000")


def test_worker_exception_detail_is_bounded_and_redacted() -> None:
    detail = safe_exception_detail(
        RuntimeError("planner rejected input\npassword=unsafe token=unsafe postgresql://user:secret@host/db")
    )

    assert "planner rejected input" in detail
    assert "password" not in detail.lower()
    assert "token" not in detail.lower()
    assert "secret@" not in detail.lower()
    assert "\n" not in detail and "\r" not in detail
    assert len(detail) <= 512


def test_product_stage_failure_persists_safe_exception_detail() -> None:
    handlers = object.__new__(LocalProductStageHandlers)
    handlers.telemetry = TelemetryClient()

    def raise_stage_failure(_request: StageExecutionRequest) -> None:
        raise RuntimeError("planner rejected input password=unsafe")

    handlers._execute = raise_stage_failure
    request = StageExecutionRequest(
        request_id="request-1",
        job_id="job-1",
        run_id="run-1",
        stage_id="ANALYTICAL_PLANNING",
        attempt_id="attempt-1",
        plan_id="plan-1",
        configuration_fingerprint="config",
        policy_config_fingerprint="policy",
        cancellation_token_id="cancel-1",
    )

    class Probe:
        def is_cancelled(self) -> bool:
            return False

    result = handlers.execute_with_context(request, Probe())

    assert result.status is StageResultStatus.FAILED
    assert result.metadata["error_type"] == "RuntimeError"
    assert "planner rejected input" in result.metadata["error_detail"]
    assert "password" not in result.metadata["error_detail"].lower()
