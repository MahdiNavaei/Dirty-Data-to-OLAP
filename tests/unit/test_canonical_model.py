from datetime import datetime, timezone

import pytest

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService, CanonicalizationError
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalAttribute,
    CanonicalConflictType,
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalIdentityMembership,
    CanonicalValueReference,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
    MappingStatus,
    NullSemanticState,
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecisionStatus,
    SurvivorshipPolicy,
)
from dirty_data_to_olap.domain.contracts.canonical import SourceAttributeMapping
from dirty_data_to_olap.domain.contracts.evidence_fusion import DecisionExplanation, DecisionState, FusionScore, RelationshipDecision, SemanticMappingDecision


STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def context(checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, *, artifact="evidence-1", content="hash-1"):
    return ReviewCompatibilityContext(
        review_checkpoint_id=checkpoint,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id=artifact,
        subject_content_hash=content,
        subject_schema_version="1.0",
        model_version="canonical-v1",
        source_schema_fingerprints={"crm": "schema-crm"},
        policy_version="review-policy-v1",
        domain_assertion_refs=("domain-1",),
        subject_semantic_id="semantic-1",
        applicability_fingerprint="applicability-1",
    )


def review(policy, ctx, status=ReviewDecisionStatus.ACCEPTED, reviewed_at=STAMP):
    return policy.create_decision(ctx, decision=status, actor="reviewer", rationale="bounded synthetic review", reviewed_at=reviewed_at)


def entity_type(kind=CanonicalEntityKind.IDENTITY):
    return CanonicalEntityType(
        canonical_entity_type_id="cet_customer",
        semantic_id="customer",
        business_name="Customer",
        kind=kind,
        identity_strategy="reviewed_er_identity" if kind is CanonicalEntityKind.IDENTITY else "event_reference_only",
        identity_attribute_ids=(),
        source_table_refs=(),
        canonical_attribute_ids=(),
        relationship_refs=(),
        domain_assertion_refs=("domain-1",),
        review_state="ACCEPTED_BY_REVIEW",
        provenance_refs=("prov-customer",),
    )


def canonical_attribute():
    return CanonicalAttribute(
        canonical_attribute_id="ca_customer_name",
        canonical_entity_type_id="cet_customer",
        semantic_name="display_name",
        normalized_logical_type="string",
        normalization_ref="nfkc-casefold-v1",
        authority_policy_ref="authority-review-required-v1",
        lineage_refs=("lineage-1",),
    )


def relationship_decision(decision_id="relationship-a", subject_id="subject-a"):
    return RelationshipDecision(
        decision_id=decision_id,
        candidate_id="candidate-" + decision_id,
        subject_id=subject_id,
        from_table="orders",
        from_columns=("customer_ref",),
        to_table="customers",
        to_columns=("customer_ref",),
        proposed_cardinality="MANY_TO_ONE",
        score=FusionScore(value=0.2, eligible_weight=1, observed_weight=1, evidence_coverage=1, sufficient=True),
        confidence_band="HIGH",
        decision_state=DecisionState.REVIEW_REQUIRED,
        policy=EvidenceFusionService.load_policy(),
        explanation=DecisionExplanation(),
        input_evidence_fingerprint="input-" + decision_id,
        provenance="unit-test",
    )


def semantic_mapping_decision(decision_id="mapping-a"):
    return SemanticMappingDecision(
        decision_id=decision_id,
        candidate_id="candidate-" + decision_id,
        subject_id="subject-" + decision_id,
        source_id="crm",
        source_column_id="customer_name",
        target_source_id="erp",
        target_column_id="client_name",
        score=FusionScore(value=0.2, eligible_weight=1, observed_weight=1, evidence_coverage=1, sufficient=True),
        confidence_band="HIGH",
        decision_state=DecisionState.REVIEW_REQUIRED,
        policy=EvidenceFusionService.load_policy(),
        explanation=DecisionExplanation(),
        input_evidence_fingerprint="input-" + decision_id,
        provenance="unit-test",
    )


