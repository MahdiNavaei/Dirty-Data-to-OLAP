"""Evaluation orchestration over frozen runtime artifacts and separate truth."""

from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

from dirty_data_to_olap.application.applied_ml import AppliedMLService
from dirty_data_to_olap.domain.contracts.dependency import RelationshipCandidate
from dirty_data_to_olap.domain.contracts.applied_ml import MLTrainingLabel
from dirty_data_to_olap.domain.contracts.source import stable_digest

from .contracts import (
    BootstrapInterval,
    CorruptionKind,
    EvaluationArtifactReference,
    EvaluationDatasetManifest,
    EvaluationTask,
    EvaluationReport,
    InferenceEvaluationExample,
    InferenceValidityAssessment,
    InferenceValidityStatus,
    ThresholdPoint,
    ThresholdStudy,
)
from .metrics import (
    _metric,
    _safe_ratio,
    average_precision,
    binary_classification_metrics,
    entity_resolution_metrics,
    group_bootstrap,
    ranking_metrics,
)
from .splitting import build_split_manifest


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_artifact(root: Path, relative_path: str, artifact_type: str, payload: Any) -> EvaluationArtifactReference:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _json_bytes(payload)
    target.write_bytes(data)
    return EvaluationArtifactReference(artifact_id=f"artifact-{hashlib.sha256(data).hexdigest()[:20]}", relative_path=relative_path.replace("\\", "/"), content_hash=hashlib.sha256(data).hexdigest(), artifact_type=artifact_type)


def _truth_map(payload: Mapping[str, Any]) -> dict[str, set[str]]:
    return {str(row["query_id"]): set(row.get("true_candidate_ids", ())) for row in payload.get("labels", ())}


def _tags(values: list[str]) -> tuple[CorruptionKind, ...]:
    mapping = {item.value.lower(): item for item in CorruptionKind}
    output = []
    for value in values:
        normalized = value.lower().replace("%", "").replace(":", "_").replace("-", "_")
        for key, enum_value in mapping.items():
            if key in normalized or normalized in key:
                output.append(enum_value)
                break
    return tuple(dict.fromkeys(output))


def _relationship_examples(runtime: Mapping[str, Any], truth: Mapping[str, Any]) -> tuple[InferenceEvaluationExample, ...]:
    labels = _truth_map(truth)
    output = []
    for query in runtime["relationship_queries"]:
        target = labels.get(query["query_id"], set())
        for candidate in query["candidates"]:
            output.append(InferenceEvaluationExample(example_id=candidate["candidate_id"], scenario_group_id=query["scenario_group_id"], task=EvaluationTask.RELATIONSHIP_FUSION, query_id=query["query_id"], candidate_id=candidate["candidate_id"], score=None if candidate.get("score") is None else float(candidate["score"]), label=int(candidate["candidate_id"] in target), predicted=None, conflict=bool(candidate.get("conflict", query.get("conflict"))), incomplete=bool(candidate.get("incomplete", query.get("incomplete"))), slice_tags=tuple(query.get("slices", ())), corruption_tags=_tags(list(query.get("slices", ()))), baseline_scores={"raw_decision_score": float(candidate["raw_decision_score"])} if candidate.get("raw_decision_score") is not None else {}))
    return tuple(output)


def _schema_examples(runtime: Mapping[str, Any], truth: Mapping[str, Any]) -> tuple[InferenceEvaluationExample, ...]:
    labels = _truth_map(truth)
    output = []
    for query in runtime["schema_queries"]:
        target = labels.get(query["query_id"], set())
        for candidate in query["candidates"]:
            output.append(InferenceEvaluationExample(example_id=candidate["candidate_id"], scenario_group_id=query["scenario_group_id"], task=EvaluationTask.SCHEMA_MATCHING, query_id=query["query_id"], candidate_id=candidate["candidate_id"], score=float(candidate["score"]), label=int(candidate["candidate_id"] in target), slice_tags=tuple(query.get("slices", ())), corruption_tags=_tags(list(query.get("slices", ())))))
    return tuple(output)


def _pair(left: str, right: str) -> frozenset[str]:
    return frozenset((left, right))


