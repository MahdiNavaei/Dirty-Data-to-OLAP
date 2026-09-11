from __future__ import annotations

import json
from pathlib import Path

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import *


def _request(*, mapping: bool = False, relationship_ids=("rel-1",), mapping_ids=()) -> EvidenceFusionRequest:
    return EvidenceFusionRequest(request_id="fusion-test", execution_context_id="exec-test", cross_source_mapping_scope=mapping, relationship_candidate_ids=relationship_ids, mapping_candidate_ids=mapping_ids, policy=EvidenceFusionService.load_policy("mapping" if mapping else "relationship"))


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
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("ind", "inclusion_coverage", .99), _item("uniq", "target_uniqueness", .98), _item("type", "type_compatibility", 1.0))))
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


def test_structural_dimensions_are_independent_and_inclusion_is_not_double_counted():
    evidence = (
        _item("coverage", "inclusion_coverage", .8),
        _item("orphan", "orphan_ratio", .2, EvidenceDirection.CONTRADICTS),
        _item("unique", "target_uniqueness", .9),
        _item("type", "type_compatibility", 1.0),
    )
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=evidence))
    score = result.relationships[0].score
    assert set(score.contributions) == {"inclusion", "target_uniqueness", "type_compatibility"}
    assert score.eligible_weight == 2.75
    assert score.observed_weight == 2.75
    assert score.contributions["inclusion"] == .8
    assert any(signal.raw_metric_name == "orphan_ratio" for signal in result.signals)


def test_policy_weight_replay_changes_score_but_irrelevant_identity_does_not():
    policy = EvidenceFusionService.load_policy()
    evidence = (_item("coverage", "inclusion_coverage", .8), _item("orphan", "orphan_ratio", .2, EvidenceDirection.CONTRADICTS), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0))
    inputs = EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=evidence)
    base = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="policy-a", execution_context_id="policy-a", relationship_candidate_ids=("rel-1",), policy=policy), inputs)
    dimensions = tuple(item.model_copy(update={"weight": 2.0}) if item.dimension_id == "inclusion" else item for item in policy.scoring_dimensions)
    changed = policy.model_copy(update={"scoring_dimensions": dimensions, "content_hash": "local-policy-b"})
    weighted = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="policy-b", execution_context_id="policy-b", relationship_candidate_ids=("rel-1",), policy=changed), inputs)
    assert weighted.relationships[0].score.value != base.relationships[0].score.value
    assert weighted.relationships[0].decision_id != base.relationships[0].decision_id
    irrelevant = policy.model_copy(update={"content_hash": "irrelevant-policy-change"})
    same_score = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="policy-c", execution_context_id="policy-c", relationship_candidate_ids=("rel-1",), policy=irrelevant), inputs)
    assert same_score.relationships[0].score.value == base.relationships[0].score.value


def test_runtime_policy_file_change_is_replayed_and_hashed():
    source = Path("policies/evidence-fusion/relationship_fusion_v1.json")
    root = Path("workspace/test-temp/evidence-fusion/policy-replay").resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / source.name
    data = json.loads(source.read_text(encoding="utf-8"))
    data["scoring_dimensions"][0]["weight"] = 2.0
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        base_policy = EvidenceFusionService.load_policy(policy_root=source.parent)
        changed_policy = EvidenceFusionService.load_policy(policy_root=root)
        inputs = EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0)))
        base = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="file-a", execution_context_id="file-a", relationship_candidate_ids=("rel-1",), policy=base_policy), inputs)
        changed = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="file-b", execution_context_id="file-b", relationship_candidate_ids=("rel-1",), policy=changed_policy), inputs)
        assert changed_policy.content_hash != base_policy.content_hash
        assert changed.relationships[0].score.value != base.relationships[0].score.value
        assert changed.relationships[0].decision_id != base.relationships[0].decision_id
    finally:
        target.unlink(missing_ok=True)
        root.rmdir()


