"""Run Valentine from the dedicated Step18 matching interpreter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "schema_matching"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _runtime_metadata() -> dict[str, object]:
    runtime = Path(sys.executable).resolve().parent.parent
    module = importlib.util.find_spec("valentine")
    return {"interpreter_path_relative": Path(sys.executable).resolve().relative_to(ROOT).as_posix(), "runtime_path_relative": runtime.relative_to(ROOT).as_posix(), "provider_version": importlib.metadata.version("valentine") if module else None, "provider_module_location_relative": Path(module.origin).resolve().relative_to(runtime).as_posix() if module and module.origin else None}


def _unavailable(reason: str, category: str) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    population = {}
    for scenario in manifest["scenarios"]:
        group = scenario["scenario_group_id"]
        source_table, target_table = f"crm_customers_{group}", f"erp_customers_{group}"
        columns = [f"{source_table}-{item['name']}" for item in scenario["source_columns"]] + [f"{target_table}-{item['name']}" for item in scenario["target_columns"]]
        population[group] = {"source_ids":["crm-v3","erp-v3"],"snapshot_ids":["snapshot-crm-v3","snapshot-erp-v3"],"table_ids":[source_table,target_table],"column_ids":sorted(columns)}
    RUN.mkdir(parents=True, exist_ok=True)
    receipt = {"component":"schema_matching","provider":"valentine","version":"1.0.0","adapter":"ValentineSchemaMatchingAdapter","execution_result":"UNAVAILABLE","failure_category":category,"failure_detail":reason,"scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]],"scenario_fixture_hashes":{"manifest":manifest_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":"workspace/runs/step18-inference-baseline-v4/evaluation/schema_matching/normalized_provider_results.json","output_hash":"",**_runtime_metadata()}
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"UNAVAILABLE","reason":reason,"failure_category":category}, indent=2))
    return 0


def _annotate_receipt() -> None:
    path = RUN / "provider_receipt.json"
    if not path.is_file():
        return
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt.update(_runtime_metadata())
    receipt.pop("receipt_content_hash", None)
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if importlib.util.find_spec("valentine") is None:
        return _unavailable("valentine package is not installed in the dedicated runtime", "PROVIDER_IMPORT_FAILED")
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import run_step18_v3_schema_provider as implementation
    except Exception as error:
        return _unavailable(f"project adapter import failed: {error.__class__.__name__}", "PROVIDER_IMPORT_FAILED")
    implementation.MANIFEST = MANIFEST
    implementation.RUN = RUN
    code = implementation.main()
    _annotate_receipt()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