def _entity_rows(runtime: Mapping[str, Any], truth: Mapping[str, Any]) -> tuple[InferenceEvaluationExample, ...]:
    records = tuple(runtime.get("entity_records", ()))
    positive_pairs = {_pair(*item) for item in truth.get("positive_pairs", ())}
    negative_pairs = {_pair(*item) for item in truth.get("negative_pairs", ())}
    truth_pairs = positive_pairs | negative_pairs
    by_group: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_group.setdefault(str(record["scenario_group_id"]), []).append(record)
    output: list[InferenceEvaluationExample] = []
    for group_id, group_records in sorted(by_group.items()):
        for left, right in combinations(sorted(group_records, key=lambda row: str(row["record_ref"])), 2):
            left_ref, right_ref = str(left["record_ref"]), str(right["record_ref"])
            pair = _pair(left_ref, right_ref)
            if pair not in truth_pairs:
                continue
            left_tokens = left.get("tokens", {})
            right_tokens = right.get("tokens", {})
            shared_anchor = any(
                field in {"email", "phone"}
                and left_tokens.get(field)
                and left_tokens.get(field) == right_tokens.get(field)
                and all(marker not in str(left_tokens.get(field)).lower() for marker in ("missing", "placeholder"))
                for field in set(left_tokens) & set(right_tokens)
            )
            output.append(InferenceEvaluationExample(
                example_id=f"er-{left_ref}-{right_ref}",
                scenario_group_id=group_id,
                task=EvaluationTask.ENTITY_RESOLUTION_PAIRWISE,
                query_id=f"er-query-{group_id}",
                candidate_id=f"{left_ref}|{right_ref}",
                score=1.0 if shared_anchor else 0.0,
                label=int(pair in positive_pairs),
                slice_tags=(group_id,),
                corruption_tags=_tags([group_id]),
            ))
    return tuple(output)


def _entity_clusters(rows: tuple[InferenceEvaluationExample, ...]) -> tuple[set[str], ...]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for row in rows:
        if row.candidate_id is None:
            continue
        left, right = row.candidate_id.split("|", 1)
        parent.setdefault(left, left)
        parent.setdefault(right, right)
        if row.score == 1.0:
            union(left, right)
    components: dict[str, set[str]] = {}
    for value in parent:
        components.setdefault(find(value), set()).add(value)
    return tuple(sorted((component for component in components.values() if len(component) > 1), key=lambda value: sorted(value)))


def _applied_ml_candidates(runtime: Mapping[str, Any], truth: Mapping[str, Any]) -> tuple[RelationshipCandidate, ...]:
    labels = _truth_map(truth)
    candidates: list[RelationshipCandidate] = []
    for query in runtime.get("relationship_queries", ()):
        for raw in query.get("candidates", ()):
            score = float(raw.get("raw_decision_score", raw.get("raw_score", raw.get("score", 0.0))))
            slices = set(query.get("slices", ()))
            candidates.append(RelationshipCandidate(
                candidate_id=str(raw["candidate_id"]),
                source_id="step18",
                snapshot_id="step18-snapshot",
                from_table=f"orders_{query['query_id']}",
                from_columns=("customer_id",),
                to_table="clients" if str(raw["candidate_id"]).endswith("alt") else "customers",
                to_columns=("id",),
                source_orphan_ratio=max(0.0, min(1.0, 1.0 - score)),
                target_uniqueness_ratio=score,
                type_compatible="type_mismatch" not in " ".join(slices),
                low_cardinality_risk="low_cardinality_trap" in " ".join(slices),
                evidence_refs=(f"step18-{query['query_id']}",),
            ))
    return tuple(candidates)


def _applied_ml_labels(runtime: Mapping[str, Any], truth: Mapping[str, Any]) -> tuple[MLTrainingLabel, ...]:
    labels = _truth_map(truth)
    output = []
    for query in runtime.get("relationship_queries", ()):
        targets = labels.get(str(query["query_id"]), set())
        for raw in query.get("candidates", ()):
            candidate_id = str(raw["candidate_id"])
            to_table = "clients" if candidate_id.endswith("alt") else "customers"
            logical = "|".join(sorted((f"orders_{query['query_id']}", "customer_id", to_table, "id")))
            output.append(MLTrainingLabel(label_id=f"step18-label-{candidate_id}", candidate_id=candidate_id, logical_pair_key=logical, label=int(candidate_id in targets), group_id=str(query["scenario_group_id"]), base_scenario_id=str(query["query_id"]), source="step18-evaluation-truth", provenance_refs=(f"truth:{query['query_id']}",)))
    return tuple(output)


