"""Run Valentine from the dedicated Step18 matching interpreter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "schema_matching"
NLTK_DATA = ROOT / "workspace" / "test-temp" / "step18-nltk-data"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


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
    receipt = {"component":"schema_matching","provider":"valentine","version":"1.0.0","adapter":"ValentineSchemaMatchingAdapter","execution_result":"UNAVAILABLE","failure_category":category,"failure_detail":reason,"scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]],"scenario_fixture_hashes":{"manifest":manifest_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":"workspace/runs/step18-inference-baseline-v4/evaluation/schema_matching/normalized_provider_results.json","output_hash":""}
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"UNAVAILABLE","reason":reason,"failure_category":category}, indent=2))
    return 0


def _nltk_preflight() -> dict[str, object]:
    """Verify all Cupid resources from the project-local directory only."""
    os.environ["NLTK_DATA"] = str(NLTK_DATA.resolve())
    import nltk

    nltk.data.path[:] = [str(NLTK_DATA.resolve())]
    resource_paths = {
        "punkt_tab": "tokenizers/punkt_tab/",
        "stopwords": "corpora/stopwords/",
        "wordnet": "corpora/wordnet/",
        "omw-1.4": "corpora/omw-1.4/",
    }
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name, resource_path in resource_paths.items():
        try:
            resolved[name] = str(nltk.data.find(resource_path))
        except LookupError:
            missing.append(name)
    if missing:
        raise RuntimeError("LOCAL_NLTK_RESOURCE_MISSING:" + ",".join(missing))

    hashes: dict[str, str] = {}
    for name in resource_paths:
        relative = resource_paths[name].split("/", 1)[1]
        root = NLTK_DATA / resource_paths[name].split("/", 1)[0] / relative
        candidates = [root, root.with_name(root.name + ".zip")]
        target = next((item for item in candidates if item.exists()), None)
        if target is None:
            missing.append(name)
            continue
        files = [target] if target.is_file() else sorted(item for item in target.rglob("*") if item.is_file())
        digest = hashlib.sha256()
        for item in files:
            digest.update(item.relative_to(NLTK_DATA).as_posix().encode())
            digest.update(item.read_bytes())
        hashes[name] = digest.hexdigest()
    presence = {name: name in resolved and name in hashes for name in resource_paths}
    report = {"status": "COMPLETE" if all(presence.values()) else "LOCAL_NLTK_RESOURCE_MISSING", "nltk_version": nltk.__version__, "resource_names": list(resource_paths), "nltk_data_path_relative": NLTK_DATA.relative_to(ROOT).as_posix(), "resource_presence": presence, "resource_hashes": hashes, "global_search_disabled": True}
    preflight = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "provisioning" / "nltk_preflight.json"
    preflight.parent.mkdir(parents=True, exist_ok=True)
    preflight.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if not all(presence.values()):
        raise RuntimeError("LOCAL_NLTK_RESOURCE_MISSING:" + ",".join(name for name, present in presence.items() if not present))
    return report


def main() -> int:
    if importlib.util.find_spec("valentine") is None:
        return _unavailable("valentine package is not installed in the dedicated runtime", "PROVIDER_IMPORT_FAILED")
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        _nltk_preflight()
    except Exception as error:
        return _unavailable(str(error), "LOCAL_NLTK_RESOURCE_MISSING")
    try:
        import run_step18_v3_schema_provider as implementation
    except Exception as error:
        return _unavailable(f"project adapter import failed: {error.__class__.__name__}", "PROVIDER_IMPORT_FAILED")
    implementation.MANIFEST = MANIFEST
    implementation.RUN = RUN
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
