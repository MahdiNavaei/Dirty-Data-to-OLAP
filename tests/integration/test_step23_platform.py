from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, LocalStagingStore, SQLiteControlStore
from dirty_data_to_olap.application.platform import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ConcurrencyConflictError,
    CleanupAuthorizationError,
    GateEvidenceService,
    PlatformCacheService,
    PlatformError,
    PlatformIntegrityService,
    PlatformLifecycleService,
    StaleDependencyError,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactDependency,
    ArtifactManifest,
    ArtifactPublicationState,
    ArtifactStorageMode,
    CleanupAction,
    CleanupCandidate,
    CacheEntry,
    CacheInputRef,
    CacheKey,
    CleanupAuthorization,
    CleanupDeletionPermit,
    GateEvidenceStatus,
    RetentionClass,
    RetentionPolicy,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
    StagedDatasetManifest,
    StagedDatasetPart,
    staged_part_artifact_id,
    staged_part_logical_key,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport


def _stores(tmp_path: Path, *, quota: int | None = None) -> tuple[LocalArtifactStore, SQLiteControlStore]:
    project = tmp_path / "project"
    artifacts = LocalArtifactStore(project / "workspace" / "artifacts", project_root=project, disk_budget_bytes=quota)
    control = SQLiteControlStore(project / "workspace" / "control.sqlite", project_root=project)
    return artifacts, control


def _manifest(run_id: str, artifact_id: str, *, content_hash: str | None = None, created_at: datetime | None = None, kind: str = "metadata", retention: RetentionClass = RetentionClass.RUN_SCOPED, storage_mode: ArtifactStorageMode = ArtifactStorageMode.MANAGED, locator: str | None = None) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=artifact_id,
        run_id=run_id,
        stage_id="STEP23",
        attempt_id="attempt-1",
        artifact_kind=kind,
        media_type="application/json",
        producer="step23-test",
        storage_mode=storage_mode,
        external_locator=locator,
        content_hash=content_hash,
        created_at=created_at or datetime.now(timezone.utc),
        retention_class=retention,
    )


def test_local_artifacts_are_immutable_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store, control = _stores(tmp_path)
    control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg"))
    first = store.publish(_manifest("run-1", "artifact-1"), b"same bytes")
    second = store.publish(_manifest("run-1", "artifact-1"), b"same bytes")
    assert second == first
    other = store.publish(_manifest("run-1", "artifact-2"), b"same bytes")
    assert other.storage_key == first.storage_key
    assert other.artifact_id != first.artifact_id
    with pytest.raises(ArtifactConflictError):
        store.publish(_manifest("run-1", "artifact-1"), b"different bytes")
    assert store.verify(first).state.value == "VERIFIED"


def test_claimed_hash_and_quota_fail_closed(tmp_path: Path) -> None:
    store, _ = _stores(tmp_path, quota=3)
    with pytest.raises(ArtifactIntegrityError):
        store.publish(_manifest("run-1", "artifact-1", content_hash="d" * 64), b"abc")
    with pytest.raises(ArtifactIntegrityError):
        store.publish(_manifest("run-1", "artifact-2"), b"abcd")
    assert store.list_artifacts() == ()


def test_concurrent_publication_converges_without_mixed_content(tmp_path: Path) -> None:
    store, _ = _stores(tmp_path)
    same_manifest = _manifest("run-1", "concurrent")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.publish(same_manifest, b"same"), range(4)))
    assert {item.content_hash for item in results} == {hashlib.sha256(b"same").hexdigest()}
    conflicting = _manifest("run-1", "conflicting")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.publish, conflicting, value) for value in (b"left", b"right")]
        outcomes = [future.result() if not future.exception() else future.exception() for future in futures]
    assert sum(isinstance(item, ArtifactConflictError) for item in outcomes) == 1
    assert len(store.list_artifacts()) == 2


