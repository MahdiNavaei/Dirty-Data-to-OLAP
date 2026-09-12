from decimal import Decimal
from pathlib import Path

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader
from dirty_data_to_olap.application.validation import ValidationService, _as_refs, _equal
from dirty_data_to_olap.domain.contracts.validation import AccountingBoundary, ValidationStatus
from tools.step22_reference_support import REQUIRED_CHECK_IDS, build_reference_context


def test_validation_comparison_is_exact_for_numeric_and_structured_values():
    assert _equal("6", Decimal("6.000"))
    assert not _equal("6", Decimal("6.001"))
    assert _equal({"quantity": "6"}, {"quantity": Decimal("6.000")})


def test_validation_lineage_accepts_json_reference_arrays_only_as_references():
    assert _as_refs('["source-1", "source-2"]') == ("source-1", "source-2")
    assert _as_refs("source-1") == ("source-1",)


def test_g6_gate_requires_exact_membership_accounting_and_all_fact_scopes():
    context = build_reference_context("retail")
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    statuses = {item.check_id: item.status for item in outcome.report.checks}
    assert outcome.report.g6_status is not None
    assert outcome.report.g6_status.value == "PASS"
    assert outcome.report.g6_eligible is True
    assert all(statuses[item] is ValidationStatus.PASS for item in REQUIRED_CHECK_IDS)
    assert statuses["monetary_reconciliation"] is ValidationStatus.NOT_APPLICABLE
    assert len(outcome.report.discrepancies) == 0
    assert {item.boundary for item in context.inputs.accounting.scopes} == {
        AccountingBoundary.SOURCE_TO_CANONICAL,
        AccountingBoundary.CANONICAL_TO_ANALYTICAL,
    }
    assert len(context.inputs.canonical_model.instances) == 11
    assert len(context.inputs.canonical_model.source_record_maps) == 12
