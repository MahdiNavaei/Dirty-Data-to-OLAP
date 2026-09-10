"""Step15 baseline-first orchestration for learned relationship evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from dirty_data_to_olap.adapters.ml.dataset import (
    build_dataset,
    make_grouped_split,
    ranking_metrics,
)
from dirty_data_to_olap.adapters.ml.features import (
    build_feature_vector,
    default_feature_schema,
)
from dirty_data_to_olap.adapters.ml.sklearn_ranking import (
    OptionalMLUnavailable,
    SklearnRelationshipRanker,
)
from dirty_data_to_olap.domain.contracts.applied_ml import (
    ActiveLearningReason,
    ActiveLearningSuggestion,
    AppliedMLResult,
    MLCapability,
    MLCalibrationExperiment,
    MLCalibrationStatus,
    MLDatasetRow,
    MLFailure,
    MLFailureKind,
    MLModelStatus,
    MLStageStatus,
    MLBaselineEvaluation,
    MLRankStabilityObservation,
    MLTask,
    LearnedRankingEvidence,
    MLTrainingLabel,
    RankingMetrics,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest


def _value(vector: Any, feature_id: str) -> float:
    return float(vector.values.get(feature_id, 0.0))


def _structural_score(vector: Any) -> float:
    return _value(vector, "inclusion_coverage") if "inclusion_coverage" not in vector.missing_feature_ids else 0.0


def _uniqueness_score(vector: Any) -> float:
    return _value(vector, "target_uniqueness_ratio") if "target_uniqueness_ratio" not in vector.missing_feature_ids else 0.0


def _declared_score(vector: Any) -> float:
    return _value(vector, "declared_fk_present") if "declared_fk_present" not in vector.missing_feature_ids else 0.0


def _matcher_rank_score(vector: Any) -> float:
    ranks = [_value(vector, key) for key in vector.values if key.endswith("_rank") and not key.endswith("_missing") and key.startswith("matcher_") and key not in vector.missing_feature_ids]
    return 1.0 / min(ranks) if ranks else 0.0


_BASELINES = (
    ("ind_coverage", "IND coverage baseline", _structural_score),
    ("target_uniqueness", "target uniqueness baseline", _uniqueness_score),
    ("declared_constraint", "declared constraint baseline", _declared_score),
    ("individual_matcher_rank", "individual matcher rank baseline", _matcher_rank_score),
)


def _zero_metrics() -> RankingMetrics:
    return RankingMetrics(
        recall_at_k={"1": 0.0, "3": 0.0},
        mean_reciprocal_rank=0.0,
        ndcg_at_k={"1": 0.0, "3": 0.0},
        candidate_coverage=0.0,
        hard_negative_exposure=0,
    )


class AppliedMLService:
    """Optional learned evidence; it never emits acceptance or canonical truth."""

    def __init__(
        self,
        *,
        artifact_root: Path | None = None,
        random_seed: int = 20260910,
        max_active_learning_suggestions: int = 5,
    ) -> None:
        self.artifact_root = artifact_root
        self.random_seed = random_seed
        self.max_active_learning_suggestions = max_active_learning_suggestions

    def rank(
        self,
        candidates: Iterable[Any],
        *,
        labels: Iterable[MLTrainingLabel] | None = None,
        schema_candidates: Any = None,
        schema_scores: Any = None,
        schema_signals: Any = None,
        profiles: Any = None,
        quality: Any = None,
        dependencies: Any = None,
        declared_fk_by_candidate: Mapping[str, bool | None] | None = None,
    ) -> AppliedMLResult:
        feature_schema = default_feature_schema()
        candidate_items = tuple(candidates)
        vectors = tuple(
            build_feature_vector(
                candidate,
                schema_candidates=schema_candidates,
                schema_scores=schema_scores,
                schema_signals=schema_signals,
                profiles=profiles,
                quality=quality,
                dependencies=dependencies,
                declared_fk_present=(
                    declared_fk_by_candidate or {}
                ).get(str(candidate.candidate_id)),
            )
            for candidate in candidate_items
        )
        capability_available, engine, engine_version = SklearnRelationshipRanker.capability()
        capability = MLCapability(
            capability_id="step15-sklearn-ranking",
            available=capability_available,
            engine=engine,
            version=engine_version,
            detail=(
                "optional local transparent logistic ranking adapter"
                if capability_available
                else engine_version
            ),
        )
        dataset_rows: tuple[MLDatasetRow, ...] = ()
        dataset_manifest = None
        split_manifest = None
        baseline_metrics = _zero_metrics()
        baselines = tuple(
            MLBaselineEvaluation(
                baseline_id=baseline_id,
                name=name,
                metrics=baseline_metrics,
                ranked_candidate_ids=tuple(vector.candidate_id for vector in sorted(vectors, key=lambda item: (-score_fn(item), item.candidate_id))),
                limitations=("transparent single-family ranking aid only", "missing aggregate evidence is not treated as positive evidence"),
            ) for baseline_id, name, score_fn in _BASELINES
        )
        if labels is None:
            return AppliedMLResult(
                task=MLTask.RELATIONSHIP_CANDIDATE_RANKING,
                status=MLStageStatus.SKIPPED_NOT_CONFIGURED,
                feature_schema=feature_schema,
                baselines=baselines,
                capability=capability,
                limitations=(
                    "no explicit benchmark labels were supplied",
                    "learned ranking is not trained on operational data",
                ),
            )
        try:
            dataset_rows, dataset_manifest = build_dataset(
                vectors, labels, feature_schema
            )
            split_manifest = make_grouped_split(
                dataset_rows, seed=self.random_seed
            )
            baseline_metrics = ranking_metrics(
                dataset_rows,
                _structural_score,
                split_manifest=split_manifest,
                split="test",
            )
            baselines = tuple(item.model_copy(update={"metrics": ranking_metrics(dataset_rows, score_fn, split_manifest=split_manifest, split="test")}) for item, (_, _, score_fn) in zip(baselines, _BASELINES))
        except ValueError as exc:
            return AppliedMLResult(
                task=MLTask.RELATIONSHIP_CANDIDATE_RANKING,
                status=MLStageStatus.FAILED,
                feature_schema=feature_schema,
                baselines=baselines,
                capability=capability,
                failures=(
                    MLFailure(
                        failure_id="dataset-validation",
                        kind=MLFailureKind.INVALID_DATASET,
                        detail=str(exc),
                    ),
                ),
            )
        if not capability_available:
            return AppliedMLResult(
                task=MLTask.RELATIONSHIP_CANDIDATE_RANKING,
                status=MLStageStatus.UNAVAILABLE,
                feature_schema=feature_schema,
                dataset_manifest=dataset_manifest,
                split_manifest=split_manifest,
                baselines=baselines,
                capability=capability,
                failures=(
                    MLFailure(
                        failure_id="sklearn-unavailable",
                        kind=MLFailureKind.CAPABILITY_UNAVAILABLE,
                        detail=engine_version,
                    ),
                ),
                limitations=("deterministic baseline retained",),
            )
        ranker = SklearnRelationshipRanker(
            feature_schema, random_seed=self.random_seed
        )
        artifact_path = (
            self.artifact_root / "relationship_ranker.json"
            if self.artifact_root is not None
            else None
        )
        try:
            model = ranker.train(
                dataset_rows,
                split_manifest=split_manifest,
                dataset_manifest=dataset_manifest,
                model_id=f"relationship-ranker-{stable_digest(dataset_manifest.dataset_fingerprint)[:16]}",
                artifact_path=artifact_path,
            )
        except (OptionalMLUnavailable, ValueError, RuntimeError) as exc:
            kind = (
                MLFailureKind.CAPABILITY_UNAVAILABLE
                if isinstance(exc, OptionalMLUnavailable)
                else MLFailureKind.TRAINING_FAILED
            )
            return AppliedMLResult(
                task=MLTask.RELATIONSHIP_CANDIDATE_RANKING,
                status=MLStageStatus.INSUFFICIENT_DATA
                if "both positive and negative" in str(exc)
                else MLStageStatus.FAILED,
                feature_schema=feature_schema,
                dataset_manifest=dataset_manifest,
                split_manifest=split_manifest,
                baselines=baselines,
                capability=capability,
                failures=(
                    MLFailure(failure_id="model-training", kind=kind, detail=str(exc)),
                ),
                limitations=("deterministic baseline retained",),
            )
        score_by_candidate = {
            vector.candidate_id: ranker.score(vector) for vector in vectors
        }
        model = model.model_copy(
            update={
                "training_metrics": ranking_metrics(
                    dataset_rows,
                    ranker.score,
                    split_manifest=split_manifest,
                    split="train",
                ),
                "validation_metrics": ranking_metrics(dataset_rows, ranker.score, split_manifest=split_manifest, split="validation"),
                "test_metrics": ranking_metrics(dataset_rows, ranker.score, split_manifest=split_manifest, split="test"),
                "reverse_pair_leakage_status": "PASS_GROUPS_SHARED_BY_LOGICAL_PAIR",
            }
        )
        shuffled_rows = tuple(row.model_copy(update={"label": row.label.model_copy(update={"label": 1 - row.label.label})}) for row in dataset_rows)
        shuffle_metrics = None
        try:
            shuffled_ranker = SklearnRelationshipRanker(feature_schema, random_seed=self.random_seed + 1)
            shuffled_ranker.train(shuffled_rows, split_manifest=split_manifest, dataset_manifest=dataset_manifest, model_id="label-shuffle-control")
            shuffle_metrics = ranking_metrics(shuffled_rows, shuffled_ranker.score, split_manifest=split_manifest, split="test")
        except (OptionalMLUnavailable, ValueError, RuntimeError):
            shuffle_metrics = None
        model = model.model_copy(update={"label_shuffle_test_metrics": shuffle_metrics})
        ranked = sorted(
            vectors,
            key=lambda vector: (-score_by_candidate[vector.candidate_id], vector.candidate_id),
        )
        rank_by_candidate = {
            vector.candidate_id: index for index, vector in enumerate(ranked, start=1)
        }
        structural_rank = {vector.candidate_id: index for index, vector in enumerate(sorted(vectors, key=lambda item: (-_structural_score(item), item.candidate_id)), start=1)}
        learned = tuple(
            LearnedRankingEvidence(
                evidence_id=f"learned_{stable_digest((model.specification.model_id, vector.candidate_id))[:24]}",
                candidate_id=vector.candidate_id,
                model_id=model.specification.model_id,
                feature_schema_id=feature_schema.schema_id,
                ranking_score=score_by_candidate[vector.candidate_id],
                rank=rank_by_candidate[vector.candidate_id],
                model_status=MLModelStatus.EXPERIMENTAL,
                input_evidence_refs=tuple(ref.artifact_id for ref in vector.feature_source_refs),
                missing_feature_ids=vector.missing_feature_ids,
                risk_flags=vector.risk_flags,
            )
            for vector in ranked
        )
        contributions = tuple(
            item
            for vector in vectors
            for item in ranker.contributions(vector)
        )
        stability = tuple(
            MLRankStabilityObservation(
                observation_id=f"stability_{stable_digest(vector.candidate_id)[:24]}",
                candidate_id=vector.candidate_id,
                score_mean=score_by_candidate[vector.candidate_id],
                score_stddev=0.0,
                rank_mean=float(rank_by_candidate[vector.candidate_id]),
                rank_stddev=0.0,
                top_rank_frequency=1.0 if rank_by_candidate[vector.candidate_id] <= 3 else 0.0,
                methods=("INSUFFICIENT_STABILITY_EVIDENCE",),
            )
            for vector in ranked
        )
        suggestions = tuple(
            ActiveLearningSuggestion(
                suggestion_id=f"label_query_{stable_digest(vector.candidate_id)[:24]}",
                candidate_id=vector.candidate_id,
                priority_score=float(
                    abs(rank_by_candidate[vector.candidate_id] - structural_rank[vector.candidate_id])
                    + (0.5 if vector.missing_feature_ids else 0.0)
                    + (0.5 if vector.risk_flags else 0.0)
                ),
                reasons=tuple(
                    [ActiveLearningReason.MODEL_BASELINE_DISAGREEMENT]
                    if abs(rank_by_candidate[vector.candidate_id] - structural_rank[vector.candidate_id]) >= 2
                    else [ActiveLearningReason.MISSING_CRITICAL_FEATURES]
                    if vector.missing_feature_ids
                    else [ActiveLearningReason.HARD_CONFLICT]
                ),
                model_id=model.specification.model_id,
                max_suggestions_policy=self.max_active_learning_suggestions,
            )
            for vector in sorted(
                vectors,
                key=lambda item: (
                    -abs(rank_by_candidate[item.candidate_id] - structural_rank[item.candidate_id]),
                    item.candidate_id,
                ),
            )[: self.max_active_learning_suggestions]
            if vector.missing_feature_ids or vector.risk_flags or abs(rank_by_candidate[vector.candidate_id] - structural_rank[vector.candidate_id]) >= 2
        )
        calibration = MLCalibrationExperiment(
            experiment_id=f"calibration_{model.specification.model_id}",
            method="grouped_holdout_calibration_experiment_not_applied",
            status=(
                MLCalibrationStatus.CALIBRATION_EXPERIMENT
                if len([row for row in dataset_rows if split_manifest.assignments.get(row.row_id) in {"validation", "test"}]) >= 5 and len({row.label.group_id for row in dataset_rows if split_manifest.assignments.get(row.row_id) in {"validation", "test"}}) >= 2 and {row.label.label for row in dataset_rows if split_manifest.assignments.get(row.row_id) in {"validation", "test"}} == {0, 1}
                else MLCalibrationStatus.INSUFFICIENT_CALIBRATION_DATA
            ),
            calibration_rows=sum(
                1
                for row in dataset_rows
                if split_manifest.assignments.get(row.row_id) in {"validation", "test"}
            ),
            limitations=(
                "calibration does not change or relabel the ranking score",
                "no probability is emitted by the Step15 contract",
            ),
        )
        return AppliedMLResult(
            task=MLTask.RELATIONSHIP_CANDIDATE_RANKING,
            status=MLStageStatus.EXECUTED_EXPERIMENTAL,
            feature_schema=feature_schema,
            dataset_manifest=dataset_manifest,
            split_manifest=split_manifest,
            baselines=baselines,
            model=model,
            learned_evidence=learned,
            contributions=contributions,
            stability=stability,
            active_learning=suggestions,
            calibration=calibration,
            capability=capability,
            limitations=(
                "learned outputs are candidate ranking evidence only",
                "no acceptance, canonicalization, or production probability is emitted",
            ),
        )