def test_control_store_restart_cas_dependencies_and_atomic_bundle(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    run = control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg"))
    attempt = control.create_stage_attempt(StageAttemptRecord(attempt_id="attempt-1", run_id=run.run_id, stage_id="STEP23", attempt_number=1, policy_config_fingerprint="p"))
    updated = control.update_run(run.model_copy(update={"status": RunStatus.RUNNING}), expected_revision=run.revision)
    assert updated.revision == 1
    with pytest.raises(ConcurrencyConflictError):
        control.update_run(run.model_copy(update={"status": RunStatus.FAILED}), expected_revision=run.revision)
    upstream = artifacts.publish(_manifest("run-1", "upstream"), b"upstream")
    downstream = artifacts.publish(_manifest("run-1", "downstream"), b"downstream")
    control.register_artifact(upstream)
    control.register_artifact_with_dependencies(downstream, [ArtifactDependency(artifact_id="downstream", upstream_artifact_id="upstream", expected_content_hash=upstream.content_hash, relationship_kind="DERIVED_FROM")])
    assert control.resolve_dependencies("downstream")[0].upstream_artifact_id == "upstream"
    assert control.get_stage_attempt(attempt.attempt_id) == attempt
    reopened = SQLiteControlStore(control.path, project_root=control.project_root)
    assert reopened.get_run("run-1").status is RunStatus.RUNNING
    assert reopened.get_artifact("downstream").content_hash == downstream.content_hash
    with pytest.raises(PlatformError):
        reopened.register_artifact_with_dependencies(artifacts.publish(_manifest("run-1", "atomic-failure"), b"x"), [ArtifactDependency(artifact_id="atomic-failure", upstream_artifact_id="missing", expected_content_hash="e" * 64, relationship_kind="INPUT")])
    assert reopened.get_artifact("atomic-failure") is None


def test_stale_dependency_and_missing_artifact_integrity_are_explicit(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg"))
    upstream = artifacts.publish(_manifest("run-1", "upstream"), b"one")
    downstream = artifacts.publish(_manifest("run-1", "downstream"), b"two")
    control.register_artifact(upstream)
    control.register_artifact(downstream)
    with control._transaction() as connection:
        connection.execute("INSERT INTO artifact_dependencies(artifact_id, upstream_artifact_id, expected_content_hash, relationship_kind) VALUES (?, ?, ?, ?)", ("downstream", "upstream", "f" * 64, "INPUT"))
    with pytest.raises(StaleDependencyError):
        control.resolve_dependencies("downstream")
    blob = artifacts._blob_path(upstream.content_hash)
    blob.unlink()
    assert artifacts.verify(upstream).state.value == "MISSING"
    assert PlatformIntegrityService().scan("run-1", control_store=control, artifact_store=artifacts).failed


def test_external_artifact_and_g6_gate_recover_after_restart(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    run = control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg", git_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2"))
    target = control.project_root / "workspace" / "target.duckdb"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"controlled target")
    external = artifacts.register_external(_manifest("run-1", "target", kind="MaterializationArtifact", storage_mode=ArtifactStorageMode.EXTERNAL, locator="workspace/target.duckdb"), "workspace/target.duckdb")
    (control.project_root / "workspace" / "other.duckdb").write_bytes(b"other target")
    with pytest.raises(ArtifactConflictError):
        artifacts.register_external(_manifest("run-1", "target", kind="MaterializationArtifact", storage_mode=ArtifactStorageMode.EXTERNAL, locator="workspace/target.duckdb"), "workspace/other.duckdb")
    report_source = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "step22-reference-run" / "validation" / "validation_report.json"
    report_path = control.project_root / "workspace" / "validation_report.json"
    report_path.write_bytes(report_source.read_bytes())
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))
    report = ValidationReport.model_validate({key: value for key, value in report_payload.items() if key != "content_hash"})
    report_ref = artifacts.register_external(_manifest("run-1", "validation-report", kind="ValidationReport", retention=RetentionClass.PINNED_GATE_EVIDENCE, storage_mode=ArtifactStorageMode.EXTERNAL, locator="workspace/validation_report.json"), "workspace/validation_report.json")
    control.register_artifact(external)
    control.register_artifact(report_ref)
    gate = GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=run.run_id, report=report, report_artifact=report_ref, verified_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2", control_store=control, artifact_store=artifacts)
    with pytest.raises(PlatformError):
        GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=run.run_id, report=report, report_artifact=external, verified_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2", control_store=control, artifact_store=artifacts)
    with pytest.raises(ArtifactConflictError):
        control.record_gate_evidence(gate.model_copy(update={"status": GateEvidenceStatus.FAIL, "eligible": False}), validation_report=report, artifact_store=artifacts)
    reopened = SQLiteControlStore(control.path, project_root=control.project_root)
    recovered = reopened.get_gate_evidence(gate.gate_id, run_id=run.run_id)
    assert recovered.status is GateEvidenceStatus.PASS and recovered.eligible is True
    assert artifacts.verify(external).state.value == "VERIFIED"
    assert artifacts.read(report_ref) == report_path.read_bytes()


