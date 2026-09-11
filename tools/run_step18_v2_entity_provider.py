"""Run Splink on the Step14 labeled population used by Step18 truth."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule, ERClusteringPolicy, ERComparisonSpecification,
    ERThresholdPolicy, ERTrainingPolicy, EntityResolutionMode,
    EntityResolutionNormalizationRule, EntityResolutionSpec,
    IdentityFieldSpecification,
)

FIXTURE = ROOT / "benchmarks" / "entity_resolution" / "step14_labeled_fixture.json"


def _helpers():
    path = ROOT / "tests" / "integration" / "entity_resolution" / "test_step14_real_splink.py"
    spec = importlib.util.spec_from_file_location("step14_helpers_v2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation" / "entity_resolution"
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    helper = _helpers()
    rows_by_source = {source: tuple({"record_ref": r["record_ref"], "name": r["fields"]["name_token"], "email": r["fields"]["email_token"], "phone": r["fields"]["phone_token"]} for r in fixture["records"] if r["source_id"] == source) for source in ("crm", "erp")}
    catalogs, snapshots = {}, {}
    for source, rows in rows_by_source.items():
        catalogs[source], snapshots[source] = helper._fixture(source, f"{source}_customers_v2", run_root, rows)
    fields = tuple(IdentityFieldSpecification(field_id=field, source_id=source, snapshot_id=f"snapshot-{source}", table_id=f"{source}_customers_v2", column_id=f"{source}_customers_v2-{field}", physical_name=field, semantic_role=field, normalization_rule_id=f"norm-{field}") for source in ("crm", "erp") for field in ("name", "email", "phone"))
    spec = EntityResolutionSpec(spec_id="step18-v2-splink", entity_family="person", mode=EntityResolutionMode.LINK_ONLY, source_ids=("crm", "erp"), snapshot_ids={"crm":"snapshot-crm", "erp":"snapshot-erp"}, table_ids_by_source={"crm": ("crm_customers_v2",), "erp": ("erp_customers_v2",)}, identity_fields=fields, normalization_rules=tuple(EntityResolutionNormalizationRule(rule_id=f"norm-{field}", version="1", applies_to=(field,)) for field in ("name", "email", "phone")), blocking_rules=(ERBlockingRule(rule_id="block-email", version="1", field_ids=("email",), sql_expression="l.email = r.email"), ERBlockingRule(rule_id="block-phone", version="1", field_ids=("phone",), sql_expression="l.phone = r.phone")), comparisons=(ERComparisonSpecification(comparison_id="cmp-name", field_id="name", method="exact"), ERComparisonSpecification(comparison_id="cmp-email", field_id="email")), training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-phone",), max_u_pairs=500), threshold_policy=ERThresholdPolicy(match_probability_threshold=0.8, review_probability_threshold=0.5), clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1"))
    policy = PrivacyPolicyService(project_root=run_root)
    decision = policy.authorize_entity_resolution_analysis(spec.privacy_context, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=tuple(field.column_id for field in fields), batch_ids=("batch-crm_customers_v2", "batch-erp_customers_v2"))
    if not decision.allowed:
        raise RuntimeError(f"entity-resolution authorization denied: {decision.reason}")
    result = SplinkEntityResolutionAdapter(project_root=run_root, privacy_policy=policy).run(spec, catalogs, snapshots, authorization=policy.entity_resolution_authorization_for_decision(decision), artifact_root=run_root / "provider-artifacts")
    payload = {"schema_version":"1", "provider":"splink", "adapter":"SplinkEntityResolutionAdapter", "version":"4.0.17", "fixture_hash":hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), "result":result.model_dump(mode="json")}
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    run_root.mkdir(parents=True, exist_ok=True)
    output = run_root / "normalized_provider_result.json"
    output.write_bytes(data)
    receipt = {"component":"entity_resolution", "adapter":"SplinkEntityResolutionAdapter", "provider":"splink", "version":"4.0.17", "execution_result":result.status.value, "scenario_group_ids":["er-exact","er-spelling","er-missing-fields","er-unicode","er-common-name","er-placeholder","er-shared-household","er-same-source","er-transitive","er-cluster"], "scenario_fixture_hash":payload["fixture_hash"], "output_artifact":output.relative_to(ROOT).as_posix(), "output_hash":hashlib.sha256(data).hexdigest(), "record_refs":sorted(r["record_ref"] for r in fixture["records"])}
    (run_root / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
