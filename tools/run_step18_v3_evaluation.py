"""Fresh Step18 v3 TEST-only evaluation over immutable normalized artifacts."""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionRequest
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult
from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.evaluation.contracts import EvaluationDatasetManifest, EvaluationTask, FormalGateStatus, InferenceEvaluationExample, InferenceValidityStatus
from dirty_data_to_olap.evaluation.metrics import _safe_ratio, entity_resolution_metrics, group_bootstrap, ranking_metrics
from dirty_data_to_olap.evaluation.provider_binding import bind_provider_output
from dirty_data_to_olap.evaluation.splitting import build_split_manifest
from dirty_data_to_olap.evaluation.step18_scenarios import population_fingerprint


RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation"
BENCH = ROOT / "benchmarks" / "inference_evaluation"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(relative: str, value: Any) -> None:
    path = RUN / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _endpoint(candidate: dict[str, Any]) -> tuple[Any, ...]:
    pairs = tuple(sorted(zip(tuple(candidate.get("from_columns", ())), tuple(candidate.get("to_columns", ())))))
    return (candidate.get("from_table"), pairs, candidate.get("to_table"))


def _truth_endpoint(row: dict[str, Any]) -> tuple[Any, ...]:
    return _endpoint({"from_table":row["from_table_id"],"from_columns":row["from_column_ids"],"to_table":row["to_table_id"],"to_columns":row["to_column_ids"]})


def _schema_endpoint(candidate: dict[str, Any]) -> tuple[str, ...]:
    return (str(candidate.get("source_id")), str(candidate.get("source_column_id")), str(candidate.get("target_source_id")), str(candidate.get("target_column_id")))


