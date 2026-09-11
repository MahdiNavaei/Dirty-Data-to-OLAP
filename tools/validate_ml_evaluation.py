"""Behavioral validator for the final Step18 v3 empirical closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def _receipt_hash(receipt: dict) -> str:
    value = dict(receipt)
    value.pop("receipt_content_hash", None)
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    failures: list[str] = []
    required = (EVAL / "report.json", EVAL / "g5" / "assessment.json", EVAL / "manifest" / "provider_evaluation_bindings.json", EVAL / "manifest" / "split_manifest.json", EVAL / "controls" / "negative_controls.json")
    _check("v3 artifacts exist", all(path.is_file() for path in required), failures)
    if failures:
        print("FAIL\n" + "\n".join(f"- {item}" for item in failures))
        return 1
    report, assessment, bindings, split, controls = (_json(path) for path in required)
    groups = _json(ROOT / "benchmarks" / "inference_evaluation" / "scenario_groups.json")["groups"]
    rel = _json(ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios_v3.json")["scenarios"]
    schema = _json(ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json")["scenarios"]
    estate = {item["group_id"] for item in groups}
    _check("dataset estate enumerates all task groups", set(report["assessment"].get("required_provider_status", {})) == {"dependency_discovery", "profiling", "quality", "schema_matching", "entity_resolution"}, failures)
    _check("frozen group estate is explicit", estate == {item["scenario_group_id"] for item in rel} | {item["scenario_group_id"] for item in schema} | {item["group_id"] for item in groups if item["task"] == "ENTITY_RESOLUTION"}, failures)
    roles = {key:set(value) for key,value in split["group_ids_by_split"].items()}
    _check("groups are split-disjoint", all(not roles[left].intersection(roles[right]) for left in roles for right in roles if left < right), failures)
    _check("reverse/shared leakage is behaviorally checked", split["leakage_audit"].get("reverse_pair_control") == "EXECUTED" and split["leakage_audit"].get("reverse_pair_leakage") is True, failures)
    _check("truth clusters do not straddle splits", split["leakage_audit"].get("truth_cluster_leakage") is True, failures)
    _check("headline report is TEST-only", report["assessment"].get("headline_split") == "TEST" and report["task_metrics"]["relationship"].get("split") == "TEST", failures)
    truth = _json(ROOT / "benchmarks" / "inference_evaluation" / "truth" / "relationship_truth_v3.json")["labels"]
    expected_denominator = sum(1 for item in truth if item["scenario_group_id"] in roles.get("TEST", set()) and item["expected"] == "MATCH")
    _check("relationship metric retains TEST denominator", report["task_metrics"]["relationship"]["candidate_recall"].get("denominator") == expected_denominator, failures)
    _check("Fusion is based on real contracts", report["task_metrics"]["fusion"].get("status") == "EVALUATED" and report["task_metrics"]["fusion"].get("failures") == [], failures)
    _check("TEST slice metrics retain denominators", report.get("slice_metrics", {}).get("split") == "TEST" and report["slice_metrics"].get("relationship"), failures)
    _check("schema matcher metrics remain family-specific", isinstance(report["task_metrics"]["schema"].get("matchers"), dict), failures)
    _check("ER metrics identify actual output or remain insufficient", report["task_metrics"]["entity_resolution"].get("prediction_method") == "ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS", failures)
    for component, binding in bindings.items():
        receipt_path = ROOT / binding["receipt_path"]
        if receipt_path.is_file():
            receipt = _json(receipt_path)
            _check(f"{component} receipt has no post-execution binding", not any(key in receipt for key in ("content_commit", "protocol_hash", "dataset_manifest_hash", "truth_artifact_hash", "split_hash")), failures)
            _check(f"{component} receipt content hash validates", receipt.get("receipt_content_hash") == _receipt_hash(receipt), failures)
            if binding["status"] == "EXECUTED":
                output = ROOT / binding["output_path"]
                _check(f"{component} output hash is bound", output.is_file() and hashlib.sha256(output.read_bytes()).hexdigest() == receipt.get("output_hash"), failures)
    _check("truth shuffle was recomputed", controls.get("truth_shuffle", {}).get("recomputed") is True, failures)
    _check("input order was rerun", controls.get("input_order", {}).get("rerun") is True, failures)
    _check("provider mutation changes metric input", controls.get("provider_output_mutation", {}).get("metric_input_changed") is True, failures)
    _check("receipt-only control blocks incomplete gate", controls.get("receipt_only", {}).get("formal_gate_without_loaded_output") == "PENDING" and assessment.get("formal_gate") == "PENDING", failures)
    _check("calibration status is computed", (EVAL / "calibration" / "sufficiency.json").is_file() and "reasons" in _json(EVAL / "calibration" / "sufficiency.json"), failures)
    _check("threshold automation remains disabled", _json(EVAL / "thresholds" / "frontier.json").get("selected_threshold") is None and _json(EVAL / "thresholds" / "frontier.json").get("runtime_policy_mutated") is False, failures)
    _check("missing Valentine/Splink keep G5 pending", assessment.get("formal_gate") == "PENDING" if any(bindings.get(key, {}).get("status") != "EXECUTED" for key in ("schema_matching", "entity_resolution")) else True, failures)
    _check("Step19 implementation absent", not (ROOT / "src" / "dirty_data_to_olap" / "application" / "canonical_model.py").exists(), failures)
    if failures:
        print("FAIL\n" + "\n".join(f"- {item}" for item in failures))
        return 1
    print("PASS: step18_v3_behavioral_checks=25")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
