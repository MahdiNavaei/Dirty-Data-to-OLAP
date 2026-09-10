"""Evaluate labeled schema candidates without treating scores as confidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchCandidate, SchemaMatchEvaluation, SchemaMatchScore


def evaluate_candidates(*, candidates: Iterable[SchemaMatchCandidate], scores: Iterable[SchemaMatchScore], ground_truth: Iterable[tuple[str, str]], matcher_id: str, fixture_id: str, sample_identity: str, k_values: tuple[int, ...] = (1, 3, 5)) -> SchemaMatchEvaluation:
    truth = {tuple(pair) for pair in ground_truth}
    by_left: dict[str, list[tuple[float, str]]] = defaultdict(list)
    score_map = {score.score_id: score for score in scores if score.matcher.matcher_id == matcher_id}
    candidate_count = 0
    for candidate in candidates:
        relevant = [score_map[ref] for ref in candidate.score_refs if ref in score_map]
        if not relevant:
            continue
        best = max(relevant, key=lambda score: (score.raw_native_score, score.score_id))
        by_left[candidate.source_column_id].append((best.raw_native_score, candidate.target_column_id))
        candidate_count += 1
    for values in by_left.values():
        values.sort(key=lambda item: (-item[0], item[1]))
    hits = {}
    reciprocal_ranks = []
    for left, right in truth:
        ranked = [target for _, target in by_left.get(left, ())]
        rank = ranked.index(right) + 1 if right in ranked else None
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    for k in k_values:
        hits[str(k)] = sum(1 for left, right in truth if right in [target for _, target in by_left.get(left, ())[:k]]) / len(truth) if truth else 0.0
    return SchemaMatchEvaluation(evaluation_id=f"evaluation-{fixture_id}-{matcher_id}-{sample_identity}", fixture_id=fixture_id, matcher_id=matcher_id, sample_identity=sample_identity, recall_at_k=hits, mean_reciprocal_rank=sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0, labeled_positive_count=len(truth), evaluated_candidate_count=candidate_count, limitations=("metrics are candidate-ranking observations over the bounded staged sample", "native matcher scores are not probabilities or calibrated confidence"))


__all__ = ["evaluate_candidates"]