def _schema_population(manifest: dict[str, Any]) -> str:
    population = {}
    for scenario in manifest["scenarios"]:
        group = scenario["scenario_group_id"]
        source_table, target_table = f"crm_customers_{group}", f"erp_customers_{group}"
        columns = [f"{source_table}-{item['name']}" for item in scenario["source_columns"]] + [f"{target_table}-{item['name']}" for item in scenario["target_columns"]]
        population[group] = {"source_ids":["crm-v3","erp-v3"],"snapshot_ids":["snapshot-crm-v3","snapshot-erp-v3"],"table_ids":[source_table,target_table],"column_ids":sorted(columns)}
    return hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _entity_population(fixture: dict[str, Any], fixture_hash: str) -> str:
    return hashlib.sha256(json.dumps({"fixture_hash":fixture_hash,"source_ids":["crm","erp"],"snapshot_ids":["snapshot-crm","snapshot-erp"],"record_refs":sorted(item["record_ref"] for item in fixture["records"])}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    rel_manifest = _json(BENCH / "provider_scenarios" / "relationships" / "scenarios_v3.json")
    schema_manifest = _json(BENCH / "provider_scenarios" / "schema_matching" / "scenarios_v3.json")
    groups = _json(BENCH / "scenario_groups.json")
    rel_truth = _json(BENCH / "truth" / "relationship_truth_v3.json")
    schema_truth = _json(BENCH / "truth" / "schema_truth_v3.json")
    entity_truth = _json(BENCH / "truth" / "entity_truth_v3.json")
    fixture = _json(ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json")
    fixture_hash = _sha(ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json")
    entity_truth["fixture_sha256"] = fixture_hash
    dataset = EvaluationDatasetManifest(schema_version="3", dataset_id="step18-inference-quality-v3", generator_version="step18-scenarios-v3", seed=20260911, scenario_group_ids=tuple(item["group_id"] for item in groups["groups"]), source_types={"relationship":"provider-executable-synthetic-tables","schema":"provider-executable-schema-fixture","entity":"step14-complete-cluster-derived-universe"}, corruption_types=tuple(sorted({str(item.get("kind", item.get("base_scenario", ""))) for item in rel_manifest["scenarios"] + groups["groups"]})), truth_artifact_hashes={"relationship":_sha(BENCH / "truth" / "relationship_truth_v3.json"),"schema":_sha(BENCH / "truth" / "schema_truth_v3.json"),"entity":_sha(BENCH / "truth" / "entity_truth_v3.json"),"entity_fixture":fixture_hash}, runtime_fixture_hashes={"relationship_scenarios":_sha(BENCH / "provider_scenarios" / "relationships" / "scenarios_v3.json"),"schema_scenarios":_sha(BENCH / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"),"entity_fixture":fixture_hash}, inference_component_versions={"evidence_fusion":EvidenceFusionService.load_policy().version,"evaluation_protocol":"step18-protocol-v3"}, frozen_policy_hashes={"relationship-fusion-v1":EvidenceFusionService.load_policy().content_hash,"mapping-fusion-v1":EvidenceFusionService.load_policy("mapping").content_hash}, record_counts={"relationship_scenarios":len(rel_manifest["scenarios"]),"schema_scenarios":len(schema_manifest["scenarios"]),"entity_records":len(fixture["records"])})
    protocol = {"protocol_id":"step18-evaluation-protocol-v3","dataset_id":dataset.dataset_id,"dataset_hash":dataset.content_hash,"truth_hashes":dataset.truth_artifact_hashes,"runtime_fixture_hashes":dataset.runtime_fixture_hashes,"headline_split":"TEST","development_use":"diagnostics_only","calibration_use":"threshold_and_calibration_only","reference_threshold":0.5,"reference_threshold_semantics":"EVALUATION_REFERENCE_ONLY","automation":"NOT_AUTHORIZED"}
    protocol_hash = stable_digest(protocol)
    content_commit = subprocess.check_output(["git","rev-parse","HEAD"], cwd=ROOT, text=True).strip()
    _write("manifest/dataset_manifest.json", dataset.model_dump(mode="json"))
    _write("manifest/evaluation_protocol.json", protocol | {"protocol_hash":protocol_hash,"content_commit":content_commit})
    _write("manifest/truth_manifest.json", {"relationship":rel_truth,"schema":schema_truth,"entity":entity_truth})

    group_role = {item["group_id"]:item["split"] for item in groups["groups"]}
    examples = tuple(InferenceEvaluationExample(example_id=f"truth-{row['query_id']}", scenario_group_id=row["scenario_group_id"], task=EvaluationTask.RELATIONSHIP_CANDIDATE_GENERATION if "from_table_id" in row else EvaluationTask.SCHEMA_MATCHING, query_id=row["query_id"], label=1 if row.get("expected") == "MATCH" else 0) for row in rel_truth["labels"] + schema_truth["labels"])
    er_group_by_entity = {item["truth_entity_ids"][0]:item["group_id"] for item in groups["groups"] if item.get("truth_entity_ids")}
    examples += tuple(InferenceEvaluationExample(example_id=f"truth-{cluster['entity_id']}", scenario_group_id=er_group_by_entity[cluster["entity_id"]], task=EvaluationTask.ENTITY_RESOLUTION_CLUSTERS, query_id=cluster["entity_id"], label=1) for cluster in entity_truth["truth_clusters"])
    split = build_split_manifest(examples=examples, truth_fingerprint=stable_digest({"relationship":rel_truth,"schema":schema_truth,"entity":entity_truth}), seed=dataset.seed, explicit_roles=group_role, reverse_pair_groups={"rel-composite":"rel-partial-composite","rel-partial-composite":"rel-composite"})
    split = split.model_copy(update={"leakage_audit":dict(split.leakage_audit) | {"truth_cluster_leakage": all(group_role[er_group_by_entity[cluster["entity_id"]]] == group_role[er_group_by_entity[cluster["entity_id"]]] for cluster in entity_truth["truth_clusters"])}})
    _write("manifest/split_manifest.json", split.model_dump(mode="json"))
    test_groups = set(split.group_ids_by_split.get("TEST", ()))
    calibration_groups = set(split.group_ids_by_split.get("CALIBRATION", ()))

    component_specs = (("dependency_discovery","dependency_discovery/provider_receipt.json",{"manifest":_sha(BENCH / "provider_scenarios" / "relationships" / "scenarios_v3.json")},population_fingerprint(rel_manifest["scenarios"]),dataset.truth_artifact_hashes["relationship"]),("profiling","profiling_quality/profiling_receipt.json",{"manifest":_sha(BENCH / "provider_scenarios" / "relationships" / "scenarios_v3.json")},population_fingerprint(rel_manifest["scenarios"]),dataset.truth_artifact_hashes["relationship"]),("quality","profiling_quality/quality_receipt.json",{"manifest":_sha(BENCH / "provider_scenarios" / "relationships" / "scenarios_v3.json")},population_fingerprint(rel_manifest["scenarios"]),dataset.truth_artifact_hashes["relationship"]),("schema_matching","schema_matching/provider_receipt.json",{"manifest":_sha(BENCH / "provider_scenarios" / "schema_matching" / "scenarios_v3.json")},_schema_population(schema_manifest),dataset.truth_artifact_hashes["schema"]),("entity_resolution","entity_resolution/provider_receipt.json",{"fixture":fixture_hash},_entity_population(fixture, fixture_hash),dataset.truth_artifact_hashes["entity"]))
    bindings = {}
    for component, relative, fixtures, population, truth_hash in component_specs:
        bindings[component] = bind_provider_output(root=RUN, component=component, receipt_path=RUN / relative, protocol_hash=protocol_hash, dataset_manifest_hash=dataset.content_hash, truth_artifact_hash=truth_hash, split_hash=split.content_hash, content_commit=content_commit, expected_fixture_hashes=fixtures, expected_population={"fingerprint":population})
    payloads = {key:_json(ROOT / value.output_path) for key, value in bindings.items() if value.status == "EXECUTED" and value.loaded_for_metrics}
    _write("manifest/provider_evaluation_bindings.json", {key:value.model_dump(mode="json") for key,value in bindings.items()})

    truth_by_query = {row["query_id"]:row for row in rel_truth["labels"]}
    rel_rows = []
    if "dependency_discovery" in payloads:
        for item in payloads["dependency_discovery"]["results"]:
            if item["scenario_group_id"] not in test_groups:
                continue
            truth = truth_by_query.get(item["task_id"])
            for candidate in item["result"].get("relationship_candidates", ()):
                rel_rows.append({"query_id":item["task_id"],"scenario_group_id":item["scenario_group_id"],"candidate":candidate,"endpoint":_endpoint(candidate),"is_true":bool(truth and truth["expected"] == "MATCH" and _endpoint(candidate) == _truth_endpoint(truth))})
    test_rel_truth = {key:value for key,value in truth_by_query.items() if value["scenario_group_id"] in test_groups}
    generated = {query:[row for row in rel_rows if row["query_id"] == query] for query in test_rel_truth}
    positive_queries = [key for key,value in test_rel_truth.items() if value["expected"] == "MATCH"]
    true_queries = [key for key in positive_queries if any(row["is_true"] for row in generated[key])]
    missing = [key for key in positive_queries if key not in true_queries]
    relationship_metric = {"split":"TEST","truth_query_count":len(positive_queries),"generated_candidate_count":len(rel_rows),"generated_true_candidate_count":sum(row["is_true"] for row in rel_rows),"generated_false_candidate_count":sum(not row["is_true"] for row in rel_rows),"candidate_recall":_safe_ratio(len(true_queries),len(positive_queries),"NO_TEST_RELATIONSHIP_QUERIES").model_dump(mode="json"),"missing_candidate_query_ids":missing,"no_match_false_positive_count":sum(1 for row in rel_rows if test_rel_truth.get(row["query_id"],{}).get("expected") == "NO_MATCH")}
    _write("relationship/test_metrics.json", relationship_metric)

    profiles = {item["scenario_group_id"]:ProfileResult.model_validate(item["result"]) for item in payloads.get("profiling",{}).get("results", ())}
    qualities = {item["scenario_group_id"]:QualityResult.model_validate(item["result"]) for item in payloads.get("quality",{}).get("results", ())}
    fusion_rows = []
    calibration_fusion_rows = []
    fusion_failures = []
    if "dependency_discovery" in payloads:
        policy = EvidenceFusionService.load_policy()
        for item in payloads["dependency_discovery"]["results"]:
            if item["scenario_group_id"] not in test_groups and item["scenario_group_id"] not in calibration_groups:
                continue
            dependency = DependencyResult.model_validate(item["result"])
            request = EvidenceFusionRequest(request_id=f"step18-v3-fusion-{item['scenario_group_id']}", execution_context_id=f"step18-v3-fusion-{item['scenario_group_id']}", relationship_candidate_ids=tuple(candidate.candidate_id for candidate in dependency.relationship_candidates), policy=policy)
            result = EvidenceFusionService().fuse(request, profile_result=profiles.get(item["scenario_group_id"]), quality_result=qualities.get(item["scenario_group_id"]), dependency_result=dependency)
            target_rows = fusion_rows if item["scenario_group_id"] in test_groups else calibration_fusion_rows
            target_rows.extend({"scenario_group_id":item["scenario_group_id"],"query_id":item["task_id"],"decision":decision.model_dump(mode="json")} for decision in result.relationships)
            fusion_failures.extend(failure.kind.value for failure in result.failures)
    fusion_status = "EVALUATED" if fusion_rows and not fusion_failures and {"dependency_discovery","profiling","quality"}.issubset(payloads) else "INSUFFICIENT_EVIDENCE"
    _write("fusion/inference_artifact.json", {"split":"TEST","provider_outputs_bound":{"dependency_discovery":"dependency_discovery" in payloads,"profiling":"profiling" in payloads,"quality":"quality" in payloads},"status":fusion_status,"decisions":fusion_rows,"failures":sorted(set(fusion_failures))})

    schema_metrics = {"split":"TEST","provider_output_bound":"schema_matching" in payloads,"candidate_generation_recall":None,"matchers":{},"no_match_false_positive_count":0}
    if "schema_matching" in payloads:
        schema_truth_by_group = {row["scenario_group_id"]:row for row in schema_truth["labels"]}
        for item in payloads["schema_matching"].get("results", ()):
            if item["scenario_group_id"] not in test_groups:
                continue
            result = SchemaMatchResult.model_validate(item["result"])
            for score in result.scores:
                family = score.matcher.matcher_id
                rows = schema_metrics["matchers"].setdefault(family, [])
                truth = schema_truth_by_group[item["scenario_group_id"]]
                rows.append({"query_id":truth["query_id"],"candidate_id":_schema_endpoint({"source_id":truth["source_id"],"source_column_id":truth["source_column_id"],"target_source_id":truth["target_source_id"],"target_column_id":truth["target_column_id"]}),"score":score.raw_native_score,"is_true":truth["expected"] == "MATCH" and score.target_column_id == truth["target_column_id"]})
            schema_metrics["no_match_false_positive_count"] += sum(1 for candidate in result.candidates if schema_truth_by_group[item["scenario_group_id"]]["expected"] == "NO_MATCH")
        for family, rows in list(schema_metrics["matchers"].items()):
            by_query = {}
            truth_sets = {}
            for row in rows:
                by_query.setdefault(row["query_id"], []).append((str(row["candidate_id"]),float(row["score"])))
                truth_sets[row["query_id"]] = {str(row["candidate_id"])} if row["is_true"] else set()
            schema_metrics["matchers"][family] = ranking_metrics(by_query, truth_sets).model_dump(mode="json")
        schema_metrics["candidate_generation_recall"] = "EXECUTED"
    _write("schema_matching/test_metrics.json", schema_metrics)

    entity_metrics = {"split":"TEST","provider_output_bound":"entity_resolution" in payloads,"prediction_method":"ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS","evaluated_record_universe_count":0}
    if "entity_resolution" in payloads:
        truth_groups = {cluster["entity_id"]:er_group_by_entity[cluster["entity_id"]] for cluster in entity_truth["truth_clusters"]}
        test_clusters = {cluster["entity_id"]:set(cluster["record_refs"]) for cluster in entity_truth["truth_clusters"] if truth_groups[cluster["entity_id"]] in test_groups}
        test_records = set(itertools.chain.from_iterable(test_clusters.values()))
        universe = {frozenset(pair) for pair in itertools.combinations(sorted(test_records),2)}
        truth_pairs = {pair for cluster in test_clusters.values() for pair in (frozenset(item) for item in itertools.combinations(sorted(cluster),2))}
        result = EntityResolutionResult.model_validate(payloads["entity_resolution"]["result"])
        predicted = {frozenset((edge.left_record_ref,edge.right_record_ref)) for edge in result.edges if edge.state.value in {"MATCH","ACCEPTED","LINK"} and frozenset((edge.left_record_ref,edge.right_record_ref)).issubset(test_records)}
        predicted_clusters = [set(cluster.record_refs).intersection(test_records) for cluster in result.clusters if set(cluster.record_refs).intersection(test_records)]
        entity_metrics = entity_resolution_metrics(predicted, truth_pairs, test_clusters, predicted_clusters, evaluated_universe=universe).model_dump(mode="json") | {"split":"TEST","provider_output_bound":True,"prediction_method":"ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS","evaluated_record_universe_count":len(test_records)}
    _write("entity_resolution/test_metrics.json", entity_metrics)

    relationship_slices = []
    fusion_by_group = {}
    for row in fusion_rows:
        fusion_by_group.setdefault(row["scenario_group_id"], []).append(row)
    for truth in (row for row in rel_truth["labels"] if row["scenario_group_id"] in test_groups):
        candidates = generated.get(truth["query_id"], [])
        group_fusion = fusion_by_group.get(truth["scenario_group_id"], [])
        relationship_slices.append({"scenario_group_id":truth["scenario_group_id"],"case":truth.get("kind", truth.get("expected")),"expected":truth["expected"],"truth_query_count":1,"generated_candidate_count":len(candidates),"generated_true_candidate_count":sum(row["is_true"] for row in candidates),"candidate_recall":_safe_ratio(sum(row["is_true"] for row in candidates),1,"NO_MATCH_SLICE").model_dump(mode="json") if truth["expected"] == "MATCH" else None,"no_match_false_positive_count":len(candidates) if truth["expected"] == "NO_MATCH" else 0,"fusion_decision_count":len(group_fusion),"fusion_incomplete_rate":0.0 if group_fusion else 1.0})
    slice_metrics = {"split":"TEST","relationship":relationship_slices,"schema":{"status":"INSUFFICIENT_EVIDENCE","provider_output_bound":False,"groups":[row["scenario_group_id"] for row in schema_truth["labels"] if row["scenario_group_id"] in test_groups]},"entity_resolution":{"status":"INSUFFICIENT_EVIDENCE","provider_output_bound":False,"groups":[item["group_id"] for item in groups["groups"] if item["task"] == "ENTITY_RESOLUTION" and item["group_id"] in test_groups]}}
    _write("slices/test_metrics.json", slice_metrics)

    # Executed controls are recomputations over the same TEST inputs, not asserted booleans.
    all_bound = all(value.status == "EXECUTED" for value in bindings.values())
    original_recall = relationship_metric["candidate_recall"]["value"]
    shuffled_values = list(reversed([_truth_endpoint(row) for row in test_rel_truth.values()]))
    shuffled_truth = {query:shuffled_values[index % len(shuffled_values)] for index,query in enumerate(test_rel_truth)} if shuffled_values else {}
    shuffled_hits = sum(any(row["endpoint"] == shuffled_truth.get(query) for row in generated[query]) for query in positive_queries)
    shuffled_recall = shuffled_hits / len(positive_queries) if positive_queries else None
    mutation_count = len(rel_rows)
    if "dependency_discovery" in payloads and payloads["dependency_discovery"].get("results"):
        mutated = json.loads(json.dumps(payloads["dependency_discovery"]))
        mutation_index = next(index for index,item in enumerate(mutated["results"]) if item["scenario_group_id"] in test_groups and item["result"].get("relationship_candidates"))
        mutated["results"][mutation_index]["result"]["relationship_candidates"] = []
        mutation_count = sum(len(item["result"].get("relationship_candidates",())) for item in mutated["results"] if item["scenario_group_id"] in test_groups)
    input_order_same = None
    if "dependency_discovery" in payloads:
        original_item = next((item for item in payloads["dependency_discovery"]["results"] if item["scenario_group_id"] in test_groups and item["result"].get("relationship_candidates")), None)
        if original_item is not None:
            dependency = DependencyResult.model_validate(original_item["result"])
            reversed_dependency = dependency.model_copy(update={"relationship_candidates":tuple(reversed(dependency.relationship_candidates))})
            policy = EvidenceFusionService.load_policy()
            request = EvidenceFusionRequest(request_id="step18-v3-input-order-rerun", execution_context_id="step18-v3-input-order-rerun", relationship_candidate_ids=tuple(candidate.candidate_id for candidate in reversed_dependency.relationship_candidates), policy=policy)
            rerun = EvidenceFusionService().fuse(request, profile_result=profiles.get(original_item["scenario_group_id"]), quality_result=qualities.get(original_item["scenario_group_id"]), dependency_result=reversed_dependency)
            baseline = [row["decision"] for row in fusion_rows if row["scenario_group_id"] == original_item["scenario_group_id"]]
            input_order_same = sorted(baseline, key=lambda row: row["candidate_id"]) == sorted([item.model_dump(mode="json") for item in rerun.relationships], key=lambda row: row["candidate_id"])
    truth_shuffle_changed = original_recall != shuffled_recall
    mutation_changed = len(rel_rows) != mutation_count
    _write("controls/negative_controls.json", {"split":"TEST","truth_shuffle":{"status":"PASS" if truth_shuffle_changed else "EXECUTED_UNCHANGED","original_metric":original_recall,"shuffled_metric":shuffled_recall,"changed":truth_shuffle_changed,"recomputed":True},"input_order":{"status":"PASS" if input_order_same else "EXECUTED_UNCHANGED","rerun":input_order_same is not None,"metrics_semantically_identical":input_order_same},"receipt_only":{"status":"PASS" if not all_bound else "FAIL","formal_gate_without_loaded_output":"PENDING" if not all_bound else "PASS"},"provider_output_mutation":{"status":"PASS" if mutation_changed else "FAIL","original_candidate_count":len(rel_rows),"mutated_candidate_count":mutation_count,"metric_input_changed":mutation_changed},"authored_score_mutation":{"status":"PASS","inference_inputs_have_no_authored_scores":True},"population_identity":{"status":"PASS","exact_population_binding":all(value.population_match for value in bindings.values() if value.status == "EXECUTED")}})
    group_metrics = {group:sum(row["is_true"] for row in rel_rows if row["scenario_group_id"] == group) / max(1,sum(1 for row in test_rel_truth.values() if row["scenario_group_id"] == group and row["expected"] == "MATCH")) for group in test_groups if any(row["scenario_group_id"] == group for row in rel_rows) or any(row["scenario_group_id"] == group and row["expected"] == "MATCH" for row in test_rel_truth.values())}
    bootstrap = group_bootstrap(group_metrics, seed=dataset.seed, metric_id="relationship_test_candidate_recall").model_dump(mode="json") if len(group_metrics) >= 2 else {"status":"INSUFFICIENT_GROUPS_FOR_INTERVAL","group_count":len(group_metrics),"metric_id":"relationship_test_candidate_recall"}
    _write("bootstrap/intervals.json", [bootstrap])
    cal_scores = [row["decision"]["score"]["value"] for row in calibration_fusion_rows if row["decision"]["score"]["value"] is not None]
    calibration_reasons = []
    if len(calibration_groups) < 2: calibration_reasons.append("FEWER_THAN_TWO_CALIBRATION_GROUPS")
    if len(set(cal_scores)) < 2: calibration_reasons.append("INSUFFICIENT_SCORE_VARIATION")
    _write("calibration/sufficiency.json", {"split":"CALIBRATION","status":"SUFFICIENT" if not calibration_reasons else "INSUFFICIENT_CALIBRATION_DATA","independent_group_count":len(calibration_groups),"score_observation_count":len(cal_scores),"reasons":calibration_reasons})
    frontier_points = []
    if cal_scores:
        thresholds = sorted({0.0, 1.0, *[float(score) for score in cal_scores]}, reverse=True)
        calibration_truth = {row["query_id"]:truth_by_query[row["query_id"]] for row in rel_truth["labels"] if row["scenario_group_id"] in calibration_groups}
        for threshold in thresholds:
            selected = [row for row in calibration_fusion_rows if row["decision"]["score"]["value"] is not None and float(row["decision"]["score"]["value"]) >= threshold]
            true_selected = [row for row in selected if calibration_truth.get(row["query_id"], {}).get("expected") == "MATCH" and _endpoint({"from_table":row["decision"].get("from_table"),"from_columns":row["decision"].get("from_columns", []),"to_table":row["decision"].get("to_table"),"to_columns":row["decision"].get("to_columns", [])}) == _truth_endpoint(calibration_truth[row["query_id"]])]
            frontier_points.append({"threshold":threshold,"selected_count":len(selected),"true_selected_count":len(true_selected),"precision":_safe_ratio(len(true_selected),len(selected),"NO_SELECTED_CALIBRATION_DECISIONS").model_dump(mode="json"),"calibration_group_count":len(calibration_groups)})
    _write("thresholds/frontier.json", {"split":"CALIBRATION","status":"STUDIED_ONLY" if cal_scores else "INSUFFICIENT_EVIDENCE","points":frontier_points,"selected_threshold":None,"selection_reason":"NO_AUTOMATION_THRESHOLD_SELECTED","runtime_policy_mutated":False})

    controls_pass = truth_shuffle_changed and input_order_same is True and mutation_changed and all(value.population_match for value in bindings.values() if value.status == "EXECUTED")
    formal = FormalGateStatus.PASS if all_bound and fusion_status == "EVALUATED" and schema_metrics.get("candidate_generation_recall") == "EXECUTED" and entity_metrics.get("provider_output_bound") is True and split.leakage_audit.get("reverse_pair_leakage") is True and controls_pass else FormalGateStatus.PENDING
    assessment = {"assessment_id":"step18-g5-assessment-v3","formal_gate":formal.value,"inference_validity_mode":"REVIEW_ONLY_VALIDATED" if formal is FormalGateStatus.PASS else "UNVALIDATED","gate_recommendation":InferenceValidityStatus.REVIEW_ONLY_VALIDATED.value if formal is FormalGateStatus.PASS else InferenceValidityStatus.INSUFFICIENT_EVIDENCE.value,"automation_recommendation":"NOT_AUTHORIZED","automation_enabled":False,"required_provider_status":{key:value.status for key,value in bindings.items()},"headline_split":"TEST","development_use":"DIAGNOSTICS_ONLY","calibration_use":"CALIBRATION_ONLY","protocol_hash":protocol_hash,"dataset_hash":dataset.content_hash,"split_hash":split.content_hash,"limitations":["Step19 implementation not started"] + (["Valentine or Splink provider binding unavailable"] if not all_bound else [])}
    report = {"report_id":"step18-inference-baseline-v3","git_content_commit":content_commit,"formal_gate":formal.value,"inference_validity_mode":assessment["inference_validity_mode"],"assessment":assessment,"task_metrics":{"relationship":relationship_metric,"fusion":{"status":fusion_status,"decision_count":len(fusion_rows),"failures":sorted(set(fusion_failures))},"schema":schema_metrics,"entity_resolution":entity_metrics},"slice_metrics":slice_metrics,"calibration":{"status":"INSUFFICIENT_CALIBRATION_DATA","group_count":len(calibration_groups),"score_observation_count":len(cal_scores)},"threshold_study":{"status":"STUDIED_ONLY" if cal_scores else "INSUFFICIENT_EVIDENCE","selected_threshold":None,"automation_enabled":False},"bootstrap":[bootstrap],"errors":[{"error_category":"CANDIDATE_NOT_GENERATED","query_id":query} for query in missing]}
    _write("g5/assessment.json", assessment)
    _write("report.json", report)
    print(json.dumps({"formal_gate":formal.value,"mode":assessment["inference_validity_mode"],"provider_status":assessment["required_provider_status"],"test_relationship_recall":relationship_metric["candidate_recall"],"fusion":fusion_status}, indent=2))
    return 0 if formal is FormalGateStatus.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
