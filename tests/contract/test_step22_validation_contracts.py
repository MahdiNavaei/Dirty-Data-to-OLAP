import pytest

from dirty_data_to_olap.domain.contracts.canonical import RecordDisposition
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingEntry,
    RecordAccountingScope,
    SourceTruthManifest,
    ValidationPolicy,
    ValidationStatus,
)
from tools.step22_reference_support import load_truth


def test_independent_source_truth_is_closed_and_hashed():
    truth = load_truth("retail")
    assert isinstance(truth, SourceTruthManifest)
    assert len(truth.records) == 11
    assert len(truth.record_refs) == 11
    assert truth.content_hash
    assert truth.source_snapshot_fingerprint


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