def test_non_observed_evidence_survives_without_numeric_normalization():
    item = _item("unavailable", "inclusion_coverage", .9, family=EvidenceFamily.DEPENDENCY).model_copy(update={"presence": EvidencePresenceState.UNAVAILABLE})
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(item,)))
    signal = next(item for item in result.signals if item.evidence_id == "unavailable")
    assert signal.presence is EvidencePresenceState.UNAVAILABLE
    assert signal.normalized_value is None and not signal.score_bearing
    assert "unavailable" in result.bundles[0].unavailable_evidence_refs


def test_actual_applied_ml_evidence_binds_to_relationship_subject_without_scoring():
    from dirty_data_to_olap.domain.contracts.applied_ml import AppliedMLResult, MLCapability, MLFeatureDefinition, MLFeatureFamily, MLFeatureSchema, MLModelStatus, MLStageStatus, MLTask, LearnedRankingEvidence

    schema = MLFeatureSchema(schema_id="schema", task=MLTask.RELATIONSHIP_CANDIDATE_RANKING, features=(MLFeatureDefinition(feature_id="coverage", family=MLFeatureFamily.DEPENDENCY, source_contract="DependencyResult", missing_semantics="missing", version="1"),))
    learned = LearnedRankingEvidence(evidence_id="ml-evidence", candidate_id="rel-1", model_id="model", feature_schema_id=schema.schema_id, ranking_score=.99, rank=1, model_status=MLModelStatus.EXPERIMENTAL, input_evidence_refs=("coverage",))
    ml = AppliedMLResult(task=MLTask.RELATIONSHIP_CANDIDATE_RANKING, status=MLStageStatus.EXECUTED_EXPERIMENTAL, feature_schema=schema, learned_evidence=(learned,), capability=MLCapability(capability_id="cap", available=True, engine="test", version="1", detail="test"))
    base_inputs = EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0)))
    base = EvidenceFusionService().fuse(_request(), base_inputs)
    with_ml = EvidenceFusionService().fuse(_request(), base_inputs, applied_ml_result=ml)
    bundle = with_ml.bundles[0]
    signal = next(item for item in bundle.signals if item.evidence_id == "ml-evidence")
    assert signal.role is EvidenceRole.DERIVED_INTERPRETATION
    assert not signal.score_bearing and signal.derived_from_refs == ("coverage",)
    assert with_ml.relationships[0].score == base.relationships[0].score


def test_multiple_target_and_producer_state_change_replay_identity():
    evidence = (_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0))
    one = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=evidence))
    two = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(), _candidate("rel-2", "clients")), evidence_items=evidence))
    incomplete_statuses = tuple(item.model_copy(update={"state": ProducerResultState.INCOMPLETE}) if item.family is EvidenceFamily.DEPENDENCY else item for item in _statuses())
    stale = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=incomplete_statuses, relationship_candidates=(_candidate(),), evidence_items=evidence))
    assert any(item.conflict_type is ConflictType.MULTIPLE_TARGET_AMBIGUITY for item in two.conflicts)
    assert two.relationships[0].input_evidence_fingerprint != one.relationships[0].input_evidence_fingerprint
    assert stale.relationships[0].input_evidence_fingerprint != one.relationships[0].input_evidence_fingerprint
    assert stale.completeness is EvidenceFusionCompleteness.INCOMPLETE_REQUIRED_EVIDENCE


def test_multiple_expected_results_and_duplicate_fingerprints_are_explicit():
    from dirty_data_to_olap.application.evidence_fusion import _producer_state
    from dirty_data_to_olap.domain.contracts.evidence_fusion import ExpectedProducerResult

    assert _producer_state(type("Result", (), {"status": "NOT_CONFIGURED"})()) is ProducerResultState.NOT_CONFIGURED
    first = ProducerEvidenceStatus(producer_id="profiling", family=EvidenceFamily.PROFILE, state=ProducerResultState.COMPLETE, result_id="profile-a", input_fingerprint="fingerprint-a")
    second = first.model_copy(update={"input_fingerprint": "fingerprint-b"})
    expected = (ExpectedProducerResult(family=EvidenceFamily.PROFILE, producer_id="profiling", result_id="profile-a"), ExpectedProducerResult(family=EvidenceFamily.PROFILE, producer_id="profiling", result_id="profile-b"))
    statuses = _statuses() + (first, second)
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="expected", execution_context_id="expected", relationship_candidate_ids=("rel-1",), policy=EvidenceFusionService.load_policy(), expected_producer_results=expected), EvidenceFusionInputs(producer_statuses=statuses, relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0))))
    assert any(item.kind is FusionFailureKind.STALE_EVIDENCE for item in result.failures)
    assert any("profile-b" in item.detail for item in result.failures)


