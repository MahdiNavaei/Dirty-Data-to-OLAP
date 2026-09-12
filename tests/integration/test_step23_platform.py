from __future__ import annotations

import hashlib
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
    GateEvidence,
    GateEvidenceStatus,
    RetentionClass,
    RetentionPolicy,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
    StagedDatasetManifest,
    StagedDatasetPart,
)


def _stores(tmp_path: Path, *, quota: int | None = None) -> tuple[LocalArtifactStore, SQLiteControlStore]:
    project = tmp_path / "project"
    artifacts = LocalArtifactStore(project / "workspace" / "artifacts", project_root=project, disk_budget_bytes=quota)
    control = SQLiteControlStore(project / "workspace" / "control.sqlite", project_root=project)
    return artifacts, control


def _manifest(run_id: str, artifact_id: str, *, content_hash: str | None = None, created_at: datetime | None = None, kind: str = "metadata") -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=artifact_id,
        run_id=run_id,
        stage_id="STEP23",
        attempt_id="attempt-1",
        artifact_kind=kind,
        media_type="application/json",
        producer="step23-test",
        content_hash=content_hash,
        created_at=created_at or datetime.now(timezone.utc),
        retention_class=RetentionClass.RUN_SCOPED,
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
    external = artifacts.register_external(_manifest("run-1", "target", kind="MaterializationArtifact").model_copy(update={"storage_mode": ArtifactStorageMode.EXTERNAL}), "workspace/target.duckdb")
    report_path = control.project_root / "workspace" / "validation_report.json"
    report_path.write_text("{\"g6_status\":\"PASS\",\"g6_eligible\":true}", encoding="utf-8")
    report = artifacts.register_external(_manifest("run-1", "validation-report", kind="ValidationReport").model_copy(update={"storage_mode": ArtifactStorageMode.EXTERNAL, "retention_class": RetentionClass.PINNED_GATE_EVIDENCE}), "workspace/validation_report.json")
    control.register_artifact(external)
    control.register_artifact(report)
    gate = control.record_gate_evidence(GateEvidence(gate_id="G6_DATA_CORRECTNESS", run_id=run.run_id, status=GateEvidenceStatus.PASS, eligible=True, validation_report_artifact_id=report.artifact_id, validation_report_content_hash=report.content_hash, policy_version="step22-data-correctness-v2", verified_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2"))
    reopened = SQLiteControlStore(control.path, project_root=control.project_root)
    recovered = reopened.get_gate_evidence(gate.gate_id, run_id=run.run_id)
    assert recovered.status is GateEvidenceStatus.PASS and recovered.eligible is True
    assert artifacts.verify(external).state.value == "VERIFIED"
    assert artifacts.read(report) == b'{"g6_status":"PASS","g6_eligible":true}'


def test_staging_manifest_and_cache_are_durable(tmp_path: Path) -> None:
    artifacts, control = _stores(tmp_path)
    control.create_run(RunRecord(run_id="run-1", project_id="project", configuration_fingerprint="cfg"))
    staging = LocalStagingStore(artifacts, control_store=control)
    base = StagedDatasetManifest(dataset_id="dataset-1", dataset_version="v1", run_id="run-1", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", schema_fingerprint="schema-1", format="parquet", parts=(StagedDatasetPart(part_id="placeholder", logical_key="runs/run-1/placeholder", artifact_ref=artifacts.publish(_manifest("run-1", "placeholder"), b"p"), schema_fingerprint="schema-1"),))
    part = staging.publish_part(base, "part-000", b"parquet-reference", attempt_id="attempt-1", producer="step23")
    control.register_artifact(part.artifact_ref)
    manifest = base.model_copy(update={"parts": (part,), "row_count": 1})
    staging.register_manifest(manifest)
    assert control.get_staged_dataset("dataset-1", "v1").parts[0].part_id == "part-000"
    key = CacheKey(stage_id="STEP23", component_id="platform", input_artifacts=(CacheInputRef(artifact_id=part.artifact_ref.artifact_id, content_hash=part.artifact_ref.content_hash),), policy_version="p1", configuration_fingerprint="cfg", engine_version="local", code_version="step23")
    entry = CacheEntry(cache_key_hash=key.key_hash, cache_key=key, output_artifact_id=part.artifact_ref.artifact_id, output_content_hash=part.artifact_ref.content_hash)
    control.record_cache_entry(entry)
    assert PlatformCacheService().resolve(key, control_store=control, artifact_store=artifacts).artifact_id == part.artifact_ref.artifact_id


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
    dry = service.execute_cleanup(plan, CleanupAuthorization(plan_id=plan.plan_id, actor="test", authorized=True), control_store=control, artifact_store=artifacts)
    assert dry.executed is False and artifacts.exists(candidate)
    executed = service.execute_cleanup(plan.model_copy(update={"dry_run": False}), CleanupAuthorization(plan_id=plan.plan_id, actor="test", authorized=True), control_store=control, artifact_store=artifacts)
    assert executed.deleted_artifact_ids == ("old",)
    assert control.get_artifact("old").publication_state is ArtifactPublicationState.TOMBSTONED
    with pytest.raises(CleanupAuthorizationError):
        artifacts.delete(pinned, CleanupAuthorization(plan_id=plan.plan_id, actor="test", authorized=True))


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
            reason="forced negative",
            retention_class=RetentionClass.RUN_SCOPED,
            age_seconds=1,
            byte_size=candidate.byte_size,
            planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED,
        ),),
    })
    result = PlatformLifecycleService().execute_cleanup(forced, CleanupAuthorization(plan_id=forced.plan_id, actor="test", authorized=True), control_store=control, artifact_store=artifacts)
    assert result.rejected_artifact_ids == (candidate.artifact_id,)
    assert artifacts.exists(candidate)