def test_staging_manifest_and_cache_are_durable(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg"))
    staging = LocalStagingStore(artifacts, control_store=control)
    seed_logical_key = staged_part_logical_key(run_id="run-1", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", dataset_id="dataset-1", dataset_version="v1", part_id="seed")
    seed_artifact_id = staged_part_artifact_id(run_id="run-1", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", dataset_id="dataset-1", dataset_version="v1", part_id="seed", schema_fingerprint="schema-1")
    seed_ref = artifacts.publish(ArtifactManifest(artifact_id=seed_artifact_id, run_id="run-1", stage_id="SOURCE_SNAPSHOT_STAGE", attempt_id="attempt-1", artifact_kind="STAGED_DATASET_PART", media_type="application/vnd.apache.parquet", producer="test", logical_key=seed_logical_key), b"seed")
    base = StagedDatasetManifest(dataset_id="dataset-1", dataset_version="v1", run_id="run-1", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", schema_fingerprint="schema-1", format="parquet", parts=(StagedDatasetPart(part_id="seed", logical_key=seed_logical_key, artifact_ref=seed_ref, schema_fingerprint="schema-1"),))
    part = staging.publish_part(base, "part-000", b"parquet-reference", attempt_id="attempt-1", producer="step23")
    control.register_artifact(part.artifact_ref)
    manifest = base.model_copy(update={"parts": (part,), "row_count": 1})
    staging.register_manifest(manifest)
    assert control.get_staged_dataset("run-1", "dataset-1", "v1").parts[0].part_id == "part-000"
    key = CacheKey(stage_id="STEP23", component_id="platform", input_artifacts=(CacheInputRef(artifact_id=part.artifact_ref.artifact_id, content_hash=part.artifact_ref.content_hash),), policy_version="p1", configuration_fingerprint="cfg", engine_version="local", code_version="step23")
    entry = CacheEntry(cache_key_hash=key.key_hash, cache_key=key, output_artifact_id=part.artifact_ref.artifact_id, output_content_hash=part.artifact_ref.content_hash)
    control.record_cache_entry(entry)
    assert PlatformCacheService().resolve(key, control_store=control, artifact_store=artifacts).artifact_id == part.artifact_ref.artifact_id


def test_staged_identity_is_run_scoped_and_manifest_closure_is_fail_closed(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    for run_id in ("run-1", "run-2"):
        control.create_run(RunRecord(run_id=run_id, project_id="project", configuration_fingerprint="cfg"))
    staging = LocalStagingStore(artifacts, control_store=control)
    manifests = []
    for run_id in ("run-1", "run-2"):
        logical_key = staged_part_logical_key(run_id=run_id, source_id="source", snapshot_id="snapshot", table_id="table", dataset_id="dataset", dataset_version="v1", part_id="part-000")
        artifact_id = staged_part_artifact_id(run_id=run_id, source_id="source", snapshot_id="snapshot", table_id="table", dataset_id="dataset", dataset_version="v1", part_id="part-000", schema_fingerprint="schema")
        ref = artifacts.publish(ArtifactManifest(artifact_id=artifact_id, run_id=run_id, stage_id="SOURCE_SNAPSHOT_STAGE", attempt_id="attempt-1", artifact_kind="STAGED_DATASET_PART", media_type="application/vnd.apache.parquet", producer="test", logical_key=logical_key), b"same-part")
        control.register_artifact(ref)
        manifest = StagedDatasetManifest(dataset_id="dataset", dataset_version="v1", run_id=run_id, source_id="source", snapshot_id="snapshot", table_id="table", schema_fingerprint="schema", format="parquet", parts=(StagedDatasetPart(part_id="part-000", logical_key=logical_key, artifact_ref=ref, row_count=1, schema_fingerprint="schema"),), row_count=1)
        staging.register_manifest(manifest)
        manifests.append(manifest)
    assert manifests[0].parts[0].artifact_ref.artifact_id != manifests[1].parts[0].artifact_ref.artifact_id
    assert manifests[0].parts[0].logical_key != manifests[1].parts[0].logical_key
    assert control.get_staged_dataset("run-1", "dataset", "v1") == manifests[0]
    assert control.get_staged_dataset("run-2", "dataset", "v1") == manifests[1]
    with pytest.raises(ValueError):
        staging.register_manifest(manifests[0].model_copy(update={"run_id": "run-2"}))
    with pytest.raises(ValueError):
        staging.register_manifest(manifests[0].model_copy(update={"table_id": "other-table"}))
    with pytest.raises(ValueError):
        staging.register_manifest(manifests[0].model_copy(update={"row_count": 2}))


def test_sqlite_migrates_legacy_staging_and_gate_rows_without_reset(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    run = control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg"))
    logical_key = staged_part_logical_key(run_id=run.run_id, source_id="source", snapshot_id="snapshot", table_id="table", dataset_id="dataset", dataset_version="v1", part_id="part-000")
    artifact_id = staged_part_artifact_id(run_id=run.run_id, source_id="source", snapshot_id="snapshot", table_id="table", dataset_id="dataset", dataset_version="v1", part_id="part-000", schema_fingerprint="schema")
    staged_ref = artifacts.publish(ArtifactManifest(artifact_id=artifact_id, run_id=run.run_id, stage_id="SOURCE_SNAPSHOT_STAGE", attempt_id="attempt-1", artifact_kind="STAGED_DATASET_PART", media_type="application/vnd.apache.parquet", producer="test", logical_key=logical_key), b"legacy-part")
    control.register_artifact(staged_ref)
    manifest = StagedDatasetManifest(dataset_id="dataset", dataset_version="v1", run_id=run.run_id, source_id="source", snapshot_id="snapshot", table_id="table", schema_fingerprint="schema", format="parquet", parts=(StagedDatasetPart(part_id="part-000", logical_key=logical_key, artifact_ref=staged_ref, row_count=1, schema_fingerprint="schema"),), row_count=1)
    control.record_staged_dataset(manifest)
    report_ref = artifacts.publish(_manifest(run.run_id, "legacy-report", kind="ValidationReport"), b"legacy-report")
    control.register_artifact(report_ref)
    with sqlite3.connect(control.path) as connection:
        connection.execute("DROP INDEX idx_staged_dataset_scope")
        connection.execute("ALTER TABLE staged_datasets RENAME TO staged_datasets_v3")
        connection.execute("CREATE TABLE staged_datasets (run_id TEXT NOT NULL REFERENCES runs(run_id), dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL, manifest TEXT NOT NULL, PRIMARY KEY(dataset_id, dataset_version))")
        raw_manifest = json.loads(connection.execute("SELECT manifest FROM staged_datasets_v3").fetchone()[0])
        raw_manifest.pop("identity_version", None)
        connection.execute("INSERT INTO staged_datasets(run_id, dataset_id, dataset_version, manifest) VALUES (?, ?, ?, ?)", (run.run_id, "dataset", "v1", json.dumps(raw_manifest, sort_keys=True, separators=(",", ":"))))
        connection.execute("DROP TABLE staged_datasets_v3")
        connection.execute("ALTER TABLE gate_evidence RENAME TO gate_evidence_v3")
        connection.execute("CREATE TABLE gate_evidence (gate_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id), status TEXT NOT NULL, eligible INTEGER NOT NULL, validation_report_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), validation_report_content_hash TEXT NOT NULL, policy_version TEXT NOT NULL, verified_content_commit TEXT NOT NULL, recorded_at TEXT NOT NULL, provenance_refs TEXT NOT NULL, PRIMARY KEY(gate_id, run_id))")
        connection.execute("INSERT INTO gate_evidence(gate_id, run_id, status, eligible, validation_report_artifact_id, validation_report_content_hash, policy_version, verified_content_commit, recorded_at, provenance_refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("G6_DATA_CORRECTNESS", run.run_id, "PASS", 1, report_ref.artifact_id, report_ref.content_hash, "legacy-policy", "f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2", "2026-01-01T00:00:00+00:00", "[]"))
        connection.execute("DROP TABLE gate_evidence_v3")
        connection.execute("UPDATE schema_meta SET schema_version = 1")
    migrated = SQLiteControlStore(control.path, project_root=control.project_root)
    recovered_manifest = migrated.get_staged_dataset(run.run_id, "dataset", "v1")
    recovered_gate = migrated.get_gate_evidence("G6_DATA_CORRECTNESS", run_id=run.run_id)
    assert migrated.schema_version == 6
    assert recovered_manifest.identity_version == "legacy-v1"
    assert recovered_manifest.parts[0].artifact_ref.artifact_id == staged_ref.artifact_id
    assert recovered_gate.validation_report_run_id == run.run_id
    assert recovered_gate.validation_report_id == f"legacy:{report_ref.artifact_id}"
    assert "legacy:gate-evidence-unverified" in recovered_gate.provenance_refs


def test_cleanup_is_plan_first_and_pinned_evidence_is_protected(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    run = control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg", status=RunStatus.SUCCEEDED))
    candidate = artifacts.publish(_manifest("run-1", "old", created_at=old), b"old")
    pinned = artifacts.publish(_manifest("run-1", "gate", created_at=old, kind="ValidationReport").model_copy(update={"retention_class": RetentionClass.PINNED_GATE_EVIDENCE}), b"gate")
    control.register_artifact(candidate)
    control.register_artifact(pinned)
    service = PlatformLifecycleService()
    policy = (RetentionPolicy(retention_class=RetentionClass.RUN_SCOPED, max_age_seconds=1, reason="old run artifact"),)
    plan = service.build_cleanup_plan(control_store=control, run_id=run.run_id, policy=policy, now=datetime.now(timezone.utc))
    assert [item.artifact_id for item in plan.candidates] == ["old"]
    dry = service.execute_cleanup(plan, CleanupAuthorization(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, actor="test", authorized=True), control_store=control, artifact_store=artifacts)
    assert dry.executed is False and artifacts.exists(candidate)
    executable = plan.model_copy(update={"dry_run": False})
    executed = service.execute_cleanup(executable, CleanupAuthorization(plan_id=executable.plan_id, plan_content_hash=executable.content_hash, actor="test", authorized=True), control_store=control, artifact_store=artifacts)
    assert executed.deleted_artifact_ids == ("old",)
    assert control.get_artifact("old").publication_state is ArtifactPublicationState.TOMBSTONED
    with pytest.raises(CleanupAuthorizationError):
        artifacts.delete(pinned, CleanupDeletionPermit(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, run_id=run.run_id, artifact_id=pinned.artifact_id, expected_content_hash=pinned.content_hash, expected_byte_size=pinned.byte_size, retention_class=pinned.retention_class, planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED))


def test_cleanup_rejects_active_run_and_retained_dependents(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    run = control.create_run(RunRecord(run_id="run-active", project_id="project", configuration_fingerprint="cfg", status=RunStatus.RUNNING))
    candidate = artifacts.publish(_manifest(run.run_id, "active-artifact"), b"active")
    control.register_artifact(candidate)
    plan = PlatformLifecycleService().build_cleanup_plan(
        control_store=control,
        run_id=run.run_id,
        policy=(RetentionPolicy(retention_class=RetentionClass.RUN_SCOPED, max_age_seconds=0, reason="active negative"),),
    )
    assert plan.candidates == ()
    forced = plan.model_copy(update={
        "dry_run": False,
        "candidates": (CleanupCandidate(
            artifact_id=candidate.artifact_id,
            expected_content_hash=candidate.content_hash,
            reason="forced negative",
            retention_class=RetentionClass.RUN_SCOPED,
            age_seconds=1,
            byte_size=candidate.byte_size,
            planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED,
        ),),
    })
    result = PlatformLifecycleService().execute_cleanup(forced, CleanupAuthorization(plan_id=forced.plan_id, plan_content_hash=forced.content_hash, actor="test", authorized=True), control_store=control, artifact_store=artifacts)
    assert result.rejected_artifact_ids == (candidate.artifact_id,)
    assert artifacts.exists(candidate)
