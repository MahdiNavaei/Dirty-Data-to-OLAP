"""Execute Splink over the complete Step14 labelled record population."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.entity_resolution import ERBlockingRule, ERClusteringPolicy, ERComparisonSpecification, ERThresholdPolicy, ERTrainingPolicy, EntityResolutionMode, EntityResolutionNormalizationRule, EntityResolutionSpec, IdentityFieldSpecification
from step18_provider_fixtures import entity_source, entity_spec


FIXTURE = ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation" / "entity_resolution"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if importlib.util.find_spec("splink") is None:
        RUN.mkdir(parents=True, exist_ok=True)
        record_refs = sorted(item["record_ref"] for item in fixture["records"])
        population = {"fixture_hash":fixture_hash,"source_ids":["crm","erp"],"snapshot_ids":["snapshot-crm","snapshot-erp"],"record_refs":record_refs}
        receipt = {"component":"entity_resolution","provider":"splink","version":"4.0.17","adapter":"SplinkEntityResolutionAdapter","execution_result":"UNAVAILABLE","scenario_group_ids":["er-exact","er-spelling","er-phone-policy","er-missing-fields","er-common-name","er-placeholder","er-shared-household","er-unicode","er-same-source","er-transitive","er-cluster"],"scenario_fixture_hashes":{"fixture":fixture_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":"workspace/runs/step18-inference-baseline-v3/evaluation/entity_resolution/normalized_provider_result.json","output_hash":""}
        receipt["receipt_content_hash"] = _receipt_hash(receipt)
        (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status":"UNAVAILABLE","reason":"splink package is not installed"}, indent=2))
        return 0
    rows_by_source = {source: tuple({"record_ref":r["record_ref"],"name":r["fields"]["name_token"],"email":r["fields"]["email_token"],"phone":r["fields"]["phone_token"]} for r in fixture["records"] if r["source_id"] == source) for source in ("crm", "erp")}
    catalogs, snapshots = {}, {}
    for source, rows in rows_by_source.items():
        catalogs[source], snapshots[source] = entity_source(source, f"{source}_customers_v4", RUN, rows)
    spec = entity_spec("v4")
    policy = PrivacyPolicyService(project_root=RUN)
    decision = policy.authorize_entity_resolution_analysis(spec.privacy_context, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=tuple(field.column_id for field in spec.identity_fields), batch_ids=tuple(f"batch-{source}_customers_v4" for source in ("crm","erp")))
    if not decision.allowed:
        raise RuntimeError(f"entity-resolution authorization denied: {decision.reason}")
    result = SplinkEntityResolutionAdapter(project_root=RUN, privacy_policy=policy).run(spec, catalogs, snapshots, authorization=policy.entity_resolution_authorization_for_decision(decision), artifact_root=RUN / "provider-artifacts")
    RUN.mkdir(parents=True, exist_ok=True)
    data = (json.dumps({"schema_version":"3","provider":"splink","adapter":"SplinkEntityResolutionAdapter","version":"4.0.17","fixture_hash":fixture_hash,"result":result.model_dump(mode="json")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output = RUN / "normalized_provider_result.json"
    output.write_bytes(data)
    record_refs = sorted(item["record_ref"] for item in fixture["records"])
    population = {"fixture_hash":fixture_hash,"source_ids":["crm","erp"],"snapshot_ids":["snapshot-crm","snapshot-erp"],"record_refs":record_refs}
    receipt = {"component":"entity_resolution","provider":"splink","version":"4.0.17","adapter":"SplinkEntityResolutionAdapter","execution_result":result.status.value,"scenario_group_ids":["er-exact","er-spelling","er-phone-policy","er-missing-fields","er-common-name","er-placeholder","er-shared-household","er-unicode","er-same-source","er-transitive","er-cluster"],"scenario_fixture_hashes":{"fixture":fixture_hash},"population_fingerprint":hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),"output_artifact":output.relative_to(ROOT).as_posix(),"output_hash":hashlib.sha256(data).hexdigest()}
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"record_count":len(record_refs),"output_hash":receipt["output_hash"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
