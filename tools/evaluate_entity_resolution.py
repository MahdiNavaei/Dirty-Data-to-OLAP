"""Aggregate-only benchmark evaluation for Step14 ER outputs."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping


def _pair(left: str, right: str) -> frozenset[str]:
    return frozenset((left, right))


def load_labeled_fixture(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pairs_from_entities(entities: Iterable[Mapping[str, Any]]) -> set[frozenset[str]]:
    return {
        _pair(left, right)
        for entity in entities
        for left, right in combinations(entity.get("record_refs", ()), 2)
    }


def _labeled_pairs(labels: Mapping[str, Any]) -> tuple[set[frozenset[str]], set[frozenset[str]]]:
    positives = {
        _pair(*item)
        for item in labels.get("positive_pairs", ())
        if len(item) == 2
    }
    if not positives:
        positives = _pairs_from_entities(labels.get("ground_truth_entities", ()))
    if not positives:
        positives = {
            _pair(*item["record_refs"][:2])
            for item in labels.get("cases", ())
            if item.get("kind") == "positive" and len(item.get("record_refs", ())) == 2
        }
    negatives = {
        _pair(*item)
        for item in labels.get("negative_pairs", ())
        if len(item) == 2
    }
    if not negatives:
        negatives = {
            _pair(*item["record_refs"][:2])
            for item in labels.get("cases", ())
            if item.get("kind") == "negative" and len(item.get("record_refs", ())) == 2
        }
    return positives, negatives


def _components(pairs: Iterable[frozenset[str]]) -> list[set[str]]:
    components: list[set[str]] = []
    for pair in pairs:
        merged = [component for component in components if component.intersection(pair)]
        if not merged:
            components.append(set(pair))
            continue
        combined = set(pair)
        for component in merged:
            combined.update(component)
            components.remove(component)
        components.append(combined)
    return components


def evaluate_entity_resolution(*, edges: Iterable[Mapping[str, Any]], clusters: Iterable[Mapping[str, Any]], labels: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    positives, negatives = _labeled_pairs(labels)
    edge_rows = list(edges)
    candidate_pairs = {_pair(row["left_record_ref"], row["right_record_ref"]) for row in edge_rows}
    predicted = {_pair(row["left_record_ref"], row["right_record_ref"]) for row in edge_rows if float(row.get("match_probability", 0.0)) >= threshold}
    tp = len(predicted & positives)
    fp = len(predicted & negatives) + len(predicted - positives - negatives)
    fn = len(positives - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    cluster_rows = [set(cluster.get("record_refs", ())) for cluster in clusters]
    if not cluster_rows:
        cluster_rows = _components(predicted)
    truth_entities = {
        str(entity.get("entity_id", index)): set(entity.get("record_refs", ()))
        for index, entity in enumerate(labels.get("ground_truth_entities", ()))
    }
    contaminated = 0
    contaminated_truth_entities: set[str] = set()
    fragmented_truth_entities: set[str] = set()
    largest_contaminated = 0
    largest = 0
    for refs in cluster_rows:
        largest = max(largest, len(refs))
        truth_hits = {
            entity_id
            for entity_id, truth_refs in truth_entities.items()
            if refs.intersection(truth_refs)
        }
        if len(truth_hits) > 1 or any(_pair(left, right) in negatives for left, right in combinations(refs, 2)):
            contaminated += 1
            largest_contaminated = max(largest_contaminated, len(refs))
            contaminated_truth_entities.update(truth_hits)
    for entity_id, truth_refs in truth_entities.items():
        overlapping = sum(bool(truth_refs.intersection(cluster)) for cluster in cluster_rows)
        if overlapping > 1:
            fragmented_truth_entities.add(entity_id)
    blocking_recall = len({pair for pair in positives if pair in candidate_pairs}) / len(positives) if positives else 0.0
    return {
        "threshold": threshold,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "blocking_recall": blocking_recall,
        "candidate_pairs": len(candidate_pairs),
        "false_merges": len(predicted & negatives),
        "false_positive_links": fp,
        "hard_negative_count": len(negatives),
        "hard_negative_exposed_at_threshold": len(predicted & negatives),
        "contaminated_clusters": contaminated,
        "contaminated_ground_truth_entities": len(contaminated_truth_entities),
        "largest_contaminated_cluster": largest_contaminated,
        "largest_cluster_size": largest,
        "fragmented_ground_truth_entities": len(fragmented_truth_entities),
        "fragmentation_rate": len(fragmented_truth_entities) / len(truth_entities) if truth_entities else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--threshold", type=float, action="append", default=[])
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    labels = load_labeled_fixture(args.labels)
    thresholds = args.threshold or [0.8, 0.9, 0.95]
    output = {"fixture_id": labels.get("fixture_id"), "evaluations": [evaluate_entity_resolution(edges=result.get("edges", ()), clusters=result.get("clusters", ()), labels=labels, threshold=value) for value in thresholds], "limitations": ["benchmark labels are synthetic and not identity truth", "probabilities are not calibrated business confidence"]}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
