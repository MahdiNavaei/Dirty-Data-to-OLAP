"""Small, tested, dependency-light evaluation metrics."""

from __future__ import annotations

import math
import random
from itertools import combinations
from typing import Callable, Iterable, Mapping, Sequence

from .contracts import (
    BootstrapInterval,
    ClassificationMetrics,
    EntityResolutionMetrics,
    MetricValue,
    RankingMetrics,
)


def _metric(value: float | None, numerator: int | None, denominator: int | None, reason: str | None = None) -> MetricValue:
    return MetricValue(value=None if value is None else float(value), numerator=numerator, denominator=denominator, undefined_reason=reason if value is None else None)


def _safe_ratio(numerator: int, denominator: int, reason: str) -> MetricValue:
    return _metric(numerator / denominator, numerator, denominator) if denominator else _metric(None, numerator, denominator, reason)


def average_precision(rows: Iterable[tuple[str, int, float | None]]) -> MetricValue:
    ordered = sorted(rows, key=lambda item: (-(float(item[2]) if item[2] is not None else float("-inf")), item[0]))
    positives = sum(int(label) for _, label, _ in ordered)
    if not positives:
        return _metric(None, 0, len(ordered), "NO_POSITIVE_LABELS")
    hits = 0
    total = 0.0
    for index, (_, label, _) in enumerate(ordered, start=1):
        if label:
            hits += 1
            total += hits / index
    return _metric(total / positives, hits, positives)


def binary_classification_metrics(rows: Iterable[tuple[str, int, int, float | None]], *, universe_count: int | None = None) -> ClassificationMetrics:
    values = tuple(rows)
    tp = sum(1 for _, truth, pred, _ in values if truth == 1 and pred == 1)
    fp = sum(1 for _, truth, pred, _ in values if truth == 0 and pred == 1)
    fn = sum(1 for _, truth, pred, _ in values if truth == 1 and pred == 0)
    tn = sum(1 for _, truth, pred, _ in values if truth == 0 and pred == 0) if universe_count is None else universe_count - tp - fp - fn
    positives = sum(truth for _, truth, _, _ in values)
    return ClassificationMetrics(
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn if tn >= 0 else None,
        precision=_safe_ratio(tp, tp + fp, "NO_PREDICTED_POSITIVES"),
        recall=_safe_ratio(tp, tp + fn, "NO_TRUTH_POSITIVES"),
        f1=_metric(2 * tp / (2 * tp + fp + fn), 2 * tp, 2 * tp + fp + fn) if 2 * tp + fp + fn else _metric(None, 0, 0, "NO_POSITIVE_OR_PREDICTED_POSITIVES"),
        average_precision=average_precision(tuple((item[0], item[1], item[3]) for item in values)),
        evaluated_count=len(values),
        positive_count=positives,
    )


def _ndcg(relevance: Sequence[int], k: int) -> float | None:
    if not any(relevance):
        return None
    def dcg(values: Sequence[int]) -> float:
        return sum(value / math.log2(index + 2) for index, value in enumerate(values[:k]))
    ideal = dcg(sorted(relevance, reverse=True))
    return dcg(relevance) / ideal if ideal else None