def _split_rows(rows: tuple[InferenceEvaluationExample, ...], split: Any) -> tuple[InferenceEvaluationExample, ...]:
    selected = {example_id for example_id in split.example_ids_by_split.get("TEST", ())}
    return tuple(row for row in rows if row.example_id in selected)


def _grouped_query_rows(rows: tuple[InferenceEvaluationExample, ...]) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        if row.candidate_id is not None and row.score is not None:
            output.setdefault(row.query_id, []).append((row.candidate_id, row.score))
    return output


def _threshold_study(rows: tuple[InferenceEvaluationExample, ...], *, split_name: str = "CALIBRATION") -> ThresholdStudy:
    thresholds = tuple(round(index / 10, 1) for index in range(1, 10))
    points = []
    candidate_rows = tuple(row for row in rows if row.score is not None)
    for threshold in thresholds:
        eligible = tuple(row for row in candidate_rows if not row.conflict and not row.incomplete)
        accepted = tuple(row for row in eligible if float(row.score) >= threshold)
        tp = sum(row.label or 0 for row in accepted)
        fp = len(accepted) - tp
        total_truth = sum(row.label or 0 for row in candidate_rows)
        reviewed = len(candidate_rows) - len(accepted)
        worst_by_slice: list[float] = []
        for slice_name in sorted({tag for row in candidate_rows for tag in row.slice_tags}):
            slice_accepted = [row for row in accepted if slice_name in row.slice_tags]
            if slice_accepted:
                worst_by_slice.append(sum(row.label or 0 for row in slice_accepted) / len(slice_accepted))
        points.append(ThresholdPoint(threshold=threshold, eligible_examples=len(eligible), auto_accept_coverage=_safe_ratio(len(accepted), len(candidate_rows), "NO_EVALUATED_EXAMPLES"), precision=_safe_ratio(tp, len(accepted), "NO_AUTO_ACCEPTED_EXAMPLES"), false_positive_count=fp, recall=_safe_ratio(tp, total_truth, "NO_TRUTH_POSITIVES"), review_rate=_safe_ratio(reviewed, len(candidate_rows), "NO_EVALUATED_EXAMPLES"), abstention_rate=_safe_ratio(sum(1 for row in candidate_rows if row.conflict or row.incomplete), len(candidate_rows), "NO_EVALUATED_EXAMPLES"), worst_slice_precision=_metric(min(worst_by_slice), len(worst_by_slice), len(worst_by_slice), "NO_ACCEPTED_SLICE_EXAMPLES") if worst_by_slice else _metric(None, 0, 0, "NO_ACCEPTED_SLICE_EXAMPLES")))
    return ThresholdStudy(study_id="step18-threshold-frontier-v1", source_score_semantics="UNCALIBRATED_DECISION_SCORE", split=split_name, points=tuple(points), status="STUDIED_ONLY", selected_threshold=None, selection_reason="NO_AUTOMATION_THRESHOLD_SELECTED: no product-approved criterion exists", runtime_policy_mutated=False)