def hypothesis(review_decision):
    return CanonicalHypothesisService().build(
        run_id="run-1",
        execution_context_id="ctx-1",
        model_version="canonical-v1",
        evidence_reviews=(review_decision,),
        entity_types=(entity_type(),),
        source_ids=("crm", "erp"),
        domain_assertion_refs=("domain-1",),
        entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED},
        attributes=(canonical_attribute(),),
        evidence_refs=("evidence-1",),
        provenance_refs=("prov-1",),
        created_at=STAMP,
    )


def test_review_policy_binds_exact_context_and_preserves_invalidation():
    policy = ReviewPolicyService()
    ctx = context()
    decision = review(policy, ctx)
    assert policy.require_compatible(decision, ctx) is decision
    with pytest.raises(ReviewCompatibilityError):
        policy.require_compatible(decision, context(artifact="different"))
    invalidated = policy.invalidate(decision, "source snapshot superseded")
    assert decision.decision is ReviewDecisionStatus.ACCEPTED
    assert invalidated.decision is ReviewDecisionStatus.INVALIDATED
    with pytest.raises(ReviewCompatibilityError):
        policy.require_compatible(invalidated, ctx)


def test_each_material_relationship_requires_its_exact_evidence_review():
    policy = ReviewPolicyService()
    decision_a = relationship_decision("relationship-a", "subject-a")
    decision_b = relationship_decision("relationship-b", "subject-b")
    review_a = policy.create_decision(policy.evidence_context(decision_a, ("domain-1",)), decision=ReviewDecisionStatus.ACCEPTED, actor="reviewer", rationale="review A", reviewed_at=STAMP)
    relationship = __import__("dirty_data_to_olap.domain.contracts.canonical", fromlist=["CanonicalRelationship"]).CanonicalRelationship(
        relationship_id="rel-b", from_entity_type_id="cet_customer", to_entity_type_id="cet_customer", cardinality="MANY_TO_ONE", upstream_decision_ref=decision_b.decision_id, review_decision_ref=review_a.review_decision_id, provenance_refs=("prov-1",)
    )
    with pytest.raises(CanonicalizationError, match="INCOMPLETE_REVIEW"):
        CanonicalHypothesisService(policy).build(run_id="r", execution_context_id="c", model_version="v1", evidence_reviews=(review_a,), relationship_decisions=(decision_b,), entity_types=(entity_type(),), source_ids=("crm",), domain_assertion_refs=("domain-1",), entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED}, relationships=(relationship,), evidence_refs=("e",), provenance_refs=("p",))
    exact = policy.create_decision(policy.evidence_context(decision_b, ("domain-1",)), decision=ReviewDecisionStatus.ACCEPTED, actor="reviewer", rationale="review B", reviewed_at=STAMP)
    good = relationship.model_copy(update={"review_decision_ref": exact.review_decision_id})
    built = CanonicalHypothesisService(policy).build(run_id="r", execution_context_id="c", model_version="v1", evidence_reviews=(exact,), relationship_decisions=(decision_b,), evidence_domain_assertion_refs={decision_b.decision_id: ("domain-1",)}, entity_types=(entity_type(),), source_ids=("crm",), domain_assertion_refs=("domain-1",), entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED}, relationships=(good,), evidence_refs=("e",), provenance_refs=("p",))
    assert built.upstream_decision_refs == (decision_b.decision_id,)
    stale = decision_b.model_copy(update={"subject_id": "changed-subject"})
    with pytest.raises(CanonicalizationError, match="INCOMPLETE_REVIEW"):
        CanonicalHypothesisService(policy).build(run_id="r", execution_context_id="c", model_version="v1", evidence_reviews=(exact,), relationship_decisions=(stale,), evidence_domain_assertion_refs={stale.decision_id: ("domain-1",)}, entity_types=(entity_type(),), source_ids=("crm",), domain_assertion_refs=("domain-1",), entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED}, relationships=(good,), evidence_refs=("e",), provenance_refs=("p",))


