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
    CanonicalModel,
    CanonicalModelHypothesis,
    CanonicalRelationship,
    CanonicalSourceTable,
    CanonicalSurvivorshipDecision,
    CanonicalValueReference,
    EntityResolutionRequirement,
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
)
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult, EntityResolutionStatus
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
        spec_by_family = {spec.entity_family: spec for spec in entity_resolution_specs}
        missing = [family for family, requirement in requirements.items() if requirement is EntityResolutionRequirement.ER_REQUIRED and family not in spec_by_family]
        if missing:
            raise CanonicalizationError("MISSING_REQUIRED_ER", ",".join(sorted(missing)))
        payload = {
            "run_id": run_id,
            "execution_context_id": execution_context_id,
            "model_version": model_version,
            "upstream_decision_refs": sorted({ref for item in relationships for ref in (item.upstream_decision_ref,)} | {ref for item in source_attribute_mappings for ref in (item.upstream_decision_ref,)} | {item.decision_id for item in relationship_decisions} | {item.decision_id for item in semantic_mapping_decisions}),
            "upstream_review_decision_refs": sorted(review.review_decision_id for review in evidence_reviews),
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


class CanonicalFinalizationService:
    def __init__(self, review_policy: ReviewPolicyService | None = None) -> None:
        self.review_policy = review_policy or ReviewPolicyService()

    def identity_context(self, hypothesis: CanonicalModelHypothesis, er_hashes: Mapping[str, str] | None = None) -> ReviewCompatibilityContext:
        fingerprints = dict(hypothesis.source_schema_fingerprints)
        fingerprints.update({f"er:{family}": digest for family, digest in (er_hashes or {}).items()})
        return ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY,
            subject_stage="CANONICAL_HYPOTHESES",
            subject_artifact_id=hypothesis.artifact_id,
            subject_content_hash=hypothesis.content_hash,
            subject_schema_version=hypothesis.schema_version,
            model_version=hypothesis.model_version,
            source_schema_fingerprints=fingerprints,
            policy_version="canonical-identity-v1",
            domain_assertion_refs=hypothesis.domain_assertion_refs,
            subject_semantic_id=stable_id("csem", sorted(item.semantic_id for item in hypothesis.entity_types)),
            applicability_fingerprint=stable_digest({"hypothesis": hypothesis.content_hash, "er": fingerprints}),
        )

    def finalize(
        self,
        *,
        hypothesis: CanonicalModelHypothesis,
        identity_review: ReviewDecision,
        er_results: Mapping[str, EntityResolutionResult] | None = None,
        memberships: Mapping[str, Sequence[str]] | None = None,
        source_record_metadata: Mapping[str, Mapping[str, str]] | None = None,
        survivorship_decisions: Sequence[CanonicalSurvivorshipDecision] = (),
        conflicts: Sequence[CanonicalConflict] = (),
        lineage_refs: Sequence[str] = (),
        record_accounting_refs: Sequence[str] = (),
        finalized_at: datetime | None = None,
    ) -> CanonicalModel:
        er_results = dict(er_results or {})
        er_hashes = {family: stable_digest(result.model_dump(mode="json")) for family, result in er_results.items()}
        try:
            self.review_policy.require_compatible(identity_review, self.identity_context(hypothesis, er_hashes))
        except ReviewCompatibilityError as error:
            raise CanonicalizationError("STALE_REVIEW", str(error)) from error
        for family, requirement in hypothesis.entity_resolution_requirements.items():
            if requirement is EntityResolutionRequirement.ER_REQUIRED:
                result = er_results.get(family)
                if result is None:
                    raise CanonicalizationError("MISSING_REQUIRED_ER", family)
                if result.status is not EntityResolutionStatus.COMPLETE:
                    raise CanonicalizationError("INCOMPATIBLE_ER_RESULT", family)
        if any(requirement is EntityResolutionRequirement.ER_REQUIRED for requirement in hypothesis.entity_resolution_requirements.values()) and identity_review.decision is ReviewDecisionStatus.SKIPPED:
            raise CanonicalizationError("INCOMPLETE_REVIEW", "required ER identity cannot be skipped")
        memberships = {key: tuple(sorted(set(value))) for key, value in (memberships or {}).items()}
        instances: list[CanonicalEntityInstance] = []
        maps: list[SourceRecordCanonicalMap] = []
        metadata = source_record_metadata or {}
        for entity_type in sorted(hypothesis.entity_types, key=lambda item: item.canonical_entity_type_id):
            if entity_type.kind is CanonicalEntityKind.EVENT:
                continue
            er_family = entity_type.entity_resolution_family or entity_type.semantic_id
            family_members = memberships.get(entity_type.semantic_id) or memberships.get(er_family, ())
            if not family_members:
                continue
            entity_id = canonical_entity_id(entity_type.canonical_entity_type_id, family_members, hypothesis.model_version)
            family_result = er_results.get(er_family)
            linkage_refs = set()
            if family_result is not None:
                linkage_refs.update(artifact.artifact_id for artifact in family_result.artifacts)
                member_set = set(family_members)
                linkage_refs.update(edge.edge_id for edge in family_result.edges if {edge.left_record_ref, edge.right_record_ref}.issubset(member_set))
            instance = CanonicalEntityInstance(canonical_entity_id=entity_id, canonical_entity_type_id=entity_type.canonical_entity_type_id, source_record_refs=family_members, identity_decision_ref=identity_review.review_decision_id, review_decision_ref=identity_review.review_decision_id, linkage_evidence_refs=tuple(sorted(linkage_refs | set(er_hashes.values()))), source_cluster_evidence_refs=(), canonical_model_version=hypothesis.model_version, provenance_refs=hypothesis.provenance_refs)
            instances.append(instance)
            for record_ref in family_members:
                item = metadata.get(record_ref, {})
                if not all(item.get(key) for key in ("source_id", "snapshot_id", "table_id")):
                    raise CanonicalizationError("PROVENANCE_INCOMPLETE", f"missing source metadata for {record_ref}")
                maps.append(SourceRecordCanonicalMap(record_ref=record_ref, source_id=item["source_id"], snapshot_id=item["snapshot_id"], table_id=item["table_id"], canonical_entity_id=entity_id, canonical_entity_type_id=entity_type.canonical_entity_type_id, identity_decision_ref=identity_review.review_decision_id, review_decision_ref=identity_review.review_decision_id, linkage_evidence_refs=tuple(sorted(linkage_refs | set(er_hashes.values()))), cluster_evidence_ref=None, canonical_model_version=hypothesis.model_version, terminal_disposition=RecordDisposition.CONSOLIDATED if len(family_members) > 1 else RecordDisposition.EMITTED_DIRECT, disposition_reason="accepted canonical identity review", provenance_refs=hypothesis.provenance_refs))
        if any(hypothesis.entity_resolution_requirements.get(entity.entity_resolution_family or entity.semantic_id) is EntityResolutionRequirement.ER_REQUIRED and not (memberships.get(entity.semantic_id) or memberships.get(entity.entity_resolution_family or entity.semantic_id)) for entity in hypothesis.entity_types):
            raise CanonicalizationError("UNRESOLVED_IDENTITY", "required identity family has no accepted membership")
        accepted_mappings = tuple(item for item in hypothesis.source_attribute_mappings if item.status is MappingStatus.ACCEPTED)
        payload = {"hypothesis": hypothesis.content_hash, "review": identity_review.content_hash, "instances": [item.model_dump(mode="json") for item in instances], "maps": [item.model_dump(mode="json") for item in maps], "model_version": hypothesis.model_version, "survivorship": [item.model_dump(mode="json") for item in survivorship_decisions], "conflicts": [item.model_dump(mode="json") for item in conflicts]}
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
