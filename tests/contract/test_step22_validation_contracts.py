import pytest

from dirty_data_to_olap.domain.contracts.canonical import RecordDisposition
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingEntry,
    RecordAccountingScope,
    SourceTruthAccountingExpectation,
    SourceTruthAggregateExpectation,
    SourceTruthManifest,
    ValidationPolicy,
    ValidationStatus,
)
from tools.step22_reference_support import build_reference_context, load_truth


def test_independent_source_truth_is_closed_and_hashed():
    truth = load_truth("retail")
    assert isinstance(truth, SourceTruthManifest)
    assert len(truth.records) == 12
    assert len(truth.record_refs) == 12
    assert len(truth.entities) == 11
    assert len(truth.accounting_expectations) == 23
    assert len(truth.aggregate_expectations) == 3
    assert all(isinstance(item, SourceTruthAccountingExpectation) for item in truth.accounting_expectations)
    assert all(isinstance(item, SourceTruthAggregateExpectation) for item in truth.aggregate_expectations)
    assert {item.fact_id for item in truth.facts} == {"fact_order_line"}
    assert truth.content_hash
    assert truth.source_snapshot_fingerprint


def test_expectations_are_boundary_scoped_and_aggregates_are_fact_scoped():
    truth = load_truth("generic")
    assert {item.boundary for item in truth.accounting_expectations} == {
        AccountingBoundary.SOURCE_TO_CANONICAL,
        AccountingBoundary.CANONICAL_TO_ANALYTICAL,
    }
    assert all(item.fact_id == "fact_device_reading" for item in truth.aggregate_expectations)
    assert {item.operation for item in truth.aggregate_expectations} == {"MAX"}
    assert all(item.unit_semantics for item in truth.aggregate_expectations)


def test_runtime_accounting_and_analytical_input_binding_are_not_built_from_truth():
    context = build_reference_context("retail")
    assert context.inputs.accounting.provenance_refs[0] == "CanonicalFinalizationService"
    assert context.inputs.analytical_dataset.dataset_id == context.inputs.analytical_input_binding.dataset_id
    assert context.inputs.analytical_dataset.content_hash == context.inputs.analytical_input_binding.dataset_content_hash
    assert context.inputs.bindings.analytical_dataset_content_hash == context.inputs.analytical_dataset.content_hash
    assert context.inputs.bindings.analytical_input_binding_content_hash == context.inputs.analytical_input_binding.content_hash
    assert context.inputs.accounting.scopes[0].input_object_ref == context.truth.source_snapshot_id


def test_accounting_requires_exactly_one_terminal_disposition_per_input():
    with pytest.raises(ValueError, match="exact input record universe"):
        RecordAccountingScope(
            scope_id="scope",
            boundary=AccountingBoundary.SOURCE_TO_CANONICAL,
            input_object_ref="snapshot",
            input_record_refs=("r1", "r2"),
            entries=(RecordAccountingEntry(
                input_record_ref="r1",
                disposition=RecordDisposition.EMITTED_DIRECT,
                output_or_group_ref="out-1",
                transformation_or_policy_ref="policy:v1",
                reason="emitted",
                provenance_refs=("test",),
            ),),
            policy_version="v1",
            provenance_refs=("test",),
        )


def test_policy_requires_reason_for_monetary_not_applicable():
    with pytest.raises(ValueError):
        ValidationPolicy(
            policy_id="policy",
            policy_version="v1",
            required_check_ids=("check",),
            allowed_terminal_dispositions=tuple(RecordDisposition),
            orphan_policy={"required_fk": "FAIL"},
            monetary_status=ValidationStatus.NOT_APPLICABLE,
            monetary_reason=" ",
            provenance_refs=("test",),
        )


def test_duplicate_accounting_is_not_row_loss():
    scope = RecordAccountingScope(
        scope_id="dedup",
        boundary=AccountingBoundary.SOURCE_TO_CANONICAL,
        input_object_ref="snapshot",
        input_record_refs=("crm-1", "erp-1"),
        entries=(
            RecordAccountingEntry(input_record_ref="crm-1", disposition=RecordDisposition.CONSOLIDATED, output_or_group_ref="customer-1", transformation_or_policy_ref="policy:dedup", reason="reviewed duplicate", provenance_refs=("test",)),
            RecordAccountingEntry(input_record_ref="erp-1", disposition=RecordDisposition.CONSOLIDATED, output_or_group_ref="customer-1", transformation_or_policy_ref="policy:dedup", reason="reviewed duplicate", provenance_refs=("test",)),
        ),
        policy_version="v1",
        provenance_refs=("test",),
    )
    assert scope.counts[RecordDisposition.CONSOLIDATED.value] == 2
    assert {item.output_or_group_ref for item in scope.entries} == {"customer-1"}
