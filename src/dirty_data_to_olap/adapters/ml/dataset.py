"""Grouped dataset construction, ranking metrics, and leakage controls."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from dirty_data_to_olap.domain.contracts.applied_ml import (
    MLDatasetManifest,
    MLDatasetRow,
    MLFeatureSchema,
    MLFeatureVector,
    MLTrainingLabel,
    MLSplitManifest,
    RankingMetrics,
    ml_dataset_fingerprint,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest


def build_dataset(
    feature_vectors: Iterable[MLFeatureVector],
    labels: Iterable[MLTrainingLabel],
    feature_schema: MLFeatureSchema,
    *,
    dataset_id: str = "relationship-ranker-benchmark",
    label_source: str = "explicit_benchmark_labels",
) -> tuple[tuple[MLDatasetRow, ...], MLDatasetManifest]:
    vectors = {vector.candidate_id: vector for vector in feature_vectors}
    label_items = tuple(labels)
    if len({label.candidate_id for label in label_items}) != len(label_items):
        raise ValueError("each candidate must have exactly one explicit label")
    rows: list[MLDatasetRow] = []
    for label in sorted(label_items, key=lambda item: item.candidate_id):
        vector = vectors.get(label.candidate_id)
        if vector is None:
            raise ValueError(f"label has no feature vector: {label.candidate_id}")
        rows.append(
            MLDatasetRow(
                row_id=f"row_{label.label_id}",
                feature_vector=vector,
                label=label,
            )
        )
    built = tuple(rows)
    groups = {row.label.group_id for row in built}
    manifest = MLDatasetManifest(
        dataset_id=dataset_id,
        task=feature_schema.task,
        row_count=len(built),
        positive_count=sum(row.label.label for row in built),
        negative_count=sum(1 - row.label.label for row in built),
        group_count=len(groups),
        feature_schema_id=feature_schema.schema_id,
        dataset_fingerprint=ml_dataset_fingerprint(built, feature_schema),
        label_fingerprint=stable_digest(
            [row.label.model_dump(mode="json") for row in built]
        ),
        label_source=label_source,
        leakage_controls=(
            "labels are supplied separately from feature construction",
            "base scenario groups are held out together",
            "reverse-direction logical pairs share one group",
            "identifiers are provenance only and excluded from numeric features",
        ),
        scope_summary={"dataset_scope": "synthetic benchmark only"},
    )
    return built, manifest


def make_grouped_split(
    rows: tuple[MLDatasetRow, ...],
    *,
    seed: int = 20260910,
) -> MLSplitManifest:
    """Assign complete base-scenario groups deterministically to train/valid/test."""

    if not rows:
        raise ValueError("cannot split an empty dataset")
    by_group: dict[str, list[MLDatasetRow]] = defaultdict(list)
    by_pair: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_group[row.label.group_id].append(row)
        by_pair[row.label.logical_pair_key].add(row.label.group_id)
    if any(len(groups) != 1 for groups in by_pair.values()):
        raise ValueError("reverse or duplicate logical pairs cross split groups")
    groups = sorted(by_group)
    assignments: dict[str, str] = {}
    for index, group in enumerate(groups):
        if len(groups) >= 3:
            split = ("train", "validation", "test")[index % 3]
        elif len(groups) == 2:
            split = ("train", "test")[index % 2]
        else:
            split = "train"
        for row in by_group[group]:
            assignments[row.row_id] = split
    limitations: list[str] = []
    if len(groups) < 3:
        limitations.append("fewer than three independent groups; validation/test coverage is limited")
    if len({row.label.label for row in rows if assignments[row.row_id] == "train"}) < 2:
        limitations.append("training split does not contain both classes")
    reverse_groups = {
        pair_key: next(iter(group_ids)) for pair_key, group_ids in by_pair.items()
    }
    return MLSplitManifest(
        split_id=f"grouped_{stable_digest({'groups': groups, 'seed': seed})[:16]}",
        strategy="deterministic_grouped_base_scenario_modulo",
        seed=seed,
        assignments=assignments,
        row_group_by_id={row.row_id: row.label.group_id for row in rows},
        reverse_pair_group_by_key=reverse_groups,
        split_fingerprint=stable_digest(
            {
                "assignments": assignments,
                "groups": groups,
                "seed": seed,
            }
        ),
        limitations=tuple(limitations),
    )


def _dcg(relevances: list[int]) -> float:
    return sum(
        relevance / math.log2(index + 2)
        for index, relevance in enumerate(relevances)
    )


def ranking_metrics(
    rows: Iterable[MLDatasetRow],
    score_fn: Callable[[MLFeatureVector], float],
    *,
    split: str | None = None,
    split_manifest: MLSplitManifest | None = None,
) -> RankingMetrics:
    all_rows = tuple(rows)
    grouped: dict[str, list[tuple[MLDatasetRow, float]]] = defaultdict(list)
    selected = []
    for row in all_rows:
        if split_manifest is not None and split is not None:
            if split_manifest.assignments.get(row.row_id) != split:
                continue
        selected.append(row)
        grouped[row.label.group_id].append((row, float(score_fn(row.feature_vector))))
    if not grouped:
        return RankingMetrics(
            recall_at_k={"1": 0.0, "3": 0.0},
            mean_reciprocal_rank=0.0,
            ndcg_at_k={"1": 0.0, "3": 0.0},
            candidate_coverage=0.0,
            hard_negative_exposure=0,
        )
    recalls = {1: [], 3: []}
    ndcgs = {1: [], 3: []}
    reciprocal: list[float] = []
    hard_negative_exposure = 0
    for items in grouped.values():
        ranked = sorted(items, key=lambda item: (-item[1], item[0].row_id))
        relevance = [row.label.label for row, _ in ranked]
        positive_positions = [i for i, value in enumerate(relevance) if value]
        first = positive_positions[0] if positive_positions else None
        reciprocal.append(0.0 if first is None else 1.0 / (first + 1))
        if first is not None:
            hard_negative_exposure += first
        ideal = sorted(relevance, reverse=True)
        for k in (1, 3):
            recalls[k].append(float(any(relevance[:k])))
            denominator = _dcg(ideal[:k])
            ndcgs[k].append(0.0 if denominator == 0 else _dcg(relevance[:k]) / denominator)
    return RankingMetrics(
        recall_at_k={str(k): sum(values) / len(values) for k, values in recalls.items()},
        mean_reciprocal_rank=sum(reciprocal) / len(reciprocal),
        ndcg_at_k={str(k): sum(values) / len(values) for k, values in ndcgs.items()},
        candidate_coverage=len(selected) / max(1, len(all_rows)),
        hard_negative_exposure=hard_negative_exposure,
    )
