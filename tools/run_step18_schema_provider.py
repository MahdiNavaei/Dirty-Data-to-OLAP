"""Execute the real Valentine adapter and persist only its normalized result."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from dirty_data_to_olap.adapters.matching.valentine import ValentineSchemaMatchingAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.schema_matching import SchemaMatchingService
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchMode, SchemaMatchRequest, SchemaMatcherReference, schema_match_config_hash


ROOT = Path(__file__).resolve().parents[1]


def _test_helpers():
    path = ROOT / "tests" / "integration" / "matching" / "test_step13_real_valentine.py"
    spec = importlib.util.spec_from_file_location("step13_real_valentine_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v1" / "evaluation" / "schema_matching"
    work_root = run_root
    work_root.mkdir(parents=True, exist_ok=True)
    helper = _test_helpers()
    crm_catalog, crm_snapshot = helper._source("crm", "crm_customers", (("customer_code", "text"), ("status", "text"), ("order_total", "numeric")), work_root)
    erp_catalog, erp_snapshot = helper._source("erp", "erp_customers", (("client_no", "text"), ("status", "text"), ("order_total", "numeric")), work_root)
    references = (SchemaMatcherReference(matcher_id="coma-schema", name="Coma", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 3, "threshold": 0.0}),)
    request = SchemaMatchRequest(request_id="step18-real-valentine", source_ids=("crm", "erp"), snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"}, selected_table_ids_by_source={"crm": ("crm_customers",), "erp": ("erp_customers",)}, mode=SchemaMatchMode.SCHEMA_ONLY, matcher_references=references)
    policy = PrivacyPolicyService(project_root=work_root)
    result = SchemaMatchingService(ValentineSchemaMatchingAdapter(project_root=work_root, privacy_policy=policy), project_root=work_root, privacy_policy=policy).match(request, {"crm": crm_catalog, "erp": erp_catalog}, {"crm": crm_snapshot, "erp": erp_snapshot}, artifact_root=run_root / "provider-artifacts")
    payload = result.model_dump(mode="json")
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output = run_root / "real_provider_result.json"
    output.write_bytes(data)
    receipt = {"component": "schema_matching", "adapter": "ValentineSchemaMatchingAdapter", "provider": "valentine", "version": "1.0.0", "configuration_fingerprint": schema_match_config_hash(request), "dataset_id": "step18-inference-quality-v1", "scenario_scope": "schema-renamed/schema-hard-negative/schema-multilingual/schema-no-match", "output_artifact": output.relative_to(ROOT).as_posix(), "output_hash": hashlib.sha256(data).hexdigest(), "execution_result": result.status.value, "candidate_count": len(result.candidates), "failure_count": len(result.failures)}
    (run_root / "real_provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
