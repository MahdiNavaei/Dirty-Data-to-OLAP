"""Project-owned, sampling-aware profiling contracts.

These models describe observations only.  They intentionally contain no
DataProfiler, pandas, NumPy or other profiler-native objects.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .source import AdapterReference, SourceTableKind, TableObservationStatus, stable_digest
from .source import _SourceModel as _ProfileModel


class ProfileMode(str, Enum):
    FULL = "FULL"
    SAMPLE = "SAMPLE"


class ProfileCompleteness(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class ProfileObservationStatus(str, Enum):
    NOT_OBSERVED = "NOT_OBSERVED"
    PARTIALLY_OBSERVED = "PARTIALLY_OBSERVED"
    FULLY_OBSERVED = "FULLY_OBSERVED"


class UniquenessSemantics(str, Enum):
    EXACT_ON_FULL_SCOPE = "EXACT_ON_FULL_SCOPE"
    SAMPLE_OBSERVATION = "SAMPLE_OBSERVATION"
    ESTIMATED = "ESTIMATED"
    NOT_COMPUTED_BOUNDED = "NOT_COMPUTED_BOUNDED"


class ProfileFailureKind(str, Enum):
    INPUT_INVALID = "INPUT_INVALID"
    TABLE_NOT_OBSERVED = "TABLE_NOT_OBSERVED"
    BATCH_INTEGRITY_FAILED = "BATCH_INTEGRITY_FAILED"
    COLUMN_FAILED = "COLUMN_FAILED"
    ENGINE_FAILED = "ENGINE_FAILED"
    ARTIFACT_FAILED = "ARTIFACT_FAILED"
    INCOMPATIBLE_DIFF = "INCOMPATIBLE_DIFF"


class ProfileDiffStatus(str, Enum):
    COMPARABLE = "COMPARABLE"
    LIMITED_COMPARABILITY = "LIMITED_COMPARABILITY"
    INCOMPATIBLE = "INCOMPATIBLE"


class PatternType(str, Enum):
    EMAIL_LIKE = "EMAIL_LIKE"
    PHONE_LIKE = "PHONE_LIKE"
    UUID_LIKE = "UUID_LIKE"
    INTEGER_STRING = "INTEGER_STRING"
    DECIMAL_STRING = "DECIMAL_STRING"
    DATE_STRING = "DATE_STRING"
    URL_LIKE = "URL_LIKE"


class ExpensiveStatisticsPolicy(_ProfileModel):
    correlation_enabled: bool = False
    chi_square_enabled: bool = False
    max_columns_for_expensive_stats: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def require_explicit_bound(self) -> "ExpensiveStatisticsPolicy":
        if (self.correlation_enabled or self.chi_square_enabled) and self.max_columns_for_expensive_stats < 2:
            raise ValueError("expensive cross-column statistics require an explicit column bound")
        return self


class SemanticLabelPolicy(_ProfileModel):
    enabled: bool = False
    model_reference: str | None = None

    @model_validator(mode="after")
    def require_model_reference(self) -> "SemanticLabelPolicy":
        if self.enabled and not self.model_reference:
            raise ValueError("semantic labels require an explicit model reference")
        return self


class NullMarkerPolicy(_ProfileModel):
    configured_markers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_markers(self) -> "NullMarkerPolicy":
        if len(set(self.configured_markers)) != len(self.configured_markers):
            raise ValueError("configured null markers must be unique")
        return self


class ProfileRequest(_ProfileModel):
    profile_request_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    selected_table_ids: tuple[str, ...] = Field(min_length=1)
    selected_column_ids: tuple[str, ...] = ()
    mode: ProfileMode
    sample_limit: int | None = Field(default=None, gt=0, le=10_000_000)
    seed: int | None = None
    metric_policy_version: str = "1.0"
    expensive_statistics: ExpensiveStatisticsPolicy = Field(default_factory=ExpensiveStatisticsPolicy)
    semantic_label_policy: SemanticLabelPolicy = Field(default_factory=SemanticLabelPolicy)
    null_marker_policy: NullMarkerPolicy = Field(default_factory=NullMarkerPolicy)
    profile_config_version: str = "1.0"

    @model_validator(mode="after")
    def validate_sampling(self) -> "ProfileRequest":
        if len(set(self.selected_table_ids)) != len(self.selected_table_ids):
            raise ValueError("selected table IDs must be unique")
        if self.mode is ProfileMode.SAMPLE and (self.sample_limit is None or self.seed is None):
            raise ValueError("SAMPLE profiling requires sample_limit and seed")
        if self.mode is ProfileMode.FULL and self.sample_limit is not None:
            raise ValueError("FULL profiling cannot carry a sample limit")
        return self


class ProfileObservationScope(_ProfileModel):
    source_snapshot_mode: str
    source_snapshot_was_bounded: bool
    source_table_observation_status: TableObservationStatus
    source_rows_observed: int = Field(ge=0)
    rows_available_in_snapshot: int = Field(ge=0)
    rows_profiled: int = Field(ge=0)
    profiling_mode: ProfileMode
    sample_method: str
    seed: int | None = None
    sample_limit: int | None = Field(default=None, ge=1)
    sample_record_refs: tuple[str, ...] = ()
    sample_identity: str | None = None
    all_available_staged_rows_covered: bool
    known_source_row_count: int | None = Field(default=None, ge=0)
    completeness: ProfileObservationStatus

    @model_validator(mode="after")
    def validate_scope(self) -> "ProfileObservationScope":
        if self.rows_profiled > self.rows_available_in_snapshot:
            raise ValueError("profiled rows cannot exceed staged snapshot rows")
        if self.source_table_observation_status is TableObservationStatus.NOT_OBSERVED:
            if self.rows_profiled or self.rows_available_in_snapshot or self.completeness is not ProfileObservationStatus.NOT_OBSERVED:
                raise ValueError("not-observed source tables cannot claim profile coverage")
            return self
        if self.profiling_mode is ProfileMode.SAMPLE:
            if self.sample_limit is None or self.seed is None or self.sample_method != "deterministic_reservoir_v1":
                raise ValueError("sampled profiles require deterministic sampling provenance")
            if len(self.sample_record_refs) != self.rows_profiled:
                raise ValueError("sample record references must cover the sampled rows")
        if self.profiling_mode is ProfileMode.FULL:
            if self.sample_method != "none" or self.sample_record_refs:
                raise ValueError("full profiles cannot carry sample provenance")
            if self.rows_profiled != self.rows_available_in_snapshot or not self.all_available_staged_rows_covered:
                raise ValueError("FULL profiling must cover every available staged row")
        if self.completeness is ProfileObservationStatus.NOT_OBSERVED and self.rows_profiled:
            raise ValueError("not-observed scope cannot contain profiled rows")
        return self


class NumericSummary(_ProfileModel):
    count: int = Field(ge=0)
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    variance: float | None = None
    standard_deviation: float | None = None
    zero_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    quantiles: Mapping[str, float] = Field(default_factory=dict)


class LengthSummary(_ProfileModel):
    count: int = Field(ge=0)
    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)
    mean: float | None = None
    empty_string_count: int = Field(ge=0)


class DateTimeSummary(_ProfileModel):
    observed_count: int = Field(ge=0)
    parse_success_count: int = Field(ge=0)
    minimum: str | None = None
    maximum: str | None = None


class ProfileProvenance(_ProfileModel):
    execution_context_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    column_id: str | None = None
    schema_fingerprint: str
    input_batch_ids: tuple[str, ...]
    input_batch_hashes: tuple[str, ...]
    profiling_adapter: AdapterReference
    dataprofiler_version: str
    profile_config_hash: str
    created_at: datetime


class ValuePatternSummary(_ProfileModel):
    pattern_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    column_id: str
    pattern_type: PatternType
    match_count: int = Field(ge=0)
    observed_rows: int = Field(ge=0)
    support_ratio: float = Field(ge=0, le=1)
    observation_scope: ProfileObservationScope
    method: str
    method_version: str
    provenance: ProfileProvenance


class ColumnProfile(_ProfileModel):
    profile_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    column_id: str
    physical_type: str
    primitive_type_observations: Mapping[str, int]
    physical_null_count: int = Field(ge=0)
    configured_null_marker_count: int = Field(ge=0)
    unknown_missing_count: int = Field(ge=0)
    rows_observed: int = Field(ge=0)
    observed_distinct_count: int | None = Field(default=None, ge=0)
    observed_distinct_ratio: float | None = Field(default=None, ge=0, le=1)
    uniqueness_semantics: UniquenessSemantics
    length_summary: LengthSummary | None = None
    numeric_summary: NumericSummary | None = None
    datetime_summary: DateTimeSummary | None = None
    categorical_summary: Mapping[str, float | int | None] = Field(default_factory=dict)
    pattern_summary_refs: tuple[str, ...] = ()
    semantic_label_evidence: tuple[Mapping[str, Any], ...] = ()
    observation_scope: ProfileObservationScope
    provenance: ProfileProvenance
    status: ProfileCompleteness
    failure_refs: tuple[str, ...] = ()


class TableProfile(_ProfileModel):
    profile_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    table_kind: SourceTableKind
    observation_scope: ProfileObservationScope
    rows_available_in_snapshot: int = Field(ge=0)
    rows_profiled: int = Field(ge=0)
    column_profile_refs: tuple[str, ...]
    duplicate_row_count: int | None = Field(default=None, ge=0)
    duplicate_observation_complete: bool
    failure_refs: tuple[str, ...] = ()
    provenance: ProfileProvenance
    status: ProfileCompleteness


class ProfileFailure(_ProfileModel):
    failure_id: str
    profile_request_id: str
    source_id: str
    snapshot_id: str
    table_id: str | None = None
    column_id: str | None = None
    kind: ProfileFailureKind
    detail: str
    engine: str
    config_hash: str
    rows_observed: int = Field(default=0, ge=0)
    retryable: bool


class ProfileArtifactReference(_ProfileModel):
    artifact_id: str
    artifact_type: str
    artifact_location: str
    content_hash: str
    publication_state: str = "COMPLETE"


class ProfileResult(_ProfileModel):
    profile_request: ProfileRequest
    tables: tuple[TableProfile, ...]
    columns: tuple[ColumnProfile, ...]
    patterns: tuple[ValuePatternSummary, ...]
    failures: tuple[ProfileFailure, ...]
    completeness: ProfileCompleteness
    artifacts: tuple[ProfileArtifactReference, ...] = ()


class ProfileMetricChange(_ProfileModel):
    before: Any
    after: Any
    delta: Any | None = None


class ProfileDiff(_ProfileModel):
    diff_id: str
    left_profile_id: str
    right_profile_id: str
    source_id: str
    table_id: str
    column_id: str
    status: ProfileDiffStatus
    reasons: tuple[str, ...] = ()
    changes: Mapping[str, ProfileMetricChange] = Field(default_factory=dict)


def profile_config_hash(request: ProfileRequest) -> str:
    return stable_digest(request)


def profile_id_for(source_id: str, snapshot_id: str, table_id: str, column_id: str, request: ProfileRequest) -> str:
    return "prof_" + stable_digest({"source_id": source_id, "snapshot_id": snapshot_id, "table_id": table_id, "column_id": column_id, "config": profile_config_hash(request)})[:32]


def table_profile_id_for(source_id: str, snapshot_id: str, table_id: str, request: ProfileRequest) -> str:
    return "tblprof_" + stable_digest({"source_id": source_id, "snapshot_id": snapshot_id, "table_id": table_id, "config": profile_config_hash(request)})[:32]


def pattern_id_for(source_id: str, snapshot_id: str, table_id: str, column_id: str, pattern_type: PatternType, request: ProfileRequest) -> str:
    return "pat_" + stable_digest({"source_id": source_id, "snapshot_id": snapshot_id, "table_id": table_id, "column_id": column_id, "pattern": pattern_type.value, "config": profile_config_hash(request)})[:32]


def diff_column_profiles(left: ColumnProfile, right: ColumnProfile) -> ProfileDiff:
    reasons: list[str] = []
    status = ProfileDiffStatus.COMPARABLE
    if (left.source_id, left.table_id, left.column_id) != (right.source_id, right.table_id, right.column_id):
        status = ProfileDiffStatus.INCOMPATIBLE
        reasons.append("source, table or column identity differs")
    elif left.observation_scope.source_snapshot_mode != right.observation_scope.source_snapshot_mode:
        status = ProfileDiffStatus.LIMITED_COMPARABILITY
        reasons.append("source snapshot observation modes differ")
    elif left.provenance.profile_config_hash != right.provenance.profile_config_hash:
        status = ProfileDiffStatus.LIMITED_COMPARABILITY
        reasons.append("profile configuration differs")
    fields = {
        "physical_null_count": (left.physical_null_count, right.physical_null_count),
        "configured_null_marker_count": (left.configured_null_marker_count, right.configured_null_marker_count),
        "observed_distinct_ratio": (left.observed_distinct_ratio, right.observed_distinct_ratio),
        "length_summary": (left.length_summary, right.length_summary),
        "numeric_summary": (left.numeric_summary, right.numeric_summary),
        "primitive_type_observations": (left.primitive_type_observations, right.primitive_type_observations),
    }
    changes: dict[str, ProfileMetricChange] = {}
    for name, (before, after) in fields.items():
        if before != after:
            delta = after - before if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None
            changes[name] = ProfileMetricChange(before=before, after=after, delta=delta)
    return ProfileDiff(
        diff_id="diff_" + stable_digest({"left": left.profile_id, "right": right.profile_id})[:32],
        left_profile_id=left.profile_id,
        right_profile_id=right.profile_id,
        source_id=left.source_id,
        table_id=left.table_id,
        column_id=left.column_id,
        status=status,
        reasons=tuple(reasons),
        changes=changes,
    )
