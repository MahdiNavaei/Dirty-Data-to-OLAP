"""Project-owned bounded entity-resolution evidence contracts.

The official Splink runtime is deliberately absent from this module.  Native
Splink relations, model objects and raw identity values stop at the adapter
boundary; these models contain only reproducible, linkable evidence metadata.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .source import AdapterReference, BatchReference, SourceRecordReference, _SourceModel, stable_digest


class EntityResolutionMode(str, Enum):
    LINK_ONLY = "LINK_ONLY"
    DEDUPE_ONLY = "DEDUPE_ONLY"
    LINK_AND_DEDUPE = "LINK_AND_DEDUPE"


class EntityResolutionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class EntityResolutionFailureKind(str, Enum):
    INPUT_INVALID = "INPUT_INVALID"
    AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
    STAGED_INPUT_INTEGRITY_FAILED = "STAGED_INPUT_INTEGRITY_FAILED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    TRAINING_FAILED = "TRAINING_FAILED"
    PREDICTION_FAILED = "PREDICTION_FAILED"
    CLUSTERING_FAILED = "CLUSTERING_FAILED"
    ARTIFACT_FAILED = "ARTIFACT_FAILED"
    UNSUPPORTED_MODE = "UNSUPPORTED_MODE"
    SAFETY_GUARD = "SAFETY_GUARD"


class EntityResolutionCapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    NOT_REQUESTED = "NOT_REQUESTED"


class EntityResolutionNormalizationRule(_SourceModel):
    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    operations: tuple[str, ...] = ("NFKC", "CASEFOLD", "TRIM_COLLAPSE_WHITESPACE")
    applies_to: tuple[str, ...] = Field(min_length=1)
    country_context: str | None = None
    preserve_original: bool = True

    @model_validator(mode="after")
    def conservative(self) -> "EntityResolutionNormalizationRule":
        forbidden = {"TRANSLITERATE", "PHONETIC_GUESS", "COUNTRY_GUESS"}
        if forbidden.intersection(self.operations):
            raise ValueError("entity normalization cannot transliterate or guess country/meaning")
        if not self.preserve_original:
            raise ValueError("normalization must preserve the source value outside the adapter")
        return self


class IdentityFieldSpecification(_SourceModel):
    field_id: str = Field(min_length=1)
    source_id: str
    snapshot_id: str
    table_id: str
    column_id: str
    physical_name: str = Field(min_length=1)
    semantic_role: str = Field(min_length=1)
    normalization_rule_id: str
    nullable: bool = True


class ERBlockingRule(_SourceModel):
    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    field_ids: tuple[str, ...] = Field(min_length=1)
    sql_expression: str = Field(min_length=1)
    purpose: str = "high_recall_bounded_candidate_generation"
    max_pairs: int = Field(default=100_000, ge=1)

    @model_validator(mode="after")
    def no_cartesian(self) -> "ERBlockingRule":
        if "CARTESIAN" in self.sql_expression.upper() or self.sql_expression.strip() in {"1=1", "TRUE"}:
            raise ValueError("unbounded Cartesian blocking is forbidden")
        return self


class ERComparisonSpecification(_SourceModel):
    comparison_id: str = Field(min_length=1)
    field_id: str
    method: str = "exact"
    thresholds: tuple[float, ...] = ()
    term_frequency_adjustment: bool = False
    null_level: str = "null_is_not_agreement"
    weight_semantics: str = "match_weight_is_log_bayes_factor_not_probability"


class ERTrainingPolicy(_SourceModel):
    policy_id: str = "er-training-v1"
    u_method: str = "random_sampling"
    m_method: str = "expectation_maximisation"
    random_seed: int = 0
    max_u_pairs: int = Field(default=100_000, ge=1)
    max_em_iterations: int = Field(default=20, ge=1, le=1_000)
    em_blocking_rule_ids: tuple[str, ...] = Field(min_length=1)
    require_provenance: bool = True


class ERThresholdPolicy(_SourceModel):
    policy_id: str = "er-threshold-v1"
    match_probability_threshold: float = Field(default=0.95, gt=0, le=1)
    review_probability_threshold: float = Field(default=0.80, gt=0, le=1)
    match_weight_threshold: float | None = None
    require_independent_evidence: bool = True

    @model_validator(mode="after")
    def ordered(self) -> "ERThresholdPolicy":
        if self.review_probability_threshold >= self.match_probability_threshold:
            raise ValueError("review threshold must be below match threshold")
        return self


class ERClusteringPolicy(_SourceModel):
    policy_id: str = "er-clustering-v1"
    algorithm: str = "splink_connected_components"
    threshold_policy_id: str
    max_cluster_size: int = Field(default=100, ge=2)
    reject_placeholder_only_edges: bool = True
    reject_conflicting_anchor_edges: bool = True
    reject_unsafe_bridge_clusters: bool = True


class ERExecutionBudget(_SourceModel):
    max_records: int = Field(default=100_000, ge=1)
    max_candidate_pairs: int = Field(default=500_000, ge=1)
    max_all_pairs_diagnostic: int = Field(default=1_000_000, ge=1)
    max_runtime_seconds: int = Field(default=300, ge=1)
    max_cluster_size: int = Field(default=100, ge=2)


class EntityResolutionPrivacyContext(_SourceModel):
    purpose: str = "ENTITY_RESOLUTION_LOCAL_ANALYSIS"
    local_only: bool = True
    network_allowed: bool = False
    external_processing_allowed: bool = False
    llm_allowed: bool = False
    raw_values_in_results: bool = False
    raw_values_in_logs: bool = False
    project_temp_root: str = "workspace/test-temp/entity-resolution"
    cleanup_required: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "EntityResolutionPrivacyContext":
        if self.purpose != "ENTITY_RESOLUTION_LOCAL_ANALYSIS" or not self.local_only or self.network_allowed or self.external_processing_allowed or self.llm_allowed:
            raise ValueError("entity resolution is local-only and non-external")
        if self.raw_values_in_results or self.raw_values_in_logs or not self.cleanup_required:
            raise ValueError("entity resolution cannot publish/log raw identity values and must clean up")
        return self


class EntityResolutionSpec(_SourceModel):
    spec_id: str = Field(min_length=1)
    entity_family: str = Field(min_length=1)
    mode: EntityResolutionMode
    source_ids: tuple[str, ...] = Field(min_length=1)
    snapshot_ids: Mapping[str, str]
    table_ids_by_source: Mapping[str, tuple[str, ...]]
    identity_fields: tuple[IdentityFieldSpecification, ...] = Field(min_length=1)
    normalization_rules: tuple[EntityResolutionNormalizationRule, ...] = Field(min_length=1)
    blocking_rules: tuple[ERBlockingRule, ...] = Field(min_length=1)
    comparisons: tuple[ERComparisonSpecification, ...] = Field(min_length=1)
    training_policy: ERTrainingPolicy
    threshold_policy: ERThresholdPolicy
    clustering_policy: ERClusteringPolicy
    execution_budget: ERExecutionBudget = Field(default_factory=ERExecutionBudget)
    privacy_context: EntityResolutionPrivacyContext = Field(default_factory=EntityResolutionPrivacyContext)
    @model_validator(mode="after")
    def validate_candidate_scope(self) -> "EntityResolutionSpec":
        if set(self.snapshot_ids) != set(self.source_ids) or set(self.table_ids_by_source) != set(self.source_ids):
            raise ValueError("entity resolution scope must bind every selected source")
        if self.mode is EntityResolutionMode.DEDUPE_ONLY and len(self.source_ids) != 1:
            raise ValueError("DEDUPE_ONLY requires exactly one source")
        if self.mode is EntityResolutionMode.LINK_ONLY and len(self.source_ids) < 2:
            raise ValueError("LINK_ONLY requires at least two sources")
        field_ids = {item.field_id for item in self.identity_fields}
        if len({(item.source_id, item.table_id, item.column_id) for item in self.identity_fields}) != len(self.identity_fields):
            raise ValueError("identity field physical bindings must be unique")
        if any(item.normalization_rule_id not in {rule.rule_id for rule in self.normalization_rules} for item in self.identity_fields):
            raise ValueError("every identity field requires a declared normalization rule")
        if any(field_id not in field_ids for rule in self.blocking_rules for field_id in rule.field_ids):
            raise ValueError("blocking rules must reference declared identity fields")
        if any(item.field_id not in field_ids for item in self.comparisons):
            raise ValueError("comparisons must reference declared identity fields")
        if any(rule_id not in {rule.rule_id for rule in self.blocking_rules} for rule_id in self.training_policy.em_blocking_rule_ids):
            raise ValueError("EM training rules must be declared blocking rules")
        if self.clustering_policy.threshold_policy_id != self.threshold_policy.policy_id:
            raise ValueError("clustering policy must bind the declared threshold policy")
        if self.training_policy.require_provenance and not self.training_policy.em_blocking_rule_ids:
            raise ValueError("EM training requires explicit blocking provenance")
        return self

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class EntityResolutionAuthorization(_SourceModel):
    authorization_id: str = Field(min_length=1)
    purpose: str = "ENTITY_RESOLUTION_LOCAL_ANALYSIS"
    policy_id: str
    policy_version: str
    entity_family: str
    spec_id: str
    spec_fingerprint: str
    source_ids: tuple[str, ...] = Field(min_length=1)
    snapshot_ids: Mapping[str, str]
    table_ids_by_source: Mapping[str, tuple[str, ...]]
    identity_column_ids: tuple[str, ...] = Field(min_length=1)
    batch_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    local_only: bool = True
    network_allowed: bool = False
    external_processing_allowed: bool = False
    llm_allowed: bool = False
    issued_at: datetime

    @model_validator(mode="after")
    def fail_closed(self) -> "EntityResolutionAuthorization":
        if self.purpose != "ENTITY_RESOLUTION_LOCAL_ANALYSIS" or not self.local_only or self.network_allowed or self.external_processing_allowed or self.llm_allowed:
            raise ValueError("entity resolution authorization must be exact local-only authorization")
        if set(self.snapshot_ids) != set(self.source_ids) or set(self.table_ids_by_source) != set(self.source_ids):
            raise ValueError("entity resolution authorization must cover every source")
        return self


class EntityResolutionObservationScope(_SourceModel):
    source_ids: tuple[str, ...]
    snapshot_ids: Mapping[str, str]
    table_ids_by_source: Mapping[str, tuple[str, ...]]
    input_batch_ids: tuple[str, ...]
    input_batch_hashes: tuple[str, ...]
    available_staged_rows_by_table: Mapping[str, int]
    records_read: int = Field(ge=0)
    records_sampled: int = Field(ge=0)
    sample_seed: int
    sample_algorithm_version: str
    raw_values_local_only: bool = True


class EntityResolutionEngineReference(_SourceModel):
    engine: str
    engine_version: str
    adapter: AdapterReference
    runtime_dependency: str
    source_revision: str
    native_objects_isolated: bool = True


class EntityResolutionModelEvidence(_SourceModel):
    model_id: str
    training_policy_id: str
    u_training_method: str
    m_training_method: str
    random_seed: int
    training_blocking_rule_ids: tuple[str, ...]
    trained: bool
    training_provenance: str
    match_weight_semantics: str = "log_bayes_factor"
    probability_semantics: str = "model_implied_pair_probability_under_linkage_assumptions_not_calibrated_business_confidence"

    @model_validator(mode="after")
    def no_fake_training(self) -> "EntityResolutionModelEvidence":
        if self.trained and (not self.training_provenance or self.u_training_method == "default" or self.m_training_method == "default"):
            raise ValueError("trained model evidence requires actual training provenance")
        return self


class EntityMatchEdge(_SourceModel):
    edge_id: str
    left_record_ref: str
    right_record_ref: str
    left_source_id: str
    right_source_id: str
    left_snapshot_id: str
    right_snapshot_id: str
    match_weight: float
    match_probability: float = Field(ge=0, le=1)
    decision: str
    comparison_evidence_refs: tuple[str, ...] = ()
    blocking_rule_ids: tuple[str, ...] = Field(min_length=1)
    model_evidence_ref: str
    independent_evidence_refs: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def linkable_only(self) -> "EntityMatchEdge":
        if not self.left_record_ref or not self.right_record_ref or self.left_record_ref == self.right_record_ref:
            raise ValueError("match edges require two distinct record references")
        return self


class EntityCluster(_SourceModel):
    cluster_id: str
    record_refs: tuple[str, ...] = Field(min_length=1)
    edge_refs: tuple[str, ...] = ()
    decision: str = "CANDIDATE_CLUSTER"
    diagnostic_refs: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def no_canonical_identity(self) -> "EntityCluster":
        if len(set(self.record_refs)) != len(self.record_refs) or self.decision in {"CANONICAL", "ACCEPTED_MERGE"}:
            raise ValueError("clusters are candidate evidence and cannot be canonical entities")
        return self


class EntityClusterDiagnostic(_SourceModel):
    diagnostic_id: str
    cluster_id: str
    connected_component_size: int = Field(ge=1)
    independent_evidence_count: int = Field(ge=0)
    placeholder_only: bool = False
    conflicting_anchors: bool = False
    unsafe_bridge: bool = False
    largest_cluster_guard: bool = False
    detail: str = Field(min_length=1)


class EntityResolutionFailure(_SourceModel):
    failure_id: str
    kind: EntityResolutionFailureKind
    detail: str
    stage: str
    retryable: bool = False
    raw_values_present: bool = False

    @model_validator(mode="after")
    def failure_safe(self) -> "EntityResolutionFailure":
        if self.raw_values_present:
            raise ValueError("entity resolution failures cannot contain raw identity values")
        return self


class EntityResolutionCapability(_SourceModel):
    capability_id: str
    status: EntityResolutionCapabilityStatus
    engine: str
    engine_version: str
    detail: str


class EntityResolutionEvaluation(_SourceModel):
    evaluation_id: str
    fixture_id: str
    pairwise_precision: float = Field(ge=0, le=1)
    pairwise_recall: float = Field(ge=0, le=1)
    pairwise_f1: float = Field(ge=0, le=1)
    blocking_recall: float = Field(ge=0, le=1)
    candidate_pairs: int = Field(ge=0)
    all_pairs: int = Field(ge=0)
    false_merges: int = Field(ge=0)
    contaminated_clusters: int = Field(ge=0)
    largest_cluster_size: int = Field(ge=0)
    threshold: float = Field(ge=0, le=1)
    limitations: tuple[str, ...] = ()


class EntityResolutionRunMetrics(_SourceModel):
    available_staged_records: int = Field(ge=0)
    records_read: int = Field(ge=0)
    records_sampled: int = Field(ge=0)
    all_pairs: int = Field(ge=0)
    candidate_pairs: int = Field(ge=0)
    candidate_pairs_by_rule: Mapping[str, int] = Field(default_factory=dict)
    pairs_rejected_by_budget: int = Field(ge=0)
    predictions_emitted: int = Field(ge=0)
    clusters_emitted: int = Field(ge=0)
    incomplete: bool = False


class EntityResolutionArtifactReference(_SourceModel):
    artifact_id: str
    artifact_type: str = "entity_resolution_result"
    artifact_location: str
    content_hash: str
    publication_state: str = "COMPLETE"


class EntityResolutionThresholdEvaluation(_SourceModel):
    threshold: float = Field(ge=0, le=1)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    false_merges: int = Field(ge=0)


class EntityResolutionResult(_SourceModel):
    spec: EntityResolutionSpec
    observation_scope: EntityResolutionObservationScope
    engine: EntityResolutionEngineReference
    model: EntityResolutionModelEvidence | None = None
    edges: tuple[EntityMatchEdge, ...] = ()
    clusters: tuple[EntityCluster, ...] = ()
    diagnostics: tuple[EntityClusterDiagnostic, ...] = ()
    failures: tuple[EntityResolutionFailure, ...] = ()
    capabilities: tuple[EntityResolutionCapability, ...] = ()
    evaluations: tuple[EntityResolutionEvaluation, ...] = ()
    threshold_evaluations: tuple[EntityResolutionThresholdEvaluation, ...] = ()
    metrics: EntityResolutionRunMetrics
    artifacts: tuple[EntityResolutionArtifactReference, ...] = ()
    status: EntityResolutionStatus

    @model_validator(mode="after")
    def validate_result(self) -> "EntityResolutionResult":
        if self.status is EntityResolutionStatus.COMPLETE and (not self.capabilities or self.model is None):
            raise ValueError("complete ER result requires capability and model evidence")
        return self


def entity_resolution_config_hash(spec: EntityResolutionSpec) -> str:
    return spec.fingerprint


def entity_edge_id(left_record_ref: str, right_record_ref: str, model_id: str) -> str:
    return "entity_edge_" + hashlib.sha256(json.dumps(sorted((left_record_ref, right_record_ref)) + [model_id], separators=(",", ":")).encode()).hexdigest()[:32]
