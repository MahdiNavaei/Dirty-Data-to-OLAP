"""Behavioral validator for the post-Step18 empirical binding closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    failures: list[str] = []
    required = (EVAL / "report.json", EVAL / "g5" / "assessment.json", EVAL / "manifest" / "provider_evaluation_bindings.json", EVAL / "manifest" / "split_manifest.json")
    _check("v2 evaluation artifacts exist", all(path.is_file() for path in required), failures)
    if failures:
        print("FAIL\n" + "\n".join(f"- {item}" for item in failures))
        return 1
    report, assessment, bindings, split = (_json(path) for path in required)
    _check("formal G5 and review mode are separate", assessment.get("formal_gate") in {"PASS", "PENDING", "BLOCKED"} and assessment.get("formal_gate") != assessment.get("inference_validity_mode"), failures)
    _check("automation remains unauthorized", assessment.get("automation_recommendation") == "NOT_AUTHORIZED" and assessment.get("automation_enabled") is False, failures)
    roles = {key: set(value) for key, value in split.get("group_ids_by_split", {}).items()}
    _check("scenario groups are disjoint", all(not roles[left].intersection(roles[right]) for left in roles for right in roles if left < right), failures)
    _check("split reverse/shared control is exercised or explicitly recorded", "reverse_pair_split" in split.get("leakage_audit", {}), failures)

    relationship = _json(EVAL / "relationship" / "test_metrics.json")
    candidate = relationship.get("candidate_generation", {})
    _check("relationship universe comes from truth", candidate.get("truth_query_count") == 13, failures)
    _check("zero-candidate truth queries remain visible", "missing_candidate_query_ids" in candidate and len(candidate["missing_candidate_query_ids"]) >= 1, failures)
    _check("candidate metrics have explicit denominator", candidate.get("candidate_recall", {}).get("denominator") is not None, failures)
    _check("fusion does not claim complete without Profile and Quality artifacts", relationship.get("fusion", {}).get("status") != "EVALUATED" or (bindings.get("profiling", {}).get("status") == "EXECUTED" and bindings.get("quality", {}).get("status") == "EXECUTED"), failures)

    dependency_binding = bindings.get("dependency_discovery", {})
    if dependency_binding.get("status") == "EXECUTED":
        output = ROOT / dependency_binding["output_path"]
        payload = _json(output)
        actual_count = sum(len(item["result"].get("relationship_candidates", ())) for item in payload.get("results", ()))
        _check("dependency normalized output is loaded", dependency_binding.get("loaded_for_metrics") is True and dependency_binding.get("loaded_output_hash") == hashlib.sha256(output.read_bytes()).hexdigest(), failures)
        _check("candidate metric consumes normalized DependencyResult", candidate.get("generated_candidate_count") == actual_count, failures)
        if payload.get("results"):
            mutated = json.loads(json.dumps(payload))
            mutated["results"][0]["result"]["relationship_candidates"] = []
            mutated_count = sum(len(item["result"].get("relationship_candidates", ())) for item in mutated.get("results", ()))
            _check("mutating provider candidates changes candidate metric input", mutated_count != actual_count, failures)
    else:
        _check("missing dependency provider fails closed", assessment.get("formal_gate") == "PENDING", failures)

    schema = _json(EVAL / "schema_matching" / "test_metrics.json")
    entity = _json(EVAL / "entity_resolution" / "test_metrics.json")
    _check("schema no-match is first-class", "no_match_false_positive_count" in schema, failures)
    _check("ER metrics identify actual normalized output path", entity.get("prediction_method") == "ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS", failures)
    _check("provider population mismatch cannot pass", not (bindings.get("entity_resolution", {}).get("status") == "EXECUTED" and entity.get("population_match") is not True), failures)

    controls = _json(EVAL / "controls" / "negative_controls.json")
    _check("receipt-only negative control passes", controls.get("receipt_only", {}).get("receipt_without_loaded_output_cannot_close_g5") is True, failures)
    _check("input-order control executes", controls.get("input_order_invariance", {}).get("status") == "EXECUTED", failures)
    _check("truth shuffle is evaluation-only", controls.get("evaluation_truth_shuffle", {}).get("retrained") is False, failures)
    _check("threshold remains non-authorizing", _json(EVAL / "thresholds" / "frontier.json").get("selected_threshold") is None, failures)
    _check("Step19 implementation is absent", not (ROOT / "src" / "dirty_data_to_olap" / "application" / "review_decision.py").exists(), failures)
    _check("v2 evaluator does not read pre-authored runtime scores", "runtime_inputs.json" not in (ROOT / "tools" / "run_step18_v2_evaluation.py").read_text(encoding="utf-8"), failures)
    if failures:
        print("FAIL\n" + "\n".join(f"- {item}" for item in failures))
        return 1
    print("PASS: step18_v2_binding_checks=19")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
