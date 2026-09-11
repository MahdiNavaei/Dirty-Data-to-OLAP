"""Fail-closed binding of normalized provider output to a v2 evaluation run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import ProviderEvaluationBinding


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind_provider_output(
    *,
    root: Path,
    component: str,
    receipt_path: Path,
    protocol_hash: str,
    dataset_manifest_hash: str,
    truth_artifact_hash: str,
    split_hash: str,
    content_commit: str,
    expected_fixture_hashes: Mapping[str, str],
    expected_population: Mapping[str, Any] | None = None,
) -> ProviderEvaluationBinding:
    project_root = root.parents[3]
    reasons: list[str] = []
    receipt: dict[str, Any] = {}
    output_path = root / "missing-provider-output.json"
    loaded_hash = ""
    receipt_hash = ""
    if not receipt_path.is_file():
        reasons.append("RECEIPT_MISSING")
    else:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            output_path = project_root / str(receipt["output_artifact"])
            receipt_hash = str(receipt["output_hash"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            reasons.append("RECEIPT_INVALID")
    if output_path.is_file():
        loaded_hash = _sha(output_path)
        if loaded_hash != receipt_hash:
            reasons.append("OUTPUT_HASH_MISMATCH")
    else:
        reasons.append("NORMALIZED_OUTPUT_MISSING")
    if receipt.get("content_commit") not in {None, content_commit}:
        reasons.append("CONTENT_COMMIT_MISMATCH")
    if receipt.get("protocol_hash") not in {None, protocol_hash}:
        reasons.append("PROTOCOL_HASH_MISMATCH")
    if receipt.get("dataset_manifest_hash") not in {None, dataset_manifest_hash}:
        reasons.append("DATASET_HASH_MISMATCH")
    actual_fixtures = receipt.get("scenario_fixture_hashes", {})
    if not actual_fixtures and receipt.get("scenario_fixture_hash"):
        actual_fixtures = {"manifest": str(receipt["scenario_fixture_hash"])}
    if expected_fixture_hashes and actual_fixtures != dict(expected_fixture_hashes):
        reasons.append("SCENARIO_FIXTURE_HASH_MISMATCH")
    population_match = bool(expected_population is None or receipt.get("population_fingerprint") == expected_population.get("fingerprint"))
    if not population_match:
        reasons.append("PROVIDER_TRUTH_POPULATION_MISMATCH")
    loaded_for_metrics = bool(output_path.is_file() and not reasons)
    status = "EXECUTED" if loaded_for_metrics and population_match else "INSUFFICIENT_EVIDENCE"
    return ProviderEvaluationBinding(
        component=component,
        receipt_path=receipt_path.relative_to(project_root).as_posix() if receipt_path.is_relative_to(project_root) else str(receipt_path),
        output_path=output_path.relative_to(project_root).as_posix() if output_path.is_relative_to(project_root) else str(output_path),
        receipt_output_hash=receipt_hash,
        loaded_output_hash=loaded_hash,
        content_commit=content_commit,
        protocol_hash=protocol_hash,
        dataset_manifest_hash=dataset_manifest_hash,
        scenario_fixture_hashes=dict(actual_fixtures),
        scenario_group_ids=tuple(str(item) for item in receipt.get("scenario_group_ids", ())),
        truth_artifact_hash=truth_artifact_hash,
        split_hash=split_hash,
        status=status,
        loaded_for_metrics=loaded_for_metrics,
        population_match=population_match,
        failure_reasons=tuple(dict.fromkeys(reasons)),
    )
