from __future__ import annotations

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import *


def _policy() -> FusionPolicyReference:
    return FusionPolicyReference(policy_id="test-fusion", version="1", status=FusionPolicyStatus.UNCALIBRATED, content_hash="policy-hash")


def _request(*, mapping: bool = False, relationship_ids=("rel-1",), mapping_ids=()) -> EvidenceFusionRequest:
    return EvidenceFusionRequest(request_id="fusion-test", execution_context_id="exec-test", cross_source_mapping_scope=mapping, relationship_candidate_ids=relationship_ids, mapping_candidate_ids=mapping_ids, policy=_policy())


def _statuses(*, schema=False, dependency=ProducerResultState.COMPLETE):
    result = [ProducerEvidenceStatus(producer_id="profiling", family=EvidenceFamily.PROFILE, state=ProducerResultState.COMPLETE, result_id="profile-result"), ProducerEvidenceStatus(producer_id="quality", family=EvidenceFamily.QUALITY, state=ProducerResultState.COMPLETE, result_id="quality-result"), ProducerEvidenceStatus(producer_id="dependency", family=EvidenceFamily.DEPENDENCY, state=dependency, result_id="dependency-result")]
    if schema:
        result.append(ProducerEvidenceStatus(producer_id="schema", family=EvidenceFamily.SCHEMA_MATCHING, state=ProducerResultState.COMPLETE, result_id="schema-result"))
    return tuple(result)


def _candidate(candidate_id="rel-1", to_table="customers"):
    return {"candidate_id": candidate_id, "source_id": "src", "snapshot_id": "snap", "from_table": "orders", "from_columns": ("customer_id",), "to_table": to_table, "to_columns": ("id",), "proposed_cardinality": "MANY_TO_ONE"}


def _item(evidence_id, metric_name, value, direction=EvidenceDirection.SUPPORTS, *, subject="rel:orders:customer_id->customers:id", scope="src:snap", reliability=EvidenceReliabilityState.FULL, family=EvidenceFamily.DEPENDENCY, score=True, derived=()):
    return FusionEvidenceItem(evidence_id=evidence_id, subject_id=subject, producer_id="test", family=family, metric_name=metric_name, metric_value=value, metric_semantics="test aggregate semantics", direction=direction, scope_id=scope, observation_scope=reliability, source_ids=("src",), snapshot_ids=("snap",), correlation_group=evidence_id, score_bearing=score, derived_from_refs=derived)


def test_clean_relationship_is_review_only_and_explainable():
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("ind", "inclusion_coverage", .99), _item("uniq", "target_uniqueness", .98))))
    decision = result.relationships[0]
    assert result.completeness is EvidenceFusionCompleteness.COMPLETE_REVIEW_READY
    assert decision.decision_state is DecisionState.REVIEW_REQUIRED
    assert decision.score.confidence_kind is ConfidenceKind.UNCALIBRATED_SCORE
    assert decision.score.value is not None
    assert decision.explanation.supports and decision.explanation.reconstruction


def test_missing_required_producer_is_not_negative_evidence():
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=tuple(item for item in _statuses() if item.family is not EvidenceFamily.QUALITY), relationship_candidates=(_candidate(),), evidence_items=(_item("ind", "inclusion_coverage", 1.0),)))
    assert result.completeness is EvidenceFusionCompleteness.INCOMPLETE_REQUIRED_EVIDENCE
    assert result.relationships[0].decision_state is DecisionState.INCOMPLETE_REQUIRED_EVIDENCE
    assert result.relationships[0].score.value is not None and result.relationships[0].score.evidence_coverage < 1.0
    assert result.relationships[0].missing_evidence_refs


def test_conflict_overrides_numeric_band_and_declared_fk_remains_visible():
    subject = "rel:orders:customer_id->customers:id"
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), declared_constraints=(DeclaredConstraintInput(constraint_id="fk-1", constraint_type="FOREIGN_KEY", source_id="src", snapshot_id="snap", from_table="orders", from_columns=("customer_id",), to_table="customers", to_columns=("id",), scope_id="src:snap"),), evidence_items=(_item("ind", "inclusion_coverage", .2), _item("orphans", "orphan_ratio", .8, EvidenceDirection.CONTRADICTS), _item("semantic", "domain_assertion", None, EvidenceDirection.SUPPORTS, family=EvidenceFamily.DOMAIN_ASSERTION, score=False))))
    decision = result.relationships[0]
    assert decision.confidence_band is ConfidenceBand.LOW or decision.confidence_band is ConfidenceBand.CONFLICTED
    assert any(item.conflict_type is ConflictType.DECLARED_DATA_CONFLICT for item in result.conflicts)
    assert "fk-1" in result.relationships[0].supporting_signal_refs or any("fk-1" in item.supporting_evidence_refs for item in result.conflicts)


def test_derived_ml_and_llm_style_inputs_do_not_add_numeric_votes():
    base = (_item("ind", "inclusion_coverage", .9),)
    derived = _item("ml", "learned_ranking_score", .99, EvidenceDirection.CONTEXT, family=EvidenceFamily.APPLIED_ML, score=False, derived=("ind",))
    left = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=base))
    right = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=base + (derived,)))
    assert left.relationships[0].score.value == right.relationships[0].score.value
    assert any(item.role is EvidenceRole.DIRECT_OBSERVATION for item in right.signals)


def test_duplicate_id_wrong_snapshot_and_multiple_target_are_explicit():
    same = _item("duplicate", "inclusion_coverage", .9)
    different = same.model_copy(update={"metric_value": .1})
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate("rel-1", "customers"), _candidate("rel-2", "clients")), evidence_items=(same, different)))
    assert any(item.kind is FusionFailureKind.EVIDENCE_ID_COLLISION for item in result.failures)
    assert any(item.conflict_type is ConflictType.MULTIPLE_TARGET_AMBIGUITY for item in result.conflicts)


def test_cross_source_mapping_requires_complete_schema_matching():
    candidate = {"candidate_id": "map-1", "source_id": "crm", "source_column_id": "crm.customer_code", "target_source_id": "erp", "target_column_id": "erp.client_no"}
    result = EvidenceFusionService().fuse(_request(mapping=True, relationship_ids=(), mapping_ids=("map-1",)), EvidenceFusionInputs(producer_statuses=_statuses(schema=False), mapping_candidates=(candidate,)))
    assert result.completeness is EvidenceFusionCompleteness.INCOMPLETE_REQUIRED_EVIDENCE
    assert any(item.kind is FusionFailureKind.REQUIRED_PRODUCER_FAILED for item in result.failures)
