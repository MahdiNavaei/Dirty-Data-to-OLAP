from datetime import datetime, timezone

import pytest

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalizationError
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalAttribute,
    CanonicalConflictType,
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalValueReference,
    EntityResolutionRequirement,
    NullSemanticState,
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecisionStatus,
    SurvivorshipPolicy,
)


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
    identity_review = review(policy, finalizer.identity_context(h))
    model = finalizer.finalize(
        hypothesis=h,
        identity_review=identity_review,
        memberships={"customer": ("crm:r1", "erp:r9")},
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
