from __future__ import annotations

import inspect
import json
from pathlib import Path

from tools.provision_step18_v4_runtimes import _child_environment
from tools.run_step18_v4_evaluation import _er_leakage_audit, _schema_endpoint

ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_v4_provisioning_keeps_runtime_and_sandboxes_temp(tmp_path):
    environment, record = _child_environment(tmp_path / "matching-venv")
    assert Path(environment["TEMP"]).is_relative_to(tmp_path)
    assert Path(environment["TMP"]).is_relative_to(tmp_path)
    assert Path(environment["PIP_CACHE_DIR"]).is_relative_to(tmp_path)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert record["proxy_values_redacted"] is True


def test_v4_relationship_fusion_quality_is_not_only_decision_count():
    metrics = _json("workspace/runs/step18-inference-baseline-v4/evaluation/fusion/test_metrics.json")
    assert metrics["status"] == "EVALUATED"
    assert metrics["average_precision"]["value"] is not None
    assert metrics["ranking"]["mean_reciprocal_rank"]["value"] is not None
    assert "band_composition" in metrics


def test_v4_schema_candidate_identity_uses_actual_predicted_endpoint():
    left = {"source_id":"crm", "source_table_id":"crm_t", "source_column_id":"crm_a", "target_source_id":"erp", "target_table_id":"erp_t", "target_column_id":"erp_a"}
    right = left | {"target_column_id":"erp_b"}
    assert _schema_endpoint(left) != _schema_endpoint(right)
    assert "actual_candidate_ids" in _json("workspace/runs/step18-inference-baseline-v4/evaluation/schema_matching/test_metrics.json")


def test_v4_schema_recall_has_explicit_denominator():
    recall = _json("workspace/runs/step18-inference-baseline-v4/evaluation/schema_matching/test_metrics.json")["candidate_generation_recall"]
    assert "numerator" in recall and "denominator" in recall


def test_v4_schema_fusion_path_consumes_actual_schema_result():
    source = inspect.getsource(__import__("tools.run_step18_v4_evaluation", fromlist=["main"]))
    assert "schema_match_result=result" in source
    assert "fused.mappings" in source


def test_v4_er_negative_cases_bind_their_own_records():
    groups = _json("benchmarks/inference_evaluation/scenario_groups_v4.json")["groups"]
    by_case = {item["case_id"]: item for item in groups if item["task"] == "ENTITY_RESOLUTION"}
    assert by_case["common_name_nonmatch"]["record_refs"] == ["crm-r4", "erp-r4"]
    assert by_case["placeholder_phone"]["record_refs"] == ["crm-r5", "erp-r5"]
    assert by_case["shared_household"]["record_refs"] == ["crm-r6", "erp-r6"]
    assert by_case["missing_fields"]["truth_entity_ids"] == ["truth_d"]


def test_v4_truth_cluster_leakage_audit_detects_injected_split():
    groups = _json("benchmarks/inference_evaluation/scenario_groups_v4.json")["groups"]
    truth = _json("benchmarks/inference_evaluation/truth/entity_truth_v4.json")
    injected = groups + [{"group_id":"er-injected-leak","task":"ENTITY_RESOLUTION","split":"CALIBRATION","case_id":"injected","record_refs":["crm-r11"],"expected_kind":"negative"}]
    audit = _er_leakage_audit(injected, truth)
    assert audit["truth_cluster_leakage"] is False
    assert audit["case_pair_role_consistent"] is False


def test_v4_er_test_universe_is_exactly_test_assigned_records():
    metrics = _json("workspace/runs/step18-inference-baseline-v4/evaluation/entity_resolution/test_metrics.json")
    assert metrics["evaluated_record_universe_count"] == 10
    assert metrics["defined_tn_universe"] == 45


def test_v4_g5_cannot_pass_with_missing_schema_fusion():
    report = _json("workspace/runs/step18-inference-baseline-v4/evaluation/report.json")
    assert report["formal_gate"] == "PENDING"
    assert report["assessment"]["required_task_completion"]["SCHEMA_FUSION_EVALUATED"] is False

