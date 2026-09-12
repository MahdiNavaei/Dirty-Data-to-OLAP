from __future__ import annotations

import pytest

from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactManifest,
    ArtifactStorageMode,
    CacheInputRef,
    CacheKey,
    LocalPlatformConfig,
    ResourceBudget,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StagedDatasetManifest,
    normalize_logical_key,
)


def test_logical_keys_are_posix_relative_and_traversal_free() -> None:
    assert normalize_logical_key("runs/run-1/artifact.json") == "runs/run-1/artifact.json"
    for value in ("../escape", "/absolute", "runs\\escape", "C:/drive", "runs//empty"):
        with pytest.raises(ValueError):
            normalize_logical_key(value)


def test_platform_contracts_forbid_unknown_fields_and_cache_is_deterministic() -> None:
    with pytest.raises(ValueError):
        ArtifactManifest(
            artifact_id="a1",
            run_id="run-1",
            stage_id="STAGE",
            attempt_id="attempt-1",
            artifact_kind="metadata",
            media_type="application/json",
            producer="test",
            unexpected="nope",
        )
    inputs = (CacheInputRef(artifact_id="input-1", content_hash="a" * 64),)
    left = CacheKey(stage_id="STAGE", component_id="component", input_artifacts=inputs, policy_version="p1", configuration_fingerprint="c1", engine_version="e1", code_version="code1")
    right = CacheKey(stage_id="STAGE", component_id="component", input_artifacts=inputs, policy_version="p1", configuration_fingerprint="c1", engine_version="e1", code_version="code1")
    assert left.key_hash == right.key_hash


def test_platform_config_fingerprint_changes_with_control_root(tmp_path) -> None:
    common = dict(
        project_root=str(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        artifact_root=str(tmp_path / "workspace" / "artifacts"),
        staging_root=str(tmp_path / "workspace" / "staging"),
        control_store_path=str(tmp_path / "workspace" / "control.sqlite"),
        cache_root=str(tmp_path / "workspace" / "cache"),
        resource_budget=ResourceBudget(disk_budget_bytes=1000),
    )
    first = LocalPlatformConfig(**common)
    second = LocalPlatformConfig(**{**common, "control_store_path": str(tmp_path / "workspace" / "other.sqlite")})
    assert first.configuration_fingerprint != second.configuration_fingerprint


def test_run_and_attempt_contracts_use_existing_lifecycle_vocabulary() -> None:
    run = RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="config", status=RunStatus.CREATED)
    attempt = StageAttemptRecord(attempt_id="attempt-1", run_id=run.run_id, stage_id="STAGE", attempt_number=1, policy_config_fingerprint="policy")
    assert run.status is RunStatus.CREATED
    assert attempt.status.value == "PENDING"


def test_external_manifest_requires_explicit_external_mode() -> None:
    with pytest.raises(ValueError):
        ArtifactManifest(
            artifact_id="a1",
            run_id="run-1",
            stage_id="STAGE",
            attempt_id="attempt-1",
            artifact_kind="metadata",
            media_type="application/json",
            producer="test",
            storage_mode=ArtifactStorageMode.EXTERNAL,
        )
