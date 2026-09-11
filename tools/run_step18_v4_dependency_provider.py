"""Run Desbordante twice and compare semantic inference, without rewriting output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_step18_v3_dependency_provider as implementation

FINAL_RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "dependency_discovery"
REPRO_RUN = FINAL_RUN.parent / "reproducibility" / "second"

UNORDERED_LIST_KEYS = {"results", "relationship_candidates", "ucc_evidence", "fd_evidence", "inclusion_dependencies", "artifacts", "evidence_refs", "input_batch_ids", "table_ids", "selected_table_ids", "requested_kinds"}


def _semantic(value, key: str | None = None):
    if isinstance(value, dict):
        return {name: _semantic(child, name) for name, child in value.items()}
    if isinstance(value, list):
        items = [_semantic(child, key) for child in value]
        if key in UNORDERED_LIST_KEYS:
            return sorted(items, key=lambda child: json.dumps(child, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return items
    if key in {"created_at", "content_hash", "artifact_location"}:
        return "<volatile-envelope>"
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_fingerprint(path: Path) -> tuple[str, dict]:
    semantic = _semantic(json.loads(path.read_text(encoding="utf-8")))
    encoded = (json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest(), semantic


def _task_metric_summary(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = []
    for item in payload.get("results", ()):
        result = item["result"]
        candidates = result.get("relationship_candidates", ())
        summary.append({"scenario_group_id": item["scenario_group_id"], "status": result.get("status"), "candidate_count": len(candidates), "candidate_ids": sorted(candidate.get("candidate_id") for candidate in candidates), "candidate_topology": sorted({json.dumps({key: candidate.get(key) for key in ("from_table", "from_columns", "to_table", "to_columns", "proposed_cardinality")}, sort_keys=True, separators=(",", ":")) for candidate in candidates}), "numeric_evidence": sorted({json.dumps({key: candidate.get(key) for key in ("source_orphan_ratio", "target_uniqueness_ratio", "type_compatible", "low_cardinality_risk")}, sort_keys=True, separators=(",", ":")) for candidate in candidates}), "search_stats": result.get("search_stats")})
    return {"schema_version": "1", "scenarios": sorted(summary, key=lambda item: item["scenario_group_id"])}


def _run_once(run_root: Path) -> tuple[Path, int]:
    implementation.RUN = run_root
    run_root.mkdir(parents=True, exist_ok=True)
    code = implementation.main()
    return run_root / "normalized_provider_results.json", code


def main(reuse_existing: bool = False) -> int:
    if reuse_existing:
        first_output, first_code = FINAL_RUN / "normalized_provider_results.json", 0
        second_output, second_code = REPRO_RUN / "normalized_provider_results.json", 0
    else:
        first_output, first_code = _run_once(FINAL_RUN)
        second_output, second_code = _run_once(REPRO_RUN)
    implementation.RUN = FINAL_RUN
    repro_path = FINAL_RUN.parent / "reproducibility" / "relationship_provider.json"
    repro_path.parent.mkdir(parents=True, exist_ok=True)
    if not first_output.is_file() or not second_output.is_file() or first_code or second_code:
        payload = {"status": "FAIL", "failure_category": "PROVIDER_EXECUTION_FAILED", "first_command_return_code": first_code, "second_command_return_code": second_code}
    else:
        first_semantic_hash, first_semantic = _semantic_fingerprint(first_output)
        second_semantic_hash, second_semantic = _semantic_fingerprint(second_output)
        first_metrics = _task_metric_summary(first_output)
        second_metrics = _task_metric_summary(second_output)
        payload = {"status": "PASS" if first_semantic_hash == second_semantic_hash and first_metrics == second_metrics else "FAIL", "failure_category": None if first_semantic_hash == second_semantic_hash and first_metrics == second_metrics else "REPRODUCIBILITY_FAILED", "first_artifact_content_hash": _sha(first_output), "second_artifact_content_hash": _sha(second_output), "byte_outputs_equal": _sha(first_output) == _sha(second_output), "first_semantic_reproducibility_fingerprint": first_semantic_hash, "second_semantic_reproducibility_fingerprint": second_semantic_hash, "semantic_inference_equal": first_semantic == second_semantic, "task_metrics_equal": first_metrics == second_metrics, "first_task_metrics": first_metrics, "second_task_metrics": second_metrics}
    repro_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main("--reuse-existing" in sys.argv[1:]))
