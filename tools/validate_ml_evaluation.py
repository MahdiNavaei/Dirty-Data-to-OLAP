"""Validate Step18 aggregate-safe evaluation evidence and G5 boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "workspace" / "runs" / "step18-inference-baseline-v1" / "evaluation"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    failures: list[str] = []
    report_path = EVAL / "report.json"
    protocol_path = EVAL / "manifest" / "evaluation_protocol.json"
    split_path = EVAL / "manifest" / "split_manifest.json"
    dataset_path = EVAL / "manifest" / "dataset_manifest.json"
    required = (report_path, protocol_path, split_path, dataset_path)
    _check("required evaluation manifests exist", all(path.is_file() for path in required), failures)
    if failures:
        print("FAIL\n" + "\n".join(f"- {item}" for item in failures))
        return 1
    report, protocol, split, dataset = map(_json, required)
    _check("protocol binds a full git commit", len(protocol.get("content_commit", "")) == 40, failures)
    _check("truth and runtime are separately hashed", bool(dataset.get("truth_artifact_hashes")) and bool(dataset.get("runtime_fixture_hashes")), failures)
    _check("frozen Step17 policy hashes are present", len(dataset.get("frozen_policy_hashes", {})) >= 2, failures)
    roles = split.get("group_ids_by_split", {})
    role_sets = {key: set(value) for key, value in roles.items()}
    _check("scenario groups are disjoint", all(not role_sets[left].intersection(role_sets[right]) for left in role_sets for right in role_sets if left < right), failures)
    test_ids = set(split.get("example_ids_by_split", {}).get("TEST", ()))
    calibration_ids = set(split.get("example_ids_by_split", {}).get("CALIBRATION", ()))
    _check("test examples do not enter calibration", not test_ids.intersection(calibration_ids), failures)
    threshold = _json(EVAL / "thresholds" / "frontier.json")
    _check("threshold study is calibration-only and unselected", threshold.get("split") == "CALIBRATION" and threshold.get("selected_threshold") is None and not threshold.get("runtime_policy_mutated"), failures)
    _check("threshold score is not presented as probability", threshold.get("source_score_semantics") == "UNCALIBRATED_DECISION_SCORE", failures)
    relationship = _json(EVAL / "relationship" / "test_metrics.json")
    candidate_generation = relationship.get("candidate_generation", {})
    _check("candidate-generation recall exposes missing candidates", "missing_candidate_query_ids" in candidate_generation and candidate_generation.get("candidate_recall", {}).get("denominator") is not None, failures)
    fusion = relationship.get("fusion", {})
    _check("relationship precision formula is explicit", fusion.get("precision", {}).get("denominator") == fusion.get("true_positive", 0) + fusion.get("false_positive", 0), failures)
    _check("relationship recall formula is explicit", fusion.get("recall", {}).get("denominator") == fusion.get("true_positive", 0) + fusion.get("false_negative", 0), failures)
    schema = _json(EVAL / "schema_matching" / "test_metrics.json")
    _check("schema no-match false positives are reported", "no_match_false_positive_count" in schema, failures)
    entity = _json(EVAL / "entity_resolution" / "test_metrics.json")
    _check("entity false merge and split metrics are reported", "false_merge_pair_count" in entity and "false_split_rate" in entity, failures)
    bootstrap = _json(EVAL / "bootstrap" / "intervals.json")
    _check("bootstrap is grouped and seeded", bool(bootstrap) and all(item.get("method") == "group_resample_v1" and item.get("seed") == dataset.get("seed") for item in bootstrap), failures)
    _check("G5 has no automation authorization", report.get("assessment", {}).get("automation_recommendation") == "NOT_AUTHORIZED" and report.get("assessment", {}).get("automation_enabled") is False, failures)
    _check("G5 status is evidence-backed review-only validation", report.get("assessment", {}).get("gate_recommendation") == "REVIEW_ONLY_VALIDATED", failures)
    for reference in report.get("generated_artifact_refs", ()):
        artifact = EVAL / reference["relative_path"]
        _check(f"artifact exists: {reference['relative_path']}", artifact.is_file(), failures)
        if artifact.is_file():
            _check(f"artifact hash: {reference['relative_path']}", hashlib.sha256(artifact.read_bytes()).hexdigest() == reference["content_hash"], failures)
    for receipt in (EVAL / "dependency_discovery" / "real_provider_receipt.json", EVAL / "schema_matching" / "real_provider_receipt.json", EVAL / "entity_resolution" / "real_provider_receipt.json"):
        payload = _json(receipt)
        output = ROOT / payload["output_artifact"]
        _check(f"provider receipt output hash: {receipt.parent.name}", output.is_file() and hashlib.sha256(output.read_bytes()).hexdigest() == payload["output_hash"] and payload.get("execution_result") == "COMPLETE", failures)
    runtime_imports = []
    for path in (ROOT / "src" / "dirty_data_to_olap").rglob("*.py"):
        if "evaluation" not in path.parts and "dirty_data_to_olap.evaluation" in path.read_text(encoding="utf-8"):
            runtime_imports.append(path.as_posix())
    _check("runtime DAG does not import evaluation package", not runtime_imports, failures)
    _check("Step19 implementation is absent", not (ROOT / "src" / "dirty_data_to_olap" / "application" / "review_decision.py").exists(), failures)
    if failures:
        print("FAIL\n" + "\n".join(f"- {item}" for item in failures))
        return 1
    print("PASS: step18_ml_evaluation_checks=23")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
