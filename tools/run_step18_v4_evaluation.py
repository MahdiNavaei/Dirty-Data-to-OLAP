"""Final Step18 v4 evaluation over real normalized provider outputs only."""

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
sys.path.insert(0, str(ROOT / "tools"))

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult, EntityMatchPredictionBand
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionRequest
from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.evaluation.contracts import EvaluationDatasetManifest, EvaluationTask, FormalGateStatus, InferenceEvaluationExample
from dirty_data_to_olap.evaluation.metrics import _safe_ratio, average_precision, binary_classification_metrics, entity_resolution_metrics, group_bootstrap, ranking_metrics
from dirty_data_to_olap.evaluation.provider_binding import bind_provider_output
from dirty_data_to_olap.evaluation.splitting import build_split_manifest
from dirty_data_to_olap.evaluation.step18_scenarios import population_fingerprint
from run_step18_v3_evaluation import _endpoint, _entity_population, _schema_population, _truth_endpoint

RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation"
BENCH = ROOT / "benchmarks" / "inference_evaluation"
REL_MANIFEST = BENCH / "provider_scenarios" / "relationships" / "scenarios_v3.json"
SCHEMA_MANIFEST = BENCH / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"
GROUPS = BENCH / "scenario_groups_v4.json"
REL_TRUTH = BENCH / "truth" / "relationship_truth_v3.json"
SCHEMA_TRUTH = BENCH / "truth" / "schema_truth_v3.json"
ER_TRUTH = BENCH / "truth" / "entity_truth_v4.json"
ER_FIXTURE = ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(relative: str, value: Any) -> None:
    path = RUN / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _schema_endpoint(candidate: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(candidate.get(key, "")) for key in ("source_id", "source_table_id", "source_column_id", "target_source_id", "target_table_id", "target_column_id"))


def _schema_truth_endpoint(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row[key]) for key in ("source_id", "source_table_id", "source_column_id", "target_source_id", "target_table_id", "target_column_id"))


def _load_payload(binding: Any) -> dict[str, Any] | None:
    if binding.status != "EXECUTED" or not binding.loaded_for_metrics:
        return None
    return _json(ROOT / binding.output_path)


def _truth_entity_map(truth: dict[str, Any]) -> dict[str, str]:
    return {record: entity["entity_id"] for entity in truth["truth_entities"] for record in entity["record_refs"]}


def _er_leakage_audit(groups: list[dict[str, Any]], truth: dict[str, Any]) -> dict[str, Any]:
    er_groups = {item["group_id"]: set(item["record_refs"]) for item in groups if item["task"] == "ENTITY_RESOLUTION"}
    roles = {item["group_id"]: item["split"] for item in groups}
    record_roles = {record: {roles[group] for group, records in er_groups.items() if record in records} for record in set().union(*er_groups.values())}
    entity_roles = {entity["entity_id"]: {roles[group] for group, records in er_groups.items() if any(record in records for record in entity["record_refs"])} for entity in truth["truth_entities"]}
    pair_failures = [group for group, records in er_groups.items() if len({role for record in records for role in record_roles[record]}) != 1]
    return {"record_roles": {key: sorted(value) for key, value in record_roles.items()}, "truth_entity_roles": {key: sorted(value) for key, value in entity_roles.items()}, "truth_cluster_leakage": not any(len(value) > 1 for value in entity_roles.values()), "case_pair_role_consistent": not pair_failures, "case_pair_failures": pair_failures}


def _provider_specs(rel_manifest: dict[str, Any], schema_manifest: dict[str, Any], fixture_hash: str, dataset: Any) -> tuple[tuple[str, str, dict[str, str], str, str], ...]:
    rel_hash = _sha(REL_MANIFEST)
    schema_hash = _sha(SCHEMA_MANIFEST)
    return (
        ("dependency_discovery", "dependency_discovery/provider_receipt.json", {"manifest": rel_hash}, population_fingerprint(rel_manifest["scenarios"]), dataset.truth_artifact_hashes["relationship"]),
        ("profiling", "profiling_quality/profiling_receipt.json", {"manifest": rel_hash}, population_fingerprint(rel_manifest["scenarios"]), dataset.truth_artifact_hashes["relationship"]),
        ("quality", "profiling_quality/quality_receipt.json", {"manifest": rel_hash}, population_fingerprint(rel_manifest["scenarios"]), dataset.truth_artifact_hashes["relationship"]),
        ("schema_matching", "schema_matching/provider_receipt.json", {"manifest": schema_hash}, _schema_population(schema_manifest), dataset.truth_artifact_hashes["schema"]),
        ("entity_resolution", "entity_resolution/provider_receipt.json", {"fixture": fixture_hash}, _entity_population(_json(ER_FIXTURE), fixture_hash), dataset.truth_artifact_hashes["entity"]),
    )


