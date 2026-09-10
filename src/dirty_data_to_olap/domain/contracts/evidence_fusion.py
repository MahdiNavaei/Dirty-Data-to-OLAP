"""Project-owned, review-only evidence fusion contracts.

This module contains aggregate observations and decision envelopes only.  It
does not read staged data, expose provider-native objects, or assign business
truth, canonical identity, or calibrated probabilities.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .source import _SourceModel, stable_digest


class FusionSubjectKind(str, Enum):
    RELATIONSHIP = "RELATIONSHIP"
    MAPPING = "MAPPING"


class EvidenceFamily(str, Enum):
    PROFILE = "PROFILE"
    QUALITY = "QUALITY"
    DEPENDENCY = "DEPENDENCY"
    SCHEMA_MATCHING = "SCHEMA_MATCHING"
    DECLARED_CONSTRAINT = "DECLARED_CONSTRAINT"
    DOMAIN_ASSERTION = "DOMAIN_ASSERTION"
    APPLIED_ML = "APPLIED_ML"
    SEMANTIC_AI = "SEMANTIC_AI"
    CANDIDATE_CONTAINER = "CANDIDATE_CONTAINER"


class EvidenceRole(str, Enum):
    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"
    DECLARED_METADATA = "DECLARED_METADATA"
    HUMAN_OR_DOMAIN_ASSERTION = "HUMAN_OR_DOMAIN_ASSERTION"
    DERIVED_INTERPRETATION = "DERIVED_INTERPRETATION"
    HYPOTHESIS_CONTAINER = "HYPOTHESIS_CONTAINER"


class EvidencePresenceState(str, Enum):
    OBSERVED = "OBSERVED"
    NOT_OBSERVED = "NOT_OBSERVED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    INCOMPLETE = "INCOMPLETE"


class EvidenceDirection(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    CONTEXT = "CONTEXT"


class EvidenceReliabilityState(str, Enum):
    FULL = "FULL"
    BOUNDED = "BOUNDED"
    SAMPLED = "SAMPLED"
    NULL_REDUCED = "NULL_REDUCED"
    TEMPORALLY_UNALIGNED = "TEMPORALLY_UNALIGNED"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


class ProducerResultState(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class FusionPolicyStatus(str, Enum):
    UNCALIBRATED = "UNCALIBRATED"
    EVALUATED_REVIEW_ONLY = "EVALUATED_REVIEW_ONLY"
    CALIBRATED_AUTOMATION_ALLOWED = "CALIBRATED_AUTOMATION_ALLOWED"


class ConfidenceKind(str, Enum):
    UNCALIBRATED_SCORE = "UNCALIBRATED_SCORE"


class ConfidenceBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    CONFLICTED = "CONFLICTED"
    INSUFFICIENT = "INSUFFICIENT"


class DecisionState(str, Enum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INCOMPLETE_REQUIRED_EVIDENCE = "INCOMPLETE_REQUIRED_EVIDENCE"
    FAILED = "FAILED"


class ConflictType(str, Enum):
    SEMANTIC_STRUCTURAL_CONFLICT = "SEMANTIC_STRUCTURAL_CONFLICT"
    DECLARED_DATA_CONFLICT = "DECLARED_DATA_CONFLICT"
    TYPE_SEMANTIC_CONFLICT = "TYPE_SEMANTIC_CONFLICT"
    SAMPLE_FULLSCAN_CONFLICT = "SAMPLE_FULLSCAN_CONFLICT"
    MULTIPLE_TARGET_AMBIGUITY = "MULTIPLE_TARGET_AMBIGUITY"


class FusionFailureKind(str, Enum):
    INPUT_INCOMPLETE = "INPUT_INCOMPLETE"
    REQUIRED_PRODUCER_FAILED = "REQUIRED_PRODUCER_FAILED"
    EVIDENCE_ID_COLLISION = "EVIDENCE_ID_COLLISION"
    SUBJECT_BINDING_ERROR = "SUBJECT_BINDING_ERROR"
    SNAPSHOT_SCOPE_MISMATCH = "SNAPSHOT_SCOPE_MISMATCH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    POLICY_INVALID = "POLICY_INVALID"
    UNSUPPORTED_SCORE_SEMANTICS = "UNSUPPORTED_SCORE_SEMANTICS"
    INCONSISTENT_LINEAGE = "INCONSISTENT_LINEAGE"
    ARTIFACT_PUBLICATION_FAILED = "ARTIFACT_PUBLICATION_FAILED"


class EvidenceFusionCompleteness(str, Enum):
    COMPLETE_REVIEW_READY = "COMPLETE_REVIEW_READY"
    INCOMPLETE_REQUIRED_EVIDENCE = "INCOMPLETE_REQUIRED_EVIDENCE"
    FAILED = "FAILED"


class EvidenceLineageReference(_SourceModel):
    evidence_id: str = Field(min_length=1)
    producer_id: str = Field(min_length=1)
    family: EvidenceFamily
    source_ids: tuple[str, ...] = ()
    snapshot_ids: tuple[str, ...] = ()
    scope_id: str = Field(min_length=1)
    observation_scope: str = Field(min_length=1)
    correlation_group: str = Field(min_length=1)
    derived_from_refs: tuple[str, ...] = ()


class NormalizedEvidenceSignal(_SourceModel):
    signal_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    producer_id: str = Field(min_length=1)
    family: EvidenceFamily
    role: EvidenceRole
    raw_metric_name: str = Field(min_length=1)
    raw_metric_value: float | None = None
    raw_metric_semantics: str = Field(min_length=1)
    normalization_method: str = Field(min_length=1)
    normalization_version: str = Field(min_length=1)
    normalized_value: float | None = Field(default=None, ge=-1, le=1)
    direction: EvidenceDirection
    presence: EvidencePresenceState = EvidencePresenceState.OBSERVED
    reliability: EvidenceReliabilityState = EvidenceReliabilityState.UNKNOWN
    scope_id: str = Field(min_length=1)
    source_ids: tuple[str, ...] = ()
    snapshot_ids: tuple[str, ...] = ()
    derived_from_refs: tuple[str, ...] = ()
    correlation_group: str = Field(min_length=1)
    score_bearing: bool = False
    contribution: float | None = None

    @model_validator(mode="after")
    def missing_is_not_zero(self) -> "NormalizedEvidenceSignal":
        if self.presence is not EvidencePresenceState.OBSERVED and self.normalized_value is not None:
            raise ValueError("non-observed evidence cannot carry a numeric normalized value")
        if not self.score_bearing and self.contribution is not None:
            raise ValueError("non-score-bearing evidence cannot carry a score contribution")
        return self


class EvidenceBundle(_SourceModel):
    bundle_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    subject_kind: FusionSubjectKind
    hypothesis: str = Field(min_length=1)
    lineage: tuple[EvidenceLineageReference, ...] = ()
    signals: tuple[NormalizedEvidenceSignal, ...] = ()
    supporting_evidence_refs: tuple[str, ...] = ()
    contradicting_evidence_refs: tuple[str, ...] = ()
    missing_evidence_refs: tuple[str, ...] = ()
    unavailable_evidence_refs: tuple[str, ...] = ()


class FusionPolicyReference(_SourceModel):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: FusionPolicyStatus
    score_semantics: str = "UNCALIBRATED_DECISION_SCORE"
    automation_enabled: bool = False
    auto_accept_enabled: bool = False
    auto_reject_enabled: bool = False
    requires_g5_for_automation: bool = True
    content_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def enforce_g5_boundary(self) -> "FusionPolicyReference":
        if self.status is FusionPolicyStatus.UNCALIBRATED and (self.automation_enabled or self.auto_accept_enabled or self.auto_reject_enabled):
            raise ValueError("uncalibrated fusion policy cannot enable automation")
        if self.status is FusionPolicyStatus.UNCALIBRATED and self.score_semantics not in {"UNCALIBRATED_DECISION_SCORE", "UNCALIBRATED_RANKING_SCORE"}:
            raise ValueError("uncalibrated policy requires explicit uncalibrated score semantics")
        return self


class FusionScore(_SourceModel):
    value: float | None = Field(default=None, ge=-1, le=1)
    confidence_kind: ConfidenceKind = ConfidenceKind.UNCALIBRATED_SCORE
    score_semantics: str = "UNCALIBRATED_DECISION_SCORE"
    eligible_weight: float = Field(ge=0)
    observed_weight: float = Field(ge=0)
    evidence_coverage: float = Field(ge=0, le=1)
    sufficient: bool
    contributions: Mapping[str, float] = Field(default_factory=dict)


class Conflict(_SourceModel):
    conflict_id: str = Field(min_length=1)
    conflict_type: ConflictType
    subject_id: str = Field(min_length=1)
    supporting_evidence_refs: tuple[str, ...] = ()
    contradicting_evidence_refs: tuple[str, ...] = ()
    severity: str = "HIGH"
    explanation: str = Field(min_length=1)
    policy_rule: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    provenance: str = Field(min_length=1)


class DecisionExplanation(_SourceModel):
    supports: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    reconstruction: tuple[str, ...] = ()


class RelationshipDecision(_SourceModel):
    decision_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    from_table: str = Field(min_length=1)
    from_columns: tuple[str, ...] = Field(min_length=1)
    to_table: str = Field(min_length=1)
    to_columns: tuple[str, ...] = Field(min_length=1)
    proposed_cardinality: str = Field(min_length=1)
    score: FusionScore
    confidence_band: ConfidenceBand
    decision_state: DecisionState = DecisionState.REVIEW_REQUIRED
    policy: FusionPolicyReference
    supporting_signal_refs: tuple[str, ...] = ()
    contradicting_signal_refs: tuple[str, ...] = ()
    missing_evidence_refs: tuple[str, ...] = ()
    unavailable_evidence_refs: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()
    explanation: DecisionExplanation
    input_evidence_fingerprint: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def never_accept_before_g5(self) -> "RelationshipDecision":
        if self.decision_state is not DecisionState.REVIEW_REQUIRED and self.decision_state is not DecisionState.INCOMPLETE_REQUIRED_EVIDENCE:
            raise ValueError("relationship decisions are review-only in Step17")
        return self


class SemanticMappingDecision(_SourceModel):
    decision_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_column_id: str = Field(min_length=1)
    target_source_id: str = Field(min_length=1)
    target_column_id: str = Field(min_length=1)
    score: FusionScore
    confidence_band: ConfidenceBand
    decision_state: DecisionState = DecisionState.REVIEW_REQUIRED
    policy: FusionPolicyReference
    supporting_signal_refs: tuple[str, ...] = ()
    contradicting_signal_refs: tuple[str, ...] = ()
    missing_evidence_refs: tuple[str, ...] = ()
    unavailable_evidence_refs: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()
    explanation: DecisionExplanation
    input_evidence_fingerprint: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def source_to_source_only(self) -> "SemanticMappingDecision":
        if self.source_id == self.target_source_id and self.source_column_id == self.target_column_id:
            raise ValueError("semantic mappings must be cross-source hypotheses")
        if self.decision_state is not DecisionState.REVIEW_REQUIRED:
            raise ValueError("semantic mappings are review-only in Step17")
        return self


class ProducerEvidenceStatus(_SourceModel):
    producer_id: str = Field(min_length=1)
    family: EvidenceFamily
    state: ProducerResultState
    result_id: str = Field(min_length=1)
    detail: str = ""
    input_fingerprint: str | None = None


class DeclaredConstraintInput(_SourceModel):
    constraint_id: str = Field(min_length=1)
    constraint_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    from_table: str = Field(min_length=1)
    from_columns: tuple[str, ...] = Field(min_length=1)
    to_table: str = Field(min_length=1)
    to_columns: tuple[str, ...] = Field(min_length=1)
    scope_id: str = Field(min_length=1)


class DomainAssertion(_SourceModel):
    assertion_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: str = Field(min_length=1)
    source_ids: tuple[str, ...] = ()
    snapshot_ids: tuple[str, ...] = ()
    scope_id: str = Field(min_length=1)
    asserted_by: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class FusionEvidenceItem(_SourceModel):
    """Explicit aggregate input used to translate prior-stage contracts."""

    evidence_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    producer_id: str = Field(min_length=1)
    family: EvidenceFamily
    role: EvidenceRole = EvidenceRole.DIRECT_OBSERVATION
    metric_name: str = Field(min_length=1)
    metric_value: float | None = None
    metric_semantics: str = Field(min_length=1)
    direction: EvidenceDirection = EvidenceDirection.SUPPORTS
    presence: EvidencePresenceState = EvidencePresenceState.OBSERVED
    scope_id: str = Field(min_length=1)
    observation_scope: EvidenceReliabilityState = EvidenceReliabilityState.UNKNOWN
    source_ids: tuple[str, ...] = ()
    snapshot_ids: tuple[str, ...] = ()
    derived_from_refs: tuple[str, ...] = ()
    correlation_group: str = Field(min_length=1)
    score_bearing: bool = True
    qualitative_text: str | None = None


class EvidenceFusionRequest(_SourceModel):
    request_id: str = Field(min_length=1)
    execution_context_id: str = Field(min_length=1)
    cross_source_mapping_scope: bool = False
    relationship_candidate_ids: tuple[str, ...] = ()
    mapping_candidate_ids: tuple[str, ...] = ()
    policy: FusionPolicyReference
    expected_producer_result_ids: Mapping[str, str] = Field(default_factory=dict)
    max_relationship_candidates: int = Field(default=2_000, ge=1, le=100_000)
    max_mapping_candidates: int = Field(default=2_000, ge=1, le=100_000)
    max_evidence_per_subject: int = Field(default=128, ge=1, le=10_000)
    max_conflicts_per_subject: int = Field(default=32, ge=1, le=1_000)
    max_total_decisions: int = Field(default=4_000, ge=1, le=200_000)


class EvidenceFusionInputs(_SourceModel):
    producer_statuses: tuple[ProducerEvidenceStatus, ...] = ()
    evidence_items: tuple[FusionEvidenceItem, ...] = ()
    relationship_candidates: tuple[Any, ...] = ()
    mapping_candidates: tuple[Any, ...] = ()
    declared_constraints: tuple[Any, ...] = ()
    domain_assertions: tuple[DomainAssertion, ...] = ()


class FusionFailure(_SourceModel):
    failure_id: str = Field(min_length=1)
    kind: FusionFailureKind
    detail: str = Field(min_length=1)
    subject_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class FusionArtifactReference(_SourceModel):
    artifact_id: str
    artifact_type: str
    location: str
    content_hash: str
    publication_state: str = "COMPLETE"


class EvidenceFusionResult(_SourceModel):
    request: EvidenceFusionRequest
    relationships: tuple[RelationshipDecision, ...] = ()
    mappings: tuple[SemanticMappingDecision, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    bundles: tuple[EvidenceBundle, ...] = ()
    signals: tuple[NormalizedEvidenceSignal, ...] = ()
    forwarded_repair_proposal_refs: tuple[str, ...] = ()
    failures: tuple[FusionFailure, ...] = ()
    completeness: EvidenceFusionCompleteness
    policy: FusionPolicyReference
    artifacts: tuple[FusionArtifactReference, ...] = ()


def fusion_input_fingerprint(subject_id: str, signals: tuple[NormalizedEvidenceSignal, ...], policy: FusionPolicyReference) -> str:
    return stable_digest({"subject_id": subject_id, "signals": [item.model_dump(mode="json") for item in signals], "policy": policy.model_dump(mode="json")})


def fusion_decision_id(subject_id: str, input_fingerprint: str, policy: FusionPolicyReference) -> str:
    return "fusion_decision_" + stable_digest({"subject_id": subject_id, "input": input_fingerprint, "policy": policy.policy_id, "version": policy.version})[:32]


def fusion_signal_id(evidence_id: str, subject_id: str, metric_name: str) -> str:
    return "fusion_signal_" + stable_digest((evidence_id, subject_id, metric_name))[:32]


def fusion_conflict_id(subject_id: str, conflict_type: ConflictType, refs: tuple[str, ...]) -> str:
    return "fusion_conflict_" + stable_digest((subject_id, conflict_type.value, tuple(sorted(refs))))[:32]
