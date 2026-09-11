"""Versioned contracts for offline inference evaluation artifacts.

Truth is represented by these evaluation contracts only. Runtime inference
contracts do not import this package or its benchmark labels.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from dirty_data_to_olap.domain.contracts.source import _SourceModel, stable_digest


class EvaluationTask(str, Enum):
    RELATIONSHIP_CANDIDATE_GENERATION = "RELATIONSHIP_CANDIDATE_GENERATION"
    RELATIONSHIP_FUSION = "RELATIONSHIP_FUSION"
    RELATIONSHIP_RANKING = "RELATIONSHIP_RANKING"
    SCHEMA_MATCHING = "SCHEMA_MATCHING"
    ENTITY_RESOLUTION_PAIRWISE = "ENTITY_RESOLUTION_PAIRWISE"
    ENTITY_RESOLUTION_CLUSTERS = "ENTITY_RESOLUTION_CLUSTERS"
    APPLIED_ML_RANKING = "APPLIED_ML_RANKING"
    SEMANTIC_AI_QUALITY = "SEMANTIC_AI_QUALITY"


class GroundTruthKind(str, Enum):
    RELATIONSHIP = "RELATIONSHIP"
    SCHEMA_MAPPING = "SCHEMA_MAPPING"
    ENTITY_CLUSTER = "ENTITY_CLUSTER"
    LABEL = "LABEL"


class CorruptionKind(str, Enum):
    MISSING_DECLARED_FK = "MISSING_DECLARED_FK"
    ORPHAN_RATE = "ORPHAN_RATE"
    TARGET_DUPLICATE = "TARGET_DUPLICATE"
    NULL_HEAVY = "NULL_HEAVY"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    LOW_CARDINALITY_TRAP = "LOW_CARDINALITY_TRAP"
    SAME_NAME_DIFFERENT_MEANING = "SAME_NAME_DIFFERENT_MEANING"
    RENAMED_COLUMN = "RENAMED_COLUMN"
    COMPOSITE_KEY = "COMPOSITE_KEY"
    PARTIAL_COMPOSITE_KEY = "PARTIAL_COMPOSITE_KEY"
    MULTIPLE_TARGET = "MULTIPLE_TARGET"
    MULTILINGUAL = "MULTILINGUAL"
    NO_MATCH = "NO_MATCH"
    COMMON_NAME = "COMMON_NAME"
    PLACEHOLDER = "PLACEHOLDER"
    SHARED_HOUSEHOLD = "SHARED_HOUSEHOLD"
    MISSING_FIELDS = "MISSING_FIELDS"
    TRANSITIVE_BRIDGE = "TRANSITIVE_BRIDGE"


class InferenceEvaluationExample(_SourceModel):
    example_id: str = Field(min_length=1)
    scenario_group_id: str = Field(min_length=1)
    task: EvaluationTask
    query_id: str = Field(min_length=1)
    candidate_id: str | None = None
    score: float | None = None
    label: int | None = Field(default=None, ge=0, le=1)
    predicted: int | None = Field(default=None, ge=0, le=1)
    corruption_tags: tuple[CorruptionKind, ...] = ()
    slice_tags: tuple[str, ...] = ()
    conflict: bool = False
    incomplete: bool = False
    evidence_refs: tuple[str, ...] = ()
    baseline_scores: Mapping[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_truth_in_inference_example(self) -> "InferenceEvaluationExample":
        if self.label is not None and self.task not in {
            EvaluationTask.RELATIONSHIP_CANDIDATE_GENERATION,
            EvaluationTask.RELATIONSHIP_FUSION,
            EvaluationTask.RELATIONSHIP_RANKING,
            EvaluationTask.SCHEMA_MATCHING,
            EvaluationTask.ENTITY_RESOLUTION_PAIRWISE,
            EvaluationTask.ENTITY_RESOLUTION_CLUSTERS,
            EvaluationTask.APPLIED_ML_RANKING,
        }:
            raise ValueError("labels are evaluation outputs and cannot be runtime examples")
        return self


class EvaluationDatasetManifest(_SourceModel):
    schema_version: str = "1"
    dataset_id: str
    generator_version: str
    seed: int
    scenario_group_ids: tuple[str, ...] = Field(min_length=1)
    source_types: Mapping[str, str]
    corruption_types: tuple[str, ...]
    truth_artifact_hashes: Mapping[str, str]
    runtime_fixture_hashes: Mapping[str, str]
    inference_component_versions: Mapping[str, str]
    frozen_policy_hashes: Mapping[str, str]
    record_counts: Mapping[str, int] = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class SplitManifest(_SourceModel):
    schema_version: str = "1"
    split_id: str
    algorithm: str
    seed: int
    group_ids_by_split: Mapping[str, tuple[str, ...]]
    example_ids_by_split: Mapping[str, tuple[str, ...]]
    class_counts_by_split: Mapping[str, Mapping[str, int]]
    slice_counts_by_split: Mapping[str, Mapping[str, int]]
    truth_fingerprint: str
    leakage_audit: Mapping[str, Any]

    @model_validator(mode="after")
    def groups_are_disjoint(self) -> "SplitManifest":
        groups = [set(values) for values in self.group_ids_by_split.values()]
        for index, left in enumerate(groups):
            for right in groups[index + 1:]:
                if left.intersection(right):
                    raise ValueError("scenario group crosses evaluation splits")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class MetricValue(_SourceModel):
    value: float | None
    numerator: int | None = None
    denominator: int | None = None
    undefined_reason: str | None = None

    @model_validator(mode="after")
    def explicit_undefined(self) -> "MetricValue":
        if self.value is None and not self.undefined_reason:
            raise ValueError("undefined metrics require a reason")
        return self


class ClassificationMetrics(_SourceModel):
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int | None
    precision: MetricValue
    recall: MetricValue
    f1: MetricValue
    average_precision: MetricValue
    evaluated_count: int
    positive_count: int


class RankingMetrics(_SourceModel):
    recall_at_k: Mapping[str, MetricValue]
    mean_reciprocal_rank: MetricValue
    ndcg_at_k: Mapping[str, MetricValue]
    eligible_query_count: int
    ineligible_single_candidate_query_count: int
    no_positive_query_count: int
    mean_candidate_count: MetricValue
    hard_negative_count: int


class EntityResolutionMetrics(_SourceModel):
    pairwise: ClassificationMetrics
    evaluated_pair_count: int
    false_merge_pair_count: int
    false_merge_rate: MetricValue
    contaminated_cluster_count: int
    largest_false_merge_cluster: int
    truth_clusters_split: int
    false_split_rate: MetricValue
    mean_predicted_cluster_purity: MetricValue
    mean_truth_cluster_completeness: MetricValue
    evaluated_record_universe_count: int


class BootstrapInterval(_SourceModel):
    metric_id: str
    point_estimate: float | None
    lower: float | None
    upper: float | None
    confidence_level: float
    replicate_count: int
    group_count: int
    method: str
    status: str
    seed: int


class ThresholdPoint(_SourceModel):
    threshold: float
    eligible_examples: int
    auto_accept_coverage: MetricValue
    precision: MetricValue
    false_positive_count: int
    recall: MetricValue
    review_rate: MetricValue
    abstention_rate: MetricValue
    worst_slice_precision: MetricValue


class ThresholdStudy(_SourceModel):
    study_id: str
    source_score_semantics: str
    split: str
    points: tuple[ThresholdPoint, ...]
    status: str
    selected_threshold: float | None
    selection_reason: str
    runtime_policy_mutated: bool = False


class EvaluationArtifactReference(_SourceModel):
    artifact_id: str
    relative_path: str
    content_hash: str
    artifact_type: str
    schema_version: str = "1"


class InferenceValidityStatus(str, Enum):
    REVIEW_ONLY_VALIDATED = "REVIEW_ONLY_VALIDATED"
    AUTOMATION_NOT_JUSTIFIED = "AUTOMATION_NOT_JUSTIFIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BLOCKED = "BLOCKED"


class FormalGateStatus(str, Enum):
    """Formal G5 state; review mode is intentionally a separate field."""

    PASS = "PASS"
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"


class ProviderEvaluationBinding(_SourceModel):
    """Evidence binding required before a provider result can score a task."""

    component: str
    receipt_path: str
    output_path: str
    receipt_output_hash: str
    loaded_output_hash: str
    content_commit: str
    protocol_hash: str
    dataset_manifest_hash: str
    scenario_fixture_hashes: Mapping[str, str]
    scenario_group_ids: tuple[str, ...] = ()
    truth_artifact_hash: str
    split_hash: str
    status: str
    loaded_for_metrics: bool = False
    population_match: bool = False
    failure_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def hash_contract(self) -> "ProviderEvaluationBinding":
        if self.status == "EXECUTED" and (not self.loaded_for_metrics or not self.population_match):
            raise ValueError("EXECUTED provider binding must be loaded and population-aligned")
        return self


class InferenceValidityAssessment(_SourceModel):
    assessment_id: str
    protocol_hash: str
    dataset_hash: str
    split_hash: str
    evaluated_task_ids: tuple[EvaluationTask, ...]
    task_report_refs: Mapping[str, str]
    held_out_test_status: str
    leakage_audit: Mapping[str, Any]
    calibration_status: str
    threshold_study_status: str
    automation_recommendation: str
    limitations: tuple[str, ...]
    gate_recommendation: InferenceValidityStatus
    required_provider_status: Mapping[str, str]
    formal_gate: FormalGateStatus = FormalGateStatus.PENDING
    inference_validity_mode: str = "UNVALIDATED"
    provider_bindings: Mapping[str, ProviderEvaluationBinding] = Field(default_factory=dict)
    negative_controls: Mapping[str, str] = Field(default_factory=dict)
    score_semantics: str = "UNCALIBRATED_DECISION_SCORE"
    automation_enabled: bool = False

    @model_validator(mode="after")
    def safe_automation_boundary(self) -> "InferenceValidityAssessment":
        if self.automation_enabled or self.automation_recommendation != "NOT_AUTHORIZED":
            raise ValueError("Step18 assessment cannot authorize automation")
        if self.gate_recommendation is InferenceValidityStatus.REVIEW_ONLY_VALIDATED and self.held_out_test_status != "EXECUTED_UNTOUCHED_BY_TUNING":
            raise ValueError("review-only validation requires an untouched held-out test")
        if self.formal_gate is FormalGateStatus.PASS and self.inference_validity_mode != "REVIEW_ONLY_VALIDATED":
            raise ValueError("formal G5 PASS requires an explicit review-only mode")
        return self


class EvaluationReport(_SourceModel):
    report_id: str
    protocol_hash: str
    dataset_hash: str
    split_hash: str
    git_content_commit: str
    generated_artifact_refs: tuple[EvaluationArtifactReference, ...]
    task_metrics: Mapping[str, Any]
    slice_metrics: Mapping[str, Any]
    errors: tuple[Mapping[str, Any], ...]
    bootstrap: tuple[BootstrapInterval, ...]
    assessment: InferenceValidityAssessment
