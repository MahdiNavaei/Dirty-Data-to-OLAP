"""Two-phase canonical hypothesis and finalization services."""

from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalAttribute,
    CanonicalConflict,
    CanonicalConflictType,
    CanonicalEntityInstance,
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalIdentityFieldHypothesis,
    CanonicalIdentityMembership,
    CanonicalIdentityProposal,
    CanonicalModel,
    CanonicalModelHypothesis,
    CanonicalRelationship,
    CanonicalSourceTable,
    CanonicalSurvivorshipDecision,
    CanonicalValueReference,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
    MappingStatus,
    NullSemanticState,
    RecordDisposition,
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecision,
    ReviewDecisionStatus,
    SourceAttributeMapping,
    SourceRecordCanonicalMap,
    SurvivorshipPolicy,
    canonical_entity_id,
    canonical_model_id,
    hypothesis_id,
    identity_proposal_id,
)
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityMatchPredictionBand, EntityResolutionResult, EntityResolutionStatus
from dirty_data_to_olap.domain.contracts.evidence_fusion import DecisionState, RelationshipDecision, SemanticMappingDecision
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id, utc_now
from .review_policy import ReviewCompatibilityError, ReviewPolicyService


class CanonicalizationError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _ordered(values: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(sorted(values, key=lambda item: stable_digest(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)))


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold().strip())