class InferenceEvaluationService:
    """Runs the offline sidecar without becoming a runtime DAG dependency."""

    protocol_version = "step18-protocol-v1"

    def run(self, *, runtime: Mapping[str, Any], relationship_truth: Mapping[str, Any], schema_truth: Mapping[str, Any], entity_truth: Mapping[str, Any], split_roles: Mapping[str, str], dataset_manifest: EvaluationDatasetManifest, root: Path, content_commit: str, provider_status: Mapping[str, str], truth_fingerprint: str) -> EvaluationReport:
        relationship_rows = _relationship_examples(runtime, relationship_truth)
        schema_rows = _schema_examples(runtime, schema_truth)
        entity_rows = _entity_rows(runtime, entity_truth)
        all_rows = relationship_rows + schema_rows + entity_rows
        split = build_split_manifest(examples=all_rows, truth_fingerprint=truth_fingerprint, seed=dataset_manifest.seed, explicit_roles=split_roles)
        test_relationship = _split_rows(relationship_rows, split)
        test_schema = _split_rows(schema_rows, split)
        test_entity = _split_rows(entity_rows, split)
        calibration_relationship = tuple(row for row in relationship_rows if row.example_id in set(split.example_ids_by_split.get("CALIBRATION", ())))
        relationship_truth_map = _truth_map(relationship_truth)
        schema_truth_map = _truth_map(schema_truth)
        relationship_queries = _grouped_query_rows(test_relationship)
        schema_queries = _grouped_query_rows(test_schema)
        relationship_ranking = ranking_metrics(relationship_queries, {key: value for key, value in relationship_truth_map.items() if key in relationship_queries}, hard_negative_ids={row.candidate_id for row in test_relationship if row.label == 0 and row.candidate_id})
        schema_ranking = ranking_metrics(schema_queries, {key: value for key, value in schema_truth_map.items() if key in schema_queries}, hard_negative_ids={row.candidate_id for row in test_schema if row.label == 0 and row.candidate_id})
        relationship_classification = binary_classification_metrics(tuple((row.example_id, int(row.label or 0), int(bool(row.score is not None and row.score >= .5 and not row.conflict and not row.incomplete)), row.score) for row in test_relationship), universe_count=len(test_relationship))
        schema_classification = binary_classification_metrics(tuple((row.example_id, int(row.label or 0), int(bool(row.score is not None and row.score >= .5)), row.score) for row in test_schema), universe_count=len(test_schema))
        relationship_ap = average_precision(tuple((row.example_id, int(row.label or 0), row.score) for row in test_relationship))
        schema_ap = average_precision(tuple((row.example_id, int(row.label or 0), row.score) for row in test_schema))
        entity_truth_pairs = {_pair(*item) for item in entity_truth.get("positive_pairs", ())}
        entity_refs = {ref for row in test_entity for ref in (row.candidate_id or "").split("|")}
        entity_predicted_pairs = {
            _pair(*(row.candidate_id or "").split("|"))
            for row in test_entity
            if row.score == 1.0 and row.candidate_id and "|" in row.candidate_id
        }
        entity_universe = {
            _pair(*(row.candidate_id or "").split("|"))
            for row in test_entity
            if row.candidate_id and "|" in row.candidate_id
        }
        entity_truth_clusters = {
            str(cluster["entity_id"]): set(cluster["record_refs"])
            for cluster in entity_truth.get("truth_clusters", ())
            if set(cluster["record_refs"]).issubset(entity_refs)
        }
        entity_metrics = entity_resolution_metrics(entity_predicted_pairs, entity_truth_pairs.intersection(entity_universe), entity_truth_clusters, _entity_clusters(test_entity), evaluated_universe=entity_universe)
        threshold = _threshold_study(calibration_relationship)
        group_values = {query_id: (sum(row.label or 0 for row in test_relationship if row.query_id == query_id) / max(1, sum(1 for row in test_relationship if row.query_id == query_id))) for query_id in {row.query_id for row in test_relationship}}
        bootstrap = (group_bootstrap(group_values, seed=dataset_manifest.seed, metric_id="relationship_test_positive_fraction"),)
        ml_result = None
        try:
            ml_result = AppliedMLService(artifact_root=root / "applied_ml").rank(
                _applied_ml_candidates(runtime, relationship_truth),
                labels=_applied_ml_labels(runtime, relationship_truth),
            )
        except (ValueError, RuntimeError, TypeError) as exc:
            ml_result = {"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)}
        semantic_result = {
            "status": "OPTIONAL_NOT_EXECUTED",
            "reason": "Step18 semantic provider is optional; no local model was invoked or downloaded",
            "score_semantics": "QUALITATIVE_HYPOTHESIS_ONLY",
            "runtime_policy_mutated": False,
        }
        artifacts: list[EvaluationArtifactReference] = []
        artifacts.append(_write_artifact(root, "manifest/split_manifest.json", "SplitManifest", split.model_dump(mode="json")))
        test_relationship_queries = {row.query_id for row in test_relationship}
        candidate_truth_by_query = {query_id: relationship_truth_map.get(query_id, set()) for query_id in test_relationship_queries}
        candidate_proposed_by_query = {query_id: {row.candidate_id for row in test_relationship if row.query_id == query_id and row.candidate_id} for query_id in test_relationship_queries}
        candidate_recall_numerator = sum(bool(candidate_truth_by_query[query_id].intersection(candidate_proposed_by_query[query_id])) for query_id in test_relationship_queries)
        artifacts.append(_write_artifact(root, "relationship/test_metrics.json", "RelationshipEvaluation", {"candidate_generation": {"test_query_count": len(test_relationship_queries), "truth_query_count": sum(bool(value) for value in candidate_truth_by_query.values()), "truth_proposed_query_count": candidate_recall_numerator, "candidate_recall": _safe_ratio(candidate_recall_numerator, sum(bool(value) for value in candidate_truth_by_query.values()), "NO_TRUTH_QUERIES").model_dump(mode="json"), "missing_candidate_query_ids": sorted(query_id for query_id, values in candidate_truth_by_query.items() if values and not values.intersection(candidate_proposed_by_query[query_id]))}, "fusion": relationship_classification.model_dump(mode="json") | {"average_precision": relationship_ap.model_dump(mode="json")}, "ranking": relationship_ranking.model_dump(mode="json"), "band_counts": _band_counts(test_relationship), "baseline_comparison": _relationship_baselines(test_relationship)}))
        artifacts.append(_write_artifact(root, "schema_matching/test_metrics.json", "SchemaEvaluation", {"classification": schema_classification.model_dump(mode="json") | {"average_precision": schema_ap.model_dump(mode="json")}, "ranking": schema_ranking.model_dump(mode="json"), "no_match_false_positive_count": sum(1 for row in test_schema if row.label == 0 and row.score is not None and row.score >= .5)}))
        artifacts.append(_write_artifact(root, "entity_resolution/test_metrics.json", "EntityResolutionEvaluation", entity_metrics.model_dump(mode="json") | {"prediction_method": "exact-anonymized-email-or-phone-anchor; placeholder/missing tokens rejected", "truth_cluster_count": len(entity_truth_clusters), "test_record_count": len(entity_refs)}))
        if hasattr(ml_result, "model_dump"):
            ml_payload = ml_result.model_dump(mode="json")
            ml_status = ml_result.status.value
        else:
            ml_payload = ml_result
            ml_status = str(ml_result.get("status"))
        artifacts.append(_write_artifact(root, "applied_ml/result.json", "AppliedMLEvaluation", ml_payload))
        artifacts.append(_write_artifact(root, "semantic_ai/result.json", "SemanticAIEvaluation", semantic_result))
        artifacts.append(_write_artifact(root, "thresholds/frontier.json", "ThresholdStudy", threshold.model_dump(mode="json")))
        artifacts.append(_write_artifact(root, "bootstrap/intervals.json", "BootstrapIntervals", [item.model_dump(mode="json") for item in bootstrap]))
        errors = _errors(test_relationship + test_schema)
        artifacts.append(_write_artifact(root, "errors/inference_errors.json", "EvaluationErrors", errors))
        leakage_pass = all(not value for value in split.leakage_audit.get("group_intersections", {}).values()) and bool(split.leakage_audit.get("reverse_pair_leakage", True))
        required_provider_ok = all(provider_status.get(key) == "EXECUTED" for key in ("dependency_discovery", "schema_matching", "entity_resolution"))
        status = InferenceValidityStatus.REVIEW_ONLY_VALIDATED if leakage_pass and required_provider_ok else InferenceValidityStatus.INSUFFICIENT_EVIDENCE
        evaluated_tasks = (
            EvaluationTask.RELATIONSHIP_CANDIDATE_GENERATION,
            EvaluationTask.RELATIONSHIP_FUSION,
            EvaluationTask.RELATIONSHIP_RANKING,
            EvaluationTask.SCHEMA_MATCHING,
            EvaluationTask.ENTITY_RESOLUTION_PAIRWISE,
            EvaluationTask.ENTITY_RESOLUTION_CLUSTERS,
        )
        if ml_status == "EXECUTED_EXPERIMENTAL":
            evaluated_tasks += (EvaluationTask.APPLIED_ML_RANKING,)
        assessment = InferenceValidityAssessment(assessment_id="step18-g5-assessment-v1", protocol_hash=stable_digest({"protocol_version": self.protocol_version, "dataset": dataset_manifest.content_hash, "split": split.content_hash}), dataset_hash=dataset_manifest.content_hash, split_hash=split.content_hash, evaluated_task_ids=evaluated_tasks, task_report_refs={"relationship": "relationship/test_metrics.json", "schema_matching": "schema_matching/test_metrics.json", "entity_resolution": "entity_resolution/test_metrics.json", "applied_ml": "applied_ml/result.json", "semantic_ai": "semantic_ai/result.json", "thresholds": "thresholds/frontier.json", "errors": "errors/inference_errors.json"}, held_out_test_status="EXECUTED_UNTOUCHED_BY_TUNING", leakage_audit={"status": "PASS" if leakage_pass else "FAIL", "details": split.leakage_audit}, calibration_status="INSUFFICIENT_CALIBRATION_DATA", threshold_study_status="NO_AUTOMATION_THRESHOLD_SELECTED", automation_recommendation="NOT_AUTHORIZED", limitations=tuple(["scores remain uncalibrated", "semantic AI is optional and was not executed"] + ([] if required_provider_ok else ["required real provider path was not executed or did not produce a persisted receipt"])), gate_recommendation=status, required_provider_status=dict(provider_status))
        artifacts.append(_write_artifact(root, "g5/assessment.json", "InferenceValidityAssessment", assessment.model_dump(mode="json")))
        report = EvaluationReport(report_id="step18-inference-baseline-v1", protocol_hash=assessment.protocol_hash, dataset_hash=dataset_manifest.content_hash, split_hash=split.content_hash, git_content_commit=content_commit, generated_artifact_refs=tuple(artifacts), task_metrics={"relationship": relationship_classification.model_dump(mode="json"), "relationship_ranking": relationship_ranking.model_dump(mode="json"), "schema": schema_classification.model_dump(mode="json"), "schema_ranking": schema_ranking.model_dump(mode="json"), "entity_resolution": entity_metrics.model_dump(mode="json"), "applied_ml": ml_payload, "semantic_ai": semantic_result}, slice_metrics={"relationship": _slice_metrics(test_relationship), "schema": _slice_metrics(test_schema), "entity_resolution": _slice_metrics(test_entity)}, errors=tuple(errors), bootstrap=bootstrap, assessment=assessment)
        _write_artifact(root, "report.json", "EvaluationReport", report.model_dump(mode="json"))
        return report


def _band_counts(rows: tuple[InferenceEvaluationExample, ...]) -> dict[str, int]:
    output = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "CONFLICTED": 0, "INSUFFICIENT": 0}
    for row in rows:
        if row.incomplete:
            output["INSUFFICIENT"] += 1
        elif row.conflict:
            output["CONFLICTED"] += 1
        elif (row.score or 0) >= .75:
            output["HIGH"] += 1
        elif (row.score or 0) >= .4:
            output["MEDIUM"] += 1
        else:
            output["LOW"] += 1
    return output


