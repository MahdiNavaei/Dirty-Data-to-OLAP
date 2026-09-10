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


def evaluate_entity_resolution(*, edges: Iterable[Mapping[str, Any]], clusters: Iterable[Mapping[str, Any]], labels: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    positives = {_pair(*item["record_refs"][:2]) for item in labels.get("cases", ()) if item.get("kind") == "positive" and len(item.get("record_refs", ())) == 2}
    negatives = {_pair(*item["record_refs"][:2]) for item in labels.get("cases", ()) if item.get("kind") == "negative" and len(item.get("record_refs", ())) == 2}
    edge_rows = list(edges)
    candidate_pairs = {_pair(row["left_record_ref"], row["right_record_ref"]) for row in edge_rows}
    predicted = {_pair(row["left_record_ref"], row["right_record_ref"]) for row in edge_rows if float(row.get("match_probability", 0.0)) >= threshold}
    tp = len(predicted & positives)
    fp = len(predicted & negatives) + len(predicted - positives - negatives)
    fn = len(positives - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    contaminated = 0
    largest = 0
    for cluster in clusters:
        refs = set(cluster.get("record_refs", ()))
        largest = max(largest, len(refs))
        if any(_pair(left, right) in negatives for left, right in combinations(refs, 2)):
            contaminated += 1
    blocking_recall = len({pair for pair in positives if pair in candidate_pairs}) / len(positives) if positives else 0.0
    return {"threshold": threshold, "true_positive": tp, "false_positive": fp, "false_negative": fn, "precision": precision, "recall": recall, "f1": f1, "blocking_recall": blocking_recall, "candidate_pairs": len(candidate_pairs), "false_merges": len(predicted & negatives), "contaminated_clusters": contaminated, "largest_cluster_size": largest}


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
