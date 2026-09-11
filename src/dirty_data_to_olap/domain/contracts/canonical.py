"""Project-owned, review-gated canonicalization contracts.

These contracts deliberately stop before analytical facts, dimensions, grain,
measures, and warehouse keys.  They contain references to source evidence and
decisions, never provider-native objects or evaluation truth.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .entity_resolution import EntityResolutionSpec
from .source import _SourceModel, stable_digest, stable_id, utc_now


class ReviewCheckpoint(str, Enum):
    REVIEW_EVIDENCE_DECISIONS = "REVIEW_EVIDENCE_DECISIONS"
    REVIEW_CANONICAL_IDENTITY = "REVIEW_CANONICAL_IDENTITY"
    REVIEW_ANALYTICAL_PLAN = "REVIEW_ANALYTICAL_PLAN"
    REVIEW_MATERIALIZATION_PLAN = "REVIEW_MATERIALIZATION_PLAN"


class ReviewDecisionStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"
    INVALIDATED = "INVALIDATED"


class IdentityDerivationBasis(str, Enum):
    ER_AUTHORIZED_LINKAGE = "ER_AUTHORIZED_LINKAGE"
    HUMAN_DOMAIN_REVIEW = "HUMAN_DOMAIN_REVIEW"
    SOURCE_LOCAL_EVENT_IDENTITY = "SOURCE_LOCAL_EVENT_IDENTITY"


class CanonicalEntityKind(str, Enum):
    IDENTITY = "IDENTITY"
    EVENT = "EVENT"


class EntityResolutionRequirement(str, Enum):
    ER_REQUIRED = "ER_REQUIRED"
    ER_NOT_REQUIRED = "ER_NOT_REQUIRED"


class MappingStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class NullSemanticState(str, Enum):
    SOURCE_NULL_MISSING = "SOURCE_NULL_MISSING"
    NOT_CAPTURED = "NOT_CAPTURED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_UNPARSEABLE = "INVALID_UNPARSEABLE"
    WITHHELD_REDACTED = "WITHHELD_REDACTED"
    QUARANTINED_UNACCEPTED = "QUARANTINED_UNACCEPTED"
    PRESENT = "PRESENT"


class SurvivorshipPolicy(str, Enum):
    PREFERRED_SOURCE = "PREFERRED_SOURCE"
    MOST_RECENT_VALID = "MOST_RECENT_VALID"
    MOST_COMPLETE = "MOST_COMPLETE"
    CONSENSUS = "CONSENSUS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CanonicalConflictType(str, Enum):
    DIFFERING_SOURCE_VALUES = "DIFFERING_SOURCE_VALUES"
    AUTHORITY_CONFLICT = "AUTHORITY_CONFLICT"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    NORMALIZATION_CONFLICT = "NORMALIZATION_CONFLICT"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    SEMANTIC_MAPPING_CONFLICT = "SEMANTIC_MAPPING_CONFLICT"
    MISSING_AUTHORITY_CONFLICT = "MISSING_AUTHORITY_CONFLICT"


class CanonicalFailureKind(str, Enum):
    INCOMPLETE_REVIEW = "INCOMPLETE_REVIEW"
    STALE_REVIEW = "STALE_REVIEW"
    MISSING_REQUIRED_ER = "MISSING_REQUIRED_ER"
    INCOMPATIBLE_ER_RESULT = "INCOMPATIBLE_ER_RESULT"
    UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"
    SURVIVORSHIP_REVIEW_REQUIRED = "SURVIVORSHIP_REVIEW_REQUIRED"
    INVALID_SOURCE_MAPPING = "INVALID_SOURCE_MAPPING"
    PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"


class ReviewSkipAuthorization(_SourceModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    checkpoint: ReviewCheckpoint
    scope: str = Field(min_length=1)
    applicability_fingerprint: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ReviewCompatibilityContext(_SourceModel):
    review_checkpoint_id: ReviewCheckpoint
    subject_stage: str = Field(min_length=1)
    subject_artifact_id: str = Field(min_length=1)
    subject_content_hash: str = Field(min_length=1)
    subject_schema_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    policy_version: str = Field(min_length=1)
    domain_assertion_refs: tuple[str, ...] = ()
    subject_semantic_id: str = Field(min_length=1)
    applicability_fingerprint: str = Field(min_length=1)
    skip_authorization: ReviewSkipAuthorization | None = None


class ReviewDecision(_SourceModel):
    review_decision_id: str = Field(min_length=1)
    review_checkpoint_id: ReviewCheckpoint
    subject_stage: str = Field(min_length=1)
    subject_artifact_id: str = Field(min_length=1)
    subject_content_hash: str = Field(min_length=1)
    subject_schema_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    policy_version: str = Field(min_length=1)
    domain_assertion_refs: tuple[str, ...] = ()
    subject_semantic_id: str = Field(min_length=1)
    applicability_fingerprint: str = Field(min_length=1)
    decision: ReviewDecisionStatus
    reviewed_at: datetime
    actor: str = Field(min_length=1)
    actor_source: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    superseded: bool = False
    superseded_by: str | None = None
    invalidation_reason: str | None = None
    skip_authorization: ReviewSkipAuthorization | None = None

    @model_validator(mode="after")
    def invalidation_is_explicit(self) -> "ReviewDecision":
        if self.superseded and not self.superseded_by:
            raise ValueError("superseded review decisions require superseded_by")
        if self.decision is ReviewDecisionStatus.INVALIDATED and not self.invalidation_reason:
            raise ValueError("invalidated review decisions require a reason")
        if self.decision is ReviewDecisionStatus.SKIPPED and self.skip_authorization is None:
            raise ValueError("skipped review decisions require explicit skip authorization")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"reviewed_at"}))

    def compatibility_errors(self, context: ReviewCompatibilityContext) -> tuple[str, ...]:
        errors: list[str] = []
        if self.review_checkpoint_id is not context.review_checkpoint_id:
            errors.append("WRONG_REVIEW_CHECKPOINT")
        for name in ("subject_stage", "subject_artifact_id", "subject_content_hash", "subject_schema_version", "model_version", "policy_version", "subject_semantic_id", "applicability_fingerprint"):
            if getattr(self, name) != getattr(context, name):
                errors.append("MISMATCH_" + name.upper())
        if dict(self.source_schema_fingerprints) != dict(context.source_schema_fingerprints):
            errors.append("MISMATCH_SOURCE_SCHEMA_FINGERPRINTS")
        if tuple(self.domain_assertion_refs) != tuple(context.domain_assertion_refs):
            errors.append("MISMATCH_DOMAIN_ASSERTION_SCOPE")
        if self.decision not in {ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED}:
            errors.append("DECISION_NOT_SATISFYING_GUARD")
        if self.decision is ReviewDecisionStatus.SKIPPED:
            if context.skip_authorization is None or self.skip_authorization != context.skip_authorization:
                errors.append("SKIP_POLICY_NOT_AUTHORIZED")
        if self.superseded or self.invalidation_reason:
            errors.append("DECISION_INVALIDATED_OR_SUPERSEDED")
        return tuple(errors)

    def is_compatible(self, context: ReviewCompatibilityContext) -> bool:
        return not self.compatibility_errors(context)


class CanonicalSourceTable(_SourceModel):
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(min_length=1)


class CanonicalValueReference(_SourceModel):
    value_ref: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = ()
    source_value_digest: str | None = None
    safe_value: str | None = None
    null_state: NullSemanticState = NullSemanticState.PRESENT
    source_id: str | None = None
    snapshot_id: str | None = None

    @model_validator(mode="after")
    def withheld_is_safe(self) -> "CanonicalValueReference":
        if self.null_state is NullSemanticState.WITHHELD_REDACTED and self.safe_value is not None:
            raise ValueError("withheld values must not be copied into canonical artifacts")
        return self


class SourceAttributeMapping(_SourceModel):
    mapping_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    source_schema_fingerprint: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    column_id: str = Field(min_length=1)
    canonical_entity_type_id: str = Field(min_length=1)
    canonical_attribute_id: str = Field(min_length=1)
    normalization_ref: str = Field(min_length=1)
    upstream_decision_ref: str = Field(min_length=1)
    review_decision_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    status: MappingStatus = MappingStatus.REVIEW_REQUIRED
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def accepted_mapping_has_review(self) -> "SourceAttributeMapping":
        if self.status is MappingStatus.ACCEPTED and not self.review_decision_ref:
            raise ValueError("accepted source mappings require a review decision")
        return self


class CanonicalIdentityFieldHypothesis(_SourceModel):
    identity_field_id: str = Field(min_length=1)
    canonical_attribute_id: str = Field(min_length=1)
    source_mapping_refs: tuple[str, ...] = Field(min_length=1)
    normalization_ref: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class CanonicalEntityType(_SourceModel):
    canonical_entity_type_id: str = Field(min_length=1)
    semantic_id: str = Field(min_length=1)
    business_name: str = Field(min_length=1)
    kind: CanonicalEntityKind
    entity_resolution_family: str | None = None
    identity_strategy: str = Field(min_length=1)
    identity_attribute_ids: tuple[str, ...] = ()
    source_table_refs: tuple[CanonicalSourceTable, ...] = ()
    canonical_attribute_ids: tuple[str, ...] = ()
    relationship_refs: tuple[str, ...] = ()
    domain_assertion_refs: tuple[str, ...] = ()
    review_state: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class CanonicalIdentityMembership(_SourceModel):
    membership_group_id: str = Field(min_length=1)
    canonical_entity_type_id: str = Field(min_length=1)
    entity_resolution_family: str | None = None
    source_record_refs: tuple[str, ...] = Field(min_length=1)
    derivation_basis: IdentityDerivationBasis
    er_result_refs: tuple[str, ...] = ()
    er_spec_refs: tuple[str, ...] = ()
    authorized_edge_refs: tuple[str, ...] = ()
    cluster_evidence_refs: tuple[str, ...] = ()
    actor: str | None = None
    actor_source: str | None = None
    domain_assertion_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    policy_refs: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def basis_requirements(self) -> "CanonicalIdentityMembership":
        if self.derivation_basis is IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE and not self.authorized_edge_refs:
            raise ValueError("ER-derived membership requires authorized edge refs")
        if self.derivation_basis is IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW and (not self.actor or not self.actor_source or not self.domain_assertion_refs):
            raise ValueError("human/domain membership requires actor, source and domain assertion refs")
        if self.derivation_basis is IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY and self.authorized_edge_refs:
            raise ValueError("source-local event identity cannot contain ER edge refs")
        return self


class CanonicalIdentityProposal(_SourceModel):
    proposal_id: str = Field(pattern=r"^cip_[a-f0-9]{32}$")
    hypothesis_artifact_id: str = Field(min_length=1)
    canonical_entity_type_ids: tuple[str, ...] = Field(min_length=1)
    entity_resolution_requirements: Mapping[str, EntityResolutionRequirement]
    memberships: tuple[CanonicalIdentityMembership, ...] = Field(min_length=1)
    er_result_refs: tuple[str, ...] = ()
    er_spec_refs: tuple[str, ...] = ()
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    policy_refs: tuple[str, ...] = Field(min_length=1)
    unresolved_identity_cases: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"created_at"}))


class CanonicalRelationship(_SourceModel):
    relationship_id: str = Field(min_length=1)
    from_entity_type_id: str = Field(min_length=1)
    to_entity_type_id: str = Field(min_length=1)
    cardinality: str = Field(min_length=1)
    upstream_decision_ref: str = Field(min_length=1)
    review_decision_ref: str = Field(min_length=1)
    conflict_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class CanonicalAttribute(_SourceModel):
    canonical_attribute_id: str = Field(min_length=1)
    canonical_entity_type_id: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    normalized_logical_type: str = Field(min_length=1)
    candidate_mapping_refs: tuple[str, ...] = ()
    accepted_mapping_refs: tuple[str, ...] = ()
    normalization_ref: str = Field(min_length=1)
    value_refs: tuple[CanonicalValueReference, ...] = ()
    null_semantic_states: tuple[NullSemanticState, ...] = ()
    authority_policy_ref: str = Field(min_length=1)
    survivorship_decision_ref: str | None = None
    review_decision_ref: str | None = None
    conflict_refs: tuple[str, ...] = ()
    lineage_refs: tuple[str, ...] = Field(min_length=1)


class CanonicalSurvivorshipDecision(_SourceModel):
    survivorship_decision_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    attribute_id: str = Field(min_length=1)
    eligible_value_refs: tuple[str, ...] = Field(min_length=1)
    selected_value_ref: str | None = None
    losing_value_refs: tuple[str, ...] = ()
    authority_evidence_refs: tuple[str, ...] = ()
    normalization_refs: tuple[str, ...] = ()
    review_decision_ref: str | None = None
    conflict_refs: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def retain_losers(self) -> "CanonicalSurvivorshipDecision":
        if self.selected_value_ref and self.selected_value_ref in self.losing_value_refs:
            raise ValueError("selected survivor cannot also be a losing alternative")
        return self


class CanonicalConflict(_SourceModel):
    conflict_id: str = Field(min_length=1)
    conflict_type: CanonicalConflictType
    subject_entity_type_id: str = Field(min_length=1)
    subject_attribute_id: str | None = None
    alternative_value_refs: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    policy_ref: str = Field(min_length=1)
    review_decision_ref: str | None = None
    resolution_state: str = Field(min_length=1)
    selected_value_ref: str | None = None
    rationale: str = Field(min_length=1)
    effective_at: datetime | None = None
    superseded_by: str | None = None
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class CanonicalEntityInstance(_SourceModel):
    canonical_entity_id: str = Field(pattern=r"^cent_[a-f0-9]{32}$")
    canonical_entity_type_id: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)
    identity_decision_ref: str = Field(min_length=1)
    review_decision_ref: str = Field(min_length=1)
    linkage_evidence_refs: tuple[str, ...] = ()
    source_cluster_evidence_refs: tuple[str, ...] = ()
    canonical_model_version: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class RecordDisposition(str, Enum):
    EMITTED_DIRECT = "EMITTED_DIRECT"
    CONSOLIDATED = "CONSOLIDATED"
    AGGREGATED = "AGGREGATED"
    FILTERED_EXPLICIT = "FILTERED_EXPLICIT"
    QUARANTINED = "QUARANTINED"
    UNRESOLVED = "UNRESOLVED"


class SourceRecordCanonicalMap(_SourceModel):
    record_ref: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    canonical_entity_id: str = Field(pattern=r"^cent_[a-f0-9]{32}$")
    canonical_entity_type_id: str = Field(min_length=1)
    identity_decision_ref: str = Field(min_length=1)
    review_decision_ref: str = Field(min_length=1)
    linkage_evidence_refs: tuple[str, ...] = ()
    cluster_evidence_ref: str | None = None
    canonical_model_version: str = Field(min_length=1)
    terminal_disposition: RecordDisposition = RecordDisposition.CONSOLIDATED
    disposition_reason: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class CanonicalModelHypothesis(_SourceModel):
    artifact_id: str = Field(pattern=r"^chyp_[a-f0-9]{32}$")
    run_id: str = Field(min_length=1)
    execution_context_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    upstream_decision_refs: tuple[str, ...] = ()
    upstream_review_decision_refs: tuple[str, ...] = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)
    snapshot_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    entity_types: tuple[CanonicalEntityType, ...] = Field(min_length=1)
    attributes: tuple[CanonicalAttribute, ...] = ()
    relationships: tuple[CanonicalRelationship, ...] = ()
    source_attribute_mappings: tuple[SourceAttributeMapping, ...] = ()
    identity_field_hypotheses: tuple[CanonicalIdentityFieldHypothesis, ...] = ()
    entity_resolution_requirements: Mapping[str, EntityResolutionRequirement]
    entity_resolution_specs: tuple[EntityResolutionSpec, ...] = ()
    normalization_refs: tuple[str, ...] = ()
    unresolved_ambiguities: tuple[str, ...] = ()
    unresolved_semantic_conflicts: tuple[str, ...] = ()
    source_authority_policy_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"created_at"}))


class CanonicalModel(_SourceModel):
    model_id: str = Field(pattern=r"^cmodel_[a-f0-9]{32}$")
    model_version: str = Field(min_length=1)
    hypothesis_artifact_id: str = Field(min_length=1)
    finalized_at: datetime
    review_decision_refs: tuple[str, ...] = Field(min_length=1)
    entity_types: tuple[CanonicalEntityType, ...] = Field(min_length=1)
    attributes: tuple[CanonicalAttribute, ...] = ()
    relationships: tuple[CanonicalRelationship, ...] = ()
    instances: tuple[CanonicalEntityInstance, ...] = ()
    source_attribute_mappings: tuple[SourceAttributeMapping, ...] = ()
    source_record_maps: tuple[SourceRecordCanonicalMap, ...] = ()
    survivorship_decisions: tuple[CanonicalSurvivorshipDecision, ...] = ()
    conflicts: tuple[CanonicalConflict, ...] = ()
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    record_accounting_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    unresolved_items: tuple[str, ...] = ()

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"finalized_at"}))


def hypothesis_id(payload: Mapping[str, Any]) -> str:
    return stable_id("chyp", payload)


def canonical_model_id(payload: Mapping[str, Any]) -> str:
    return stable_id("cmodel", payload)


def identity_proposal_id(payload: Mapping[str, Any]) -> str:
    return stable_id("cip", payload)


def canonical_entity_id(entity_type_id: str, source_record_refs: tuple[str, ...], model_version: str) -> str:
    return stable_id("cent", {"entity_type_id": entity_type_id, "source_record_refs": sorted(source_record_refs), "model_version": model_version})
