"""Run Splink from the dedicated Step18 entity-resolution interpreter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "entity_resolution"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _runtime_metadata() -> dict[str, object]:
    runtime = Path(sys.executable).resolve().parent.parent
    module = importlib.util.find_spec("splink")
    return {"interpreter_path_relative": Path(sys.executable).resolve().relative_to(ROOT).as_posix(), "runtime_path_relative": runtime.relative_to(ROOT).as_posix(), "provider_version": importlib.metadata.version("splink") if module else None, "provider_module_location_relative": Path(module.origin).resolve().relative_to(runtime).as_posix() if module and module.origin else None}


def _unavailable(reason: str, category: str) -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    record_refs = sorted(item["record_ref"] for item in fixture["records"])
    population = {"fixture_hash":fixture_hash,"source_ids":["crm","erp"],"snapshot_ids":["snapshot-crm","snapshot-erp"],"record_refs":record_refs}
    RUN.mkdir(parents=True, exist_ok=True)
    receipt = {"component":"entity_resolution","provider":"splink","version":"4.0.17","adapter":"SplinkEntityResolutionAdapter","execution_result":"UNAVAILABLE","failure_category":category,"failure_detail":reason,"mode":"LINK_AND_DEDUPE","scenario_group_ids":["er-exact","er-spelling","er-phone-policy","er-missing-fields","er-common-name","er-placeholder","er-shared-household","er-unicode","er-same-source","er-transitive","er-cluster"],"scenario_fixture_hashes":{"fixture":fixture_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":"workspace/runs/step18-inference-baseline-v4/evaluation/entity_resolution/normalized_provider_result.json","output_hash":"",**_runtime_metadata()}
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
    receipt["mode"] = "LINK_AND_DEDUPE"
    receipt.pop("receipt_content_hash", None)
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if importlib.util.find_spec("splink") is None:
        return _unavailable("splink package is not installed in the dedicated runtime", "PROVIDER_IMPORT_FAILED")
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import run_step18_v3_entity_provider as implementation
    except Exception as error:
        return _unavailable(f"project adapter import failed: {error.__class__.__name__}", "PROVIDER_IMPORT_FAILED")
    implementation.FIXTURE = FIXTURE
    implementation.RUN = RUN
    code = implementation.main()
    _annotate_receipt()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
