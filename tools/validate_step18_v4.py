"""Behavioral validator for the Step18 v4 final empirical closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def main() -> int:
    failures = []
    required = ("report.json", "g5/assessment.json", "manifest/provider_evaluation_bindings.json", "manifest/split_manifest.json", "manifest/er_split_assignment.json", "manifest/task_completion.json", "controls/negative_controls.json", "relationship/test_metrics.json", "fusion/test_metrics.json", "schema_matching/test_metrics.json", "entity_resolution/test_metrics.json")
    failures.extend("missing:" + path for path in required if not (EVAL / path).is_file())
    if failures:
        print("FAIL\n" + "\n".join(failures)); return 1
    report, assessment, bindings, split, er_split, completion, controls = (load(EVAL / path) for path in ("report.json", "g5/assessment.json", "manifest/provider_evaluation_bindings.json", "manifest/split_manifest.json", "manifest/er_split_assignment.json", "manifest/task_completion.json", "controls/negative_controls.json"))
    groups = load(ROOT / "benchmarks/inference_evaluation/scenario_groups_v4.json")["groups"]
    rel_truth = load(ROOT / "benchmarks/inference_evaluation/truth/relationship_truth_v3.json")["labels"]
    schema_truth = load(ROOT / "benchmarks/inference_evaluation/truth/schema_truth_v3.json")["labels"]
    er_truth = load(ROOT / "benchmarks/inference_evaluation/truth/entity_truth_v4.json")
    def check(name, value):
        if not value: failures.append(name)
    check("v4 identity", report.get("report_id") == "step18-inference-baseline-v4" and report.get("assessment", {}).get("headline_split") == "TEST")
    check("explicit estate", {item["group_id"] for item in groups} == {item["scenario_group_id"] for item in rel_truth} | {item["scenario_group_id"] for item in schema_truth} | {item["group_id"] for item in groups if item["task"] == "ENTITY_RESOLUTION"})
    roles = {key: set(value) for key, value in split["group_ids_by_split"].items()}
    check("split disjoint", all(not roles[left].intersection(roles[right]) for left in roles for right in roles if left < right))
    check("reverse leakage control", split["leakage_audit"].get("reverse_pair_control") == "EXECUTED" and split["leakage_audit"].get("reverse_pair_leakage") is True)
    check("real truth cluster leakage", er_split.get("truth_cluster_leakage") is True and er_split.get("case_pair_role_consistent") is True and "truth_entity_roles" in er_split)
    check("singleton semantics", "singleton" in er_truth.get("singleton_semantics", "") and len(er_truth.get("truth_entities", ())) > len(er_truth.get("truth_clusters", ())))
    rel = report["task_metrics"]["relationship"]
    check("relationship metrics complete", all(key in rel for key in ("candidate_generation_precision", "candidate_generation_recall", "candidate_generation_f1", "zero_candidate_truth_queries", "false_positives_by_scenario", "candidate_count_per_query")))
    fusion = report["task_metrics"]["fusion"]
    check("fusion quality metrics", fusion.get("status") == "EVALUATED" and fusion.get("average_precision", {}).get("value") is not None and fusion.get("ranking", {}).get("mean_reciprocal_rank", {}).get("value") is not None and "band_composition" in fusion)
    schema = report["task_metrics"]["schema"]
    check("schema recall has denominator", "denominator" in schema.get("candidate_generation_recall", {}) and (schema.get("status") != "EVALUATED" or schema["candidate_generation_recall"].get("value") is not None))
    check("schema predicted IDs are actual or explicitly unavailable", (schema.get("status") == "EVALUATED" and schema.get("actual_candidate_ids") is True) or (schema.get("status") != "EVALUATED" and schema.get("actual_candidate_ids") is False))
    check("schema fusion not falsely complete", schema.get("fusion", {}).get("status") == "EVALUATED" if bindings["schema_matching"]["status"] == "EXECUTED" else schema.get("fusion", {}).get("status") == "INSUFFICIENT_EVIDENCE")
    er = report["task_metrics"]["entity_resolution"]
    check("ER TEST universe", er.get("evaluated_record_universe_count") == len(er.get("test_record_refs", ())) and er.get("defined_tn_universe") == 45)
    check("ER negative group bindings", er.get("hard_negative_slices", {}).get("common_name_nonmatch", {}).get("record_refs") == ["crm-r4", "erp-r4"] and er.get("hard_negative_slices", {}).get("placeholder_phone", {}).get("record_refs") == ["crm-r5", "erp-r5"] and er.get("hard_negative_slices", {}).get("shared_household", {}).get("record_refs") == ["crm-r6", "erp-r6"])
    for component, binding in bindings.items():
        receipt_path = ROOT / binding["receipt_path"]
        check(component + " receipt immutable", receipt_path.is_file())
        if receipt_path.is_file():
            receipt = load(receipt_path)
            check(component + " receipt hash", receipt.get("receipt_content_hash") == receipt_hash(receipt))
            check(component + " no post-binding fields", not any(key in receipt for key in ("content_commit", "protocol_hash", "dataset_manifest_hash", "truth_artifact_hash", "split_hash")))
            if binding["status"] == "EXECUTED":
                output = ROOT / binding["output_path"]
                check(component + " output hash", output.is_file() and hashlib.sha256(output.read_bytes()).hexdigest() == receipt.get("output_hash"))
    check("task completion is explicit", set(completion) >= {"RELATIONSHIP_CANDIDATE_EVALUATED", "RELATIONSHIP_FUSION_EVALUATED", "SCHEMA_CANDIDATE_EVALUATED", "SCHEMA_MATCHER_RANKING_EVALUATED", "SCHEMA_FUSION_EVALUATED", "ER_PAIRWISE_EVALUATED", "ER_CLUSTER_EVALUATED", "NEGATIVE_CONTROLS_PASS", "REPRODUCIBILITY_PASS", "LEAKAGE_AUDIT_PASS"})
    check("provider status cannot pass G5", report["formal_gate"] == "PENDING" if not all(completion.values()) or any(item["status"] != "EXECUTED" for item in bindings.values()) else True)
    check("controls are computed", controls["truth_shuffle"].get("recomputed") is True and controls["provider_output_mutation"].get("metric_input_changed") is True and controls["receipt_only"].get("formal_gate_without_loaded_output") == "PENDING")
    check("calibration task scoped", report.get("calibration", {}).get("task") == "RELATIONSHIP_FUSION" and report["calibration"].get("independent_group_count") == 1)
    check("threshold non-authorizing", report.get("threshold_study", {}).get("selected_threshold") is None and report["threshold_study"].get("automation_enabled") is False)
    check("Step19 absent", not (ROOT / "src/dirty_data_to_olap/application/canonical_model.py").exists() and report.get("assessment", {}).get("limitations", [""])[0] == "Step19 implementation not started")
    if failures:
        print("FAIL\n" + "\n".join("- " + item for item in failures)); return 1
    print("PASS: step18_v4_behavioral_checks=22")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