def test_each_material_mapping_requires_its_exact_evidence_review():
    policy = ReviewPolicyService()
    decision_a = semantic_mapping_decision("mapping-a")
    decision_b = semantic_mapping_decision("mapping-b")
    review_a = policy.create_decision(policy.evidence_context(decision_a, ("domain-1",)), decision=ReviewDecisionStatus.ACCEPTED, actor="reviewer", rationale="review A", reviewed_at=STAMP)
    mapping = SourceAttributeMapping(
        mapping_id="map-b",
        source_id="crm",
        snapshot_id="snapshot-crm",
        source_schema_fingerprint="schema-crm",
        table_id="customers",
        column_id="customer_name",
        canonical_entity_type_id="cet_customer",
        canonical_attribute_id="ca_customer_name",
        normalization_ref="nfkc-casefold-v1",
        upstream_decision_ref=decision_b.decision_id,
        review_decision_ref=review_a.review_decision_id,
        status=MappingStatus.ACCEPTED,
        provenance_refs=("prov-1",),
    )
    args = dict(run_id="r", execution_context_id="c", model_version="v1", entity_types=(entity_type(),), source_ids=("crm",), domain_assertion_refs=("domain-1",), entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED}, source_attribute_mappings=(mapping,), evidence_refs=("e",), provenance_refs=("p",))
    with pytest.raises(CanonicalizationError, match="INCOMPLETE_REVIEW"):
        CanonicalHypothesisService(policy).build(evidence_reviews=(review_a,), semantic_mapping_decisions=(decision_b,), **args)
    exact = policy.create_decision(policy.evidence_context(decision_b, ("domain-1",)), decision=ReviewDecisionStatus.ACCEPTED, actor="reviewer", rationale="review B", reviewed_at=STAMP)
    built = CanonicalHypothesisService(policy).build(evidence_reviews=(exact,), semantic_mapping_decisions=(decision_b,), evidence_domain_assertion_refs={decision_b.decision_id: ("domain-1",)}, **dict(args, source_attribute_mappings=(mapping.model_copy(update={"review_decision_ref": exact.review_decision_id}),)))
    assert built.upstream_decision_refs == (decision_b.decision_id,)


def test_rejected_deferred_and_bare_skipped_reviews_cannot_authorize():
    policy = ReviewPolicyService()
    decision = relationship_decision()
    ctx = policy.evidence_context(decision, ("domain-1",))
    for status in (ReviewDecisionStatus.REJECTED, ReviewDecisionStatus.DEFERRED):
        review_decision = policy.create_decision(ctx, decision=status, actor="reviewer", rationale=status.value, reviewed_at=STAMP)
        with pytest.raises(CanonicalizationError, match="INCOMPLETE_REVIEW"):
            CanonicalHypothesisService(policy).build(run_id="r", execution_context_id="c", model_version="v1", evidence_reviews=(review_decision,), entity_types=(entity_type(),), source_ids=("crm",), domain_assertion_refs=("domain-1",), entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED}, evidence_refs=("e",), provenance_refs=("p",))
    with pytest.raises(ValueError, match="SKIPPED"):
        policy.create_decision(ctx, decision=ReviewDecisionStatus.SKIPPED, actor="reviewer", rationale="no policy", reviewed_at=STAMP)


def test_hypothesis_is_deterministic_across_input_order_and_time():
    policy = ReviewPolicyService()
    first = hypothesis(review(policy, context()))
    second = CanonicalHypothesisService().build(
        run_id="run-1",
        execution_context_id="ctx-1",
        model_version="canonical-v1",
        evidence_reviews=(review(policy, context(), reviewed_at=STAMP.replace(day=2)),),
        entity_types=(entity_type(),),
        source_ids=("erp", "crm"),
        domain_assertion_refs=("domain-1",),
        entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED},
        attributes=(canonical_attribute(),),
        evidence_refs=("evidence-1",),
        provenance_refs=("prov-1",),
        created_at=STAMP.replace(day=2),
    )
    assert first.artifact_id == second.artifact_id
    assert first.content_hash == second.content_hash
    assert first.artifact_id.startswith("chyp_")