class CanonicalHypothesisService:
    def __init__(self, review_policy: ReviewPolicyService | None = None) -> None:
        self.review_policy = review_policy or ReviewPolicyService()

    def build(
        self,
        *,
        run_id: str,
        execution_context_id: str,
        model_version: str,
        evidence_reviews: Sequence[ReviewDecision],
        relationship_decisions: Sequence[RelationshipDecision] = (),
        semantic_mapping_decisions: Sequence[SemanticMappingDecision] = (),
        evidence_domain_assertion_refs: Mapping[str, Sequence[str]] | None = None,
        entity_types: Sequence[CanonicalEntityType],
        source_ids: Sequence[str],
        domain_assertion_refs: Sequence[str],
        entity_resolution_requirements: Mapping[str, EntityResolutionRequirement],
        attributes: Sequence[CanonicalAttribute] = (),
        relationships: Sequence[CanonicalRelationship] = (),
        source_attribute_mappings: Sequence[SourceAttributeMapping] = (),
        identity_field_hypotheses: Sequence[CanonicalIdentityFieldHypothesis] = (),
        entity_resolution_specs: Sequence[Any] = (),
        snapshot_fingerprints: Mapping[str, str] | None = None,
        source_schema_fingerprints: Mapping[str, str] | None = None,
        normalization_refs: Sequence[str] = (),
        source_authority_policy_refs: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        provenance_refs: Sequence[str] = (),
        unresolved_ambiguities: Sequence[str] = (),
        unresolved_semantic_conflicts: Sequence[str] = (),
        created_at: datetime | None = None,
    ) -> CanonicalModelHypothesis:
        if not evidence_reviews:
            raise CanonicalizationError("INCOMPLETE_REVIEW", "REVIEW_EVIDENCE_DECISIONS is required before hypothesis publication")
        for review in evidence_reviews:
            if review.review_checkpoint_id is not ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS:
                raise CanonicalizationError("INCOMPLETE_REVIEW", "only REVIEW_EVIDENCE_DECISIONS can feed hypotheses")
            if review.decision not in {ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED} or review.superseded:
                raise CanonicalizationError("INCOMPLETE_REVIEW", "evidence review is not acceptable")
        if any(item.decision_state not in {DecisionState.REVIEW_REQUIRED, DecisionState.INCOMPLETE_REQUIRED_EVIDENCE} for item in relationship_decisions):
            raise CanonicalizationError("INVALID_SOURCE_MAPPING", "fusion relationship decisions must remain review-only")
        if any(item.decision_state not in {DecisionState.REVIEW_REQUIRED, DecisionState.INCOMPLETE_REQUIRED_EVIDENCE} for item in semantic_mapping_decisions):
            raise CanonicalizationError("INVALID_SOURCE_MAPPING", "fusion mapping decisions must remain review-only")
        requirements = {key: EntityResolutionRequirement(value) for key, value in entity_resolution_requirements.items()}
        domain_by_decision = {key: tuple(sorted(value)) for key, value in (evidence_domain_assertion_refs or {}).items()}
        decisions_by_id = {item.decision_id: item for item in (*relationship_decisions, *semantic_mapping_decisions)}
        if len(decisions_by_id) != len(relationship_decisions) + len(semantic_mapping_decisions):
            raise CanonicalizationError("INCOMPLETE_REVIEW", "evidence decision IDs must be unique")
        reviews_by_id = {item.review_decision_id: item for item in evidence_reviews}
        if len(reviews_by_id) != len(evidence_reviews):
            raise CanonicalizationError("INCOMPLETE_REVIEW", "review decision IDs must be unique")
        bound_review_ids: set[str] = set()
        for relationship in relationships:
            upstream = decisions_by_id.get(relationship.upstream_decision_ref)
            if not isinstance(upstream, RelationshipDecision) or relationship.review_decision_ref is None:
                raise CanonicalizationError("INCOMPLETE_REVIEW", f"relationship {relationship.relationship_id} lacks its exact reviewed decision")
            review = reviews_by_id.get(relationship.review_decision_ref)
            if review is None:
                raise CanonicalizationError("INCOMPLETE_REVIEW", f"relationship {relationship.relationship_id} references an unknown review")
            try:
                expected_context = self.review_policy.evidence_context(upstream, domain_by_decision.get(upstream.decision_id, ())).model_copy(update={"subject_artifact_id": review.subject_artifact_id, "subject_content_hash": review.subject_content_hash})
                self.review_policy.require_compatible(review, expected_context)
            except ReviewCompatibilityError as error:
                raise CanonicalizationError("INCOMPLETE_REVIEW", str(error)) from error
            bound_review_ids.add(review.review_decision_id)
        for mapping in source_attribute_mappings:
            upstream = decisions_by_id.get(mapping.upstream_decision_ref)
            if not isinstance(upstream, SemanticMappingDecision) or mapping.review_decision_ref is None:
                raise CanonicalizationError("INCOMPLETE_REVIEW", f"mapping {mapping.mapping_id} lacks its exact reviewed decision")
            review = reviews_by_id.get(mapping.review_decision_ref)
            if review is None:
                raise CanonicalizationError("INCOMPLETE_REVIEW", f"mapping {mapping.mapping_id} references an unknown review")
            try:
                expected_context = self.review_policy.evidence_context(upstream, domain_by_decision.get(upstream.decision_id, ())).model_copy(update={"subject_artifact_id": review.subject_artifact_id, "subject_content_hash": review.subject_content_hash})
                self.review_policy.require_compatible(review, expected_context)
            except ReviewCompatibilityError as error:
                raise CanonicalizationError("INCOMPLETE_REVIEW", str(error)) from error
            bound_review_ids.add(review.review_decision_id)
        spec_by_family = {spec.entity_family: spec for spec in entity_resolution_specs}
        missing = [family for family, requirement in requirements.items() if requirement is EntityResolutionRequirement.ER_REQUIRED and family not in spec_by_family]
        if missing:
            raise CanonicalizationError("MISSING_REQUIRED_ER", ",".join(sorted(missing)))
        payload = {
            "run_id": run_id,
            "execution_context_id": execution_context_id,
            "model_version": model_version,
            "upstream_decision_refs": sorted({ref for item in relationships for ref in (item.upstream_decision_ref,)} | {ref for item in source_attribute_mappings for ref in (item.upstream_decision_ref,)} | {item.decision_id for item in relationship_decisions} | {item.decision_id for item in semantic_mapping_decisions}),
            "upstream_review_decision_refs": sorted(bound_review_ids or {review.review_decision_id for review in evidence_reviews}),
            "source_ids": sorted(set(source_ids)),
            "snapshot_fingerprints": dict(sorted((snapshot_fingerprints or {}).items())),
            "source_schema_fingerprints": dict(sorted((source_schema_fingerprints or {}).items())),
            "domain_assertion_refs": sorted(set(domain_assertion_refs)),
            "entity_types": [item.model_dump(mode="json") for item in _ordered(entity_types)],
            "attributes": [item.model_dump(mode="json") for item in _ordered(attributes)],
            "relationships": [item.model_dump(mode="json") for item in _ordered(relationships)],
            "source_attribute_mappings": [item.model_dump(mode="json") for item in _ordered(source_attribute_mappings)],
            "identity_field_hypotheses": [item.model_dump(mode="json") for item in _ordered(identity_field_hypotheses)],
            "entity_resolution_requirements": dict(sorted((key, value.value) for key, value in requirements.items())),
            "entity_resolution_specs": [item.model_dump(mode="json") for item in _ordered(entity_resolution_specs)],
            "normalization_refs": sorted(set(normalization_refs)),
            "source_authority_policy_refs": sorted(set(source_authority_policy_refs)),
            "evidence_refs": sorted(set(evidence_refs)),
            "provenance_refs": sorted(set(provenance_refs)),
            "unresolved_ambiguities": sorted(set(unresolved_ambiguities)),
            "unresolved_semantic_conflicts": sorted(set(unresolved_semantic_conflicts)),
        }
        if not payload["domain_assertion_refs"] or not payload["evidence_refs"] or not payload["provenance_refs"]:
            raise CanonicalizationError("PROVENANCE_INCOMPLETE", "hypothesis requires domain, evidence and provenance references")
        artifact = hypothesis_id(payload)
        return CanonicalModelHypothesis(
            artifact_id=artifact,
            run_id=run_id,
            execution_context_id=execution_context_id,
            model_version=model_version,
            upstream_decision_refs=tuple(payload["upstream_decision_refs"]),
            upstream_review_decision_refs=tuple(payload["upstream_review_decision_refs"]),
            source_ids=tuple(payload["source_ids"]),
            snapshot_fingerprints=payload["snapshot_fingerprints"],
            source_schema_fingerprints=payload["source_schema_fingerprints"],
            domain_assertion_refs=tuple(payload["domain_assertion_refs"]),
            entity_types=_ordered(entity_types),
            attributes=_ordered(attributes),
            relationships=_ordered(relationships),
            source_attribute_mappings=_ordered(source_attribute_mappings),
            identity_field_hypotheses=_ordered(identity_field_hypotheses),
            entity_resolution_requirements=requirements,
            entity_resolution_specs=_ordered(entity_resolution_specs),
            normalization_refs=tuple(payload["normalization_refs"]),
            unresolved_ambiguities=tuple(payload["unresolved_ambiguities"]),
            unresolved_semantic_conflicts=tuple(payload["unresolved_semantic_conflicts"]),
            source_authority_policy_refs=tuple(payload["source_authority_policy_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            provenance_refs=tuple(payload["provenance_refs"]),
            created_at=created_at or utc_now(),
        )


class CanonicalIdentityProposalService:
    def build(
        self,
        *,
        hypothesis: CanonicalModelHypothesis,
        memberships: Sequence[CanonicalIdentityMembership],
        er_results: Mapping[str, EntityResolutionResult] | None = None,
        source_schema_fingerprints: Mapping[str, str] | None = None,
        policy_refs: Sequence[str],
        provenance_refs: Sequence[str],
        created_at: datetime | None = None,
    ) -> CanonicalIdentityProposal:
        er_results = dict(er_results or {})
        entity_types = {item.canonical_entity_type_id: item for item in hypothesis.entity_types}
        seen_records: set[str] = set()
        normalized: list[CanonicalIdentityMembership] = []
        result_refs: set[str] = set()
        spec_refs: set[str] = set()
        result_hashes: dict[str, str] = {}
        required_families = {family for family, requirement in hypothesis.entity_resolution_requirements.items() if requirement is EntityResolutionRequirement.ER_REQUIRED}
        for family in sorted(required_families):
            result = er_results.get(family)
            if result is None:
                raise CanonicalizationError("MISSING_REQUIRED_ER", family)
            self._validate_er_result_compatibility(hypothesis, result, family)
            result_hashes[family] = stable_digest(result.model_dump(mode="json"))
        for membership in memberships:
            entity_type = entity_types.get(membership.canonical_entity_type_id)
            if entity_type is None:
                raise CanonicalizationError("UNRESOLVED_IDENTITY", f"unknown entity type {membership.canonical_entity_type_id}")
            if seen_records.intersection(membership.source_record_refs):
                raise CanonicalizationError("UNRESOLVED_IDENTITY", "a source record appears in multiple identity groups")
            seen_records.update(membership.source_record_refs)
            family = membership.entity_resolution_family or entity_type.entity_resolution_family or entity_type.semantic_id
            requirement = hypothesis.entity_resolution_requirements.get(family, EntityResolutionRequirement.ER_NOT_REQUIRED)
            if membership.derivation_basis is IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE:
                if requirement is not EntityResolutionRequirement.ER_REQUIRED:
                    raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", f"ER linkage is not required/authorized for {family}")
                result = er_results.get(family)
                if result is None:
                    raise CanonicalizationError("MISSING_REQUIRED_ER", family)
                self.validate_er_membership(hypothesis, membership, result, family)
                artifact_ids = tuple(sorted(item.artifact_id for item in result.artifacts))
                membership = membership.model_copy(update={"entity_resolution_family": family, "er_result_refs": artifact_ids, "er_spec_refs": (result.spec.spec_id,), "evidence_refs": tuple(sorted(set(membership.evidence_refs) | set(membership.authorized_edge_refs) | set(artifact_ids)))})
                result_refs.update(artifact_ids)
                spec_refs.add(result.spec.spec_id)
            elif requirement is EntityResolutionRequirement.ER_REQUIRED:
                result = er_results[family]
                if membership.derivation_basis is not IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW:
                    raise CanonicalizationError("MISSING_REQUIRED_ER", family)
                if not set(membership.source_record_refs).issubset(self._result_population(result)):
                    raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", f"human override contains records outside the evaluated population for {family}")
                result_refs.update(item.artifact_id for item in result.artifacts)
                spec_refs.add(result.spec.spec_id)
            elif entity_type.kind is CanonicalEntityKind.EVENT and membership.derivation_basis is not IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY:
                raise CanonicalizationError("UNRESOLVED_IDENTITY", "event identity requires explicit source-local identity basis")
            normalized.append(membership)
        if not normalized:
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "identity proposal requires at least one membership group")
        proposed_families = {item.entity_resolution_family or entity_types[item.canonical_entity_type_id].entity_resolution_family or entity_types[item.canonical_entity_type_id].semantic_id for item in normalized}
        if not required_families.issubset(proposed_families):
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "required identity family has no proposed membership")
        payload = {
            "hypothesis": hypothesis.content_hash,
            "entity_types": sorted(entity_types[item.canonical_entity_type_id].semantic_id for item in normalized),
            "requirements": {key: value.value for key, value in sorted(hypothesis.entity_resolution_requirements.items())},
            "memberships": [item.model_dump(mode="json") for item in sorted(normalized, key=lambda item: item.membership_group_id)],
            "er_result_refs": sorted(result_refs),
            "er_result_hashes": dict(sorted(result_hashes.items())),
            "er_spec_refs": sorted(spec_refs),
            "source_schema_fingerprints": dict(sorted((source_schema_fingerprints or hypothesis.source_schema_fingerprints).items())),
            "domain_assertion_refs": sorted(hypothesis.domain_assertion_refs),
            "policy_refs": sorted(set(policy_refs)),
            "provenance_refs": sorted(set(provenance_refs) | set(hypothesis.provenance_refs)),
        }
        if not payload["policy_refs"] or not payload["provenance_refs"]:
            raise CanonicalizationError("PROVENANCE_INCOMPLETE", "identity proposal requires policy and provenance refs")
        return CanonicalIdentityProposal(
            proposal_id=identity_proposal_id(payload),
            hypothesis_artifact_id=hypothesis.artifact_id,
            canonical_entity_type_ids=tuple(sorted({item.canonical_entity_type_id for item in normalized})),
            entity_resolution_requirements=hypothesis.entity_resolution_requirements,
            memberships=tuple(sorted(normalized, key=lambda item: item.membership_group_id)),
            er_result_refs=tuple(sorted(result_refs)),
            er_result_hashes=payload["er_result_hashes"],
            er_spec_refs=tuple(sorted(spec_refs)),
            source_schema_fingerprints=payload["source_schema_fingerprints"],
            domain_assertion_refs=hypothesis.domain_assertion_refs,
            policy_refs=payload["policy_refs"],
            unresolved_identity_cases=hypothesis.unresolved_ambiguities,
            provenance_refs=payload["provenance_refs"],
            created_at=created_at or utc_now(),
        )

    @staticmethod
    def validate_er_membership(hypothesis: CanonicalModelHypothesis, membership: CanonicalIdentityMembership, result: EntityResolutionResult, family: str) -> None:
        CanonicalIdentityProposalService._validate_er_result_compatibility(hypothesis, result, family)
        edges = {item.edge_id: item for item in result.edges}
        if any(edge_id not in edges for edge_id in membership.authorized_edge_refs):
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", "membership references a missing ER edge")
        allowed = {EntityMatchPredictionBand.STRONG_LINK_EVIDENCE}
        if result.spec.clustering_policy.include_review_edges:
            allowed.add(EntityMatchPredictionBand.REVIEW_LINK_EVIDENCE)
        selected_edges = [edges[edge_id] for edge_id in membership.authorized_edge_refs]
        if any(edge.model_prediction_band not in allowed for edge in selected_edges):
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", "membership uses an edge outside the authorized cluster policy")
        proposed_records = set(membership.source_record_refs)
        selected_records = {ref for edge in selected_edges for ref in (edge.left_record_ref, edge.right_record_ref)}
        if not selected_records.issubset(proposed_records):
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", "authorized edge endpoint is outside the proposed membership")
        if selected_records != proposed_records:
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "every proposed ER member must be present in the selected-edge graph")
        adjacency = {record_ref: set() for record_ref in proposed_records}
        for edge in selected_edges:
            adjacency[edge.left_record_ref].add(edge.right_record_ref)
            adjacency[edge.right_record_ref].add(edge.left_record_ref)
        visited: set[str] = set()
        pending = [next(iter(proposed_records))]
        while pending:
            record_ref = pending.pop()
            if record_ref in visited:
                continue
            visited.add(record_ref)
            pending.extend(adjacency[record_ref] - visited)
        if visited != proposed_records:
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "authorized ER edges must form one connected component")
        if not proposed_records:
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "ER-derived membership cannot be empty")
        if not proposed_records.issubset(CanonicalIdentityProposalService._result_population(result)):
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", "membership contains records absent from the ER evaluated output")

    @staticmethod
    def _validate_er_result_compatibility(hypothesis: CanonicalModelHypothesis, result: EntityResolutionResult, family: str) -> None:
        expected = next((item for item in hypothesis.entity_resolution_specs if item.entity_family == family), None)
        if expected is None:
            raise CanonicalizationError("MISSING_REQUIRED_ER", family)
        if result.status is not EntityResolutionStatus.COMPLETE or result.spec.spec_id != expected.spec_id or result.spec.entity_family != family or result.spec.fingerprint != expected.fingerprint:
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", family)
        if set(result.spec.source_ids) != set(expected.source_ids) or dict(result.spec.snapshot_ids) != dict(expected.snapshot_ids) or {key: tuple(value) for key, value in result.spec.table_ids_by_source.items()} != {key: tuple(value) for key, value in expected.table_ids_by_source.items()}:
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", f"scope mismatch for {family}")
        scope = result.observation_scope
        if set(scope.source_ids) != set(expected.source_ids) or dict(scope.snapshot_ids) != dict(expected.snapshot_ids) or {key: tuple(value) for key, value in scope.table_ids_by_source.items()} != {key: tuple(value) for key, value in expected.table_ids_by_source.items()}:
            raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", f"observation scope mismatch for {family}")

    @staticmethod
    def _result_population(result: EntityResolutionResult) -> set[str]:
        return {ref for edge in result.edges for ref in (edge.left_record_ref, edge.right_record_ref)} | {ref for cluster in result.clusters for ref in cluster.record_refs}