def test_actual_semantic_result_requires_explicit_subject_binding_and_stays_qualitative():
    from dirty_data_to_olap.domain.contracts.semantic_ai import LLMEvidence, SemanticAuthorization, SemanticCapability, SemanticCapabilityStatus, SemanticContextManifest, SemanticEvidenceResult, SemanticGenerationReference, SemanticHypothesis, SemanticHypothesisKind, SemanticPromptReference, SemanticProviderReference, SemanticSupportState, SemanticTask

    context = SemanticContextManifest(manifest_id="manifest", subject_refs=("upstream-rel",), requested_evidence_refs=(), provided_context_item_refs=(), allowed_provider_evidence_refs=("coverage",), items=(), input_char_count=0, context_builder_version="1", input_fingerprint="context")
    provider = SemanticProviderReference(provider="fixture", api_version="1", endpoint="http://127.0.0.1", model="fixture", model_digest="digest")
    task = SemanticTask.RELATIONSHIP_SEMANTIC_HYPOTHESIS
    prompt = SemanticPromptReference(prompt_version="v1", system_prompt_hash="system", task_template_hash="task", schema_version="schema", context_builder_version="1", prompt_hash="prompt")
    authorization = SemanticAuthorization(authorization_id="auth", policy_id="policy", policy_version="1", request_id="semantic-request", task=task, context_manifest_fingerprint=context.input_fingerprint, subject_refs=("upstream-rel",), evidence_refs=("coverage",), model="fixture", model_digest="digest", prompt_version="v1")
    generation = SemanticGenerationReference(temperature=0, seed=1, num_predict=64, timeout_seconds=1, retry_count=0, stream=False, think=False, structured_schema_id="schema", structured_schema_hash="schema-hash", config_fingerprint="config")
    evidence = LLMEvidence(evidence_id="llm-evidence", request_id="semantic-request", task=task, subject_refs=("upstream-rel",), hypotheses=(SemanticHypothesis(hypothesis_id="hypothesis", kind=SemanticHypothesisKind.SUPPORTS_HYPOTHESIS, statement="candidate relationship is plausible"),), provider=provider, prompt=prompt, context_manifest=context, authorization=authorization, generation=generation, response_hash="response")
    semantic = SemanticEvidenceResult(request_id="semantic-request", state=SemanticSupportState.CANDIDATE_ONLY, evidence=evidence, capability=SemanticCapability(capability_id="capability", status=SemanticCapabilityStatus.AVAILABLE, provider="fixture", model="fixture", detail="fixture"))
    binding = FusionSubjectBinding(upstream_subject_ref="upstream-rel", candidate_id="rel-1", fusion_subject_id="rel:orders:customer_id->customers:id", binding_basis="explicit_candidate_id", evidence_refs=("coverage",))
    inputs = EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0)), subject_bindings=(binding,))
    result = EvidenceFusionService().fuse(_request(), inputs, semantic_results=(semantic,))
    signal = next(item for item in result.signals if item.evidence_id == "hypothesis")
    assert signal.role is EvidenceRole.DERIVED_INTERPRETATION and not signal.score_bearing and signal.normalized_value is None


