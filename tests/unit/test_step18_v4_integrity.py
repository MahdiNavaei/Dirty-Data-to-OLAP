from __future__ import annotations

import inspect
import json
from pathlib import Path
import hashlib
import subprocess
from types import SimpleNamespace

import pytest

from tools.provision_step18_v4_runtimes import _child_environment
from tools.step18_provider_fixtures import entity_spec
from tools.run_step18_v4_evaluation import _er_leakage_audit, _fuse_joint_schema_rows, _schema_endpoint
from dirty_data_to_olap.adapters.entity_resolution.splink import SplinkEntityResolutionAdapter
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionMode
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService

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


def test_v4_provisioning_uses_package_name_expected_version_and_import_check():
    source = inspect.getsource(__import__("tools.provision_step18_v4_runtimes", fromlist=["main"]))
    assert '"package_name"' in source
    assert '"expected_version"' in source
    assert 'm.version(package)' in source
    assert "importlib.import_module(module)" in source
    assert '"valentine==1.0.0"' in source
    assert '"splink==4.0.17"' in source
    assert '"-e"' not in source


def test_v4_provider_runners_are_decoupled_from_optional_test_modules_and_sentinels():
    for name in ("tools.run_step18_v4_schema_provider", "tools.run_step18_v4_entity_provider", "tools.run_step18_v3_schema_provider", "tools.run_step18_v3_entity_provider"):
        source = inspect.getsource(__import__(name, fromlist=["main"]))
        assert "tests/integration" not in source
        assert "valentine-runtime" not in source
        assert "splink-runtime" not in source


def test_v4_available_valentine_path_executes_without_import_nameerror():
    runtime = ROOT / "workspace/test-temp/step18-runtimes/matching-venv/Scripts/python.exe"
    if not runtime.is_file():
        pytest.skip("dedicated Step18 matching runtime is not provisioned")
    completed = subprocess.run(
        [str(runtime), str(ROOT / "tools/run_step18_v4_schema_provider.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"scenario_count": 11' in completed.stdout
    assert "NameError" not in completed.stdout + completed.stderr
    receipt = _json("workspace/runs/step18-inference-baseline-v4/evaluation/schema_matching/provider_receipt.json")
    output = ROOT / receipt["output_artifact"]
    assert receipt["output_hash"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_v4_provider_receipts_are_immutable_execution_evidence():
    for name in ("schema_matching", "entity_resolution"):
        receipt = _json(f"workspace/runs/step18-inference-baseline-v4/evaluation/{name}/provider_receipt.json")
        assert receipt["receipt_content_hash"] == hashlib.sha256(
            (json.dumps({key: value for key, value in receipt.items() if key != "receipt_content_hash"}, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        assert not {"content_commit", "protocol_hash", "dataset_manifest_hash", "truth_artifact_hash", "split_hash"}.intersection(receipt)
    for name in ("tools.run_step18_v4_schema_provider", "tools.run_step18_v4_entity_provider"):
        assert "_annotate_receipt" not in inspect.getsource(__import__(name, fromlist=["main"]))


def test_v4_er_spec_includes_same_source_duplicate_capability():
    spec = entity_spec("v4")
    assert spec.mode is EntityResolutionMode.LINK_AND_DEDUPE
    assert SplinkEntityResolutionAdapter._all_pairs(spec, [{"source_dataset": "crm"}, {"source_dataset": "crm"}]) == 1


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


def test_v4_joint_schema_fusion_calls_one_fusion_request_per_scenario(monkeypatch):
    item = _json("workspace/runs/step18-inference-baseline-v4/evaluation/schema_matching/normalized_provider_results.json")["results"][1]
    combined = SchemaMatchResult.model_validate(item["result"])
    calls = []

    def fake_fuse(self, request, **kwargs):
        calls.append(kwargs["schema_match_result"])
        return SimpleNamespace(mappings=(), failures=())

    monkeypatch.setattr(EvidenceFusionService, "fuse", fake_fuse)
    _fuse_joint_schema_rows([{"group": item["scenario_group_id"], "result": combined}], {}, {}, {})
    assert calls == [combined]


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


def test_v4_g5_passes_only_after_schema_fusion_is_evaluated():
    report = _json("workspace/runs/step18-inference-baseline-v4/evaluation/report.json")
    assert report["formal_gate"] == "PASS"
    assert report["assessment"]["required_task_completion"]["SCHEMA_FUSION_EVALUATED"] is True


def test_v4_reproducibility_separates_exact_bytes_from_semantic_evidence():
    repro = _json("workspace/runs/step18-inference-baseline-v4/evaluation/reproducibility/relationship_provider.json")
    assert repro["status"] == "PASS"
    assert repro["semantic_inference_equal"] is True
    assert repro["task_metrics_equal"] is True
    assert repro["byte_outputs_equal"] is False
    assert repro["first_artifact_content_hash"] != repro["second_artifact_content_hash"]


def test_v4_threshold_frontier_is_complete_and_non_authorizing():
    frontier = _json("workspace/runs/step18-inference-baseline-v4/evaluation/thresholds/frontier.json")
    assert frontier["selected_threshold"] is None
    assert frontier["runtime_policy_mutated"] is False
    required = {"eligible_decisions", "selected_count", "coverage", "tp", "fp", "precision", "recall", "review_remainder", "conflict_incomplete_abstention"}
    assert required <= set(frontier["points"][0])