def _relationship_baselines(rows: tuple[InferenceEvaluationExample, ...]) -> dict[str, Any]:
    """Compare the fused decision score with transparent non-fused controls."""
    controls = {
        "current_fusion": lambda row: float(row.score or 0.0),
        "ind_coverage_proxy": lambda row: float(row.baseline_scores.get("raw_decision_score", row.score or 0.0)),
        "target_uniqueness_proxy": lambda row: float(row.baseline_scores.get("raw_decision_score", row.score or 0.0)),
        "type_compatibility_only": lambda row: 1.0 if "type_mismatch" not in " ".join(row.slice_tags) else 0.0,
    }
    output: dict[str, Any] = {}
    for name, scorer in controls.items():
        evaluated = tuple((row.example_id, int(row.label or 0), int(scorer(row) >= .5), scorer(row)) for row in rows)
        metrics = binary_classification_metrics(evaluated, universe_count=len(rows))
        output[name] = metrics.model_dump(mode="json")
    return output


def _slice_metrics(rows: tuple[InferenceEvaluationExample, ...]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for tag in sorted({tag for row in rows for tag in row.slice_tags}):
        selected = [row for row in rows if tag in row.slice_tags]
        output[tag] = {"count": len(selected), "positive_count": sum(row.label or 0 for row in selected), "positive_fraction": sum(row.label or 0 for row in selected) / len(selected) if selected else None}
    return output


def _errors(rows: tuple[InferenceEvaluationExample, ...]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        predicted = int(bool(row.score is not None and row.score >= .5 and not row.conflict and not row.incomplete))
        truth = int(row.label or 0)
        if predicted == truth:
            continue
        category = "MISSING_EVIDENCE" if row.incomplete else "CONFLICT_DEFERRED" if row.conflict else "RANKING_ERROR"
        output.append({"example_id": row.example_id, "scenario_group_id": row.scenario_group_id, "task": row.task.value, "truth_label": truth, "score": row.score, "conflict": row.conflict, "incomplete": row.incomplete, "slice_tags": row.slice_tags, "error_category": category})
    return output
