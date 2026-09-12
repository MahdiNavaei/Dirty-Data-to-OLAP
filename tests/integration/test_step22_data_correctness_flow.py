from pathlib import Path

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader
from dirty_data_to_olap.application.validation import ValidationService
from dirty_data_to_olap.domain.contracts.validation import ValidationStatus
from tests.step22_support import reference_context
from tools.validate_step22_data_correctness import run_negative_controls


def test_retail_reference_reconciles_target_without_promoting_missing_canonical_evidence():
    context = reference_context("retail")
    target_path = Path(context.inputs.materialization.target_relative_path)
    before = target_path.read_bytes()
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    assert target_path.read_bytes() == before
    assert outcome.report.g6_status.value == "PENDING"
    assert outcome.report.g6_eligible is False
    assert outcome.report.discrepancies == ()
    assert {item.check_id for item in outcome.report.checks if item.status is ValidationStatus.PASS} >= {
        "artifact_binding",
        "source_record_accounting",
        "fact_business_values",
        "foreign_key_integrity",
        "quantity_global",
        "quantity_slices",
        "lineage_source_to_target",
        "lineage_target_to_source",
    }
    assert {item.check_id for item in outcome.report.checks if item.status is ValidationStatus.NOT_EVALUATED} == {
        "dedup_explainability",
        "canonical_entity_counts",
        "canonical_event_relationships",
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


def test_generic_device_location_reading_uses_same_validator():
    context = reference_context("generic")
    outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(Path(".")))
    assert outcome.report.g6_status.value == "PENDING"
    assert next(item for item in outcome.report.checks if item.check_id == "fact_business_values").status is ValidationStatus.PASS
    assert next(item for item in outcome.report.checks if item.check_id == "quantity_global").status is ValidationStatus.PASS