def test_semantic_multiple_subject_refs_must_resolve_to_one_subject():
    from dirty_data_to_olap.domain.contracts.semantic_ai import LLMEvidence, SemanticContextManifest, SemanticEvidenceResult, SemanticHypothesis, SemanticHypothesisKind, SemanticSupportState

    context = SemanticContextManifest(manifest_id="manifest-multi", subject_refs=("upstream-rel", "upstream-rel-2"), requested_evidence_refs=(), provided_context_item_refs=(), allowed_provider_evidence_refs=(), items=(), input_char_count=0, context_builder_version="1", input_fingerprint="context-multi")
    evidence = LLMEvidence.model_construct(evidence_id="llm-multi", request_id="semantic-multi", subject_refs=("upstream-rel", "upstream-rel-2"), hypotheses=(SemanticHypothesis(hypothesis_id="hypothesis-multi", kind=SemanticHypothesisKind.SUPPORTS_HYPOTHESIS, statement="ambiguous candidate"),), context_manifest=context)
    semantic = SemanticEvidenceResult.model_construct(request_id="semantic-multi", state=SemanticSupportState.CANDIDATE_ONLY, evidence=evidence)
    bindings = (FusionSubjectBinding(upstream_subject_ref="upstream-rel", candidate_id="rel-1", fusion_subject_id="rel:orders:customer_id->customers:id", binding_basis="explicit"), FusionSubjectBinding(upstream_subject_ref="upstream-rel-2", candidate_id="rel-2", fusion_subject_id="rel:orders:customer_id->clients:id", binding_basis="explicit"))
    candidates = (_candidate(), _candidate("rel-2", "clients"))
    inputs = EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=candidates, evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0)), subject_bindings=bindings)
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="semantic-multi", execution_context_id="semantic-multi", relationship_candidate_ids=("rel-1", "rel-2"), policy=EvidenceFusionService.load_policy()), inputs, semantic_results=(semantic,))
    assert any(item.kind is FusionFailureKind.SUBJECT_BINDING_ERROR for item in result.failures)


