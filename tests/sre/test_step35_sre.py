from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendService, Principal
from dirty_data_to_olap.application.jobs import BoundedWorkerPool
from dirty_data_to_olap.application.product_runtime import LocalProductExecutionSubmission
from dirty_data_to_olap.application.reliability import (
    RecoveryDisposition,
    ReliabilityError,
    backup_control_store,
    build_recovery_projection,
    classify_recovery,
    inspect_control_store_backup,
    restore_control_store,
)
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, JobStatus, StageSpec
from dirty_data_to_olap.domain.contracts.platform import RunRecord
from dirty_data_to_olap.domain.contracts.source import stable_id


def _command(run_id: str) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=stable_id("step35-command", {"run": run_id}),
        run_id=run_id,
        action=ExecutionAction.SUBMIT,
        idempotency_scope=f"step35:{run_id}",
        idempotency_key="backup-recovery",
        request_fingerprint="a" * 64,
        principal_subject="step35-test",
        principal_source="TEST",
    )


def test_sqlite_backup_manifest_and_restore_preserve_durable_state(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step35", project_id="project", configuration_fingerprint="cfg"))
    job, duplicate = control.enqueue_execution_command(_command(run.run_id), run)
    assert duplicate is False

    manifest = backup_control_store(control, tmp_path / "backup.sqlite")
    assert manifest.schema_version == control.schema_version
    assert inspect_control_store_backup(tmp_path / "backup.sqlite").database_sha256 == manifest.database_sha256

    control.create_run(RunRecord(run_id="run-after-backup", project_id="project", configuration_fingerprint="cfg"))
    restored = restore_control_store(tmp_path / "backup.sqlite", tmp_path / "restored.sqlite", project_root=tmp_path)
    try:
        assert restored.get_run(run.run_id) is not None
        assert restored.get_job(job.job_id).status is JobStatus.QUEUED
        assert restored.get_run("run-after-backup") is None
        assert restored.schema_version == control.schema_version
    finally:
        restored.close()
        control.close()


def test_backup_rejects_modified_or_incomplete_evidence(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    control.create_run(RunRecord(run_id="run-step35", project_id="project", configuration_fingerprint="cfg"))
    backup_control_store(control, tmp_path / "backup.sqlite")
    backup = tmp_path / "backup.sqlite"
    backup.write_bytes(backup.read_bytes()[:-1])
    with pytest.raises(ReliabilityError):
        inspect_control_store_backup(backup)
    control.close()


def test_recovery_projection_uses_typed_jobs_and_artifact_integrity(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step35", project_id="project", configuration_fingerprint="cfg"))
    job, _ = control.enqueue_execution_command(_command(run.run_id), run)
    projection = build_recovery_projection(control_store=control, artifact_store=artifacts, run_id=run.run_id)
    assert projection["schema_version"] == control.schema_version
    assert projection["summary"]["job_count"] == 1
    assert projection["jobs"][0]["job"]["job_id"] == job.job_id
    assert projection["jobs"][0]["recovery_disposition"] == RecoveryDisposition.SAFE_TO_RESUME.value
    assert "raw_sql" not in str(projection).lower()
    assert classify_recovery(job, artifact_integrity_states=("MISSING",)) is RecoveryDisposition.REQUIRES_RECONCILIATION
    control.close()


def test_expired_worker_delivery_is_reconciled_or_resumed_by_durable_phase(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step35", project_id="project", configuration_fingerprint="cfg"))
    plan = control.register_execution_plan(ExecutionPlan(
        plan_id="plan-step35", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),)
    ))
    job = control.enqueue_stage_job(run_id=run.run_id, plan_id=plan.plan_id, stage_id="WORK")
    now = datetime.now(timezone.utc)
    claimed = control.claim_next_job(worker_id="worker", now=now, lease_seconds=1)
    assert claimed is not None
    uncertain = claimed.model_copy(update={"delivery_phase": "HANDLER_DELIVERY_STARTED", "lease_expires_at": now})
    assert classify_recovery(uncertain, now=now) is RecoveryDisposition.REQUIRES_RECONCILIATION
    control.close()


class _IdleWorker:
    def run_once(self):
        return type("Outcome", (), {"status": "IDLE"})()


class _FailingSubmission:
    def submit_command(self, *, command, run):
        raise OSError("control-store-backed delivery is unavailable")


def test_shutdown_stops_new_claims_and_submission_after_close_is_unavailable(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step35", project_id="project", configuration_fingerprint="cfg"))
    pool = BoundedWorkerPool((_IdleWorker(),), max_jobs_per_pump=10)
    submission = LocalProductExecutionSubmission(control, pool)
    submission.close()
    result = submission.submit_command(command=_command(run.run_id), run=run)
    assert result.status == "UNAVAILABLE"
    assert pool.shutdown_requested is True
    assert pool.pump() == ()
    assert control.list_jobs(run_id=run.run_id) == ()
    control.close()


def test_backend_fails_closed_when_delivery_outcome_is_unknown(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step35", project_id="project", configuration_fingerprint="cfg"))
    control.register_execution_plan(ExecutionPlan(
        plan_id="plan-step35", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),)
    ))
    backend = BackendService(control_store=control, artifact_store=artifacts, execution=_FailingSubmission())
    result, replay = backend.submit(
        run_id=run.run_id,
        principal=Principal(subject="operator", scopes=frozenset({"runs:write"}), project_ids=frozenset({"project"})),
        idempotency_key="delivery-unknown",
    )
    assert replay is False
    assert result.status == "DELIVERY_UNKNOWN"
    stored = control.get_idempotency(scope=f"execution:submit:{run.run_id}:operator", key="delivery-unknown")
    assert stored is not None and stored.state == "UNKNOWN"
    control.close()
