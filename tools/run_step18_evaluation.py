"""Run the frozen Step18 offline evaluation protocol.

The runner reads inference-only fixtures and evaluation-only truth separately.
Truth is joined only inside the evaluator after runtime artifacts are formed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    EvidenceDirection, EvidenceFamily, EvidenceFusionInputs, EvidenceFusionRequest,
    EvidenceRole, FusionEvidenceItem, ProducerEvidenceStatus, ProducerResultState,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.evaluation.service import InferenceEvaluationService
from dirty_data_to_olap.evaluation.contracts import EvaluationDatasetManifest


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks" / "inference_evaluation"


def _read(name: str) -> dict:
    return json.loads((BENCHMARK / name).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_ready(relative_path: str) -> bool:
    receipt_path = ROOT / relative_path
    if not receipt_path.is_file():
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        output = ROOT / str(receipt["output_artifact"])
        return output.is_file() and receipt.get("execution_result") == "COMPLETE" and _sha(output) == receipt.get("output_hash")
    except (KeyError, OSError, json.JSONDecodeError):
        return False


def _provider_status() -> dict[str, str]:
    try:
        import sklearn  # noqa: F401
        ml = "EXECUTED"
    except Exception:
        ml = "UNAVAILABLE"
    schema = "EXECUTED" if _receipt_ready("workspace/runs/step18-inference-baseline-v1/evaluation/schema_matching/real_provider_receipt.json") else "UNAVAILABLE"
    entity = "EXECUTED" if _receipt_ready("workspace/runs/step18-inference-baseline-v1/evaluation/entity_resolution/real_provider_receipt.json") else "UNAVAILABLE"
    dependency = "EXECUTED" if _receipt_ready("workspace/runs/step18-inference-baseline-v1/evaluation/dependency_discovery/real_provider_receipt.json") else "UNAVAILABLE"
    return {"dependency_discovery": dependency, "schema_matching": schema, "entity_resolution": entity, "applied_ml": ml, "semantic_ai": "OPTIONAL_NOT_EXECUTED"}


def _fusion_item(evidence_id: str, subject: str, metric: str, value: float, *, direction: EvidenceDirection = EvidenceDirection.SUPPORTS) -> FusionEvidenceItem:
    dimension = "inclusion" if metric in {"inclusion_coverage", "orphan_ratio"} else "target_uniqueness" if metric == "target_uniqueness" else "type_compatibility"
    return FusionEvidenceItem(evidence_id=evidence_id, subject_id=subject, producer_id="step18-fixture", family=EvidenceFamily.DEPENDENCY, role=EvidenceRole.DIRECT_OBSERVATION, metric_name=metric, metric_value=value, metric_semantics="synthetic aggregate contract evidence; evaluation-only", direction=direction, scope_id="step18:synthetic", observation_scope="FULL", source_ids=("step18",), snapshot_ids=("step18-snapshot",), snapshot_by_source={"step18": "step18-snapshot"}, correlation_group=evidence_id, score_dimension_id=dimension, score_bearing=True)


def _execute_fusion(runtime: dict) -> tuple[dict, list[dict]]:
    policy = EvidenceFusionService.load_policy()
    statuses = tuple(ProducerEvidenceStatus(producer_id=name, family=family, state=ProducerResultState.COMPLETE, result_id=f"{name}-step18") for name, family in (("profile", EvidenceFamily.PROFILE), ("dependency", EvidenceFamily.DEPENDENCY), ("quality", EvidenceFamily.QUALITY)))
    output = json.loads(json.dumps(runtime))
    receipts: list[dict] = []
    for query in output["relationship_queries"]:
        candidates = []
        items = []
        for raw in query["candidates"]:
            candidate = {"candidate_id": raw["candidate_id"], "source_id": "step18", "snapshot_id": "step18-snapshot", "from_table": "orders", "from_columns": ("customer_id",), "to_table": "clients" if raw["candidate_id"].endswith("alt") else "customers", "to_columns": ("id",), "proposed_cardinality": "MANY_TO_ONE"}
            candidates.append(candidate)
            score = float(raw["score"])
            raw["raw_decision_score"] = score
            direction = EvidenceDirection.SUPPORTS if score >= .5 else EvidenceDirection.CONTRADICTS
            if not query.get("incomplete"):
                items.extend((_fusion_item(raw["candidate_id"] + ":coverage", _subject(candidate), "inclusion_coverage", score), _fusion_item(raw["candidate_id"] + ":unique", _subject(candidate), "target_uniqueness", score), _fusion_item(raw["candidate_id"] + ":type", _subject(candidate), "type_compatibility", score, direction=direction)))
            if query.get("conflict"):
                items.append(FusionEvidenceItem(evidence_id=raw["candidate_id"] + ":semantic", subject_id=_subject(candidate), producer_id="step18-semantic", family=EvidenceFamily.SEMANTIC_AI, role=EvidenceRole.DERIVED_INTERPRETATION, metric_name="semantic_hypothesis", metric_value=None, metric_semantics="qualitative synthetic hypothesis", direction=EvidenceDirection.SUPPORTS, scope_id="step18", observation_scope="FULL", source_ids=("step18",), snapshot_ids=("step18-snapshot",), snapshot_by_source={"step18": "step18-snapshot"}, correlation_group=raw["candidate_id"], score_bearing=False, qualitative_text="evaluation-only semantic support"))
        request = EvidenceFusionRequest(request_id="step18-" + query["query_id"], execution_context_id="step18-" + query["query_id"], relationship_candidate_ids=tuple(item["candidate_id"] for item in candidates), policy=policy)
        result = EvidenceFusionService().fuse(request, EvidenceFusionInputs(producer_statuses=statuses, relationship_candidates=tuple(candidates), evidence_items=tuple(items)))
        by_candidate = {item.candidate_id: item for item in result.relationships}
        for raw in query["candidates"]:
            decision = by_candidate.get(raw["candidate_id"])
            if decision is not None:
                raw["score"] = decision.score.value
                raw["conflict"] = decision.confidence_band.value == "CONFLICTED"
                raw["incomplete"] = decision.decision_state.value == "INCOMPLETE_REQUIRED_EVIDENCE"
        receipts.append({"query_id": query["query_id"], "decision_count": len(result.relationships), "conflicts": [item.conflict_type.value for item in result.conflicts], "completeness": result.completeness.value, "decision_ids": [item.decision_id for item in result.relationships]})
    return output, receipts


def _subject(candidate: dict) -> str:
    return f"rel:{candidate['from_table']}:{candidate['from_columns'][0]}->{candidate['to_table']}:{candidate['to_columns'][0]}"


def run(run_id: str) -> dict:
    runtime_path = BENCHMARK / "runtime_inputs.json"
    relationship_path = BENCHMARK / "relationship_truth.json"
    schema_path = BENCHMARK / "schema_truth.json"
    entity_path = BENCHMARK / "entity_truth.json"
    groups = _read("scenario_groups.json")
    runtime = _read("runtime_inputs.json")
    relationship_truth = _read("relationship_truth.json")
    schema_truth = _read("schema_truth.json")
    entity_truth = _read("entity_truth.json")
    relationship_policy = EvidenceFusionService.load_policy()
    mapping_policy = EvidenceFusionService.load_policy("mapping")
    dataset = EvaluationDatasetManifest(
        dataset_id=groups["dataset_id"],
        generator_version=groups["generator_version"],
        seed=int(groups["seed"]),
        scenario_group_ids=tuple(item["group_id"] for item in groups["groups"]),
        source_types={"relationship": "synthetic_tabular_contract_fixture", "schema": "synthetic_tabular_contract_fixture", "entity": "aggregate_safe_token_fixture"},
        corruption_types=tuple(sorted({tag for query in runtime["relationship_queries"] + runtime["schema_queries"] for tag in query.get("slices", ())})),
        truth_artifact_hashes={"relationship": _sha(relationship_path), "schema": _sha(schema_path), "entity": _sha(entity_path)},
        runtime_fixture_hashes={"runtime_inputs": _sha(runtime_path), "scenario_groups": _sha(BENCHMARK / "scenario_groups.json")},
        inference_component_versions={"evidence_fusion": relationship_policy.version, "relationship_policy": relationship_policy.content_hash, "mapping_policy": mapping_policy.content_hash, "evaluation_protocol": InferenceEvaluationService.protocol_version},
        frozen_policy_hashes={"relationship-fusion-v1": relationship_policy.content_hash, "mapping-fusion-v1": mapping_policy.content_hash},
        record_counts={"relationship_queries": len(runtime["relationship_queries"]), "schema_queries": len(runtime["schema_queries"]), "entity_records": len(runtime["entity_records"])},
    )
    root = ROOT / "workspace" / "runs" / run_id / "evaluation"
    root.mkdir(parents=True, exist_ok=True)
    try:
        content_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        content_commit = "UNAVAILABLE"
    protocol = {
        "protocol_id": "step18-evaluation-protocol-v1",
        "protocol_version": InferenceEvaluationService.protocol_version,
        "dataset_id": dataset.dataset_id,
        "dataset_hash": dataset.content_hash,
        "truth_artifact_hashes": dataset.truth_artifact_hashes,
        "runtime_fixture_hashes": dataset.runtime_fixture_hashes,
        "split_algorithm": "frozen scenario roles from scenario_groups.json; no row-level random split",
        "metric_definitions": {"classification": "precision/recall/F1/AP with explicit denominators", "ranking": "Recall@K/MRR/NDCG with stable candidate-ID ties", "entity_resolution": "pairwise plus false merge/split and cluster purity/completeness", "uncertainty": "seeded scenario-group bootstrap"},
        "threshold_method": "calibration-only frontier over original uncalibrated scores; no selection",
        "calibration_method": "Platt-style experiment only if independent groups/classes are sufficient; otherwise insufficient",
        "frozen_policy_hashes": dataset.frozen_policy_hashes,
        "content_commit": content_commit,
        "python": sys.version,
        "platform": platform.platform(),
    }
    protocol_bytes = (json.dumps(protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    (root / "manifest").mkdir(parents=True, exist_ok=True)
    (root / "manifest" / "evaluation_protocol.json").write_bytes(protocol_bytes)
    provider_status = _provider_status()
    runtime, fusion_receipts = _execute_fusion(runtime)
    (root / "fusion").mkdir(parents=True, exist_ok=True)
    (root / "fusion" / "inference_artifact.json").write_text(json.dumps({"policy_hash": relationship_policy.content_hash, "receipts": fusion_receipts, "note": "actual EvidenceFusionService outputs over evaluation-only contract evidence; no truth labels"}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report = InferenceEvaluationService().run(runtime=runtime, relationship_truth=relationship_truth, schema_truth=schema_truth, entity_truth=entity_truth, split_roles={item["group_id"]: item["split"] for item in groups["groups"]}, dataset_manifest=dataset, root=root, content_commit=content_commit, provider_status=provider_status, truth_fingerprint=stable_digest({"relationship": relationship_truth, "schema": schema_truth, "entity": entity_truth}))
    summary = {"report": report.model_dump(mode="json"), "provider_status": provider_status, "dataset": dataset.model_dump(mode="json"), "protocol_hash": hashlib.sha256(protocol_bytes).hexdigest()}
    (root / "manifest" / "dataset_manifest.json").write_text(json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "manifest" / "provider_receipt.json").write_text(json.dumps(provider_status, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="step18-inference-baseline-v1")
    args = parser.parse_args()
    run(args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