def ranking_metrics(queries: Mapping[str, Sequence[tuple[str, float]]], truth: Mapping[str, set[str]], *, k_values: tuple[int, ...] = (1, 3, 5), hard_negative_ids: set[str] | None = None) -> RankingMetrics:
    eligible = 0
    single = 0
    no_positive = 0
    reciprocal: list[float] = []
    ndcg_values: dict[str, list[float]] = {str(k): [] for k in k_values}
    recall_values: dict[str, list[float]] = {str(k): [] for k in k_values}
    counts: list[int] = []
    hard_negative_count = 0
    for query_id, candidates in sorted(queries.items()):
        ranked = sorted(candidates, key=lambda item: (-float(item[1]), item[0]))
        counts.append(len(ranked))
        target = set(truth.get(query_id, set()))
        hard_negative_count += sum(1 for candidate_id, _ in ranked if hard_negative_ids and candidate_id in hard_negative_ids and candidate_id not in target)
        if not target:
            no_positive += 1
            continue
        if len(ranked) < 2:
            single += 1
            continue
        eligible += 1
        positions = [index + 1 for index, (candidate_id, _) in enumerate(ranked) if candidate_id in target]
        reciprocal.append(1.0 / min(positions) if positions else 0.0)
        relevance = [int(candidate_id in target) for candidate_id, _ in ranked]
        for k in k_values:
            recall_values[str(k)].append(sum(relevance[:k]) / len(target))
            value = _ndcg(relevance, k)
            if value is not None:
                ndcg_values[str(k)].append(value)
    return RankingMetrics(
        recall_at_k={key: _metric(sum(values) / len(values), len(values), len(values), "NO_ELIGIBLE_QUERIES") if values else _metric(None, 0, 0, "NO_ELIGIBLE_QUERIES") for key, values in recall_values.items()},
        mean_reciprocal_rank=_metric(sum(reciprocal) / len(reciprocal), len(reciprocal), len(reciprocal), "NO_ELIGIBLE_QUERIES") if reciprocal else _metric(None, 0, 0, "NO_ELIGIBLE_QUERIES"),
        ndcg_at_k={key: _metric(sum(values) / len(values), len(values), len(values), "NO_DEFINED_IDEAL") if values else _metric(None, 0, 0, "NO_DEFINED_IDEAL") for key, values in ndcg_values.items()},
        eligible_query_count=eligible,
        ineligible_single_candidate_query_count=single,
        no_positive_query_count=no_positive,
        mean_candidate_count=_metric(sum(counts) / len(counts), len(counts), len(counts), "NO_QUERIES") if counts else _metric(None, 0, 0, "NO_QUERIES"),
        hard_negative_count=hard_negative_count,
    )


