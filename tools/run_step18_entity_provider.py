"""Execute the real Splink adapter and persist only its normalized result."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule,
    ERClusteringPolicy,
    ERComparisonSpecification,
    ERThresholdPolicy,
    ERTrainingPolicy,
    EntityResolutionMode,
    EntityResolutionNormalizationRule,
    EntityResolutionSpec,
    IdentityFieldSpecification,
)


ROOT = Path(__file__).resolve().parents[1]


def _test_helpers():
    path = ROOT / "tests" / "integration" / "entity_resolution" / "test_step14_real_splink.py"
    spec = importlib.util.spec_from_file_location("step14_real_splink_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v1" / "evaluation" / "entity_resolution"
    run_root.mkdir(parents=True, exist_ok=True)
    helper = _test_helpers()
    crm_rows = tuple(
        {"record_ref": f"crm-r{i}", "name": name, "email": email, "phone": phone}
        for i, (name, email, phone) in enumerate(
            (
                ("Alice Smith", "alice@example.com", "+12025550101"),
                ("Bob Jones", "BOB@example.com", "+12025550102"),
                ("Common Name", "common-crm@example.com", "0000000000"),
                ("Household A", "house-a@example.com", "+12025550103"),
            ),
            1,
        )
    )
    erp_rows = tuple(
        {"record_ref": f"erp-r{i}", "name": name, "email": email, "phone": phone}
        for i, (name, email, phone) in enumerate(
            (
                ("Alice Smyth", "alice@example.com", "+12025550101"),
                ("Bob Jones", "bob@example.com", "+12025550102"),
                ("Common Name", "common-erp@example.com", "0000000000"),
                ("Household B", "house-b@example.com", "+12025550103"),
            ),
            1,
        )
    )
    crm_catalog, crm_snapshot = helper._fixture("crm", "crm_customers", run_root, crm_rows)
    erp_catalog, erp_snapshot = helper._fixture("erp", "erp_customers", run_root, erp_rows)
    fields = tuple(
        IdentityFieldSpecification(
            field_id=field,
            source_id=source,
            snapshot_id=f"snapshot-{source}",
            table_id=table,
            column_id=f"{table}-{field}",
            physical_name=field,
            semantic_role=field,
            normalization_rule_id=f"norm-{field}",
        )
        for source, table in (("crm", "crm_customers"), ("erp", "erp_customers"))
        for field in ("name", "email", "phone")
    )
    spec = EntityResolutionSpec(
        spec_id="step18-real-splink",
        entity_family="person",
        mode=EntityResolutionMode.LINK_ONLY,
        source_ids=("crm", "erp"),
        snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"},
        table_ids_by_source={"crm": ("crm_customers",), "erp": ("erp_customers",)},
        identity_fields=fields,
        normalization_rules=tuple(EntityResolutionNormalizationRule(rule_id=f"norm-{field}", version="1", applies_to=(field,)) for field in ("name", "email", "phone")),
        blocking_rules=(
            ERBlockingRule(rule_id="block-email", version="1", field_ids=("email",), sql_expression="l.email = r.email"),
            ERBlockingRule(rule_id="block-phone", version="1", field_ids=("phone",), sql_expression="l.phone = r.phone"),
        ),
        comparisons=(
            ERComparisonSpecification(comparison_id="cmp-name", field_id="name", method="exact"),
            ERComparisonSpecification(comparison_id="cmp-email", field_id="email"),
        ),
        training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-phone",), max_u_pairs=100),
        threshold_policy=ERThresholdPolicy(match_probability_threshold=0.8, review_probability_threshold=0.5),
        clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1"),
    )
    policy = PrivacyPolicyService(project_root=run_root)
    decision = policy.authorize_entity_resolution_analysis(
        spec.privacy_context,
        spec=spec,
        source_ids=spec.source_ids,
        snapshot_ids=spec.snapshot_ids,
        table_ids_by_source=spec.table_ids_by_source,
        identity_column_ids=tuple(field.column_id for field in fields),
        batch_ids=("batch-crm_customers", "batch-erp_customers"),
    )
    if not decision.allowed:
        raise RuntimeError(f"entity-resolution authorization denied: {decision.reason}")
    result = SplinkEntityResolutionAdapter(project_root=run_root, privacy_policy=policy).run(
        spec,
        {"crm": crm_catalog, "erp": erp_catalog},
        {"crm": crm_snapshot, "erp": erp_snapshot},
        authorization=policy.entity_resolution_authorization_for_decision(decision),
        artifact_root=run_root / "provider-artifacts",
    )
    payload = result.model_dump(mode="json")
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output = run_root / "real_provider_result.json"
    output.write_bytes(data)
    receipt = {
        "component": "entity_resolution",
        "adapter": "SplinkEntityResolutionAdapter",
        "provider": "splink",
        "version": "4.0.17",
        "dataset_id": "step18-inference-quality-v1",
        "scenario_scope": "er-basic/er-hard-negative/er-unicode/er-transitive",
        "output_artifact": output.relative_to(ROOT).as_posix(),
        "output_hash": hashlib.sha256(data).hexdigest(),
        "execution_result": result.status.value,
        "edge_count": len(result.edges),
        "record_count": result.observation_scope.records_read,
        "all_pairs": result.metrics.all_pairs,
    }
    (run_root / "real_provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
