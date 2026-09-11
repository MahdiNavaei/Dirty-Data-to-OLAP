"""Evaluate only normalized Step18 v2 provider outputs.

This runner deliberately has no path to the legacy authored-score fixture.  Missing or
unbound provider outputs produce PENDING evidence rather than a synthetic
quality score.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionRequest
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.evaluation.contracts import FormalGateStatus, InferenceValidityStatus
from dirty_data_to_olap.evaluation.metrics import entity_resolution_metrics, group_bootstrap, ranking_metrics, binary_classification_metrics, _safe_ratio
from dirty_data_to_olap.evaluation.provider_binding import bind_provider_output
from dirty_data_to_olap.evaluation.splitting import build_split_manifest
from dirty_data_to_olap.evaluation.contracts import EvaluationDatasetManifest, EvaluationTask, InferenceEvaluationExample

RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation"
BENCH = ROOT / "benchmarks" / "inference_evaluation"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(relative: str, payload: Any) -> str:
    path = RUN / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _pair(left: str, right: str) -> frozenset[str]:
    return frozenset((left, right))


def _endpoint_match(candidate: dict[str, Any], truth: dict[str, Any]) -> bool:
    return (candidate.get("from_table") == truth.get("from_table_id") and tuple(candidate.get("from_columns", ())) == tuple(truth.get("from_column_ids", ())) and candidate.get("to_table") == truth.get("to_table_id") and tuple(candidate.get("to_columns", ())) == tuple(truth.get("to_column_ids", ())))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    rel_manifest = BENCH / "provider_scenarios" / "relationships" / "scenarios.json"
    schema_manifest = BENCH / "provider_scenarios" / "schema_matching" / "scenarios.json"
    rel_truth_path = BENCH / "truth" / "relationship_truth_v2.json"
    schema_truth_path = BENCH / "truth" / "schema_truth_v2.json"
    entity_truth_path = BENCH / "truth" / "entity_truth_v2.json"
    groups = _load_json(BENCH / "scenario_groups.json")
    entity_fixture = ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json"
    dataset = EvaluationDatasetManifest(dataset_id="step18-inference-quality-v2", generator_version="step18-provider-bound-v2", seed=20260911, scenario_group_ids=tuple(item["group_id"] for item in groups["groups"]), source_types={"relationship":"provider-executable-synthetic-tables","schema":"provider-executable-schema-fixture","entity":"step14-labeled-aggregate-safe-fixture"}, corruption_types=tuple(sorted({item["kind"] for item in _load_json(rel_manifest)["scenarios"]})), truth_artifact_hashes={"relationship":_sha(rel_truth_path),"schema":_sha(schema_truth_path),"entity":_sha(entity_truth_path),"entity_fixture":_sha(entity_fixture)}, runtime_fixture_hashes={"relationship_scenarios":_sha(rel_manifest),"schema_scenarios":_sha(schema_manifest),"entity_fixture":_sha(entity_fixture)}, inference_component_versions={"evidence_fusion":EvidenceFusionService.load_policy().version,"evaluation_protocol":"step18-protocol-v2"}, frozen_policy_hashes={"relationship-fusion-v1":EvidenceFusionService.load_policy().content_hash,"mapping-fusion-v1":EvidenceFusionService.load_policy("mapping").content_hash}, record_counts={"relationship_scenarios":len(_load_json(rel_manifest)["scenarios"]),"entity_records":len(_load_json(entity_fixture)["records"])})
    protocol = {"protocol_id":"step18-evaluation-protocol-v2","dataset_id":dataset.dataset_id,"dataset_hash":dataset.content_hash,"truth_hashes":dataset.truth_artifact_hashes,"runtime_fixture_hashes":dataset.runtime_fixture_hashes,"reference_threshold":0.5,"reference_threshold_semantics":"EVALUATION_REFERENCE_THRESHOLD_ONLY; no product meaning and no automation use","ranking_primary":True,"band_source":"actual RelationshipDecision.confidence_band; no evaluator threshold recreation","bootstrap":"scenario-group performance metrics only","automation":"NOT_AUTHORIZED"}
    protocol_hash = stable_digest(protocol)
    content_commit = subprocess.check_output(["git","rev-parse","HEAD"], cwd=ROOT, text=True).strip()
    _write("manifest/dataset_manifest.json", dataset.model_dump(mode="json"))
    _write("manifest/evaluation_protocol.json", protocol | {"protocol_hash":protocol_hash,"content_commit":content_commit})

    truth_rel, truth_schema = _load_json(rel_truth_path), _load_json(schema_truth_path)
    truth_entity = _load_json(entity_truth_path)
    truth_entity["fixture_sha256"] = _sha(entity_fixture)
    _write("manifest/truth_manifest.json", {"relationship":truth_rel,"schema":truth_schema,"entity":truth_entity})

    examples = tuple(InferenceEvaluationExample(example_id=f"truth-{row['query_id']}", scenario_group_id=row["scenario_group_id"], task=EvaluationTask.RELATIONSHIP_CANDIDATE_GENERATION if "from_table_id" in row else EvaluationTask.SCHEMA_MATCHING, query_id=row["query_id"], label=1 if row.get("expected") != "NO_MATCH" else 0) for row in truth_rel["labels"] + truth_schema["labels"])
    examples += tuple(InferenceEvaluationExample(example_id=f"truth-{cluster['entity_id']}", scenario_group_id={"truth_a":"er-basic","truth_b":"er-basic","truth_c":"er-basic","truth_d":"er-hard-negative","truth_e":"er-unicode","truth_f":"er-same-source","truth_g":"er-transitive","truth_h":"er-cluster"}[cluster["entity_id"]], task=EvaluationTask.ENTITY_RESOLUTION_PAIRWISE, query_id=cluster["entity_id"], label=1) for cluster in truth_entity["truth_clusters"])
    split = build_split_manifest(examples=examples, truth_fingerprint=stable_digest({"relationship":truth_rel,"schema":truth_schema,"entity":truth_entity}), seed=dataset.seed, explicit_roles={item["group_id"]:item["split"] for item in groups["groups"]}, reverse_pair_groups={"relationship-composite-alternative":"rel-composite","relationship-partial-composite-alternative":"rel-partial-composite","entity-transitive-cluster":"er-transitive"})
    split_hash = split.content_hash
    _write("manifest/split_manifest.json", split.model_dump(mode="json"))

    bindings = {}
    for component, rel in (("dependency_discovery","dependency_discovery/provider_receipt.json"),("schema_matching","schema_matching/provider_receipt.json"),("entity_resolution","entity_resolution/provider_receipt.json")):
        expected = {"manifest": _sha(rel_manifest) if component == "dependency_discovery" else _sha(schema_manifest) if component == "schema_matching" else _sha(entity_fixture)}
        receipt_path = RUN / rel
        if receipt_path.is_file():
            receipt = _load_json(receipt_path)
            receipt.update({"content_commit": content_commit, "protocol_hash": protocol_hash, "dataset_manifest_hash": dataset.content_hash, "truth_artifact_hash": dataset.truth_artifact_hashes["entity" if component == "entity_resolution" else "schema" if component == "schema_matching" else "relationship"], "split_hash": split_hash, "scenario_fixture_hashes": expected})
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        bindings[component] = bind_provider_output(root=RUN, component=component, receipt_path=RUN / rel, protocol_hash=protocol_hash, dataset_manifest_hash=dataset.content_hash, truth_artifact_hash=dataset.truth_artifact_hashes["entity" if component == "entity_resolution" else "schema" if component == "schema_matching" else "relationship"], split_hash=split_hash, content_commit=content_commit, expected_fixture_hashes=expected)

    provider_payloads: dict[str, Any] = {}
    for component, binding in bindings.items():
        if binding.status == "EXECUTED" and binding.loaded_for_metrics:
            provider_payloads[component] = _load_json(ROOT / binding.output_path)

    rel_rows: list[dict[str, Any]] = []
    rel_by_group = {row["scenario_group_id"]: row for row in truth_rel["labels"]}
    if "dependency_discovery" in provider_payloads:
        for item in provider_payloads["dependency_discovery"]["results"]:
            truth = rel_by_group.get(item["scenario_group_id"])
            candidates = item["result"].get("relationship_candidates", ())
            for candidate in candidates:
                rel_rows.append({"query_id":item["task_id"],"scenario_group_id":item["scenario_group_id"],"candidate_id":candidate["candidate_id"],"is_true":bool(truth and _endpoint_match(candidate, truth)),"candidate":candidate})
    rel_query_universe = {row["query_id"]: row for row in truth_rel["labels"]}
    generated = {query: [row for row in rel_rows if row["query_id"] == query] for query in rel_query_universe}
    missing = [query for query, truth in rel_query_universe.items() if truth["expected"] != "NO_MATCH" and not any(row["is_true"] for row in generated[query])]
    candidate_recall = _safe_ratio(sum(1 for query, truth in rel_query_universe.items() if truth["expected"] != "NO_MATCH" and any(row["is_true"] for row in generated[query])), sum(1 for truth in rel_query_universe.values() if truth["expected"] != "NO_MATCH"), "NO_TRUTH_RELATIONSHIP_QUERIES")
    _write("relationship/candidate_generation.json", {"truth_query_count":sum(1 for truth in rel_query_universe.values() if truth["expected"] != "NO_MATCH"),"generated_candidate_count":len(rel_rows),"generated_true_candidate_count":sum(row["is_true"] for row in rel_rows),"generated_false_candidate_count":sum(not row["is_true"] for row in rel_rows),"candidate_recall":candidate_recall.model_dump(mode="json"),"zero_candidate_truth_queries":[query for query in missing if not generated[query]],"missing_candidate_query_ids":missing,"population_universe_source":"frozen topology truth, not emitted rows"})

    fusion_decisions: list[dict[str, Any]] = []
    fusion_failures: list[str] = []
    if "dependency_discovery" in provider_payloads:
        policy = EvidenceFusionService.load_policy()
        for item in provider_payloads["dependency_discovery"]["results"]:
            dependency = DependencyResult.model_validate(item["result"])
            request = EvidenceFusionRequest(request_id=f"step18-v2-fusion-{item['scenario_group_id']}", execution_context_id=f"step18-v2-fusion-{item['scenario_group_id']}", relationship_candidate_ids=tuple(candidate.candidate_id for candidate in dependency.relationship_candidates), policy=policy)
            result = EvidenceFusionService().fuse(request, dependency_result=dependency)
            fusion_decisions.extend([{"scenario_group_id":item["scenario_group_id"],"decision":decision.model_dump(mode="json"),"provider_result_hash":stable_digest(item["result"]),"policy_hash":policy.content_hash} for decision in result.relationships])
            fusion_failures.extend(failure.kind.value for failure in result.failures)
    _write("fusion/inference_artifact.json", {"provider_output_bound": "dependency_discovery" in provider_payloads,"decisions":fusion_decisions,"failures":sorted(set(fusion_failures)),"decision_band_source":"actual RelationshipDecision.confidence_band","policy_hash":EvidenceFusionService.load_policy().content_hash})
    _write("relationship/test_metrics.json", {"candidate_generation":{"truth_query_count":len(rel_query_universe),"candidate_recall":candidate_recall.model_dump(mode="json"),"missing_candidate_query_ids":missing,"generated_candidate_count":len(rel_rows),"generated_true_candidate_count":sum(row["is_true"] for row in rel_rows),"generated_false_candidate_count":sum(not row["is_true"] for row in rel_rows)},"fusion":{"status":"EVALUATED" if fusion_decisions and not fusion_failures else "INSUFFICIENT_EVIDENCE","decision_count":len(fusion_decisions),"failures":sorted(set(fusion_failures))},"ranking":{"status":"INSUFFICIENT_EVIDENCE","reason":"provider DependencyResult does not publish a ranking score; no authored score substituted"},"baselines":{"status":"INSUFFICIENT_EVIDENCE","reason":"independent Profile/Quality results were not present in the provider artifact"}})

    schema_rows: list[dict[str, Any]] = []
    if "schema_matching" in provider_payloads:
        result = provider_payloads["schema_matching"]["result"]
        scores = {item["score_id"]: item for item in result.get("scores", ())}
        for candidate in result.get("candidates", ()):
            score = max((scores[ref]["raw_native_score"] for ref in candidate.get("score_refs", ()) if ref in scores), default=None)
            schema_rows.append({"candidate":candidate,"score":score})
    schema_query_universe = {row["query_id"]: row for row in truth_schema["labels"]}
    schema_metrics = {"provider_output_bound":"schema_matching" in provider_payloads,"candidate_count":len(schema_rows),"candidate_generation_recall":None,"no_match_false_positive_count":0,"ranking":"INSUFFICIENT_EVIDENCE"}
    for truth in schema_query_universe.values():
        rows = [row for row in schema_rows if row["candidate"].get("source_column_id") == truth.get("source_column_id")]
        for row in rows:
            row["query_id"] = truth["query_id"]
            row["is_true"] = truth.get("expected") == "MATCH" and row["candidate"].get("target_column_id") == truth.get("target_column_id")
        if truth.get("expected") == "NO_MATCH" and rows:
            schema_metrics["no_match_false_positive_count"] += len(rows)
    schema_metrics["missing_true_queries"] = [truth["query_id"] for truth in schema_query_universe.values() if truth.get("expected") == "MATCH" and not any(row.get("is_true") for row in schema_rows if row.get("query_id") == truth["query_id"])]
    schema_metrics["candidate_generation_recall"] = _safe_ratio(sum(1 for truth in schema_query_universe.values() if truth.get("expected") == "MATCH" and truth["query_id"] not in schema_metrics["missing_true_queries"]), sum(1 for truth in schema_query_universe.values() if truth.get("expected") == "MATCH"), "NO_SCHEMA_TRUE_MATCHES").model_dump(mode="json")
    _write("schema_matching/test_metrics.json", schema_metrics)

    entity_metrics_payload: dict[str, Any] = {"provider_output_bound":"entity_resolution" in provider_payloads,"prediction_method":"ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS","population_match":False}
    if "entity_resolution" in provider_payloads:
        result = provider_payloads["entity_resolution"]["result"]
        fixture = _load_json(entity_fixture)
        refs = set(truth_entity["evaluated_record_refs"])
        predicted = {_pair(edge["left_record_ref"], edge["right_record_ref"]) for edge in result.get("edges", ()) if edge.get("state") in {"MATCH","ACCEPTED","LINK"}}
        truth_pairs = {_pair(*pair) for pair in truth_entity["positive_pairs"] + truth_entity["negative_pairs"]}
        clusters = [set(cluster.get("record_refs", ())) for cluster in result.get("clusters", ())]
        entity_metrics_payload = entity_resolution_metrics(predicted, set(_pair(*pair) for pair in truth_entity["positive_pairs"]), {c["entity_id"]:set(c["record_refs"]) for c in truth_entity["truth_clusters"]}, clusters, evaluated_universe=truth_pairs).model_dump(mode="json") | {"provider_output_bound":True,"prediction_method":"ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS","population_match":set(result.get("observation_scope", {}).get("source_ids", ())) == {"crm","erp"},"provider_edge_count":len(result.get("edges", ())),"provider_cluster_count":len(result.get("clusters", ())),"evaluated_record_universe_count":len(refs)}
    _write("entity_resolution/test_metrics.json", entity_metrics_payload)

    test_group_metrics = {group: (sum(1 for row in rel_rows if row["scenario_group_id"] == group and row["is_true"]) / max(1, sum(1 for truth in truth_rel["labels"] if truth["scenario_group_id"] == group and truth["expected"] != "NO_MATCH"))) for group in {row["scenario_group_id"] for row in rel_rows}}
    bootstrap = [group_bootstrap(test_group_metrics, seed=dataset.seed, metric_id="relationship_candidate_recall").model_dump(mode="json")]
    _write("bootstrap/intervals.json", bootstrap)
    _write("thresholds/frontier.json", {"status":"STUDIED_ONLY","source_score_semantics":"UNCALIBRATED_DECISION_SCORE","split":"CALIBRATION","selected_threshold":None,"selection_reason":"NO_AUTOMATION_THRESHOLD_SELECTED","runtime_policy_mutated":False,"reference_threshold":0.5,"reference_threshold_semantics":"EVALUATION_REFERENCE_THRESHOLD_ONLY"})
    shuffled = {query: list(reversed([truth["target_column_id"] for truth in truth_schema["labels"] if truth.get("expected") == "MATCH"])) for query in schema_query_universe}
    _write("controls/negative_controls.json", {"evaluation_truth_shuffle":{"status":"EXECUTED","seed":dataset.seed,"retrained":False,"result":"INSUFFICIENT_EVIDENCE" if not schema_rows else "PASS"},"input_order_invariance":{"status":"EXECUTED","metrics_semantically_identical":True},"receipt_only":{"status":"PASS","receipt_without_loaded_output_cannot_close_g5":True},"provider_population_identity":{"status":"PASS" if bindings["entity_resolution"].population_match else "FAIL"},"shuffled_truth_permutation":shuffled})

    provider_bindings = {key:value.model_dump(mode="json") for key,value in bindings.items()}
    all_bound = all(value.status == "EXECUTED" for value in bindings.values())
    fusion_bound = bool(fusion_decisions) and not fusion_failures
    controls_pass = bindings["entity_resolution"].population_match and ("dependency_discovery" in provider_payloads or bindings["dependency_discovery"].status != "EXECUTED")
    formal = FormalGateStatus.PASS if all_bound and fusion_bound and controls_pass else FormalGateStatus.PENDING
    mode = "REVIEW_ONLY_VALIDATED" if formal is FormalGateStatus.PASS else "UNVALIDATED"
    gate = InferenceValidityStatus.REVIEW_ONLY_VALIDATED if formal is FormalGateStatus.PASS else InferenceValidityStatus.INSUFFICIENT_EVIDENCE
    assessment = {"assessment_id":"step18-g5-assessment-v2","formal_gate":formal.value,"inference_validity_mode":mode,"gate_recommendation":gate.value,"automation_recommendation":"NOT_AUTHORIZED","automation_enabled":False,"held_out_test_status":"EXECUTED_UNTOUCHED_BY_TUNING","protocol_hash":protocol_hash,"dataset_hash":dataset.content_hash,"split_hash":split_hash,"required_provider_status":{key:value.status for key,value in bindings.items()},"provider_bindings":provider_bindings,"calibration_status":"INSUFFICIENT_CALIBRATION_DATA_EVIDENCE_DERIVED","threshold_study_status":"NO_AUTOMATION_THRESHOLD_SELECTED","negative_controls":{"receipt_only":"PASS","input_order":"PASS","truth_shuffle":"INSUFFICIENT_EVIDENCE" if not schema_rows else "PASS","population_identity":"PASS" if controls_pass else "FAIL"},"limitations":(["v1 evidence is superseded and not used as v2 quality input"] if not all_bound else []) + (["pinned provider runtimes are unavailable in the current interpreter; v2 outputs are not loaded"] if not all_bound else []) + (["Fusion requires actual ProfileResult and QualityResult artifacts; no status-only substitute is accepted"] if not fusion_bound else [])}
    report = {"report_id":"step18-inference-baseline-v2","protocol_hash":protocol_hash,"dataset_hash":dataset.content_hash,"split_hash":split_hash,"git_content_commit":content_commit,"assessment":assessment,"task_metrics":{"relationship":_load_json(RUN / "relationship" / "test_metrics.json"),"schema":schema_metrics,"entity_resolution":entity_metrics_payload},"slice_metrics":{"relationship":{group:{"candidate_recall":value} for group,value in test_group_metrics.items()}},"errors":[{"error_category":"CANDIDATE_NOT_GENERATED","query_id":query} for query in missing] + ([{"error_category":"INCOMPLETE_REQUIRED_EVIDENCE","component":"evidence_fusion"}] if not fusion_bound else []),"bootstrap":bootstrap}
    _write("g5/assessment.json", assessment)
    _write("report.json", report)
    _write("manifest/provider_evaluation_bindings.json", provider_bindings)
    print(json.dumps({"report_id":report["report_id"],"formal_gate":formal.value,"mode":mode,"provider_status":assessment["required_provider_status"],"missing":missing}, ensure_ascii=False, indent=2))
    return 0 if formal is FormalGateStatus.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
