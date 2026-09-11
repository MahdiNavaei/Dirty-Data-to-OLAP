from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dirty_data_to_olap.evaluation.provider_binding import bind_provider_output


def _write_bound_fixture(root: Path, *, include_output: bool) -> Path:
    run = root / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation"
    run.mkdir(parents=True)
    output = run / "normalized.json"
    payload = b'{"provider": "real"}\n'
    if include_output:
        output.write_bytes(payload)
    receipt = run / "provider_receipt.json"
    receipt.write_text(json.dumps({"output_artifact": "workspace/runs/step18-inference-baseline-v2/evaluation/normalized.json", "output_hash": hashlib.sha256(payload).hexdigest(), "scenario_fixture_hashes": {"manifest": "fixture"}, "execution_result": "COMPLETE"}), encoding="utf-8")
    return receipt


def test_valid_receipt_and_loaded_output_are_required(tmp_path: Path):
    receipt = _write_bound_fixture(tmp_path, include_output=True)
    binding = bind_provider_output(root=tmp_path / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation", component="dependency_discovery", receipt_path=receipt, protocol_hash="protocol", dataset_manifest_hash="dataset", truth_artifact_hash="truth", split_hash="split", content_commit="commit", expected_fixture_hashes={"manifest": "fixture"})
    assert binding.status == "EXECUTED"
    assert binding.loaded_for_metrics is True


def test_receipt_without_normalized_output_cannot_close(tmp_path: Path):
    receipt = _write_bound_fixture(tmp_path, include_output=False)
    binding = bind_provider_output(root=tmp_path / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation", component="dependency_discovery", receipt_path=receipt, protocol_hash="protocol", dataset_manifest_hash="dataset", truth_artifact_hash="truth", split_hash="split", content_commit="commit", expected_fixture_hashes={"manifest": "fixture"})
    assert binding.status == "INSUFFICIENT_EVIDENCE"
    assert "NORMALIZED_OUTPUT_MISSING" in binding.failure_reasons
