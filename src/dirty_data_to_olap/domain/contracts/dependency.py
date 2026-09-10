"""Project-owned contracts for bounded structural dependency discovery.

The contracts describe measured evidence only.  They deliberately contain no
Desbordante objects and no raw cell values; relationship acceptance belongs to
the later evidence-fusion/review stages.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .source import AdapterReference, ObservationMode, _SourceModel, stable_digest


class DependencyKind(str, Enum):
    UCC = "UCC"
    FD = "FD"
    AFD = "AFD"
    IND = "IND"
    APPROXIMATE_IND = "APPROXIMATE_IND"


class DependencyEvidenceState(str, Enum):
    OBSERVED = "OBSERVED"
    TRUNCATED = "TRUNCATED"
    REJECTED = "REJECTED"


class DependencyStageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class DependencyCapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    NOT_REQUESTED = "NOT_REQUESTED"


class DependencyFailureKind(str, Enum):
    INPUT_INVALID = "INPUT_INVALID"
    STAGED_INPUT_INTEGRITY_FAILED = "STAGED_INPUT_INTEGRITY_FAILED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    ENGINE_FAILED = "ENGINE_FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ARTIFACT_FAILED = "ARTIFACT_FAILED"
    UNSUPPORTED_ALGORITHM = "UNSUPPORTED_ALGORITHM"


class NullPolicy(str, Enum):
    EXCLUDE_PHYSICAL_NULL = "EXCLUDE_PHYSICAL_NULL"
    NULLS_EQUAL = "NULLS_EQUAL"
    NULLS_BREAK_DEPENDENCY = "NULLS_BREAK_DEPENDENCY"
    LITERAL_NULL_IS_VALUE = "LITERAL_NULL_IS_VALUE"


class DependencySearchPolicy(_SourceModel):
    """Hard bounds and pruning rules; no unbounded combination search."""

    max_tables: int = Field(default=32, ge=1, le=10_000)
    max_columns_per_table: int = Field(default=64, ge=1, le=10_000)
    max_determinant_width: int = Field(default=3, ge=1, le=8)
    max_ucc_arity: int = Field(default=3, ge=1, le=8)
    max_fd_lhs_arity: int = Field(default=3, ge=1, le=8)
    max_ind_arity: int = Field(default=3, ge=1, le=8)
    max_column_pairs: int = Field(default=2_000, ge=1, le=1_000_000)
    max_output_candidates: int = Field(default=500, ge=1, le=100_000)
    max_runtime_seconds: int = Field(default=120, ge=1, le=86_400)
    min_support_ratio: float = Field(default=0.80, ge=0, le=1)
    max_error_ratio: float = Field(default=0.20, ge=0, le=1)
    low_cardinality_distinct_limit: int = Field(default=8, ge=1)
    low_cardinality_distinct_ratio: float = Field(default=0.05, ge=0, le=1)
    reject_low_cardinality_ind: bool = True


class DependencyObservationScope(_SourceModel):
    source_id: str
    snapshot_id: str
    table_ids: tuple[str, ...]
    mode: ObservationMode
    rows_by_table: Mapping[str, int]
    complete_by_table: Mapping[str, bool]
    input_batch_ids: tuple[str, ...]
    input_batch_hashes: tuple[str, ...]
    input_record_reference_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_tables(self) -> "DependencyObservationScope":
        if set(self.rows_by_table) != set(self.table_ids):
            raise ValueError("dependency scope row counts must cover selected tables")
        if set(self.complete_by_table) != set(self.table_ids):
            raise ValueError("dependency scope completeness must cover selected tables")
        return self


class DependencyPrivacyContext(_SourceModel):
    """Explicit local-only authorization for structural analysis."""

    purpose: str = "dependency_discovery_structural_analysis"
    exposure_context: str = "DEPENDENCY_LOCAL_ANALYSIS"
    raw_staging_allowed: bool = True
    local_only: bool = True
    external_processing_allowed: bool = False
    network_allowed: bool = False
    llm_allowed: bool = False
    raw_values_in_results: bool = False
    raw_values_in_logs: bool = False
    project_temp_root: str = "privacy_ephemeral/dependency_discovery"
    cleanup_required: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "DependencyPrivacyContext":
        if self.purpose != "dependency_discovery_structural_analysis":
            raise ValueError("dependency analysis requires its explicit purpose")
        if not self.local_only or self.external_processing_allowed or self.network_allowed or self.llm_allowed:
            raise ValueError("dependency structural analysis is local-only and non-external")
        if self.raw_values_in_results or self.raw_values_in_logs or not self.cleanup_required:
            raise ValueError("raw values cannot leave dependency analysis and temp cleanup is mandatory")
        return self


class DependencyAuthorization(_SourceModel):
    """An authorization issued by PrivacyPolicyService for one exact input scope."""

    authorization_id: str = Field(min_length=1)
    purpose: str
    policy_id: str
    policy_version: str
    source_id: str
    snapshot_id: str
    table_ids: tuple[str, ...] = Field(min_length=1)
    artifact_ids: tuple[str, ...] = ()
    local_only: bool = True
    network_allowed: bool = False
    external_processing_allowed: bool = False
    issued_at: datetime
    required_transformation: str = "aggregate_project_owned_evidence"

    @model_validator(mode="after")
    def fail_closed(self) -> "DependencyAuthorization":
        if not self.local_only or self.network_allowed or self.external_processing_allowed:
            raise ValueError("dependency authorization must be local-only and non-external")
        return self


class DependencyRequest(_SourceModel):
    request_id: str = Field(min_length=1)
    source_id: str
    snapshot_id: str
    selected_table_ids: tuple[str, ...] = Field(min_length=1)
    requested_kinds: tuple[DependencyKind, ...] = (DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND, DependencyKind.APPROXIMATE_IND)
    search_policy: DependencySearchPolicy = Field(default_factory=DependencySearchPolicy)
    null_policy: NullPolicy = NullPolicy.EXCLUDE_PHYSICAL_NULL
    algorithm_policy_version: str = "dependency-algorithms-v1"
    privacy_context: DependencyPrivacyContext = Field(default_factory=DependencyPrivacyContext)


class DependencyProvenance(_SourceModel):
    source_id: str
    snapshot_id: str
    table_ids: tuple[str, ...]
    engine: str
    engine_version: str
    algorithm: str
    algorithm_config_hash: str
    adapter: AdapterReference
    created_at: datetime
    null_semantics: str = "unspecified"
    provider_runtime: str = "unspecified"


class DependencyViolation(_SourceModel):
    violation_id: str
    kind: DependencyKind
    record_refs: tuple[str, ...] = ()
    count: int = Field(ge=0)
    detail_code: str = Field(min_length=1)


class KeyCandidate(_SourceModel):
    candidate_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    columns: tuple[str, ...] = Field(min_length=1)
    ucc_evidence_id: str = Field(min_length=1)
    key_type: str = "UNIQUE_CANDIDATE"
    uniqueness_ratio: float = Field(ge=0, le=1)
    physical_missing_ratio: float = Field(ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    observation_scope: DependencyObservationScope
    state: DependencyEvidenceState
    evidence_refs: tuple[str, ...] = ()
    provenance: DependencyProvenance


class FunctionalDependencyEvidence(_SourceModel):
    evidence_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    determinant: tuple[str, ...] = Field(min_length=1)
    dependent: tuple[str, ...] = Field(min_length=1)
    approximate: bool = False
    support_ratio: float = Field(ge=0, le=1)
    error_ratio: float = Field(ge=0, le=1)
    violation_count: int = Field(ge=0)
    violations: tuple[DependencyViolation, ...] = ()
    project_metric_name: str = "distinct_rhs_alternatives_excess_per_eligible_row"
    project_metric_definition: str = "sum(max(distinct dependent values per determinant - 1, 0)) / eligible rows"
    metric_observations: tuple["DependencyMetricObservation", ...] = ()
    null_policy: NullPolicy
    observation_scope: DependencyObservationScope
    state: DependencyEvidenceState
    provenance: DependencyProvenance


class InclusionDependencyEvidence(_SourceModel):
    evidence_id: str
    source_id: str
    snapshot_id: str
    left_table_id: str
    left_columns: tuple[str, ...] = Field(min_length=1)
    right_table_id: str
    right_columns: tuple[str, ...] = Field(min_length=1)
    approximate: bool = False
    coverage_ratio: float = Field(ge=0, le=1)
    violation_ratio: float = Field(ge=0, le=1)
    left_distinct_count: int = Field(ge=0)
    right_distinct_count: int = Field(ge=0)
    orphan_count: int = Field(ge=0)
    target_uniqueness_ratio: float = Field(ge=0, le=1)
    type_compatible: bool
    low_cardinality_risk: bool = False
    violations: tuple[DependencyViolation, ...] = ()
    metric_observations: tuple["DependencyMetricObservation", ...] = ()
    null_policy: NullPolicy
    observation_scope: DependencyObservationScope
    state: DependencyEvidenceState
    provenance: DependencyProvenance

    @model_validator(mode="after")
    def matching_widths(self) -> "InclusionDependencyEvidence":
        if len(self.left_columns) != len(self.right_columns):
            raise ValueError("inclusion dependency column widths must match")
        return self


class RelationshipCandidate(_SourceModel):
    candidate_id: str
    source_id: str
    snapshot_id: str
    from_table: str
    from_columns: tuple[str, ...] = Field(min_length=1)
    to_table: str
    to_columns: tuple[str, ...] = Field(min_length=1)
    proposed_cardinality: str = "MANY_TO_ONE"
    source_orphan_ratio: float = Field(ge=0, le=1)
    target_uniqueness_ratio: float = Field(ge=0, le=1)
    type_compatible: bool
    low_cardinality_risk: bool
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    state: str = "CANDIDATE"
    final_acceptance_allowed: bool = False

    @model_validator(mode="after")
    def enforce_candidate_only(self) -> "RelationshipCandidate":
        if self.state != "CANDIDATE" or self.final_acceptance_allowed:
            raise ValueError("dependency discovery emits candidates, never accepted relationships")
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError("relationship column widths must match")
        return self


class UniqueColumnCombinationEvidence(_SourceModel):
    """Measured UCC evidence; it is not a primary-key assertion."""

    evidence_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    column_ids: tuple[str, ...] = Field(min_length=1)
    uniqueness_ratio: float = Field(ge=0, le=1)
    physical_missing_ratio: float = Field(ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    observation_scope: DependencyObservationScope
    state: DependencyEvidenceState
    provenance: DependencyProvenance


class DependencyMetricObservation(_SourceModel):
    """A provider-native or explicitly project-computed metric observation."""

    metric_name: str = Field(min_length=1)
    metric_value: float | None = Field(default=None, ge=0)
    direction: str = Field(min_length=1)
    threshold: float | None = Field(default=None, ge=0)
    provider_algorithm: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    project_computed: bool


class DependencyFailure(_SourceModel):
    failure_id: str
    request_id: str
    kind: DependencyFailureKind
    detail: str = Field(min_length=1)
    stage: str = "DEPENDENCY_DISCOVERY"
    table_id: str | None = None
    retryable: bool = False


class DependencyCapability(_SourceModel):
    capability_id: str
    status: DependencyCapabilityStatus
    engine: str
    engine_version: str | None = None
    requested_kinds: tuple[DependencyKind, ...] = ()
    detail: str


class DependencySearchStats(_SourceModel):
    input_tables: int = Field(ge=0)
    input_columns: int = Field(ge=0)
    columns_excluded_by_bound: int = Field(default=0, ge=0)
    tables_excluded_by_bound: int = Field(default=0, ge=0)
    candidate_column_pairs: int = Field(ge=0)
    candidate_pairs_before_pruning: int = Field(default=0, ge=0)
    pruned_by_type: int = Field(default=0, ge=0)
    pruned_by_scope: int = Field(default=0, ge=0)
    pruned_by_budget: int = Field(default=0, ge=0)
    pruned_column_pairs: int = Field(ge=0)
    evaluated_pairs: int = Field(default=0, ge=0)
    searched_determinants: int = Field(ge=0)
    ucc_search_arity: int = Field(default=0, ge=0)
    ucc_arity_bounded: bool = True
    fd_search_arity: int = Field(default=0, ge=0)
    fd_arity_bounded: bool = True
    ind_search_arity: int = Field(default=0, ge=0)
    ind_arity_bounded: bool = True
    provider_calls: int = Field(default=0, ge=0)
    output_truncation: bool = False
    runtime_timeout: bool = False
    completeness: str = "COMPLETE"
    emitted_candidates: int = Field(ge=0)
    truncated: bool = False
    truncation_reasons: tuple[str, ...] = ()


class DependencyArtifactReference(_SourceModel):
    artifact_id: str
    artifact_type: str
    artifact_location: str
    content_hash: str
    publication_state: str = "COMPLETE"


class DependencyResult(_SourceModel):
    request: DependencyRequest
    observation_scope: DependencyObservationScope
    ucc_evidence: tuple[UniqueColumnCombinationEvidence, ...] = ()
    key_candidates: tuple[KeyCandidate, ...] = ()
    functional_dependencies: tuple[FunctionalDependencyEvidence, ...] = ()
    inclusion_dependencies: tuple[InclusionDependencyEvidence, ...] = ()
    relationship_candidates: tuple[RelationshipCandidate, ...] = ()
    failures: tuple[DependencyFailure, ...] = ()
    capabilities: tuple[DependencyCapability, ...] = ()
    search_stats: DependencySearchStats
    status: DependencyStageStatus
    artifacts: tuple[DependencyArtifactReference, ...] = ()


def dependency_config_hash(request: DependencyRequest) -> str:
    policy = request.search_policy.model_dump(mode="json")
    return stable_digest(
        {
            "requested_kinds": sorted(kind.value for kind in request.requested_kinds),
            "search_policy": policy,
            "null_policy": request.null_policy.value,
            "algorithm_policy_version": request.algorithm_policy_version,
            "privacy_execution_mode": {
                "local_only": request.privacy_context.local_only,
                "external_processing_allowed": request.privacy_context.external_processing_allowed,
                "network_allowed": request.privacy_context.network_allowed,
            },
            "schema": "dependency-contracts-v2",
        }
    )


def dependency_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{stable_digest(value)[:32]}"