class CanonicalFinalizationService:
    def __init__(self, review_policy: ReviewPolicyService | None = None) -> None:
        self.review_policy = review_policy or ReviewPolicyService()

    def identity_context(
        self,
        hypothesis: CanonicalModelHypothesis,
        identity_proposal: CanonicalIdentityProposal,
        er_hashes: Mapping[str, str] | None = None,
        *,
        subject_content_hash: str | None = None,
    ) -> ReviewCompatibilityContext:
        if identity_proposal.hypothesis_artifact_id != hypothesis.artifact_id:
            raise CanonicalizationError("STALE_REVIEW", "identity proposal does not bind the hypothesis")
        supplied_er_hashes = dict(er_hashes or {})
        if any(supplied_er_hashes.get(family) != digest for family, digest in identity_proposal.er_result_hashes.items()):
            raise CanonicalizationError("STALE_REVIEW", "identity proposal is not bound to the supplied ER result hash")
        fingerprints = dict(identity_proposal.source_schema_fingerprints)
        fingerprints.update({f"er:{family}": digest for family, digest in supplied_er_hashes.items()})
        return ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY,
            subject_stage="CANONICAL_IDENTITY_PROPOSAL",
            subject_artifact_id=identity_proposal.proposal_id,
            # Domain callers retain the semantic model hash by default.  The
            # durable review boundary supplies the immutable artifact-store
            # hash so the API can bind the assertion to the published bytes.
            subject_content_hash=subject_content_hash or identity_proposal.content_hash,
            subject_schema_version=identity_proposal.schema_version,
            model_version=hypothesis.model_version,
            source_schema_fingerprints=fingerprints,
            policy_version="canonical-identity-v1",
            domain_assertion_refs=identity_proposal.domain_assertion_refs,
            subject_semantic_id=stable_id("csem", identity_proposal.canonical_entity_type_ids),
            applicability_fingerprint=stable_digest({"hypothesis": hypothesis.content_hash, "proposal": identity_proposal.content_hash, "er": fingerprints}),
        )

    def finalize(
        self,
        *,
        hypothesis: CanonicalModelHypothesis,
        identity_proposal: CanonicalIdentityProposal,
        identity_review: ReviewDecision,
        er_results: Mapping[str, EntityResolutionResult] | None = None,
        source_record_metadata: Mapping[str, Mapping[str, str]] | None = None,
        survivorship_decisions: Sequence[CanonicalSurvivorshipDecision] = (),
        conflicts: Sequence[CanonicalConflict] = (),
        lineage_refs: Sequence[str] = (),
        record_accounting_refs: Sequence[str] = (),
        finalized_at: datetime | None = None,
    ) -> CanonicalModel:
        er_results = dict(er_results or {})
        er_hashes = {family: stable_digest(result.model_dump(mode="json")) for family, result in er_results.items()}
        required_families = {family for family, requirement in hypothesis.entity_resolution_requirements.items() if requirement is EntityResolutionRequirement.ER_REQUIRED}
        for family in sorted(required_families):
            result = er_results.get(family)
            if result is None:
                raise CanonicalizationError("MISSING_REQUIRED_ER", family)
            CanonicalIdentityProposalService._validate_er_result_compatibility(hypothesis, result, family)
        try:
            self.review_policy.require_compatible(
                identity_review,
                self.identity_context(
                    hypothesis,
                    identity_proposal,
                    er_hashes,
                    subject_content_hash=identity_review.subject_content_hash,
                ),
            )
        except ReviewCompatibilityError as error:
            raise CanonicalizationError("STALE_REVIEW", str(error)) from error
        if any(requirement is EntityResolutionRequirement.ER_REQUIRED for requirement in hypothesis.entity_resolution_requirements.values()) and identity_review.decision is ReviewDecisionStatus.SKIPPED:
            raise CanonicalizationError("INCOMPLETE_REVIEW", "required ER identity cannot be skipped")
        if identity_proposal.hypothesis_artifact_id != hypothesis.artifact_id:
            raise CanonicalizationError("STALE_REVIEW", "identity proposal does not bind the hypothesis")
        instances: list[CanonicalEntityInstance] = []
        maps: list[SourceRecordCanonicalMap] = []
        metadata = source_record_metadata or {}
        entity_types = {item.canonical_entity_type_id: item for item in hypothesis.entity_types}
        for membership in identity_proposal.memberships:
            entity_type = entity_types.get(membership.canonical_entity_type_id)
            if entity_type is None:
                raise CanonicalizationError("UNRESOLVED_IDENTITY", membership.canonical_entity_type_id)
            family = membership.entity_resolution_family or entity_type.entity_resolution_family or entity_type.semantic_id
            requirement = hypothesis.entity_resolution_requirements.get(family, EntityResolutionRequirement.ER_NOT_REQUIRED)
            family_result = er_results.get(family)
            if membership.derivation_basis is IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE:
                if requirement is not EntityResolutionRequirement.ER_REQUIRED or family_result is None:
                    raise CanonicalizationError("MISSING_REQUIRED_ER", family)
                CanonicalIdentityProposalService.validate_er_membership(hypothesis, membership, family_result, family)
            elif requirement is EntityResolutionRequirement.ER_REQUIRED:
                if family_result is None:
                    raise CanonicalizationError("MISSING_REQUIRED_ER", family)
                if not set(membership.source_record_refs).issubset(CanonicalIdentityProposalService._result_population(family_result)):
                    raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", f"human override contains records outside the evaluated population for {family}")
            family_members = tuple(sorted(set(membership.source_record_refs)))
            entity_id = canonical_entity_id(entity_type.canonical_entity_type_id, family_members, hypothesis.model_version)
            linkage_refs = set()
            if family_result is not None:
                linkage_refs.update(artifact.artifact_id for artifact in family_result.artifacts)
                member_set = set(family_members)
                linkage_refs.update(edge.edge_id for edge in family_result.edges if {edge.left_record_ref, edge.right_record_ref}.issubset(member_set))
            linkage_refs.update(membership.evidence_refs)
            instance = CanonicalEntityInstance(canonical_entity_id=entity_id, canonical_entity_type_id=entity_type.canonical_entity_type_id, source_record_refs=family_members, identity_decision_ref=identity_review.review_decision_id, review_decision_ref=identity_review.review_decision_id, linkage_evidence_refs=tuple(sorted(linkage_refs | set(er_hashes.values()))), source_cluster_evidence_refs=membership.cluster_evidence_refs, canonical_model_version=hypothesis.model_version, provenance_refs=tuple(sorted(set(hypothesis.provenance_refs) | set(identity_proposal.provenance_refs))))
            instances.append(instance)
            for record_ref in family_members:
                item = metadata.get(record_ref, {})
                if not all(item.get(key) for key in ("source_id", "snapshot_id", "table_id")):
                    raise CanonicalizationError("PROVENANCE_INCOMPLETE", f"missing source metadata for {record_ref}")
                maps.append(SourceRecordCanonicalMap(
                    record_ref=record_ref,
                    source_id=item["source_id"],
                    snapshot_id=item["snapshot_id"],
                    table_id=item["table_id"],
                    canonical_entity_id=entity_id,
                    canonical_entity_type_id=entity_type.canonical_entity_type_id,
                    identity_decision_ref=identity_review.review_decision_id,
                    review_decision_ref=identity_review.review_decision_id,
                    linkage_evidence_refs=tuple(sorted(linkage_refs | set(er_hashes.values()))),
                    cluster_evidence_ref=membership.cluster_evidence_refs[0] if membership.cluster_evidence_refs else None,
                    canonical_model_version=hypothesis.model_version,
                    terminal_disposition=RecordDisposition.EMITTED_DIRECT if entity_type.kind is CanonicalEntityKind.EVENT or len(family_members) == 1 else RecordDisposition.CONSOLIDATED,
                    disposition_reason=f"accepted canonical identity proposal via {membership.derivation_basis.value}",
                    provenance_refs=tuple(sorted(set(hypothesis.provenance_refs) | set(identity_proposal.provenance_refs))),
                ))
        required_families = {entity.entity_resolution_family or entity.semantic_id for entity in hypothesis.entity_types if hypothesis.entity_resolution_requirements.get(entity.entity_resolution_family or entity.semantic_id) is EntityResolutionRequirement.ER_REQUIRED}
        proposed_families = {membership.entity_resolution_family or entity_types[membership.canonical_entity_type_id].entity_resolution_family or entity_types[membership.canonical_entity_type_id].semantic_id for membership in identity_proposal.memberships}
        if not required_families.issubset(proposed_families):
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "required identity family has no accepted membership")
        accepted_mappings = tuple(item for item in hypothesis.source_attribute_mappings if item.status is MappingStatus.ACCEPTED)
        payload = {"hypothesis": hypothesis.content_hash, "proposal": identity_proposal.content_hash, "review": identity_review.content_hash, "instances": [item.model_dump(mode="json") for item in instances], "maps": [item.model_dump(mode="json") for item in maps], "model_version": hypothesis.model_version, "survivorship": [item.model_dump(mode="json") for item in survivorship_decisions], "conflicts": [item.model_dump(mode="json") for item in conflicts]}
        model_id = canonical_model_id(payload)
        return CanonicalModel(model_id=model_id, model_version=hypothesis.model_version, hypothesis_artifact_id=hypothesis.artifact_id, finalized_at=finalized_at or utc_now(), review_decision_refs=tuple(sorted(set(hypothesis.upstream_review_decision_refs) | {identity_review.review_decision_id} | {item.review_decision_ref for item in survivorship_decisions if item.review_decision_ref})), entity_types=hypothesis.entity_types, attributes=hypothesis.attributes, relationships=hypothesis.relationships, instances=tuple(instances), source_attribute_mappings=accepted_mappings, source_record_maps=tuple(sorted(maps, key=lambda item: item.record_ref)), survivorship_decisions=tuple(sorted(survivorship_decisions, key=lambda item: item.survivorship_decision_id)), conflicts=tuple(sorted(conflicts, key=lambda item: item.conflict_id)), lineage_refs=tuple(sorted(set(lineage_refs) | set(hypothesis.provenance_refs))), record_accounting_refs=tuple(record_accounting_refs) or (stable_id("account", [item.model_dump(mode="json") for item in maps]),), provenance_refs=hypothesis.provenance_refs, unresolved_items=hypothesis.unresolved_ambiguities + hypothesis.unresolved_semantic_conflicts)

    def survivorship(
        self,
        *,
        attribute_id: str,
        candidates: Sequence[CanonicalValueReference],
        policy: SurvivorshipPolicy,
        policy_ref: str,
        review_decision_ref: str | None,
        source_priority: Sequence[str] = (),
        temporal_values: Mapping[str, datetime] | None = None,
        provenance_refs: Sequence[str],
    ) -> tuple[CanonicalSurvivorshipDecision, CanonicalConflict | None]:
        if not candidates:
            raise CanonicalizationError("SURVIVORSHIP_REVIEW_REQUIRED", "no eligible values")
        ordered = tuple(sorted(candidates, key=lambda item: item.value_ref))
        selected: CanonicalValueReference | None = None
        conflict: CanonicalConflict | None = None
        if policy is SurvivorshipPolicy.PREFERRED_SOURCE:
            for source in source_priority:
                selected = next((item for item in ordered if item.source_id == source), None)
                if selected:
                    break
            if selected is None:
                conflict = self._missing_authority(attribute_id, ordered, policy_ref, provenance_refs)
        elif policy is SurvivorshipPolicy.MOST_RECENT_VALID:
            if not temporal_values or any(item.value_ref not in temporal_values for item in ordered):
                conflict = self._conflict(attribute_id, CanonicalConflictType.TEMPORAL_CONFLICT, ordered, policy_ref, provenance_refs, "valid comparable temporal evidence is missing")
            else:
                selected = max(ordered, key=lambda item: temporal_values[item.value_ref])
        elif policy is SurvivorshipPolicy.MOST_COMPLETE:
            selected = next((item for item in ordered if item.null_state is NullSemanticState.PRESENT), None)
        elif policy is SurvivorshipPolicy.CONSENSUS:
            values = {_normalized(item.safe_value) for item in ordered if item.safe_value is not None}
            if len(values) == 1:
                selected = ordered[0]
            else:
                conflict = self._conflict(attribute_id, CanonicalConflictType.DIFFERING_SOURCE_VALUES, ordered, policy_ref, provenance_refs, "consensus is not established")
        else:
            conflict = self._conflict(attribute_id, CanonicalConflictType.MISSING_AUTHORITY_CONFLICT, ordered, policy_ref, provenance_refs, "review-required policy has no accepted survivor")
        distinct_values = {_normalized(item.safe_value) for item in ordered if item.safe_value is not None}
        if selected is not None and len(distinct_values) > 1:
            conflict = self._conflict(attribute_id, CanonicalConflictType.DIFFERING_SOURCE_VALUES, ordered, policy_ref, provenance_refs, "alternatives differ; selected by the explicitly scoped policy", resolution_state="RESOLVED_BY_POLICY", selected_value_ref=selected.value_ref, review_decision_ref=review_decision_ref)
        decision = CanonicalSurvivorshipDecision(survivorship_decision_id=stable_id("surv", {"attribute": attribute_id, "policy": policy_ref, "candidates": [item.value_ref for item in ordered], "selected": selected.value_ref if selected else None}), policy_id=policy_ref, policy_version="1", attribute_id=attribute_id, eligible_value_refs=tuple(item.value_ref for item in ordered), selected_value_ref=selected.value_ref if selected else None, losing_value_refs=tuple(item.value_ref for item in ordered if not selected or item.value_ref != selected.value_ref), authority_evidence_refs=(), normalization_refs=(), review_decision_ref=review_decision_ref, conflict_refs=(conflict.conflict_id,) if conflict else (), rationale="selected by scoped policy" if selected else "no survivor selected; review required", provenance_refs=tuple(provenance_refs))
        return decision, conflict

    @staticmethod
    def _conflict(attribute_id, kind, candidates, policy_ref, provenance_refs, rationale, *, resolution_state="REVIEW_REQUIRED", selected_value_ref=None, review_decision_ref=None):
        return CanonicalConflict(conflict_id=stable_id("ccnf", {"attribute": attribute_id, "kind": kind.value, "values": [item.value_ref for item in candidates], "selected": selected_value_ref}), conflict_type=kind, subject_entity_type_id="entity:unresolved", subject_attribute_id=attribute_id, alternative_value_refs=tuple(item.value_ref for item in candidates), evidence_refs=(), policy_ref=policy_ref, review_decision_ref=review_decision_ref, resolution_state=resolution_state, selected_value_ref=selected_value_ref, rationale=rationale, provenance_refs=tuple(provenance_refs))

    def _missing_authority(self, attribute_id, candidates, policy_ref, provenance_refs):
        return self._conflict(attribute_id, CanonicalConflictType.MISSING_AUTHORITY_CONFLICT, candidates, policy_ref, provenance_refs, "no applicable scoped source authority")
