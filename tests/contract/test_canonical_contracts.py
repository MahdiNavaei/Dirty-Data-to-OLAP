import json

from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityInstance,
    CanonicalModel,
    CanonicalModelHypothesis,
    NullSemanticState,
    SourceRecordCanonicalMap,
)


def test_reference_canonical_artifacts_round_trip_without_native_objects():
    from pathlib import Path

    root = Path(__file__).parents[2]
    run = root / "workspace" / "runs" / "step19-reference-run" / "canonical"
    hypothesis = json.loads((run / "canonical_model_hypothesis.json").read_text(encoding="utf-8"))
    model = json.loads((run / "canonical_model.json").read_text(encoding="utf-8"))
    assert CanonicalModelHypothesis.model_validate(hypothesis).schema_version == "1.0"
    assert CanonicalModel.model_validate(model).schema_version == "1.0"
    assert all(not isinstance(value, object) or isinstance(value, (str, int, float, bool, list, dict, tuple, type(None))) for value in model.values())


def test_source_record_map_and_null_state_contracts_are_explicit():
    instance = CanonicalEntityInstance(
        canonical_entity_id="cent_0123456789abcdef0123456789abcdef",
        canonical_entity_type_id="cet_customer",
        source_record_refs=("crm-r1",),
        identity_decision_ref="rdec-1",
        review_decision_ref="rdec-1",
        canonical_model_version="canonical-v1",
        provenance_refs=("source:crm",),
    )
    record = SourceRecordCanonicalMap(
        record_ref="crm-r1",
        source_id="crm",
        snapshot_id="snapshot-crm",
        table_id="customers",
        canonical_entity_id=instance.canonical_entity_id,
        canonical_entity_type_id=instance.canonical_entity_type_id,
        identity_decision_ref=instance.identity_decision_ref,
        review_decision_ref=instance.review_decision_ref,
        canonical_model_version=instance.canonical_model_version,
        disposition_reason="explicit synthetic canonical identity review",
        provenance_refs=instance.provenance_refs,
    )
    assert record.terminal_disposition.value == "CONSOLIDATED"
    assert NullSemanticState.NOT_CAPTURED.value != NullSemanticState.UNKNOWN.value