def entity_resolution_metrics(
    predicted_pairs: set[frozenset[str]],
    truth_pairs: set[frozenset[str]],
    truth_clusters: Mapping[str, set[str]],
    predicted_clusters: Sequence[set[str]],
    *,
    evaluated_universe: set[frozenset[str]],
    evaluated_record_refs: set[str] | None = None,
) -> EntityResolutionMetrics:
    """Evaluate pairwise links and a complete predicted partition.

    ``predicted_clusters`` contains only explicit multi-record clusters. Every
    evaluated record absent from those clusters is an implicit singleton. This
    distinction is important: no explicit cluster is a valid undefined purity
    state, but it is not a valid reason to report zero completeness.
    """
    universe = {frozenset(pair) for pair in evaluated_universe}
    records = set(evaluated_record_refs or set().union(*universe) if universe else set())
    if evaluated_record_refs is None:
        records.update(set().union(*truth_clusters.values()) if truth_clusters else set())
    expected_universe = {frozenset(pair) for pair in combinations(sorted(records), 2)}
    if universe != expected_universe:
        raise ValueError("evaluated pair universe must equal all pairs of evaluated records")
    explicit = [set(cluster) for cluster in predicted_clusters if cluster]
    explicit_refs = set().union(*explicit) if explicit else set()
    if not explicit_refs.issubset(records):
        raise ValueError("predicted cluster contains an out-of-universe record")
    if sum(len(cluster) for cluster in explicit) != len(explicit_refs):
        raise ValueError("predicted clusters overlap")
    partition = explicit + [{record} for record in sorted(records - explicit_refs)]
    if set().union(*partition) != records or sum(len(item) for item in partition) != len(records):
        raise ValueError("predicted clusters do not form a complete partition")
    predicted = {frozenset(pair) for pair in predicted_pairs}
    truth = {frozenset(pair) for pair in truth_pairs}
    if not predicted.issubset(universe) or not truth.issubset(universe):
        raise ValueError("predicted or truth pair is outside the evaluated universe")
    rows = [("|".join(sorted(pair)), int(pair in truth), int(pair in predicted), 1.0 if pair in predicted else 0.0) for pair in sorted(universe, key=lambda item: tuple(sorted(item)))]
    pairwise = binary_classification_metrics(rows, universe_count=len(universe))
    false_merge_pairs = predicted - truth
    contaminated = 0
    largest_false_merge = 0
    for cluster in explicit:
        hits = sum(bool(cluster.intersection(refs)) for refs in truth_clusters.values())
        if hits > 1 or any(frozenset(pair) in false_merge_pairs for pair in combinations(cluster, 2)):
            contaminated += 1
            largest_false_merge = max(largest_false_merge, len(cluster))
    split_count = 0
    completeness: list[float] = []
    for refs in truth_clusters.values():
        refs = set(refs)
        overlaps = [component for component in partition if refs.intersection(component)]
        if len(overlaps) > 1:
            split_count += 1
        completeness.append(max((len(refs.intersection(component)) / len(refs) for component in partition), default=0.0))
    explicit_multi = [cluster for cluster in explicit if len(cluster) > 1]
    purities = [max((len(cluster.intersection(refs)) for refs in truth_clusters.values()), default=0) / len(cluster) for cluster in explicit_multi]
    complete_purities = [max((len(component.intersection(refs)) for refs in truth_clusters.values()), default=0) / len(component) for component in partition]
    no_purity = "NO_PREDICTED_CLUSTERS"
    return EntityResolutionMetrics(
        pairwise=pairwise,
        evaluated_pair_count=len(universe),
        false_merge_pair_count=len(false_merge_pairs),
        false_merge_rate=_safe_ratio(len(false_merge_pairs), len(predicted), "NO_PREDICTED_LINKS"),
        contaminated_cluster_count=contaminated,
        largest_false_merge_cluster=largest_false_merge,
        truth_clusters_split=split_count,
        false_split_rate=_safe_ratio(split_count, len(truth_clusters), "NO_TRUTH_CLUSTERS"),
        mean_predicted_cluster_purity=_metric(sum(purities) / len(purities), len(purities), len(purities), no_purity) if purities else _metric(None, 0, 0, no_purity),
        mean_truth_cluster_completeness=_metric(sum(completeness) / len(completeness), len(completeness), len(completeness), "NO_TRUTH_CLUSTERS") if completeness else _metric(None, 0, 0, "NO_TRUTH_CLUSTERS"),
        evaluated_record_universe_count=len(records),
        truth_positive_pair_count=len(truth),
        truth_negative_pair_count=len(universe - truth),
        explicit_predicted_cluster_count=len(explicit_multi),
        implicit_singleton_count=len(records - explicit_refs),
        predicted_partition_component_count=len(partition),
        partition_validated=True,
        complete_partition_mean_purity=_metric(sum(complete_purities) / len(complete_purities), len(complete_purities), len(complete_purities), "NO_PREDICTED_PARTITION") if complete_purities else _metric(None, 0, 0, "NO_PREDICTED_PARTITION"),
    )


def group_bootstrap(metric_by_group: Mapping[str, float], *, seed: int, replicates: int = 1000, confidence_level: float = .95, metric_id: str) -> BootstrapInterval:
    groups = tuple(sorted(metric_by_group))
    point = sum(metric_by_group.values()) / len(groups) if groups else None
    if len(groups) < 2:
        return BootstrapInterval(metric_id=metric_id, point_estimate=point, lower=None, upper=None, confidence_level=confidence_level, replicate_count=0, group_count=len(groups), method="group_resample_v1", status="INSUFFICIENT_GROUPS_FOR_INTERVAL", seed=seed)
    rng = random.Random(seed)
    values = sorted(sum(metric_by_group[rng.choice(groups)] for _ in groups) / len(groups) for _ in range(replicates))
    alpha = (1 - confidence_level) / 2
    lower = values[max(0, int(alpha * len(values)) - 1)]
    upper = values[min(len(values) - 1, int((1 - alpha) * len(values)))]
    return BootstrapInterval(metric_id=metric_id, point_estimate=point, lower=lower, upper=upper, confidence_level=confidence_level, replicate_count=replicates, group_count=len(groups), method="group_resample_v1", status="OK", seed=seed)
