"""Run the real Valentine adapter over the frozen Step18 v2 schema fixture."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.matching.valentine import ValentineSchemaMatchingAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.schema_matching import SchemaMatchingService
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchMode, SchemaMatchRequest, SchemaMatcherReference, schema_match_config_hash

MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios.json"


def _helpers():
    path = ROOT / "tests" / "integration" / "matching" / "test_step13_real_valentine.py"
    spec = importlib.util.spec_from_file_location("step13_helpers_v2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation" / "schema_matching"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    helper = _helpers()
    crm_catalog, crm_snapshot = helper._source("crm-v2", "crm_customers_v2", tuple((item["name"], item["type"]) for item in manifest["source_columns"]), run_root)
    erp_catalog, erp_snapshot = helper._source("erp-v2", "erp_customers_v2", tuple((item["name"], item["type"]) for item in manifest["target_columns"]), run_root)
    references = (SchemaMatcherReference(matcher_id="coma-schema", name="Coma", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 10, "threshold": 0.0}), SchemaMatcherReference(matcher_id="cupid-schema", name="Cupid", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 10, "threshold": 0.0}))
    request = SchemaMatchRequest(request_id="step18-v2-valentine", source_ids=("crm-v2", "erp-v2"), snapshot_ids={"crm-v2":"snapshot-crm-v2", "erp-v2":"snapshot-erp-v2"}, selected_table_ids_by_source={"crm-v2": ("crm_customers_v2",), "erp-v2": ("erp_customers_v2",)}, mode=SchemaMatchMode.SCHEMA_ONLY, matcher_references=references)
    policy = PrivacyPolicyService(project_root=run_root)
    result = SchemaMatchingService(ValentineSchemaMatchingAdapter(project_root=run_root, privacy_policy=policy), project_root=run_root, privacy_policy=policy).match(request, {"crm-v2": crm_catalog, "erp-v2": erp_catalog}, {"crm-v2": crm_snapshot, "erp-v2": erp_snapshot}, artifact_root=run_root / "provider-artifacts")
    payload = {"schema_version":"1", "provider":"valentine", "adapter":"ValentineSchemaMatchingAdapter", "version":"1.0.0", "scenario_fixture_hash":hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), "result":result.model_dump(mode="json")}
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    run_root.mkdir(parents=True, exist_ok=True)
    output = run_root / "normalized_provider_result.json"
    output.write_bytes(data)
    receipt = {"component":"schema_matching", "adapter":"ValentineSchemaMatchingAdapter", "provider":"valentine", "version":"1.0.0", "configuration_fingerprint":schema_match_config_hash(request), "execution_result":result.status.value, "scenario_group_ids":["schema-renamed","schema-hard-negative","schema-multilingual","schema-abbreviation","schema-context","schema-no-match","schema-multiple-target"], "scenario_fixture_hash":payload["scenario_fixture_hash"], "output_artifact":output.relative_to(ROOT).as_posix(), "output_hash":hashlib.sha256(data).hexdigest()}
    (run_root / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
