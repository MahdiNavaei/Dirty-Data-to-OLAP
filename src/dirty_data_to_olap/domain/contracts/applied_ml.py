"""Project-owned contracts for optional, explainable learned evidence.

The Step15 boundary consumes aggregate evidence contracts only.  IDs and raw
values may be retained as provenance/grouping metadata, but never enter the
numeric feature vector or persisted model artifact.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .source import _SourceModel, stable_digest


class MLTask(str, Enum):
    RELATIONSHIP_CANDIDATE_RANKING = "RELATIONSHIP_CANDIDATE_RANKING"


class MLModelStatus(str, Enum):
    BENCHMARK_ONLY = "BENCHMARK_ONLY"
    EXPERIMENTAL = "EXPERIMENTAL"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    REJECTED = "REJECTED"


class MLStageStatus(str, Enum):
    EXECUTED_EXPERIMENTAL = "EXECUTED_EXPERIMENTAL"
    SKIPPED_NOT_CONFIGURED = "SKIPPED_NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"


class MLFailureKind(str, Enum):
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    INSUFFICIENT_TRAINING_DATA = "INSUFFICIENT_TRAINING_DATA"
    INVALID_DATASET = "INVALID_DATASET"
    LABEL_LEAKAGE_DETECTED = "LABEL_LEAKAGE_DETECTED"
    SPLIT_INVALID = "SPLIT_INVALID"
    FEATURE_SCHEMA_MISMATCH = "FEATURE_SCHEMA_MISMATCH"
    TRAINING_FAILED = "TRAINING_FAILED"
    INFERENCE_FAILED = "INFERENCE_FAILED"
    CALIBRATION_INSUFFICIENT = "CALIBRATION_INSUFFICIENT"
    MODEL_ARTIFACT_INVALID = "MODEL_ARTIFACT_INVALID"
    OOD_FEATURES = "OOD_FEATURES"


class MLFeatureFamily(str, Enum):
    DEPENDENCY = "DEPENDENCY"
    SCHEMA_MATCH = "SCHEMA_MATCH"
    PROFILE = "PROFILE"
    QUALITY = "QUALITY"
    DECLARED_METADATA = "DECLARED_METADATA"
    SCOPE = "SCOPE"


class MLCalibrationStatus(str, Enum):
    CALIBRATION_EXPERIMENT = "CALIBRATION_EXPERIMENT"
    INSUFFICIENT_CALIBRATION_DATA = "INSUFFICIENT_CALIBRATION_DATA"


class ActiveLearningReason(str, Enum):
    MODEL_BASELINE_DISAGREEMENT = "MODEL_BASELINE_DISAGREEMENT"
    LOW_RANK_MARGIN = "LOW_RANK_MARGIN"
    HIGH_CROSS_FOLD_VARIANCE = "HIGH_CROSS_FOLD_VARIANCE"
    MISSING_CRITICAL_FEATURES = "MISSING_CRITICAL_FEATURES"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
    HARD_CONFLICT = "HARD_CONFLICT"


class MLFeatureDefinition(_SourceModel):
    feature_id: str = Field(min_length=1)
    family: MLFeatureFamily
    value_type: str = "float"
    minimum: float | None = None
    maximum: float | None = None
    source_contract: str = Field(min_length=1)
    missing_semantics: str = Field(min_length=1)
    version: str = "1"
    nullable: bool = True
    imputation_value: float = 0.0

    @model_validator(mode="after")
    def valid_definition(self) -> "MLFeatureDefinition":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("feature minimum cannot exceed maximum")
        if any(token in self.feature_id.lower() for token in ("source_id", "table_id", "column_id", "candidate_id", "snapshot_id", "record_ref", "fixture_case", "label", "ground_truth")):
            raise ValueError("identifiers and labels cannot be feature definitions")
        return self


class MLFeatureSchema(_SourceModel):
    schema_id: str = Field(min_length=1)
    version: str = "1"
    task: MLTask
    features: tuple[MLFeatureDefinition, ...] = Field(min_length=1)
    feature_order: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_schema(self) -> "MLFeatureSchema":
        ids = tuple(item.feature_id for item in self.features)
        order = self.feature_order or ids
        if len(set(ids)) != len(ids) or set(order) != set(ids) or len(order) != len(ids):
            raise ValueError("feature schema IDs and order must be unique and complete")
        object.__setattr__(self, "feature_order", order)
        return self


class MLFeatureSourceReference(_SourceModel):
    artifact_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    evidence_family: str = Field(min_length=1)
    scope_semantics: str = Field(min_length=1)
    content_hash: str | None = None


class MLFeatureVector(_SourceModel):
    candidate_id: str = Field(min_length=1)
    logical_pair_key: str = Field(min_length=1)
    values: Mapping[str, float]
    missing_feature_ids: tuple[str, ...] = ()
    feature_source_refs: tuple[MLFeatureSourceReference, ...] = ()
    scope_evidence: Mapping[str, str] = Field(default_factory=dict)
    risk_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def finite_numeric_values(self) -> "MLFeatureVector":
        if any(not math.isfinite(float(value)) for value in self.values.values()):
            raise ValueError("feature vectors require finite numeric values")
        if any(feature_id not in self.values for feature_id in self.missing_feature_ids):
            raise ValueError("missing feature indicators must refer to vector features")
        return self


class MLTrainingLabel(_SourceModel):
    label_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    logical_pair_key: str = Field(min_length=1)
    label: int = Field(ge=0, le=1)
    group_id: str = Field(min_length=1)
    base_scenario_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class MLDatasetRow(_SourceModel):
    row_id: str = Field(min_length=1)
    feature_vector: MLFeatureVector
    label: MLTrainingLabel

    @model_validator(mode="after")
    def aligned(self) -> "MLDatasetRow":
        if self.feature_vector.candidate_id != self.label.candidate_id or self.feature_vector.logical_pair_key != self.label.logical_pair_key:
            raise ValueError("dataset row feature and label identity mismatch")
        return self


class MLDatasetManifest(_SourceModel):
    dataset_id: str
    task: MLTask
    row_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    group_count: int = Field(ge=0)
    feature_schema_id: str
    dataset_fingerprint: str
    label_fingerprint: str
    evidence_artifact_hashes: tuple[str, ...] = ()
    label_source: str
    leakage_controls: tuple[str, ...] = Field(min_length=1)
    scope_summary: Mapping[str, str] = Field(default_factory=dict)


class MLSplitManifest(_SourceModel):
    split_id: str
    strategy: str
    seed: int
    assignments: Mapping[str, str]
    row_group_by_id: Mapping[str, str]
    reverse_pair_group_by_key: Mapping[str, str] = Field(default_factory=dict)
    split_fingerprint: str
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def no_group_overlap(self) -> "MLSplitManifest":
        groups_by_split: dict[str, set[str]] = {}
        for row_id, split in self.assignments.items():
            group = self.row_group_by_id.get(row_id)
            if group is None:
                raise ValueError("every split row requires a group")
            groups_by_split.setdefault(split, set()).add(group)
        splits = list(groups_by_split)
        for index, left in enumerate(splits):
            for right in splits[index + 1:]:
                if groups_by_split[left] & groups_by_split[right]:
                    raise ValueError("group leakage across split assignments")
        for pair_key, group in self.reverse_pair_group_by_key.items():
            if group not in set(self.row_group_by_id.values()):
                raise ValueError(f"reverse pair group is not represented: {pair_key}")
        return self


class RankingMetrics(_SourceModel):
    recall_at_k: Mapping[str, float]
    mean_reciprocal_rank: float = Field(ge=0)
    ndcg_at_k: Mapping[str, float] = Field(default_factory=dict)
    candidate_coverage: float = Field(ge=0, le=1)
    hard_negative_exposure: int = Field(ge=0)


class MLBaselineEvaluation(_SourceModel):
    baseline_id: str
    name: str
    score_semantics: str = "transparent_source_backed_ranking_score"
    metrics: RankingMetrics
    ranked_candidate_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class MLModelSpecification(_SourceModel):
    model_id: str
    task: MLTask
    estimator_family: str = "sklearn.linear_model.LogisticRegression"
    hyperparameters: Mapping[str, Any] = Field(default_factory=dict)
    random_seed: int = 0
    feature_schema_id: str
    model_config_hash: str
    dataset_fingerprint: str
    split_fingerprint: str
    sklearn_version: str
    status: MLModelStatus


class MLModelEvidence(_SourceModel):
    specification: MLModelSpecification
    class_labels: tuple[int, ...] = (0, 1)
    coefficients: tuple[float, ...] = ()
    intercept: float = 0.0
    artifact_location: str | None = None
    artifact_content_hash: str | None = None
    training_rows: int = Field(ge=0)
    training_groups: int = Field(ge=0)
    class_counts: Mapping[str, int] = Field(default_factory=dict)
    training_metrics: RankingMetrics | None = None
    validation_metrics: RankingMetrics | None = None
    test_metrics: RankingMetrics | None = None
    label_shuffle_test_metrics: RankingMetrics | None = None
    reverse_pair_leakage_status: str = "NOT_EVALUATED"
    per_fold_metrics: tuple[RankingMetrics, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def linear_artifact_is_safe(self) -> "MLModelEvidence":
        if self.specification.status not in {MLModelStatus.BENCHMARK_ONLY, MLModelStatus.EXPERIMENTAL, MLModelStatus.VALIDATION_REQUIRED}:
            raise ValueError("Step15 model cannot claim a trusted production status")
        if self.artifact_location and self.artifact_location.lower().endswith((".pkl", ".joblib")):
            raise ValueError("executable model persistence is forbidden")
        return self


class MLFeatureContribution(_SourceModel):
    candidate_id: str
    feature_id: str
    feature_value: float
    coefficient: float
    contribution: float
    direction: str
    missing: bool = False


class LearnedRankingEvidence(_SourceModel):
    evidence_id: str
    candidate_id: str
    model_id: str
    feature_schema_id: str
    ranking_score: float
    score_kind: str = "UNCALIBRATED_RANKING_SCORE"
    rank: int = Field(ge=1)
    model_status: MLModelStatus
    input_evidence_refs: tuple[str, ...] = ()
    missing_feature_ids: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    contribution_refs: tuple[str, ...] = ()
    rank_stability_ref: str | None = None

    @model_validator(mode="after")
    def evidence_only(self) -> "LearnedRankingEvidence":
        if self.score_kind != "UNCALIBRATED_RANKING_SCORE":
            raise ValueError("learned ranking evidence must remain explicitly uncalibrated")
        return self


class MLRankStabilityObservation(_SourceModel):
    observation_id: str
    candidate_id: str
    score_mean: float
    score_stddev: float = Field(ge=0)
    rank_mean: float
    rank_stddev: float = Field(ge=0)
    top_rank_frequency: float = Field(ge=0, le=1)
    methods: tuple[str, ...] = Field(min_length=1)


class MLCalibrationExperiment(_SourceModel):
    experiment_id: str
    method: str
    status: MLCalibrationStatus
    calibration_rows: int = Field(ge=0)
    grouped: bool = True
    brier_score: float | None = Field(default=None, ge=0)
    expected_calibration_error: float | None = Field(default=None, ge=0)
    limitations: tuple[str, ...] = ()


class ActiveLearningSuggestion(_SourceModel):
    suggestion_id: str
    candidate_id: str
    priority_score: float = Field(ge=0)
    reasons: tuple[ActiveLearningReason, ...] = Field(min_length=1)
    model_id: str
    max_suggestions_policy: int = Field(gt=0)
    labels_mutated: bool = False
    external_processing: bool = False

    @model_validator(mode="after")
    def non_mutating(self) -> "ActiveLearningSuggestion":
        if self.labels_mutated or self.external_processing:
            raise ValueError("active learning suggestions cannot mutate labels or send data externally")
        return self


class MLCapability(_SourceModel):
    capability_id: str
    available: bool
    engine: str
    version: str
    detail: str


class MLFailure(_SourceModel):
    failure_id: str
    kind: MLFailureKind
    detail: str
    retryable: bool = False


class AppliedMLResult(_SourceModel):
    task: MLTask
    status: MLStageStatus
    feature_schema: MLFeatureSchema
    dataset_manifest: MLDatasetManifest | None = None
    split_manifest: MLSplitManifest | None = None
    baselines: tuple[MLBaselineEvaluation, ...] = ()
    model: MLModelEvidence | None = None
    learned_evidence: tuple[LearnedRankingEvidence, ...] = ()
    contributions: tuple[MLFeatureContribution, ...] = ()
    stability: tuple[MLRankStabilityObservation, ...] = ()
    active_learning: tuple[ActiveLearningSuggestion, ...] = ()
    calibration: MLCalibrationExperiment | None = None
    capability: MLCapability
    failures: tuple[MLFailure, ...] = ()
    limitations: tuple[str, ...] = ()


def ml_model_config_hash(*, task: MLTask, feature_schema: MLFeatureSchema, estimator_family: str, hyperparameters: Mapping[str, Any], random_seed: int, sklearn_version: str, missing_value_policy: str) -> str:
    return stable_digest({
        "task": task.value,
        "feature_schema": {"id": feature_schema.schema_id, "version": feature_schema.version, "order": feature_schema.feature_order},
        "estimator_family": estimator_family,
        "hyperparameters": hyperparameters,
        "random_seed": random_seed,
        "missing_value_policy": missing_value_policy,
        "sklearn_version": sklearn_version,
    })


def ml_dataset_fingerprint(rows: tuple[MLDatasetRow, ...], feature_schema: MLFeatureSchema) -> str:
    return stable_digest({
        "feature_schema": feature_schema.model_dump(mode="json"),
        "rows": [row.model_dump(mode="json") for row in sorted(rows, key=lambda row: row.row_id)],
    })


def ml_linear_score(coefficients: tuple[float, ...], intercept: float, feature_order: tuple[str, ...], values: Mapping[str, float]) -> float:
    return float(intercept + sum(coefficient * float(values[feature_id]) for coefficient, feature_id in zip(coefficients, feature_order)))