def test_actual_profile_quality_and_repair_contracts_are_consumed_and_forwarded():
    from datetime import datetime, timezone
    from dirty_data_to_olap.domain.contracts.profiling import ColumnProfile, ProfileCompleteness, ProfileMode, ProfileObservationScope, ProfileProvenance, ProfileRequest, ProfileResult, ProfileObservationStatus, TableProfile
    from dirty_data_to_olap.domain.contracts.quality import DetectionBasis, QualityDimension, QualityIssue, QualityIssueStatus, QualityResult, QualitySeverity, MeasurementSemantics, RepairProposal, RepairProposalStatus, RepairValidationPlan, Repairability
    from dirty_data_to_olap.domain.contracts.source import AdapterReference, SourceTableKind, TableObservationStatus

    scope = ProfileObservationScope(source_snapshot_mode="FULL", source_snapshot_was_bounded=False, source_table_observation_status=TableObservationStatus.FULLY_OBSERVED, source_rows_observed=10, rows_available_in_snapshot=10, rows_profiled=10, profiling_mode=ProfileMode.FULL, sample_method="none", all_available_staged_rows_covered=True, completeness=ProfileObservationStatus.FULLY_OBSERVED)
    provenance = ProfileProvenance(execution_context_id="profile-exec", source_id="src", snapshot_id="snap", table_id="orders", schema_fingerprint="schema", input_batch_ids=("batch",), input_batch_hashes=("hash",), profiling_adapter=AdapterReference(name="fixture-profiler", version="1", config_fingerprint="config"), dataprofiler_version="fixture", profile_config_hash="profile-config", created_at=datetime(2026, 9, 11, tzinfo=timezone.utc))
    table = TableProfile(profile_id="profile-orders", source_id="src", snapshot_id="snap", table_id="orders", table_kind=SourceTableKind.TABLE, observation_scope=scope, rows_available_in_snapshot=10, rows_profiled=10, column_profile_refs=(), duplicate_row_count=0, duplicate_observation_complete=True, provenance=provenance, status=ProfileCompleteness.COMPLETE)
    profile = ProfileResult(profile_request=ProfileRequest(profile_request_id="profile-result", source_id="src", snapshot_id="snap", selected_table_ids=("orders",), mode=ProfileMode.FULL), tables=(table,), columns=(), patterns=(), failures=(), completeness=ProfileCompleteness.COMPLETE)
    issue = QualityIssue(issue_id="quality-issue", issue_type="REQUIRED_VALUE_MISSING", quality_dimension=QualityDimension.COMPLETENESS, entity_type="table", entity_id="orders", source_id="src", snapshot_id="snap", table_id="orders", column_ids=(), rule_id="rule", severity=QualitySeverity.HIGH, detection_basis=DetectionBasis.EXACT_MEASUREMENT, measurement_semantics=MeasurementSemantics.EXACT_ON_FULL_SNAPSHOT_SCOPE, observation_scope=scope, affected_count=1, affected_ratio=.1, affected_record_refs=(), affected_rows_estimate=1, evidence_refs=(), profile_refs=("profile-orders",), declared_constraint_refs=(), domain_assertion_refs=(), repairability=Repairability.REVIEW_REQUIRED, repair_proposal_refs=("proposal-1",), status=QualityIssueStatus.OPEN, provenance="quality")
    plan = RepairValidationPlan(plan_id="plan-1", remeasure_metrics=("completeness",), invariants=("row_count_preserved",), row_accounting_expectation="preserve", abort_conditions=("new_issue",), lineage_requirements=("quality-issue",), expected_improvement=("fewer_missing",), forbidden_new_issues=("type_mismatch",))
    proposal = RepairProposal(proposal_id="proposal-1", issue_refs=("quality-issue",), repair_type="review_missing", repairability=Repairability.REVIEW_REQUIRED, target_layer="staged", affected_entity_type="table", table_id="orders", column_ids=(), affected_record_refs=(), transform_id="review", transform_version="1", preconditions=("review_required",), expected_effect="review", risk_notes=("none",), lineage_requirement="retain", row_accounting_requirement="preserve", validation_plan=plan, review_required=True, status=RepairProposalStatus.PROPOSED, provenance="quality")
    quality = QualityResult(quality_run_id="quality-result", source_id="src", snapshot_id="snap", rule_set_id="rules", rule_set_version="1", issues=(issue,), repair_proposals=(proposal,), rule_evaluations=(), failures=(), dimension_summaries=(), input_profile_refs=("profile-orders",), input_batch_ids=("batch",), input_batch_hashes=("hash",), provenance="quality", completeness="COMPLETE")
    inputs = EvidenceFusionInputs(producer_statuses=(ProducerEvidenceStatus(producer_id="dependency", family=EvidenceFamily.DEPENDENCY, state=ProducerResultState.COMPLETE, result_id="dependency-result"),), relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0)))
    result = EvidenceFusionService().fuse(_request(), inputs, profile_result=profile, quality_result=quality)
    bundle = result.bundles[0]
    assert any(item.family is EvidenceFamily.PROFILE for item in bundle.signals)
    assert any(item.family is EvidenceFamily.QUALITY and "quality-issue" in item.derived_from_refs for item in bundle.signals)
    assert result.forwarded_repair_proposal_refs == ("proposal-1",)
    unrelated_profile = profile.model_copy(update={"tables": (table.model_copy(update={"table_id": "order"}),)})
    unrelated_issue = issue.model_copy(update={"table_id": "order", "column_ids": ("id",)})
    unrelated_proposal = proposal.model_copy(update={"table_id": "order", "column_ids": ("id",)})
    unrelated_quality = quality.model_copy(update={"issues": (unrelated_issue,), "repair_proposals": (unrelated_proposal,)})
    isolated = EvidenceFusionService().fuse(_request(), inputs, profile_result=unrelated_profile, quality_result=unrelated_quality)
    isolated_ids = {item.evidence_id for item in isolated.bundles[0].signals}
    assert not any(item.startswith("profile:profile-orders") or item.startswith("quality:quality-issue") for item in isolated_ids)
    assert isolated.forwarded_repair_proposal_refs == ()
    same_topology_other_source = quality.model_copy(update={"quality_run_id": "quality-other-source", "source_id": "other-src", "snapshot_id": "other-snap"})
    source_bound = EvidenceFusionService().fuse(_request(), inputs, quality_results=(quality, same_topology_other_source))
    assert source_bound.forwarded_repair_proposal_refs == ("proposal-1",)
    assert any(item.metric_name.startswith("quality_coverage:") for item in source_bound.bundles[0].signals)
    column = ColumnProfile.model_construct(profile_id="profile-orders-customer", source_id="src", snapshot_id="snap", table_id="orders", column_id="customer_id", observation_scope=scope, status=ProfileCompleteness.COMPLETE)
    column_result = EvidenceFusionService().fuse(_request(), inputs, profile_result=profile.model_copy(update={"columns": (column,)}), quality_result=quality)
    assert any(item.metric_name == "column_profile" for item in column_result.bundles[0].signals)


