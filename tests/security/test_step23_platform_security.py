from __future__ import annotations

from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.platform import PathConfinementError, PlatformError, UnsupportedSchemaVersionError
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, ArtifactStorageMode


def test_external_locator_cannot_escape_or_reach_protected_workspace(tmp_path: Path) -> None:
    project = tmp_path / "project"
    store = LocalArtifactStore(project / "workspace" / "artifacts", project_root=project)
    manifest = ArtifactManifest(artifact_id="external", run_id="run-1", stage_id="STEP23", attempt_id="attempt-1", artifact_kind="external", media_type="application/octet-stream", producer="test", storage_mode=ArtifactStorageMode.EXTERNAL, external_locator="placeholder")
    for locator in ("../secret", "/secret", "C:/secret", "tests/quality_unit_artifacts/private.txt"):
        with pytest.raises(PathConfinementError):
            store.register_external(manifest, locator)


def test_control_store_rejects_protected_path_and_newer_schema(tmp_path: Path) -> None:
    project = tmp_path / "project"
    with pytest.raises(PlatformError):
        SQLiteControlStore(project / "tests" / "quality_unit_artifacts" / "control.sqlite", project_root=project)
    db = SQLiteControlStore(project / "workspace" / "control.sqlite", project_root=project)
    import sqlite3

    with sqlite3.connect(db.path) as connection:
        connection.execute("UPDATE schema_meta SET schema_version = 99")
    with pytest.raises(UnsupportedSchemaVersionError):
        SQLiteControlStore(db.path, project_root=project)


def test_symlink_to_protected_workspace_is_rejected_when_supported(tmp_path: Path) -> None:
    project = tmp_path / "project"
    store = LocalArtifactStore(project / "workspace" / "artifacts", project_root=project)
    protected = project / "tests" / "quality_unit_artifacts"
    protected.mkdir(parents=True)
    target = protected / "fixture.json"
    target.write_text("protected", encoding="utf-8")
    link = project / "workspace" / "protected-link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this Windows test environment")
    manifest = ArtifactManifest(
        artifact_id="symlink-protected",
        run_id="run-1",
        stage_id="STEP23",
        attempt_id="attempt-1",
        artifact_kind="external",
        media_type="application/octet-stream",
        producer="test",
        storage_mode=ArtifactStorageMode.EXTERNAL,
        external_locator="workspace/protected-link.json",
    )
    with pytest.raises(PathConfinementError):
        store.register_external(manifest, "workspace/protected-link.json")


def test_sqlite_metadata_filters_are_parameterized(tmp_path: Path) -> None:
    project = tmp_path / "project"
    db = SQLiteControlStore(project / "workspace" / "control.sqlite", project_root=project)
    assert db.get_run("' OR 1=1 --") is None
