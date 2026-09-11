"""Fail-closed binding of normalized provider output to a v2 evaluation run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import ProviderEvaluationBinding


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_content_hash(receipt: Mapping[str, Any]) -> str:
    payload = dict(receipt)
    payload.pop("receipt_content_hash", None)
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


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
    required_receipt_fields = ("component", "provider", "version", "adapter", "execution_result", "scenario_group_ids", "scenario_fixture_hashes", "population_fingerprint", "output_artifact", "output_hash", "receipt_content_hash")
    if not receipt_path.is_file():
        reasons.append("RECEIPT_MISSING")
    else:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            output_path = project_root / str(receipt["output_artifact"])
            receipt_hash = str(receipt["output_hash"])
            missing = [field for field in required_receipt_fields if field not in receipt]
            if missing:
                reasons.append("RECEIPT_REQUIRED_FIELD_MISSING:" + ",".join(missing))
            elif str(receipt["receipt_content_hash"]) != _receipt_content_hash(receipt):
                reasons.append("RECEIPT_CONTENT_HASH_MISMATCH")
            if str(receipt.get("execution_result", "")) != "COMPLETE":
                reasons.append("EXECUTION_RESULT_NOT_COMPLETE")
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            reasons.append("RECEIPT_INVALID")
    if output_path.is_file():
        loaded_hash = _sha(output_path)
        if loaded_hash != receipt_hash:
            reasons.append("OUTPUT_HASH_MISMATCH")
    else:
        reasons.append("NORMALIZED_OUTPUT_MISSING")
    if receipt.get("content_commit") is not None or receipt.get("protocol_hash") is not None or receipt.get("dataset_manifest_hash") is not None or receipt.get("truth_artifact_hash") is not None or receipt.get("split_hash") is not None:
        reasons.append("RECEIPT_CONTAINS_POST_EXECUTION_BINDING")
    actual_fixtures = receipt.get("scenario_fixture_hashes", {})
    if not actual_fixtures and receipt.get("scenario_fixture_hash"):
        actual_fixtures = {"manifest": str(receipt["scenario_fixture_hash"])}
    if expected_fixture_hashes and actual_fixtures != dict(expected_fixture_hashes):
        reasons.append("SCENARIO_FIXTURE_HASH_MISMATCH")
    observed_population = str(receipt.get("population_fingerprint", ""))
    population_match = bool(expected_population is None or observed_population and observed_population == expected_population.get("fingerprint"))
    if not population_match:
        reasons.append("PROVIDER_TRUTH_POPULATION_MISMATCH")
    loaded_for_metrics = bool(output_path.is_file() and not reasons)
    status = "EXECUTED" if loaded_for_metrics and population_match else "INSUFFICIENT_EVIDENCE"
    return ProviderEvaluationBinding(
        component=component,
        receipt_path=receipt_path.relative_to(project_root).as_posix() if receipt_path.is_relative_to(project_root) else str(receipt_path),
        output_path=output_path.relative_to(project_root).as_posix() if output_path.is_relative_to(project_root) else str(output_path),
        receipt_output_hash=receipt_hash,
        receipt_content_hash=str(receipt.get("receipt_content_hash", "")),
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
        expected_population_fingerprint=str(expected_population.get("fingerprint", "")) if expected_population else "",
        observed_population_fingerprint=observed_population,
        failure_reasons=tuple(dict.fromkeys(reasons)),
    )
