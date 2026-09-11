"""Execute the Step19 review-gated canonical reference flow.

The only ER input is the project-owned normalized result contract produced by
the Step18 provider adapter.  This script does not read evaluation truth,
scores as business authorization, or provider-native objects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalAttribute,
    CanonicalEntityKind,
    CanonicalIdentityMembership,
    CanonicalEntityType,
    CanonicalRelationship,
    CanonicalSourceTable,
    CanonicalValueReference,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
    MappingStatus,
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    SourceAttributeMapping,
    SurvivorshipPolicy,
)
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityMatchPredictionBand, EntityResolutionResult
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    DecisionExplanation,
    DecisionState,
    DomainAssertion,
    FusionScore,
    RelationshipDecision,
    SemanticMappingDecision,
)


RUN = ROOT / "workspace" / "runs" / "step19-reference-run" / "canonical"
ER_RESULT_PATH = ROOT / "workspace" / "runs" / "step18-inference-baseline-v5" / "evaluation" / "entity_resolution" / "normalized_provider_result.json"
STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def jdump(value):
    return value.model_dump(mode="json")


def main() -> int:
    if not ER_RESULT_PATH.exists():
        raise SystemExit(f"required project-owned ER fixture is missing: {ER_RESULT_PATH}")
    RUN.mkdir(parents=True, exist_ok=True)
    outer = json.loads(ER_RESULT_PATH.read_text(encoding="utf-8"))
    er_result = EntityResolutionResult.model_validate(outer["result"])
    selected_edge = next(edge for edge in er_result.edges if {edge.left_record_ref, edge.right_record_ref} == {"crm-r1", "erp-r1"})
    # This is an explicit Step19 synthetic project-owned fixture. Historical
    # Step18 output is not modified and is not treated as canonical truth.
    reference_er_result = er_result.model_copy(update={"edges": (selected_edge.model_copy(update={"model_prediction_band": EntityMatchPredictionBand.STRONG_LINK_EVIDENCE}),), "clusters": (), "artifacts": ()})
    er_hash = stable_digest(reference_er_result.model_dump(mode="json"))

    policy = ReviewPolicyService()
    fusion_policy = EvidenceFusionService.load_policy()
    score = FusionScore(value=0.7, eligible_weight=1.0, observed_weight=1.0, evidence_coverage=1.0, sufficient=True, contributions={"synthetic_review_fixture": 0.7})
    explanation = DecisionExplanation(supports=("domain-assertion-customer-order",), limitations=("uncalibrated score is not authorization",))
    relationship_decision = RelationshipDecision(
        decision_id="rel-reviewed-customer-order",
        candidate_id="candidate-customer-order",
        subject_id="customer-order",
        from_table="orders",
        from_columns=("customer_ref",),
        to_table="customers",
        to_columns=("customer_ref",),
        proposed_cardinality="MANY_TO_ONE",
        score=score,
        confidence_band="HIGH",
        decision_state=DecisionState.REVIEW_REQUIRED,
        policy=fusion_policy,
        supporting_signal_refs=("signal-domain-1",),
        explanation=explanation,
        input_evidence_fingerprint="evidence-fingerprint-1",
        provenance="synthetic-domain-reviewed-fixture",
    )
    mapping_decision = SemanticMappingDecision(
        decision_id="map-reviewed-customer-name",
        candidate_id="candidate-customer-name",
        subject_id="customer-name",
        source_id="crm",
        source_column_id="customers.name",
        target_source_id="erp",
        target_column_id="clients.display_name",
        score=score,
        confidence_band="HIGH",
        decision_state=DecisionState.REVIEW_REQUIRED,
        policy=EvidenceFusionService.load_policy("mapping"),
        supporting_signal_refs=("signal-schema-1",),
        explanation=explanation,
        input_evidence_fingerprint="evidence-fingerprint-2",
        provenance="synthetic-domain-reviewed-fixture",
    )
    domain = DomainAssertion(
        assertion_id="domain-assertion-customer-order",
        subject_id="customer-order",
        statement="Customer is an identity-capable business concept; Order is an event concept.",
        status="ACCEPTED_FOR_CANONICAL_HYPOTHESIS",
        source_ids=("crm", "erp"),
        snapshot_ids=("snapshot-crm", "snapshot-erp"),
        scope_id="synthetic-reference-scope",
        asserted_by="synthetic-domain-review",
        evidence_refs=("domain-evidence-1",),
    )
    relationship_review = policy.create_decision(policy.evidence_context(relationship_decision, (domain.assertion_id,)), decision="ACCEPTED", actor="synthetic-domain-review", actor_source="DOMAIN_REVIEW_FIXTURE", rationale="Reviewed relationship evidence may form a canonical relationship hypothesis.", reviewed_at=STAMP)
    mapping_review = policy.create_decision(policy.evidence_context(mapping_decision, (domain.assertion_id,)), decision="ACCEPTED", actor="synthetic-domain-review", actor_source="DOMAIN_REVIEW_FIXTURE", rationale="Reviewed mapping evidence may form a canonical attribute mapping hypothesis.", reviewed_at=STAMP)

    customer_type = CanonicalEntityType(
        canonical_entity_type_id="cet_customer",
        semantic_id="customer",
        business_name="Customer",
        kind=CanonicalEntityKind.IDENTITY,
        entity_resolution_family="person",
        identity_strategy="conditional_entity_resolution_then_review",
        identity_attribute_ids=("ca_customer_name",),
        source_table_refs=(CanonicalSourceTable(source_id="crm", snapshot_id="snapshot-crm", table_id="customers", schema_fingerprint="schema-crm"), CanonicalSourceTable(source_id="erp", snapshot_id="snapshot-erp", table_id="clients", schema_fingerprint="schema-erp")),
        canonical_attribute_ids=("ca_customer_name",),
        relationship_refs=("rel_customer_order",),
        domain_assertion_refs=(domain.assertion_id,),
        review_state="HYPOTHESIS_FROM_REVIEWED_EVIDENCE",
        provenance_refs=("source:crm", "source:erp", "domain:synthetic-review"),
    )
    order_type = CanonicalEntityType(
        canonical_entity_type_id="cet_order",
        semantic_id="order",
        business_name="Order",
        kind=CanonicalEntityKind.EVENT,
        identity_strategy="source_event_reference_only",
        domain_assertion_refs=(domain.assertion_id,),
        review_state="HYPOTHESIS_FROM_REVIEWED_EVIDENCE",
        provenance_refs=("source:crm", "domain:synthetic-review"),
    )
    customer_attribute = CanonicalAttribute(
        canonical_attribute_id="ca_customer_name",
        canonical_entity_type_id="cet_customer",
        semantic_name="display_name",
        normalized_logical_type="string",
        candidate_mapping_refs=(mapping_decision.decision_id,),
        accepted_mapping_refs=("mapping-customer-name",),
        normalization_ref="nfkc-casefold-v1",
        authority_policy_ref="preferred-source-policy-review-required-v1",
        review_decision_ref=mapping_review.review_decision_id,
        lineage_refs=("lineage:customer-name", "source:crm", "source:erp"),
    )
    relationship = CanonicalRelationship(
        relationship_id="rel_customer_order",
        from_entity_type_id="cet_order",
        to_entity_type_id="cet_customer",
        cardinality="MANY_TO_ONE",
        upstream_decision_ref=relationship_decision.decision_id,
        review_decision_ref=relationship_review.review_decision_id,
        provenance_refs=("domain:synthetic-review", "evidence:relationship-decision"),
    )
    mapping = SourceAttributeMapping(
        mapping_id="mapping-customer-name",
        source_id="crm",
        snapshot_id="snapshot-crm",
        source_schema_fingerprint="schema-crm",
        table_id="customers",
        column_id="customers.name",
        canonical_entity_type_id="cet_customer",
        canonical_attribute_id="ca_customer_name",
        normalization_ref="nfkc-casefold-v1",
        upstream_decision_ref=mapping_decision.decision_id,
        review_decision_ref=mapping_review.review_decision_id,
        evidence_refs=("signal-schema-1",),
        status=MappingStatus.ACCEPTED,
        provenance_refs=("source:crm", "snapshot:snapshot-crm", "review:evidence"),
    )
    hypothesis = CanonicalHypothesisService(policy).build(
        run_id="step19-reference-run",
        execution_context_id="step19-reference-context",
        model_version="canonical-v1",
        evidence_reviews=(mapping_review, relationship_review),
        relationship_decisions=(relationship_decision,),
        semantic_mapping_decisions=(mapping_decision,),
        evidence_domain_assertion_refs={relationship_decision.decision_id: (domain.assertion_id,), mapping_decision.decision_id: (domain.assertion_id,)},
        entity_types=(order_type, customer_type),
        source_ids=("erp", "crm"),
        domain_assertion_refs=(domain.assertion_id,),
        entity_resolution_requirements={"person": EntityResolutionRequirement.ER_REQUIRED, "order": EntityResolutionRequirement.ER_NOT_REQUIRED},
        attributes=(customer_attribute,),
        relationships=(relationship,),
        source_attribute_mappings=(mapping,),
        entity_resolution_specs=(er_result.spec,),
        snapshot_fingerprints={"crm": "fixture-crm", "erp": "fixture-erp"},
        source_schema_fingerprints={"crm": "schema-crm", "erp": "schema-erp"},
        normalization_refs=("nfkc-casefold-v1",),
        source_authority_policy_refs=("preferred-source-policy-review-required-v1",),
        evidence_refs=(relationship_decision.decision_id, mapping_decision.decision_id, domain.assertion_id),
        provenance_refs=("source:crm", "source:erp", "domain:synthetic-review", "er-artifact-bound-reference"),
        created_at=STAMP,
    )
    identity_proposal = CanonicalIdentityProposalService().build(
        hypothesis=hypothesis,
        memberships=(
            CanonicalIdentityMembership(membership_group_id="membership-customer-r1", canonical_entity_type_id="cet_customer", entity_resolution_family="person", source_record_refs=("crm-r1", "erp-r1"), derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE, authorized_edge_refs=(selected_edge.edge_id,), evidence_refs=(selected_edge.edge_id,), policy_refs=("er-clustering-v1",), rationale="authorized strong project edge in synthetic Step19 ER fixture", provenance_refs=("er:synthetic-reference",)),
            CanonicalIdentityMembership(membership_group_id="membership-order-r1", canonical_entity_type_id="cet_order", source_record_refs=("order-r1",), derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY, evidence_refs=(relationship_decision.decision_id,), policy_refs=("event-source-identity-v1",), rationale="reviewed source-local event identity; generic ER is not applicable", provenance_refs=("source:orders", "review:event-identity")),
        ),
        er_results={"person": reference_er_result},
        policy_refs=("canonical-identity-v1", "er-clustering-v1", "event-source-identity-v1"),
        provenance_refs=("synthetic-reference",),
        created_at=STAMP,
    )
    # Proposal must exist before the identity review; recompute the exact
    # context from that proposal, then review it.
    identity_context = CanonicalFinalizationService(policy).identity_context(hypothesis, identity_proposal, {"person": er_hash})
    identity_review = policy.create_decision(identity_context, decision="ACCEPTED", actor="synthetic-canonical-review", actor_source="CANONICAL_REVIEW_FIXTURE", rationale="Exact Customer ER proposal and Order source-local proposal reviewed.", reviewed_at=STAMP)
    values = (
        CanonicalValueReference(value_ref="value-customer-name-crm", source_record_refs=("crm-r1",), source_value_digest="digest-crm-name", safe_value="Alice", source_id="crm", snapshot_id="snapshot-crm"),
        CanonicalValueReference(value_ref="value-customer-name-erp", source_record_refs=("erp-r1",), source_value_digest="digest-erp-name", safe_value="Alicia", source_id="erp", snapshot_id="snapshot-erp"),
    )
    survivorship, conflict = CanonicalFinalizationService(policy).survivorship(
        attribute_id="ca_customer_name",
        candidates=values,
        policy=SurvivorshipPolicy.PREFERRED_SOURCE,
        policy_ref="preferred-source-policy-review-required-v1",
        review_decision_ref=identity_review.review_decision_id,
        source_priority=("crm",),
        provenance_refs=("source:crm", "source:erp", "review:canonical-identity"),
    )
    model = CanonicalFinalizationService(policy).finalize(
        hypothesis=hypothesis,
        identity_review=identity_review,
        identity_proposal=identity_proposal,
        er_results={"person": reference_er_result},
        source_record_metadata={"crm-r1": {"source_id": "crm", "snapshot_id": "snapshot-crm", "table_id": "customers"}, "erp-r1": {"source_id": "erp", "snapshot_id": "snapshot-erp", "table_id": "clients"}, "order-r1": {"source_id": "crm", "snapshot_id": "snapshot-crm", "table_id": "orders"}},
        survivorship_decisions=(survivorship,),
        conflicts=(conflict,) if conflict else (),
        lineage_refs=("lineage:customer-name", "lineage:identity-review"),
        record_accounting_refs=("accounting:step19-reference-run",),
        finalized_at=STAMP,
    )
    accounting = {"artifact_id": "accounting:step19-reference-run", "source_records_considered": 3, "source_records_mapped": 3, "canonical_instances_emitted": len(model.instances), "terminal_dispositions": {item.terminal_disposition.value: sum(1 for current in model.source_record_maps if current.terminal_disposition is item.terminal_disposition) for item in model.source_record_maps}, "source_records_preserved": True, "destructive_deduplication": False}

    dump(RUN / "domain_assertions.json", [jdump(domain)])
    dump(RUN / "review_evidence_decisions.json", [jdump(relationship_review), jdump(mapping_review)])
    dump(RUN / "upstream_fusion_decisions.json", [jdump(relationship_decision), jdump(mapping_decision)])
    dump(RUN / "canonical_model_hypothesis.json", jdump(hypothesis))
    dump(RUN / "entity_resolution_binding.json", {"family": "person", "status": reference_er_result.status.value, "spec_id": reference_er_result.spec.spec_id, "spec_fingerprint": reference_er_result.spec.fingerprint, "synthetic_reference_fixture": True, "historical_step18_result_modified": False, "provider_result_sha256": er_hash, "artifact_refs": [item.artifact_id for item in reference_er_result.artifacts]})
    dump(RUN / "entity_resolution_reference_result.json", jdump(reference_er_result))
    dump(RUN / "canonical_identity_proposal.json", jdump(identity_proposal))
    dump(RUN / "review_canonical_identity.json", jdump(identity_review))
    dump(RUN / "survivorship_decisions.json", [jdump(survivorship)])
    dump(RUN / "conflicts.json", [jdump(conflict)] if conflict else [])
    dump(RUN / "source_record_canonical_map.json", [jdump(item) for item in model.source_record_maps])
    dump(RUN / "record_accounting.json", accounting)
    dump(RUN / "canonical_model.json", jdump(model))
    dump(RUN / "run_manifest.json", {"run_id": "step19-reference-run", "flow": ["REVIEW_EVIDENCE_DECISIONS", "CANONICAL_HYPOTHESES", "ENTITY_RESOLUTION", "REVIEW_CANONICAL_IDENTITY", "CANONICAL_FINALIZATION"], "runtime_truth_inputs": [], "step18_evaluation_truth_used": False, "er_input": "explicit synthetic project-owned ER contract fixture only", "identity_membership_basis": {"customer": "ER_AUTHORIZED_LINKAGE via authorized strong edge", "order": "SOURCE_LOCAL_EVENT_IDENTITY via reviewed proposal"}, "canonical_model_content_hash": model.content_hash, "hypothesis_content_hash": hypothesis.content_hash})
    print(json.dumps({"run": str(RUN), "hypothesis": hypothesis.artifact_id, "model": model.model_id, "model_content_hash": model.content_hash, "instances": len(model.instances), "maps": len(model.source_record_maps), "conflicts": len(model.conflicts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
