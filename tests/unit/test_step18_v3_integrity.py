from __future__ import annotations

import json
from pathlib import Path

from dirty_data_to_olap.evaluation.contracts import EvaluationTask, InferenceEvaluationExample
from dirty_data_to_olap.evaluation.splitting import build_split_manifest
from dirty_data_to_olap.evaluation.step18_scenarios import relationship_tables, observed_orphan_rate


ROOT = Path(__file__).resolve().parents[2]


def _scenario(group: str, kind: str, **extra):
    return {"scenario_group_id": group, "task_id": group, "kind": kind, **extra}


def test_v3_generator_uses_exact_orphan_parameters_and_preserves_traps():
    assert observed_orphan_rate(_scenario("rel-orphan-005", "orphan", orphan_rate=0.005, row_count=200)) == 0.005
    assert observed_orphan_rate(_scenario("rel-orphan-02", "orphan", orphan_rate=0.02, row_count=200)) == 0.02
    assert observed_orphan_rate(_scenario("rel-orphan-10", "orphan", orphan_rate=0.10, row_count=200)) == 0.10
    for kind in ("low_cardinality", "same_domain", "type_mismatch"):
        tables = relationship_tables(_scenario("group", kind))
        assert all(row.get("customer_id", "").startswith("customer-") for row in tables["orders"] if row.get("customer_id") is not None)


def test_v3_multiple_target_has_distinct_target_endpoints():
    tables = relationship_tables(_scenario("rel-multiple-target", "multiple_target"))
    assert set(tables) == {"orders", "customers", "legacy_customers"}
    assert tables["customers"] != tables["legacy_customers"]


def test_v3_manifest_estate_is_explicit_and_split_reverse_pairs_are_behavioral():
    groups = json.loads((ROOT / "benchmarks" / "inference_evaluation" / "scenario_groups.json").read_text(encoding="utf-8"))["groups"]
    rel = json.loads((ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios_v3.json").read_text(encoding="utf-8"))["scenarios"]
    schema = json.loads((ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json").read_text(encoding="utf-8"))["scenarios"]
    estate = {item["group_id"] for item in groups}
    assert estate == {item["scenario_group_id"] for item in rel} | {item["scenario_group_id"] for item in schema} | {item["group_id"] for item in groups if item["task"] == "ENTITY_RESOLUTION"}
    rows = tuple(InferenceEvaluationExample(example_id=f"e-{item['group_id']}", scenario_group_id=item["group_id"], task=EvaluationTask.RELATIONSHIP_FUSION, query_id=item["group_id"]) for item in groups[:2])
    split = build_split_manifest(examples=rows, truth_fingerprint="truth", seed=1, explicit_roles={groups[0]["group_id"]:"DEVELOPMENT", groups[1]["group_id"]:"TEST"}, reverse_pair_groups={groups[0]["group_id"]:groups[1]["group_id"]})
    assert split.leakage_audit["reverse_pair_leakage"] is False