def test_finalization_emits_canonical_id_and_terminal_record_accounting():
    policy = ReviewPolicyService()
    evidence = review(policy, context())
    h = hypothesis(evidence)
    finalizer = CanonicalFinalizationService(policy)
    proposal = CanonicalIdentityProposalService().build(
        hypothesis=h,
        memberships=(CanonicalIdentityMembership(membership_group_id="membership-customer", canonical_entity_type_id="cet_customer", source_record_refs=("crm:r1", "erp:r9"), derivation_basis=IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW, actor="reviewer", actor_source="unit-test", domain_assertion_refs=("domain-1",), evidence_refs=("evidence-1",), policy_refs=("manual-identity-v1",), rationale="explicit unit-test identity proposal", provenance_refs=("prov-1",)),),
        policy_refs=("canonical-identity-v1",),
        provenance_refs=("prov-1",),
        created_at=STAMP,
    )
    identity_review = review(policy, finalizer.identity_context(h, proposal))
    model = finalizer.finalize(
        hypothesis=h,
        identity_proposal=proposal,
        identity_review=identity_review,
        source_record_metadata={
            "crm:r1": {"source_id": "crm", "snapshot_id": "snap-crm", "table_id": "customers"},
            "erp:r9": {"source_id": "erp", "snapshot_id": "snap-erp", "table_id": "clients"},
        },
        finalized_at=STAMP,
    )
    assert model.model_id.startswith("cmodel_")
    assert len(model.instances) == 1
    assert model.instances[0].canonical_entity_id.startswith("cent_")
    assert model.instances[0].canonical_entity_id != "cluster-1"
    assert {item.terminal_disposition.value for item in model.source_record_maps} == {"CONSOLIDATED"}
    assert {item.record_ref for item in model.source_record_maps} == {"crm:r1", "erp:r9"}


def test_required_identity_fails_closed_without_membership_or_provenance():
    policy = ReviewPolicyService()
    evidence = review(policy, context())
    # The build gate is exercised explicitly: ER_REQUIRED cannot omit its spec.
    with pytest.raises(CanonicalizationError, match="MISSING_REQUIRED_ER"):
        CanonicalHypothesisService().build(
            run_id="run-1",
            execution_context_id="ctx-1",
            model_version="canonical-v1",
            evidence_reviews=(evidence,),
            entity_types=(entity_type(),),
            source_ids=("crm",),
            domain_assertion_refs=("domain-1",),
            entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_REQUIRED},
            evidence_refs=("evidence-1",),
            provenance_refs=("prov-1",),
        )


def test_null_semantics_and_survivorship_conflicts_retain_alternatives():
    policy = ReviewPolicyService()
    finalizer = CanonicalFinalizationService(policy)
    values = (
        CanonicalValueReference(value_ref="v1", source_record_refs=("r1",), safe_value="Alice", source_id="crm"),
        CanonicalValueReference(value_ref="v2", source_record_refs=("r2",), safe_value="Alicia", source_id="erp"),
    )
    decision, conflict = finalizer.survivorship(
        attribute_id="ca_customer_name",
        candidates=values,
        policy=SurvivorshipPolicy.PREFERRED_SOURCE,
        policy_ref="authority-review-required-v1",
        review_decision_ref=None,
        source_priority=(),
        provenance_refs=("prov-1",),
    )
    assert decision.selected_value_ref is None
    assert set(decision.losing_value_refs) == {"v1", "v2"}
    assert conflict is not None
    assert conflict.conflict_type is CanonicalConflictType.MISSING_AUTHORITY_CONFLICT
    assert CanonicalValueReference(value_ref="null", null_state=NullSemanticState.SOURCE_NULL_MISSING).safe_value is None
    with pytest.raises(ValueError):
        CanonicalValueReference(value_ref="redacted", null_state=NullSemanticState.WITHHELD_REDACTED, safe_value="secret")