def test_actual_schema_match_snapshot_mapping_is_source_specific():
    from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchCandidate, SchemaMatchMode, SchemaMatchObservationScope, SchemaMatchResult, SchemaMatchScore, SchemaMatchStatus, SchemaMatcherReference
    from dirty_data_to_olap.domain.contracts.source import AdapterReference, ObservationMode

    scope = SchemaMatchObservationScope.model_construct(source_ids=("crm", "erp"), snapshot_ids={"crm": "snap-crm", "erp": "snap-erp"}, table_ids_by_source={"crm": ("orders",), "erp": ("customers",)}, column_ids_by_table={}, source_snapshot_modes={"crm": ObservationMode.FULL, "erp": ObservationMode.FULL}, complete_by_table={"orders": True, "customers": True}, staged_rows_by_table={"orders": 2, "customers": 2}, sampled_rows_by_table={"orders": 2, "customers": 2}, sample_seed=1, sample_mode="FULL", sample_algorithm_version="v1", sample_identity="sample")
    matcher = SchemaMatcherReference(matcher_id="matcher", name="test", version="1", configuration={})
    score = SchemaMatchScore.model_construct(score_id="schema-score", matcher=matcher, source_column_id="customer_code", target_column_id="client_no", raw_native_score=.9, native_score_name="similarity", native_score_semantics="native ranking only", rank=1, config_hash="config", mode=SchemaMatchMode.SCHEMA_ONLY, observation_scope=scope, provenance=AdapterReference(name="test", version="1", config_fingerprint="config"))
    candidate = SchemaMatchCandidate.model_construct(candidate_id="map-1", source_id="crm", source_snapshot_id="snap-crm", source_table_id="orders", source_column_id="customer_code", source_column_name="customer_code", target_source_id="erp", target_snapshot_id="snap-erp", target_table_id="customers", target_column_id="client_no", target_column_name="client_no", score_refs=("schema-score",), signal_refs=(), observation_scope=scope)
    schema = SchemaMatchResult.model_construct(request=None, observation_scope=scope, candidates=(candidate,), scores=(score,), signals=(), failures=(), capabilities=(), pruning=None, evaluations=(), artifacts=(), status=SchemaMatchStatus.COMPLETE)
    inputs = EvidenceFusionInputs(producer_statuses=_statuses(), mapping_candidates=(candidate,))
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="mapping", execution_context_id="mapping", cross_source_mapping_scope=True, relationship_candidate_ids=(), mapping_candidate_ids=("map-1",), policy=EvidenceFusionService.load_policy("mapping")), inputs, schema_match_result=schema)
    signal = next(item for item in result.signals if item.evidence_id == "schema-score")
    assert signal.snapshot_by_source == {"crm": "snap-crm", "erp": "snap-erp"}
    assert not any(item.kind is FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH for item in result.failures)
    broken = schema.model_copy(update={"observation_scope": scope.model_copy(update={"snapshot_ids": {"crm": "wrong", "erp": "snap-erp"}})})
    broken_candidate = candidate.model_copy(update={"observation_scope": broken.observation_scope})
    broken = broken.model_copy(update={"candidates": (broken_candidate,)})
    mismatched = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="mapping-bad", execution_context_id="mapping-bad", cross_source_mapping_scope=True, relationship_candidate_ids=(), mapping_candidate_ids=("map-1",), policy=EvidenceFusionService.load_policy("mapping")), inputs, schema_match_result=broken)
    assert any(item.kind is FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH for item in mismatched.failures)


