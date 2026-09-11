"""Execute Valentine independently for every frozen Step18 v3 schema group."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from dirty_data_to_olap.adapters.matching.valentine import ValentineSchemaMatchingAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.schema_matching import SchemaMatchingService
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchMode, SchemaMatchRequest, SchemaMatcherReference, schema_match_config_hash
from step18_provider_fixtures import schema_source


MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation" / "schema_matching"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    if importlib.util.find_spec("valentine") is None:
        RUN.mkdir(parents=True, exist_ok=True)
        population = {}
        for scenario in manifest["scenarios"]:
            group = scenario["scenario_group_id"]
            source_table, target_table = f"crm_customers_{group}", f"erp_customers_{group}"
            columns = [f"{source_table}-{item['name']}" for item in scenario["source_columns"]] + [f"{target_table}-{item['name']}" for item in scenario["target_columns"]]
            population[group] = {"source_ids":["crm-v3","erp-v3"],"snapshot_ids":["snapshot-crm-v3","snapshot-erp-v3"],"table_ids":[source_table,target_table],"column_ids":sorted(columns)}
        receipt = {"component":"schema_matching","provider":"valentine","version":"1.0.0","adapter":"ValentineSchemaMatchingAdapter","execution_result":"UNAVAILABLE","scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]],"scenario_fixture_hashes":{"manifest":manifest_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":"workspace/runs/step18-inference-baseline-v3/evaluation/schema_matching/normalized_provider_results.json","output_hash":""}
        receipt["receipt_content_hash"] = _receipt_hash(receipt)
        (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status":"UNAVAILABLE","reason":"valentine package is not installed"}, indent=2))
        return 0
    results = []
    population = {}
    for scenario in manifest["scenarios"]:
        group = scenario["scenario_group_id"]
        source_table, target_table = f"crm_customers_{group}", f"erp_customers_{group}"
        source_cols = tuple((item["name"], item["type"]) for item in scenario["source_columns"])
        target_cols = tuple((item["name"], item["type"]) for item in scenario["target_columns"])
        crm_catalog, crm_snapshot = schema_source("crm-v3", source_table, source_cols, RUN)
        erp_catalog, erp_snapshot = schema_source("erp-v3", target_table, target_cols, RUN)
        references = (SchemaMatcherReference(matcher_id="coma-schema", name="Coma", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 10, "threshold": 0.0}), SchemaMatcherReference(matcher_id="cupid-schema", name="Cupid", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 10, "threshold": 0.0}))
        request = SchemaMatchRequest(request_id=f"step18-v3-{group}", source_ids=("crm-v3", "erp-v3"), snapshot_ids={"crm-v3":f"snapshot-crm-v3", "erp-v3":f"snapshot-erp-v3"}, selected_table_ids_by_source={"crm-v3":(source_table,), "erp-v3":(target_table,)}, mode=SchemaMatchMode.SCHEMA_ONLY, matcher_references=references)
        policy = PrivacyPolicyService(project_root=RUN)
        result = SchemaMatchingService(ValentineSchemaMatchingAdapter(project_root=RUN, privacy_policy=policy), project_root=RUN, privacy_policy=policy).match(request, {"crm-v3":crm_catalog,"erp-v3":erp_catalog}, {"crm-v3":crm_snapshot,"erp-v3":erp_snapshot}, artifact_root=RUN / "provider-artifacts")
        results.append({"scenario_group_id":group,"result":result.model_dump(mode="json")})
        population[group] = {"source_ids":["crm-v3","erp-v3"],"snapshot_ids":["snapshot-crm-v3","snapshot-erp-v3"],"table_ids":[source_table,target_table],"column_ids":sorted([item.column_id for item in crm_catalog.columns + erp_catalog.columns])}
    RUN.mkdir(parents=True, exist_ok=True)
    data = (json.dumps({"schema_version":"3","provider":"valentine","adapter":"ValentineSchemaMatchingAdapter","version":"1.0.0","manifest_hash":manifest_hash,"results":results}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output = RUN / "normalized_provider_results.json"
    output.write_bytes(data)
    receipt = {"component":"schema_matching","provider":"valentine","version":"1.0.0","adapter":"ValentineSchemaMatchingAdapter","execution_result":"COMPLETE","scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]],"scenario_fixture_hashes":{"manifest":manifest_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":output.relative_to(ROOT).as_posix(),"output_hash":hashlib.sha256(data).hexdigest()}
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenario_count":len(results),"output_hash":receipt["output_hash"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