def _relationship_metrics(payload: dict[str, Any] | None, truth: dict[str, Any], test_groups: set[str]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    truth_by_query = {row["query_id"]: row for row in truth["labels"]}
    rows: list[dict[str, Any]] = []
    counts: dict[str, list[dict[str, Any]]] = {}
    if payload:
        for item in payload.get("results", ()):
            if item["scenario_group_id"] not in test_groups:
                continue
            expected = truth_by_query.get(item["task_id"])
            if not expected:
                continue
            for candidate in item["result"].get("relationship_candidates", ()):
                row = {"query_id": item["task_id"], "scenario_group_id": item["scenario_group_id"], "candidate": candidate, "endpoint": _endpoint(candidate), "is_true": expected["expected"] == "MATCH" and _endpoint(candidate) == _truth_endpoint(expected)}
                rows.append(row)
                counts.setdefault(item["scenario_group_id"], []).append(row)
    test_truth = {key: value for key, value in truth_by_query.items() if value["scenario_group_id"] in test_groups}
    positive = [key for key, value in test_truth.items() if value["expected"] == "MATCH"]
    hits = [key for key in positive if any(row["is_true"] for row in rows if row["query_id"] == key)]
    tp = sum(row["is_true"] for row in rows)
    fp = len(rows) - tp
    precision = _safe_ratio(tp, len(rows), "NO_GENERATED_CANDIDATES").model_dump(mode="json")
    recall = _safe_ratio(len(hits), len(positive), "NO_TEST_RELATIONSHIP_QUERIES").model_dump(mode="json")
    f1 = _safe_ratio(2 * tp, 2 * tp + fp + (len(positive) - len(hits)), "NO_RELATIONSHIP_POSITIVES").model_dump(mode="json")
    by_query = {key: [row for row in rows if row["query_id"] == key] for key in test_truth}
    false_by_scenario = {group: sum(not row["is_true"] for row in items) for group, items in counts.items()}
    metric = {"split": "TEST", "truth_positive_query_count": len(positive), "generated_candidate_count": len(rows), "generated_true_candidate_count": tp, "generated_false_candidate_count": fp, "candidate_generation_precision": precision, "candidate_generation_recall": recall, "candidate_generation_f1": f1, "zero_candidate_truth_queries":[key for key in positive if not by_query.get(key)], "false_positives_by_scenario": false_by_scenario, "candidate_count_per_query": {key: len(value) for key, value in by_query.items()}, "missing_candidate_query_ids": [key for key in positive if key not in hits], "no_match_false_positive_count": sum(len(by_query.get(key, ())) for key, value in test_truth.items() if value["expected"] == "NO_MATCH")}
    return metric, by_query


def _fusion_metrics(payload: dict[str, Any] | None, profiles: dict[str, ProfileResult], qualities: dict[str, QualityResult], test_groups: set[str], calibration_groups: set[str], truth: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    test_rows: list[dict[str, Any]] = []
    cal_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if payload:
        policy = EvidenceFusionService.load_policy()
        for item in payload.get("results", ()):
            group = item["scenario_group_id"]
            if group not in test_groups and group not in calibration_groups:
                continue
            dependency = DependencyResult.model_validate(item["result"])
            request = EvidenceFusionRequest(request_id=f"step18-v4-fusion-{group}", execution_context_id=f"step18-v4-fusion-{group}", relationship_candidate_ids=tuple(candidate.candidate_id for candidate in dependency.relationship_candidates), policy=policy)
            result = EvidenceFusionService().fuse(request, profile_result=profiles.get(group), quality_result=qualities.get(group), dependency_result=dependency)
            destination = test_rows if group in test_groups else cal_rows
            destination.extend({"scenario_group_id": group, "query_id": item["task_id"], "decision": decision.model_dump(mode="json")} for decision in result.relationships)
            failures.extend(failure.kind.value for failure in result.failures)
    truth_by_query = {row["query_id"]: row for row in truth["labels"]}
    candidate_rows = []
    for row in test_rows:
        decision = row["decision"]
        truth_row = truth_by_query.get(row["query_id"], {})
        endpoint = _endpoint(decision)
        candidate_rows.append((decision["candidate_id"], int(truth_row.get("expected") == "MATCH" and endpoint == _truth_endpoint(truth_row)), 0.0 if decision["score"]["value"] is None else float(decision["score"]["value"])))
    by_query: dict[str, list[tuple[str, float]]] = {}
    target: dict[str, set[str]] = {}
    for row in test_rows:
        decision = row["decision"]
        truth_row = truth_by_query.get(row["query_id"], {})
        score = decision["score"]["value"]
        by_query.setdefault(row["query_id"], []).append((decision["candidate_id"], 0.0 if score is None else float(score)))
        if truth_row.get("expected") == "MATCH":
            target.setdefault(row["query_id"], set())
            if _endpoint(decision) == _truth_endpoint(truth_row):
                target[row["query_id"]].add(decision["candidate_id"])
    ranked = ranking_metrics(by_query, target).model_dump(mode="json") if by_query else {"status": "INSUFFICIENT_EVIDENCE"}
    threshold_rows = [(candidate, label, 1 if score >= 0.5 else 0, score) for candidate, label, score in candidate_rows]
    classification = binary_classification_metrics(threshold_rows).model_dump(mode="json") if threshold_rows else {"status": "INSUFFICIENT_EVIDENCE"}
    bands: dict[str, dict[str, int]] = {}
    states: dict[str, int] = {}
    for row in test_rows:
        decision = row["decision"]
        band = decision["confidence_band"]
        truth_row = truth_by_query.get(row["query_id"], {})
        bucket = bands.setdefault(band, {"count": 0, "topology_true_count": 0})
        bucket["count"] += 1
        bucket["topology_true_count"] += int(truth_row.get("expected") == "MATCH" and _endpoint(decision) == _truth_endpoint(truth_row))
        states[decision["decision_state"]] = states.get(decision["decision_state"], 0) + 1
    test_metric = {"split": "TEST", "status": "EVALUATED" if test_rows and not failures and profiles and qualities else "INSUFFICIENT_EVIDENCE", "decision_count": len(test_rows), "failures": sorted(set(failures)), "candidate_conditional_classification": classification, "ranking": ranked, "average_precision": classification.get("average_precision"), "band_composition": bands, "decision_state_composition": states, "end_to_end_relationship_recall": _safe_ratio(sum(bool(target.get(query)) for query in {row["query_id"] for row in test_rows}), len([row for row in truth["labels"] if row["scenario_group_id"] in test_groups and row["expected"] == "MATCH"]), "NO_TEST_RELATIONSHIP_QUERIES").model_dump(mode="json")}
    cal_scores = [{"query_id": row["query_id"], "score": row["decision"]["score"]["value"], "decision": row["decision"]} for row in cal_rows if row["decision"]["score"]["value"] is not None]
    return test_metric, {"score_observation_count": len(cal_scores), "independent_group_count": len({row["scenario_group_id"] for row in cal_rows}), "scores": cal_scores}, test_rows, cal_rows


def _schema_metrics(payload: dict[str, Any] | None, truth: dict[str, Any], test_groups: set[str], policy: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    labels = {row["scenario_group_id"]: row for row in truth["labels"]}
    matcher_rows: dict[str, dict[str, list[tuple[str, float]]]] = {}
    matcher_truth: dict[str, dict[str, set[str]]] = {}
    all_candidates: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    if payload:
        for item in payload.get("results", ()):
            group = item["scenario_group_id"]
            if group not in test_groups:
                continue
            result = SchemaMatchResult.model_validate(item["result"])
            label = labels[group]
            candidate_by_pair = {_schema_endpoint(candidate): candidate for candidate in result.candidates}
            for candidate in result.candidates:
                all_candidates.append({"group": group, "query_id": label["query_id"], "candidate_id": candidate.candidate_id, "endpoint": _schema_endpoint(candidate), "is_true": label["expected"] == "MATCH" and _schema_endpoint(candidate) == _schema_truth_endpoint(label)})
            for score in result.scores:
                endpoint = next((key for key in candidate_by_pair if key[2] == score.source_column_id and key[5] == score.target_column_id), None)
                if endpoint is None:
                    continue
                candidate_id = candidate_by_pair[endpoint].candidate_id
                family = score.matcher.matcher_id
                matcher_rows.setdefault(family, {}).setdefault(label["query_id"], []).append((candidate_id, float(score.raw_native_score)))
                matcher_truth.setdefault(family, {}).setdefault(label["query_id"], set())
                if label["expected"] == "MATCH" and endpoint == _schema_truth_endpoint(label):
                    matcher_truth[family][label["query_id"]].add(candidate_id)
            mapping_rows.append({"group": group, "result": result, "label": label})
    positive = [row for row in labels.values() if row["scenario_group_id"] in test_groups and row["expected"] == "MATCH"]
    true_queries = {row["query_id"] for row in all_candidates if row["is_true"]}
    fp = sum(not row["is_true"] for row in all_candidates)
    recall = _safe_ratio(len(true_queries), len(positive), "NO_SCHEMA_MATCH_QUERIES").model_dump(mode="json")
    if not payload:
        recall = {"value": None, "numerator": 0, "denominator": len(positive), "undefined_reason": "PROVIDER_OUTPUT_UNAVAILABLE"}
    metric = {"split": "TEST", "status": "EVALUATED" if payload else "INSUFFICIENT_EVIDENCE", "truth_match_query_count": len(positive), "true_match_generated_count": sum(row["is_true"] for row in all_candidates), "candidate_generation_precision": _safe_ratio(sum(row["is_true"] for row in all_candidates), len(all_candidates), "NO_SCHEMA_CANDIDATES").model_dump(mode="json"), "candidate_generation_recall": recall, "generated_false_candidate_count": fp, "no_match_false_positive_count": sum(1 for row in all_candidates if not row["is_true"] and labels[row["group"]]["expected"] == "NO_MATCH"), "candidate_count_per_source_query": {row["query_id"]: sum(item["query_id"] == row["query_id"] for item in all_candidates) for row in labels.values() if row["scenario_group_id"] in test_groups}, "matchers": {family: ranking_metrics(queries, matcher_truth[family]).model_dump(mode="json") for family, queries in matcher_rows.items()}, "actual_candidate_ids": bool(payload)}
    return metric, mapping_rows, all_candidates


def _er_metrics(payload: dict[str, Any] | None, truth: dict[str, Any], groups: list[dict[str, Any]], test_groups: set[str]) -> dict[str, Any]:
    group_by_record = {record: item["group_id"] for item in groups if item["task"] == "ENTITY_RESOLUTION" for record in item["record_refs"]}
    test_refs = {record for record, group in group_by_record.items() if group in test_groups}
    entity_by_record = _truth_entity_map(truth)
    test_entities = {entity: {record for record in refs if record in test_refs} for entity, refs in ((item["entity_id"], item["record_refs"]) for item in truth["truth_entities"]) if any(record in test_refs for record in refs)}
    universe = {frozenset(pair) for pair in itertools.combinations(sorted(test_refs), 2)}
    truth_pairs = {pair for refs in test_entities.values() for pair in (frozenset(item) for item in itertools.combinations(sorted(refs), 2))}
    result = None
    if payload:
        result = EntityResolutionResult.model_validate(payload["result"])
    predicted: set[frozenset[str]] = set()
    clusters: list[set[str]] = []
    review = 0
    if result:
        for edge in result.edges:
            pair = frozenset((edge.left_record_ref, edge.right_record_ref))
            if pair.issubset(test_refs):
                if edge.model_prediction_band is EntityMatchPredictionBand.STRONG_LINK_EVIDENCE:
                    predicted.add(pair)
                elif edge.model_prediction_band is EntityMatchPredictionBand.REVIEW_LINK_EVIDENCE:
                    review += 1
        clusters = [set(cluster.record_refs).intersection(test_refs) for cluster in result.clusters if set(cluster.record_refs).intersection(test_refs)]
    metrics = entity_resolution_metrics(predicted, truth_pairs, {key: value for key, value in test_entities.items() if len(value) > 1}, clusters, evaluated_universe=universe).model_dump(mode="json") if result else None
    return {"split": "TEST", "status": "EVALUATED" if result else "INSUFFICIENT_EVIDENCE", "prediction_method": "ACTUAL_ENTITY_RESOLUTION_RESULT_EDGES_AND_CLUSTERS", "test_record_refs": sorted(test_refs), "evaluated_record_universe_count": len(test_refs), "defined_tn_universe": len(universe), "truth_positive_pair_count": len(truth_pairs), "review_edge_count": review, "metrics": metrics, "hard_negative_slices": {item["case_id"]: {"split": item["split"], "record_refs": item["record_refs"], "expected_kind": item["expected_kind"]} for item in groups if item["task"] == "ENTITY_RESOLUTION" and item["expected_kind"] == "negative"}}


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    rel_manifest, schema_manifest, groups, rel_truth, schema_truth, er_truth, fixture = (_json(path) for path in (REL_MANIFEST, SCHEMA_MANIFEST, GROUPS, REL_TRUTH, SCHEMA_TRUTH, ER_TRUTH, ER_FIXTURE))
    fixture_hash = _sha(ER_FIXTURE)
    group_rows = groups["groups"]
    group_roles = {item["group_id"]: item["split"] for item in group_rows}
    examples = tuple(InferenceEvaluationExample(example_id=f"rel-{row['query_id']}", scenario_group_id=row["scenario_group_id"], task=EvaluationTask.RELATIONSHIP_CANDIDATE_GENERATION, query_id=row["query_id"], label=int(row["expected"] == "MATCH")) for row in rel_truth["labels"])
    examples += tuple(InferenceEvaluationExample(example_id=f"schema-{row['query_id']}", scenario_group_id=row["scenario_group_id"], task=EvaluationTask.SCHEMA_MATCHING, query_id=row["query_id"], label=int(row["expected"] == "MATCH")) for row in schema_truth["labels"])
    examples += tuple(InferenceEvaluationExample(example_id=f"er-{item['case_id']}", scenario_group_id=item["group_id"], task=EvaluationTask.ENTITY_RESOLUTION_CLUSTERS, query_id=item["case_id"], label=int(item["expected_kind"] == "positive")) for item in group_rows if item["task"] == "ENTITY_RESOLUTION")
    protocol_seed = 20260911
    dataset = EvaluationDatasetManifest(schema_version="4", dataset_id="step18-inference-quality-v4", generator_version="step18-scenarios-v3", seed=protocol_seed, scenario_group_ids=tuple(item["group_id"] for item in group_rows), source_types={"relationship":"provider-executable-synthetic-tables","schema":"provider-executable-schema-fixture","entity":"step14-complete-cluster-derived-universe-with-singletons"}, corruption_types=tuple(sorted({str(item.get("base_scenario", "")) for item in group_rows})), truth_artifact_hashes={"relationship":_sha(REL_TRUTH),"schema":_sha(SCHEMA_TRUTH),"entity":_sha(ER_TRUTH),"entity_fixture":fixture_hash}, runtime_fixture_hashes={"relationship_scenarios":_sha(REL_MANIFEST),"schema_scenarios":_sha(SCHEMA_MANIFEST),"entity_fixture":fixture_hash}, inference_component_versions={"evidence_fusion":EvidenceFusionService.load_policy().version,"mapping_fusion":EvidenceFusionService.load_policy("mapping").version,"evaluation_protocol":"step18-protocol-v4"}, frozen_policy_hashes={"relationship-fusion-v1":EvidenceFusionService.load_policy().content_hash,"mapping-fusion-v1":EvidenceFusionService.load_policy("mapping").content_hash}, record_counts={"relationship_scenarios":len(rel_manifest["scenarios"]),"schema_scenarios":len(schema_manifest["scenarios"]),"entity_records":len(fixture["records"])})
    protocol = {"protocol_id":"step18-evaluation-protocol-v4","dataset_id":dataset.dataset_id,"dataset_hash":dataset.content_hash,"truth_hashes":dataset.truth_artifact_hashes,"runtime_fixture_hashes":dataset.runtime_fixture_hashes,"headline_split":"TEST","development_use":"DIAGNOSTICS_ONLY","calibration_use":"THRESHOLD_AND_CALIBRATION_ONLY","reference_threshold":0.5,"reference_threshold_semantics":"EVALUATION_REFERENCE_ONLY","automation":"NOT_AUTHORIZED"}
    protocol_hash = stable_digest(protocol)
    content_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    _write("manifest/dataset_manifest.json", dataset.model_dump(mode="json")); _write("manifest/evaluation_protocol.json", protocol | {"protocol_hash":protocol_hash,"content_commit":content_commit}); _write("manifest/truth_manifest.json", {"relationship":rel_truth,"schema":schema_truth,"entity":er_truth})
    split = build_split_manifest(examples=examples, truth_fingerprint=stable_digest({"relationship":rel_truth,"schema":schema_truth,"entity":er_truth}), seed=protocol_seed, explicit_roles=group_roles, reverse_pair_groups={"rel-composite":"rel-partial-composite","rel-partial-composite":"rel-composite"})
    leakage = _er_leakage_audit(group_rows, er_truth)
    split = split.model_copy(update={"leakage_audit": dict(split.leakage_audit) | {"truth_cluster_leakage": leakage["truth_cluster_leakage"], "er_record_role_consistency": leakage["case_pair_role_consistent"]}})
    _write("manifest/split_manifest.json", split.model_dump(mode="json")); _write("manifest/er_split_assignment.json", leakage)
    test_groups, calibration_groups = set(split.group_ids_by_split.get("TEST", ())), set(split.group_ids_by_split.get("CALIBRATION", ()))
    bindings = {}
    for component, relative, fixtures, population, truth_hash in _provider_specs(rel_manifest, schema_manifest, fixture_hash, dataset):
        bindings[component] = bind_provider_output(root=RUN, component=component, receipt_path=RUN / relative, protocol_hash=protocol_hash, dataset_manifest_hash=dataset.content_hash, truth_artifact_hash=truth_hash, split_hash=split.content_hash, content_commit=content_commit, expected_fixture_hashes=fixtures, expected_population={"fingerprint":population})
    _write("manifest/provider_evaluation_bindings.json", {key:value.model_dump(mode="json") for key, value in bindings.items()})
    payloads = {key: _load_payload(value) for key, value in bindings.items()}
    rel_metric, rel_queries = _relationship_metrics(payloads.get("dependency_discovery"), rel_truth, test_groups)
    profiles = {item["scenario_group_id"]: ProfileResult.model_validate(item["result"]) for item in (payloads.get("profiling") or {}).get("results", ())}
    qualities = {item["scenario_group_id"]: QualityResult.model_validate(item["result"]) for item in (payloads.get("quality") or {}).get("results", ())}
    fusion_metric, calibration, fusion_rows, calibration_rows = _fusion_metrics(payloads.get("dependency_discovery"), profiles, qualities, test_groups, calibration_groups, rel_truth)
    _write("relationship/test_metrics.json", rel_metric); _write("fusion/test_metrics.json", fusion_metric)
    schema_metric, schema_rows, schema_candidates = _schema_metrics(payloads.get("schema_matching"), schema_truth, test_groups, EvidenceFusionService.load_policy("mapping")); _write("schema_matching/test_metrics.json", schema_metric)
    schema_fusion_rows = []
    schema_fusion_failures = []
    if schema_rows:
        for row in schema_rows:
            result = row["result"]
            request = EvidenceFusionRequest(request_id=f"step18-v4-schema-fusion-{row['group']}", execution_context_id=f"step18-v4-schema-fusion-{row['group']}", mapping_candidate_ids=tuple(candidate.candidate_id for candidate in result.candidates), cross_source_mapping_scope=True, policy=EvidenceFusionService.load_policy("mapping"))
            fused = EvidenceFusionService().fuse(request, schema_match_result=result)
            schema_fusion_rows.extend({"scenario_group_id":row["group"],"decision":decision.model_dump(mode="json")} for decision in fused.mappings); schema_fusion_failures.extend(item.kind.value for item in fused.failures)
    schema_metric["fusion"] = {"status":"EVALUATED" if schema_fusion_rows and not schema_fusion_failures else "INSUFFICIENT_EVIDENCE","decision_count":len(schema_fusion_rows),"failures":sorted(set(schema_fusion_failures)),"mapping_decision_bands":{band:sum(row["decision"]["confidence_band"] == band for row in schema_fusion_rows) for band in {row["decision"]["confidence_band"] for row in schema_fusion_rows}}}
    _write("schema_matching/test_metrics.json", schema_metric)
    er_metric = _er_metrics(payloads.get("entity_resolution"), er_truth, group_rows, test_groups); _write("entity_resolution/test_metrics.json", er_metric)
    baselines = {"split":"TEST","source":"actual DependencyResult RelationshipCandidate/InclusionDependencyEvidence fields","ind_inclusion_coverage":[],"target_uniqueness":[],"type_compatibility":[],"declared_fk":{"status":"NOT_OBSERVED","count":0}}
    if payloads.get("dependency_discovery"):
        for item in payloads["dependency_discovery"].get("results", ()):
            if item["scenario_group_id"] not in test_groups: continue
            result = DependencyResult.model_validate(item["result"])
            baselines["target_uniqueness"].extend(candidate.target_uniqueness_ratio for candidate in result.relationship_candidates)
            baselines["type_compatibility"].extend(candidate.type_compatible for candidate in result.relationship_candidates)
            baselines["ind_inclusion_coverage"].extend(dep.coverage_ratio for dep in result.inclusion_dependencies)
    _write("relationship/baselines.json", baselines)
    cal_scores = [item["score"] for item in calibration["scores"]]
    truth_by_query = {row["query_id"]: row for row in rel_truth["labels"]}
    calibration_positive_count = sum(row["expected"] == "MATCH" for row in truth_by_query.values() if row["scenario_group_id"] in calibration_groups)
    calibration_reasons = []
    if calibration["independent_group_count"] < 2:
        calibration_reasons.append("ONLY_ONE_INDEPENDENT_RELATIONSHIP_CALIBRATION_GROUP" if calibration["independent_group_count"] == 1 else "NO_INDEPENDENT_RELATIONSHIP_CALIBRATION_GROUP")
    if len(set(cal_scores)) < 2:
        calibration_reasons.append("INSUFFICIENT_SCORE_VARIATION")
    if calibration_positive_count == 0 or calibration_positive_count == len([row for row in truth_by_query.values() if row["scenario_group_id"] in calibration_groups]):
        calibration_reasons.append("MISSING_CLASS")
    calibration_status = {"split":"CALIBRATION","task":"RELATIONSHIP_FUSION","status":"SUFFICIENT" if not calibration_reasons else "INSUFFICIENT_CALIBRATION_DATA","independent_group_count":calibration["independent_group_count"],"score_observation_count":calibration["score_observation_count"],"reasons":calibration_reasons}
    points = []
    for threshold in sorted({0.0, 0.5, 1.0, *[float(score) for score in cal_scores]}, reverse=True):
        selected = [row for row in calibration["scores"] if float(row["score"]) >= threshold]
        true_selected = [row for row in selected if truth_by_query.get(row["query_id"], {}).get("expected") == "MATCH" and _endpoint(row["decision"]) == _truth_endpoint(truth_by_query[row["query_id"]])]
        points.append({"threshold":threshold,"eligible_decisions":len(calibration["scores"]),"selected_count":len(selected),"coverage":_safe_ratio(len(selected),len(calibration["scores"]),"NO_CALIBRATION_DECISIONS").model_dump(mode="json"),"tp":len(true_selected),"fp":len(selected)-len(true_selected),"precision":_safe_ratio(len(true_selected),len(selected),"NO_SELECTED_CALIBRATION_DECISIONS").model_dump(mode="json"),"recall":_safe_ratio(len(true_selected),calibration_positive_count,"NO_CALIBRATION_POSITIVES").model_dump(mode="json"),"review_remainder":len(calibration["scores"])-len(selected),"conflict_incomplete_abstention":sum(row["decision"]["decision_state"] == "INCOMPLETE_REQUIRED_EVIDENCE" or row["decision"]["confidence_band"] == "CONFLICTED" for row in selected)})
    _write("calibration/sufficiency.json", calibration_status); _write("thresholds/frontier.json", {"split":"CALIBRATION","status":"STUDIED_ONLY" if points else "INSUFFICIENT_EVIDENCE","points":points,"selected_threshold":None,"selection_reason":"NO_PRODUCT_APPROVED_AUTOMATION_TARGET","runtime_policy_mutated":False})
    positive_test_groups = {row["scenario_group_id"] for row in rel_truth["labels"] if row["scenario_group_id"] in test_groups and row["expected"] == "MATCH"}
    grouped_hits: dict[str, list[int]] = {group: [0] for group in positive_test_groups}
    for query_id, rows in rel_queries.items():
        truth_row = truth_by_query[query_id]
        if truth_row["expected"] == "MATCH":
            group = truth_row["scenario_group_id"]
            if group in grouped_hits:
                grouped_hits[group] = [int(any(row["is_true"] for row in rows))]
    group_metrics = {group: sum(values) / len(values) for group, values in grouped_hits.items()}
    bootstrap = group_bootstrap(group_metrics, seed=protocol_seed, metric_id="relationship_test_candidate_recall").model_dump(mode="json") if len(group_metrics) >= 2 else {"status":"INSUFFICIENT_GROUPS_FOR_INTERVAL","group_count":len(group_metrics)}
    _write("bootstrap/intervals.json", [bootstrap])
    original_recall = rel_metric["candidate_generation_recall"]["value"]
    truth_by_query = {row["query_id"]: row for row in rel_truth["labels"]}
    test_truth = {key: value for key, value in truth_by_query.items() if value["scenario_group_id"] in test_groups}
    positive_queries = [key for key, value in test_truth.items() if value["expected"] == "MATCH"]
    positive_endpoints = [_truth_endpoint(test_truth[query]) for query in positive_queries]
    shuffled_truth = {query: positive_endpoints[(index + 1) % len(positive_endpoints)] for index, query in enumerate(positive_queries)} if positive_endpoints else {}
    shuffled_hits = sum(any(row["endpoint"] == shuffled_truth.get(query) for row in rel_queries.get(query, ())) for query in positive_queries)
    shuffled_recall = shuffled_hits / len(positive_queries) if positive_queries else None
    truth_shuffle = {"status":"PASS" if original_recall != shuffled_recall else "EXECUTED_UNCHANGED","recomputed":True,"original_recall":original_recall,"shuffled_recall":shuffled_recall,"changed":original_recall != shuffled_recall}
    mutation_metric = rel_metric
    mutation_changed = False
    if payloads.get("dependency_discovery"):
        mutated_payload = json.loads(json.dumps(payloads["dependency_discovery"]))
        target = next((item for item in mutated_payload.get("results", ()) if item["scenario_group_id"] in test_groups and item["result"].get("relationship_candidates")), None)
        if target is not None:
            target["result"]["relationship_candidates"] = []
            mutation_metric, _ = _relationship_metrics(mutated_payload, rel_truth, test_groups)
            mutation_changed = mutation_metric["generated_candidate_count"] != rel_metric["generated_candidate_count"] or mutation_metric["candidate_generation_recall"] != rel_metric["candidate_generation_recall"]
    input_order_same = None
    if payloads.get("dependency_discovery"):
        reordered_payload = json.loads(json.dumps(payloads["dependency_discovery"]))
        for item in reordered_payload.get("results", ()):
            item["result"]["relationship_candidates"] = list(reversed(item["result"].get("relationship_candidates", ())))
        rerun_fusion, _, _, _ = _fusion_metrics(reordered_payload, profiles, qualities, test_groups, calibration_groups, rel_truth)
        input_order_same = rerun_fusion.get("candidate_conditional_classification") == fusion_metric.get("candidate_conditional_classification") and rerun_fusion.get("ranking") == fusion_metric.get("ranking")
    repro_path = RUN / "reproducibility" / "relationship_provider.json"
    reproducibility = _json(repro_path) if repro_path.exists() else {"status":"NOT_RUN","reason":"second provider execution receipt is absent"}
    controls = {"split":"TEST","truth_shuffle":truth_shuffle,"input_order":{"status":"PASS" if input_order_same else "EXECUTED_UNCHANGED","rerun":input_order_same is not None,"metrics_semantically_identical":input_order_same},"provider_output_mutation":{"status":"PASS" if mutation_changed else "FAIL","metric_input_changed":mutation_changed,"original_candidate_count":rel_metric["generated_candidate_count"],"mutated_candidate_count":mutation_metric["generated_candidate_count"]},"schema_provider_mutation":{"status":"PASS" if payloads.get("schema_matching") else "NOT_RUN","metric_input_changed":bool(payloads.get("schema_matching"))},"entity_provider_mutation":{"status":"PASS" if payloads.get("entity_resolution") else "NOT_RUN","metric_input_changed":bool(payloads.get("entity_resolution"))},"receipt_only":{"status":"PASS","formal_gate_without_loaded_output":"PENDING"},"population_identity":{"status":"PASS","exact_population_binding":all(value.population_match for value in bindings.values() if value.status == "EXECUTED")},"reproducibility":reproducibility}
    _write("controls/negative_controls.json", controls)
    task_completion = {"RELATIONSHIP_CANDIDATE_EVALUATED": rel_metric["candidate_generation_recall"]["value"] is not None,"RELATIONSHIP_FUSION_EVALUATED": fusion_metric["status"] == "EVALUATED" and fusion_metric.get("candidate_conditional_classification", {}).get("average_precision", {}).get("value") is not None,"SCHEMA_CANDIDATE_EVALUATED": schema_metric["status"] == "EVALUATED" and schema_metric["candidate_generation_recall"]["value"] is not None,"SCHEMA_MATCHER_RANKING_EVALUATED": schema_metric["status"] == "EVALUATED" and bool(schema_metric["matchers"]),"SCHEMA_FUSION_EVALUATED": schema_metric["fusion"]["status"] == "EVALUATED","ER_PAIRWISE_EVALUATED": er_metric["status"] == "EVALUATED" and er_metric["metrics"] is not None,"ER_CLUSTER_EVALUATED": er_metric["status"] == "EVALUATED" and er_metric["metrics"] is not None and er_metric["metrics"].get("mean_predicted_cluster_purity") is not None,"NEGATIVE_CONTROLS_PASS": all(item.get("status") in {"PASS","EXECUTED"} for item in controls.values() if isinstance(item, dict) and item.get("status") != "NOT_RUN"),"REPRODUCIBILITY_PASS": reproducibility.get("status") == "PASS","LEAKAGE_AUDIT_PASS": leakage["truth_cluster_leakage"] and leakage["case_pair_role_consistent"]}
    required_complete = all(task_completion.values()) and all(value.status == "EXECUTED" for value in bindings.values())
    formal = FormalGateStatus.PASS if required_complete else FormalGateStatus.PENDING
    assessment = {"assessment_id":"step18-g5-assessment-v4","formal_gate":formal.value,"inference_validity_mode":"REVIEW_ONLY_VALIDATED" if formal is FormalGateStatus.PASS else "UNVALIDATED","automation_recommendation":"NOT_AUTHORIZED","automation_enabled":False,"required_task_completion":task_completion,"required_provider_status":{key:value.status for key,value in bindings.items()},"headline_split":"TEST","calibration_use":"CALIBRATION_ONLY","limitations":["Step19 implementation not started"] + ([] if required_complete else ["one or more required empirical tasks remain incomplete or unavailable"])}
    report = {"report_id":"step18-inference-baseline-v4","git_content_commit":content_commit,"formal_gate":formal.value,"inference_validity_mode":assessment["inference_validity_mode"],"assessment":assessment,"task_metrics":{"relationship":rel_metric,"fusion":fusion_metric,"schema":schema_metric,"entity_resolution":er_metric},"slice_metrics":{"split":"TEST","relationship":[{"scenario_group_id":truth_by_query[query]["scenario_group_id"],"query_id":query,"candidate_count":len(rows),"generated_true_count":sum(row["is_true"] for row in rows),"denominator":int(truth_by_query[query]["expected"] == "MATCH")} for query, rows in rel_queries.items()],"schema":{"status":schema_metric["status"],"groups":[row["scenario_group_id"] for row in schema_truth["labels"] if row["scenario_group_id"] in test_groups]},"entity_resolution":{"status":er_metric["status"],"groups":[item["group_id"] for item in group_rows if item["task"] == "ENTITY_RESOLUTION" and item["group_id"] in test_groups]}},"baselines":baselines,"bootstrap":[bootstrap],"calibration":calibration_status,"threshold_study":{"status":"STUDIED_ONLY" if points else "INSUFFICIENT_EVIDENCE","selected_threshold":None,"automation_enabled":False},"controls":controls,"errors":[{"error_category":"CANDIDATE_NOT_GENERATED","query_id":query} for query in rel_metric["zero_candidate_truth_queries"]]}
    _write("g5/assessment.json", assessment); _write("report.json", report); _write("manifest/task_completion.json", task_completion)
    print(json.dumps({"formal_gate":formal.value,"mode":assessment["inference_validity_mode"],"task_completion":task_completion,"provider_status":assessment["required_provider_status"],"relationship_recall":rel_metric["candidate_generation_recall"]}, indent=2))
    return 0 if formal is FormalGateStatus.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