def test_optional_declared_dimension_has_a_valid_shared_denominator():
    declared = DeclaredConstraintInput(constraint_id="fk-optional", constraint_type="FOREIGN_KEY", source_id="src", from_table="orders", from_columns=("customer_id",), to_table="customers", to_columns=("id",), scope_id="catalog:src")
    evidence = (_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0))
    result = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), declared_constraints=(declared,), evidence_items=evidence))
    score = result.relationships[0].score
    assert score.eligible_weight == 3.75
    assert score.observed_weight == 3.75
    assert score.evidence_coverage == 1.0
    assert score.value == round((.8 + .9 + .75 + 1.0) / 3.75, 12)
    assert -1 <= score.value <= 1


def test_type_compatibility_has_material_adverse_normalization():
    compatible = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 1.0))))
    incompatible = EvidenceFusionService().fuse(_request(), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_candidate(),), evidence_items=(_item("coverage", "inclusion_coverage", .8), _item("unique", "target_uniqueness", .9), _item("type", "type_compatibility", 0.0, EvidenceDirection.CONTRADICTS))))
    good = next(item for item in compatible.relationships[0].score.contributions if item == "type_compatibility")
    assert compatible.relationships[0].score.contributions[good] == .75
    assert incompatible.relationships[0].score.contributions[good] == -.75
    assert incompatible.relationships[0].score.contributions[good] != 0


def test_matcher_families_are_independent_and_order_invariant():
    candidate = {"candidate_id": "map-order", "source_id": "crm", "source_column_id": "customer_code", "target_source_id": "erp", "target_column_id": "client_no"}
    subject = "map:crm:customer_code<->erp:client_no"
    matcher_coma = _item("coma", "matcher_rank:coma", 1.0, subject=subject, family=EvidenceFamily.SCHEMA_MATCHING, score=True).model_copy(update={"score_dimension_id": "matcher:coma:rank"})
    matcher_cupid = _item("cupid", "matcher_rank:cupid", 2.0, subject=subject, family=EvidenceFamily.SCHEMA_MATCHING, score=True).model_copy(update={"score_dimension_id": "matcher:cupid:rank"})
    type_signal = _item("mapping-type", "type_compatibility", 1.0, subject=subject, family=EvidenceFamily.SCHEMA_MATCHING, score=True).model_copy(update={"score_dimension_id": "type_compatibility"})
    statuses = _statuses(schema=True)
    one = EvidenceFusionService().fuse(_request(mapping=True, relationship_ids=(), mapping_ids=("map-order",)), EvidenceFusionInputs(producer_statuses=statuses, mapping_candidates=(candidate,), evidence_items=(matcher_coma, matcher_cupid, type_signal)))
    two = EvidenceFusionService().fuse(_request(mapping=True, relationship_ids=(), mapping_ids=("map-order",)), EvidenceFusionInputs(producer_statuses=statuses, mapping_candidates=(candidate,), evidence_items=(type_signal, matcher_cupid, matcher_coma)))
    one_signal_set = {(item.raw_metric_name, item.normalized_value) for item in one.signals if item.family is EvidenceFamily.SCHEMA_MATCHING}
    two_signal_set = {(item.raw_metric_name, item.normalized_value) for item in two.signals if item.family is EvidenceFamily.SCHEMA_MATCHING}
    assert {"matcher_rank:coma", "matcher_rank:cupid", "type_compatibility"} <= {item[0] for item in one_signal_set}
    assert one_signal_set == two_signal_set
    assert one.mappings[0].score == two.mappings[0].score
    assert one.mappings[0].decision_id == two.mappings[0].decision_id
