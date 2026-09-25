from pathlib import Path

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader
from dirty_data_to_olap.application.validation import ValidationService
from dirty_data_to_olap.domain.contracts.validation import AccountingBoundary, ValidationStatus
from tests.step22_support import reference_context
from tools.step22_reference_support import REQUIRED_CHECK_IDS
from tools.validate_step22_data_correctness import (
    run_accounting_negative_controls,
    run_binding_negative_controls,
    run_canonical_negative_controls,
    run_multifact_control,
    run_negative_controls,
    run_oracle_mismatch_controls,
)
from tests.product_acceptance.prompt02_control_evidence import record_control_observation


def test_retail_reference_closes_g6_from_runtime_canonical_to_target():
    context = reference_context("retail")
    target_path = Path(context.inputs.materialization.target_relative_path)
    before = target_path.read_bytes()
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    assert target_path.read_bytes() == before
    assert outcome.report.g6_status.value == "PASS"
    assert outcome.report.g6_eligible is True
    assert outcome.report.discrepancies == ()
    statuses = {item.check_id: item.status for item in outcome.report.checks}
    assert all(statuses[item] is ValidationStatus.PASS for item in REQUIRED_CHECK_IDS)
    assert statuses["monetary_reconciliation"] is ValidationStatus.NOT_APPLICABLE
    assert len(context.inputs.canonical_model.instances) == 11
    assert len(context.inputs.canonical_model.source_record_maps) == 12
    assert len(context.inputs.accounting.scopes) == 2
    assert {item.boundary for item in context.inputs.accounting.scopes} == {
        AccountingBoundary.SOURCE_TO_CANONICAL,
        AccountingBoundary.CANONICAL_TO_ANALYTICAL,
    }


def test_adversarial_controls_detect_same_totals_count_and_fk_traps():
    context = reference_context("retail")
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    controls = run_negative_controls(context, outcome)
    assert len(controls) >= 7
    assert all(item["status"] == "DETECTED" for item in controls)
    assert {item["control_id"] for item in controls} >= {
        "same_total_wrong_product_allocation",
        "same_count_remove_and_duplicate_grain",
        "wrong_but_valid_customer_fk",
        "quantity_compensation",
        "lineage_loss",
        "unexplained_filter",
        "orphan_required_fk",
        "warehouse_key_collision",
    }
    grain = next(item for item in controls if item["control_id"] == "same_count_remove_and_duplicate_grain")
    record_control_observation("NC12", grain["observed_rejection"], "tests/integration/test_step22_data_correctness_flow.py:44")


def test_step22_detects_runtime_accounting_canonical_oracle_and_binding_mutations():
    context = reference_context("retail")
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    snapshot = outcome.target_snapshot
    assert snapshot is not None
    observed_controls = (
        run_binding_negative_controls(context, snapshot),
        run_accounting_negative_controls(context, snapshot),
        run_canonical_negative_controls(context, snapshot),
        run_oracle_mismatch_controls(context, snapshot),
    )
    for controls in observed_controls:
        assert controls
        assert all(item["status"] == "DETECTED" for item in controls)
    accounting = next(item for item in observed_controls[1] if item["control_id"] == "missing_source_disposition")
    canonical = next(item for item in observed_controls[2] if item["control_id"] == "false_merge")
    record_control_observation("NC05", accounting["observed_rejection"], "tests/integration/test_step22_data_correctness_flow.py:75")
    record_control_observation("NC13", canonical["observed_rejection"], "tests/integration/test_step22_data_correctness_flow.py:76")


def test_generic_device_location_reading_uses_same_validator():
    context = reference_context("generic")
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    assert outcome.report.g6_status.value == "PASS"
    assert outcome.report.g6_eligible is True
    assert outcome.report.discrepancies == ()
    assert next(item for item in outcome.report.checks if item.check_id == "fact_business_values").status is ValidationStatus.PASS
    assert next(item for item in outcome.report.checks if item.check_id == "quantity_global").status is ValidationStatus.PASS
    assert len(context.inputs.canonical_model.instances) == 7
    assert len(context.inputs.canonical_model.source_record_maps) == 7


def test_generic_multi_fact_scope_cannot_hide_a_missing_second_fact():
    context = reference_context("generic")
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    assert outcome.target_snapshot is not None
    result = run_multifact_control(context, outcome.target_snapshot)
    assert result["status"] == "DETECTED"
    assert result["detected_checks"] == ["fact_count"]
