"""Behavioral validator for the corrected Step18 v5 G5 closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "workspace" / "runs" / "step18-inference-baseline-v5" / "evaluation"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def validate_report(report: dict) -> list[str]:
    failures: list[str] = []
    er = report.get("task_metrics", {}).get("entity_resolution", {})
    metrics = er.get("metrics") or {}
    pairwise = metrics.get("pairwise") or {}
    def check(name: str, condition: bool):
        if not condition:
            failures.append(name)
    check("v5 identity", report.get("report_id") == "step18-inference-baseline-v5")
    check("formal review-only pass", report.get("formal_gate") == "PASS" and report.get("inference_validity_mode") == "REVIEW_ONLY_VALIDATED")
    check("old tn field rejected", "defined_tn_universe" not in er and "defined_tn_universe" not in metrics)
    check("pair universe", er.get("evaluated_record_universe_count") == 10 and er.get("evaluated_pair_universe_count") == 45)
    check("pair labels", er.get("truth_positive_pair_count") == 8 and er.get("truth_negative_pair_count") == 37)
    check("pair reconcile", sum(pairwise.get(key, 0) or 0 for key in ("true_positive", "false_positive", "false_negative", "true_negative")) == 45)
    check("complete partition", metrics.get("partition_validated") is True and metrics.get("predicted_partition_component_count") == 10)
    check("false split computed", metrics.get("truth_clusters_split") is not None and metrics.get("false_split_rate", {}).get("denominator") == 4 and metrics.get("mean_truth_cluster_completeness", {}).get("value") is not None)
    check("purity may be undefined", metrics.get("mean_predicted_cluster_purity", {}).get("undefined_reason") in {None, "NO_PREDICTED_CLUSTERS"})
    controls = report.get("controls", {})
    for task in ("relationship", "schema", "entity_resolution"):
        current = controls.get(task, {})
        check(task + " task controls", current.get("status") == "PASS" and all(current.get(name, {}).get("status") == "PASS" for name in ("truth_shuffle", "input_order", "provider_mutation")))
    completion = report.get("assessment", {}).get("required_task_completion", {})
    required = {"RELATIONSHIP_CANDIDATE_EVALUATED", "RELATIONSHIP_FUSION_EVALUATED", "SCHEMA_CANDIDATE_EVALUATED", "SCHEMA_MATCHER_RANKING_EVALUATED", "SCHEMA_FUSION_EVALUATED", "ER_PAIRWISE_EVALUATED", "ER_CLUSTER_EVALUATED", "NEGATIVE_CONTROLS_PASS", "REPRODUCIBILITY_PASS", "LEAKAGE_AUDIT_PASS"}
    check("task completion", required <= set(completion) and all(completion.get(key) is True for key in required))
    check("non-authorizing threshold", report.get("threshold_study", {}).get("selected_threshold") is None and report.get("threshold_study", {}).get("automation_enabled") is False)
    check("step19 absent", not (ROOT / "src/dirty_data_to_olap/application/canonical_model.py").exists() and "Step19 implementation not started" in report.get("assessment", {}).get("limitations", []))
    check("v4 behavior is not accepted", "defined_tn_universe" not in er)
    return failures


def main() -> int:
    required = ("report.json", "g5/assessment.json", "manifest/provider_evaluation_bindings.json", "manifest/evaluation_protocol.json", "manifest/task_completion.json", "controls/negative_controls.json", "entity_resolution/test_metrics.json")
    failures = ["missing:" + path for path in required if not (EVAL / path).is_file()]
    if failures:
        print("FAIL\n" + "\n".join(failures)); return 1
    report = load(EVAL / "report.json")
    failures.extend(validate_report(report))
    bindings = load(EVAL / "manifest/provider_evaluation_bindings.json")
    for component, binding in bindings.items():
        receipt_path = ROOT / binding["receipt_path"]
        if not receipt_path.is_file():
            failures.append(component + ":receipt-missing"); continue
        receipt = load(receipt_path)
        if receipt.get("receipt_content_hash") != receipt_hash(receipt):
            failures.append(component + ":receipt-hash")
        if binding.get("status") != "EXECUTED" or not binding.get("loaded_for_metrics"):
            failures.append(component + ":not-executed")
        output = ROOT / binding["output_path"]
        if not output.is_file() or hashlib.sha256(output.read_bytes()).hexdigest() != receipt.get("output_hash"):
            failures.append(component + ":output-hash")
    entity = load(ROOT / bindings["entity_resolution"]["output_path"])["result"]
    policy = entity["spec"]["clustering_policy"]
    if policy.get("include_review_edges") is not False:
        failures.append("project-cluster-policy:not-review-excluding")
    edge_by_id = {edge["edge_id"]: edge for edge in entity.get("edges", ())}
    for cluster in entity.get("clusters", ()):
        if any(edge_by_id.get(edge_id, {}).get("model_prediction_band") != "STRONG_LINK_EVIDENCE" for edge_id in cluster.get("edge_refs", ())):
            failures.append("project-cluster-policy:unauthorized-edge")
    if failures:
        print("FAIL\n" + "\n".join("- " + item for item in failures)); return 1
    print("PASS: step18_v5_behavioral_checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
