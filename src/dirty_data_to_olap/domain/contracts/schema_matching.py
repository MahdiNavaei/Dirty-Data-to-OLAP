"""Project-owned contracts for bounded, explainable schema matching.

Valentine objects and raw cell values are adapter-local implementation details.
These contracts describe candidate evidence only; they never assert a mapping or
an entity relationship.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .source import AdapterReference, ObservationMode, _SourceModel, stable_digest


class SchemaMatchMode(str, Enum):
    SCHEMA_ONLY = "SCHEMA_ONLY"
    INSTANCE_AWARE = "INSTANCE_AWARE"


class SchemaMatchStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SchemaMatchSignalFamily(str, Enum):
    NAME_LEXICAL = "NAME_LEXICAL"
    SCHEMA_STRUCTURAL = "SCHEMA_STRUCTURAL"
    PROFILE = "PROFILE"
    INSTANCE = "INSTANCE"
    DEPENDENCY = "DEPENDENCY"
    DECLARED_METADATA = "DECLARED_METADATA"
    DOMAIN_ASSERTION = "DOMAIN_ASSERTION"


class SchemaMatchFailureKind(str, Enum):
    INPUT_INVALID = "INPUT_INVALID"
    STAGED_INPUT_INTEGRITY_FAILED = "STAGED_INPUT_INTEGRITY_FAILED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    MATCHER_FAILED = "MATCHER_FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ARTIFACT_FAILED = "ARTIFACT_FAILED"
    UNSUPPORTED_MODE = "UNSUPPORTED_MODE"


class SchemaMatchCapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    NOT_REQUESTED = "NOT_REQUESTED"


class SchemaMatchSearchPolicy(_SourceModel):
    max_sources: int = Field(default=8, ge=2, le=64)
    max_source_pairs: int = Field(default=28, ge=1, le=2016)
    max_tables_per_source: int = Field(default=32, ge=1, le=10_000)
    max_table_pairs: int = Field(default=500, ge=1, le=1_000_000)
    max_columns_per_source: int = Field(default=256, ge=1, le=100_000)
    max_column_pairs: int = Field(default=10_000, ge=1, le=10_000_000)
    max_instance_rows_per_table: int = Field(default=100, ge=1, le=100_000)
    max_matchers: int = Field(default=2, ge=1, le=8)
    top_k_per_left_column: int = Field(default=3, ge=1, le=100)
    max_output_candidates: int = Field(default=2_000, ge=1, le=100_000)
    max_runtime_seconds: int = Field(default=120, ge=1, le=86_400)
    reject_type_incompatible: bool = True


class SchemaMatcherReference(_SourceModel):
    matcher_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    configuration: Mapping[str, Any] = Field(default_factory=dict)
    score_name: str = "native_similarity"
    score_semantics: str = "native_matcher_similarity; not a probability or calibrated confidence"


def _default_matchers() -> tuple[SchemaMatcherReference, ...]:
    return (
        SchemaMatcherReference(
            matcher_id="valentine-coma-schema-v1",
            name="Coma",
            version="1.0.0",
            configuration={"use_schema": True, "use_instances": False, "max_n": 0},
        ),
        SchemaMatcherReference(
            matcher_id="valentine-cupid-schema-v1",
            name="Cupid",
            version="1.0.0",
            configuration={"w_struct": 0.2, "leaf_w_struct": 0.2, "th_accept": 0.0},
        ),
    )


class SchemaMatchPrivacyContext(_SourceModel):
    purpose: str = "SCHEMA_MATCHING_LOCAL_ANALYSIS"
    local_only: bool = True
    network_allowed: bool = False
    external_processing_allowed: bool = False
    llm_allowed: bool = False
    raw_values_in_results: bool = False
    raw_values_in_logs: bool = False
    project_temp_root: str = "privacy_ephemeral/schema_matching"
    cleanup_required: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "SchemaMatchPrivacyContext":
        if self.purpose != "SCHEMA_MATCHING_LOCAL_ANALYSIS":
            raise ValueError("schema matching requires its explicit local-analysis purpose")
        if not self.local_only or self.network_allowed or self.external_processing_allowed or self.llm_allowed:
            raise ValueError("schema matching instance analysis is local-only")
        if self.raw_values_in_results or self.raw_values_in_logs or not self.cleanup_required:
            raise ValueError("schema matching cannot publish or log raw values and must clean up")
        return self


class SchemaMatchRequest(_SourceModel):
    request_id: str = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=2)
    snapshot_ids: Mapping[str, str]
    selected_table_ids_by_source: Mapping[str, tuple[str, ...]]
    selected_column_ids_by_table: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    mode: SchemaMatchMode = SchemaMatchMode.SCHEMA_ONLY
    sample_mode: str = "HASHED_DETERMINISTIC_V1"
    sample_seed: int = 0
    sample_algorithm_version: str = "hash_ordered_sample_v1"
    normalization_policy_version: str = "unicode_case_separator_camel_token_v1"
    abbreviation_dictionary_version: str | None = None
    abbreviation_dictionary: Mapping[str, str] = Field(default_factory=dict)
    matcher_references: tuple[SchemaMatcherReference, ...] = Field(default_factory=_default_matchers)
    search_policy: SchemaMatchSearchPolicy = Field(default_factory=SchemaMatchSearchPolicy)
    privacy_context: SchemaMatchPrivacyContext = Field(default_factory=SchemaMatchPrivacyContext)
    domain_assertions: tuple[Mapping[str, Any], ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> "SchemaMatchRequest":
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("selected source IDs must be unique")
        if len(self.source_ids) > self.search_policy.max_sources:
            raise ValueError("selected source count exceeds the configured bound")
        if set(self.snapshot_ids) != set(self.source_ids):
            raise ValueError("every selected source requires one pinned snapshot")
        if set(self.selected_table_ids_by_source) != set(self.source_ids):
            raise ValueError("every selected source requires an explicit table scope")
        if any(not tables for tables in self.selected_table_ids_by_source.values()):
            raise ValueError("each selected source requires at least one table")
        if len(self.matcher_references) > self.search_policy.max_matchers:
            raise ValueError("matcher count exceeds the configured bound")
        if self.abbreviation_dictionary and not self.abbreviation_dictionary_version:
            raise ValueError("abbreviation dictionaries require an explicit version")
        if self.mode is SchemaMatchMode.SCHEMA_ONLY and self.privacy_context.purpose != "SCHEMA_MATCHING_LOCAL_ANALYSIS":
            raise ValueError("schema-only requests still require the canonical privacy purpose")
        return self


class MatchingAuthorization(_SourceModel):
    authorization_id: str = Field(min_length=1)
    purpose: str
    policy_id: str
    policy_version: str
    source_ids: tuple[str, ...] = Field(min_length=2)
    snapshot_ids: Mapping[str, str]
    table_ids_by_source: Mapping[str, tuple[str, ...]]
    column_ids_by_table: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()
    local_only: bool = True
    network_allowed: bool = False
    external_processing_allowed: bool = False
    issued_at: datetime

    @model_validator(mode="after")
    def fail_closed(self) -> "MatchingAuthorization":
        if not self.local_only or self.network_allowed or self.external_processing_allowed:
            raise ValueError("matching authorization must be local-only and non-external")
        if set(self.snapshot_ids) != set(self.source_ids) or set(self.table_ids_by_source) != set(self.source_ids):
            raise ValueError("matching authorization must bind every selected source")
        return self


class SchemaMatchObservationScope(_SourceModel):
    source_ids: tuple[str, ...]
    snapshot_ids: Mapping[str, str]
    table_ids_by_source: Mapping[str, tuple[str, ...]]
    column_ids_by_table: Mapping[str, tuple[str, ...]]
    source_snapshot_modes: Mapping[str, ObservationMode | str]
    complete_by_table: Mapping[str, bool]
    staged_rows_by_table: Mapping[str, int]
    sampled_rows_by_table: Mapping[str, int]
    sample_seed: int
    sample_mode: str
    sample_algorithm_version: str
    sample_identity: str
    raw_values_local_only: bool = True
    reduced_scope: bool = False


class SchemaMatchSignal(_SourceModel):
    signal_id: str
    family: SchemaMatchSignalFamily
    value: float | None = Field(default=None, ge=0, le=1)
    semantics: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    assertion_bound: bool = False


class SchemaMatchScore(_SourceModel):
    score_id: str
    matcher: SchemaMatcherReference
    source_column_id: str
    target_column_id: str
    raw_native_score: float
    native_score_name: str
    native_score_semantics: str
    rank: int = Field(ge=1)
    config_hash: str
    mode: SchemaMatchMode
    observation_scope: SchemaMatchObservationScope
    provenance: AdapterReference


class SchemaMatchCandidate(_SourceModel):
    candidate_id: str
    source_id: str
    source_snapshot_id: str
    source_table_id: str
    source_column_id: str
    source_column_name: str
    target_source_id: str
    target_snapshot_id: str
    target_table_id: str
    target_column_id: str
    target_column_name: str
    score_refs: tuple[str, ...]
    signal_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    observation_scope: SchemaMatchObservationScope
    state: str = "CANDIDATE"
    final_acceptance_allowed: bool = False
    risk_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def candidate_only(self) -> "SchemaMatchCandidate":
        if self.state != "CANDIDATE" or self.final_acceptance_allowed:
            raise ValueError("schema matching cannot publish an accepted mapping")
        if self.source_column_id == self.target_column_id and self.source_id == self.target_source_id:
            raise ValueError("schema candidates must cross sources")
        return self


class SchemaMatchFailure(_SourceModel):
    failure_id: str
    request_id: str
    kind: SchemaMatchFailureKind
    detail: str
    matcher_id: str | None = None
    source_id: str | None = None
    table_id: str | None = None
    retryable: bool = False


class SchemaMatchCapability(_SourceModel):
    capability_id: str
    status: SchemaMatchCapabilityStatus
    engine: str
    engine_version: str
    requested_matchers: tuple[str, ...]
    detail: str


class SchemaMatchPruningSummary(_SourceModel):
    source_pairs_before_bound: int
    source_pairs_evaluated: int
    tables_before_bound: int
    tables_evaluated: int
    table_pairs_before_bound: int
    table_pairs_evaluated: int
    column_pairs_before_pruning: int
    column_pairs_pruned_by_type: int
    column_pairs_pruned_by_scope: int
    column_pairs_pruned_by_context: int
    column_pairs_pruned_by_budget: int
    column_pairs_evaluated: int
    matcher_calls: int
    evaluated_by_matcher: Mapping[str, int]
    returned_by_matcher: Mapping[str, int]
    output_candidates_emitted: int
    output_truncated: bool
    truncation_reasons: tuple[str, ...] = ()


class SchemaMatchArtifactReference(_SourceModel):
    artifact_id: str
    artifact_type: str
    artifact_location: str
    content_hash: str
    publication_state: str = "COMPLETE"


class SchemaMatchEvaluation(_SourceModel):
    evaluation_id: str
    fixture_id: str
    matcher_id: str
    sample_identity: str
    recall_at_k: Mapping[str, float]
    mean_reciprocal_rank: float = Field(ge=0)
    labeled_positive_count: int = Field(ge=0)
    evaluated_candidate_count: int = Field(ge=0)
    limitations: tuple[str, ...] = ()


class SchemaMatchResult(_SourceModel):
    request: SchemaMatchRequest
    observation_scope: SchemaMatchObservationScope
    candidates: tuple[SchemaMatchCandidate, ...] = ()
    scores: tuple[SchemaMatchScore, ...] = ()
    signals: tuple[SchemaMatchSignal, ...] = ()
    failures: tuple[SchemaMatchFailure, ...] = ()
    capabilities: tuple[SchemaMatchCapability, ...] = ()
    pruning: SchemaMatchPruningSummary
    evaluations: tuple[SchemaMatchEvaluation, ...] = ()
    artifacts: tuple[SchemaMatchArtifactReference, ...] = ()
    status: SchemaMatchStatus

    @model_validator(mode="after")
    def validate_completion(self) -> "SchemaMatchResult":
        if self.status is SchemaMatchStatus.COMPLETE and not self.capabilities:
            raise ValueError("complete matching result requires capability evidence")
        if self.status is SchemaMatchStatus.SKIPPED and self.request.source_ids:
            raise ValueError("multi-source matching cannot be skipped")
        return self


def schema_match_config_hash(request: SchemaMatchRequest) -> str:
    """Hash algorithm/configuration policy, excluding request and input identity."""

    return stable_digest({
        "mode": request.mode.value,
        "sample_mode": request.sample_mode,
        "sample_algorithm_version": request.sample_algorithm_version,
        "normalization_policy_version": request.normalization_policy_version,
        "abbreviation_dictionary_version": request.abbreviation_dictionary_version,
        "abbreviation_dictionary": request.abbreviation_dictionary,
        "matcher_references": [item.model_dump(mode="json") for item in request.matcher_references],
        "search_policy": request.search_policy.model_dump(mode="json"),
    })


def schema_match_candidate_id(left: tuple[str, str, str], right: tuple[str, str, str]) -> str:
    endpoints = tuple(sorted((left, right)))
    return "schema_candidate_" + hashlib.sha256(json.dumps(endpoints, separators=(",", ":")).encode()).hexdigest()[:32]


def schema_match_signal_id(family: SchemaMatchSignalFamily, left: str, right: str, value: Any) -> str:
    return "schema_signal_" + stable_digest({"family": family.value, "left": left, "right": right, "value": value})[:32]


def schema_match_score_id(matcher_id: str, left: str, right: str, rank: int) -> str:
    return "schema_score_" + stable_digest({"matcher": matcher_id, "left": left, "right": right, "rank": rank})[:32]
