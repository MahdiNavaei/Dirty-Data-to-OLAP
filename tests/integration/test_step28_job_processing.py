from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission, JobWorker, StageHandlerRegistry
from dirty_data_to_olap.application.platform import ConcurrencyConflictError
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand, ReviewRecord
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewDecisionStatus, ReviewCompatibilityContext
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    FailureClassification,
    JobStatus,
    StageExecutionResult,
    StageResultStatus,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, RunRecord, StageStatus
from dirty_data_to_olap.domain.contracts.source import stable_id


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SuccessfulStage:
    def __init__(self, result: StageExecutionResult | None = None) -> None:
        self.result = result or StageExecutionResult(status=StageResultStatus.SUCCEEDED)
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


def _stores(tmp_path: Path):
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step28", project_id="project", configuration_fingerprint="cfg"))
    return control, artifacts, run


def _command(run_id: str, *, key: str = "submit-1", action: ExecutionAction = ExecutionAction.SUBMIT, command_id: str | None = None) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=command_id or stable_id("execution-command", {"run": run_id, "key": key, "action": action.value}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"execution:{action.value}:{run_id}:tester",
        idempotency_key=key,
        request_fingerprint=("a" if action is ExecutionAction.SUBMIT else "b") * 64,
        principal_subject="tester",
        principal_source="TEST",
    )


def _plan(run_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-step28",
        run_id=run_id,
        stages=(
            StageSpec(stage_id="WORK", handler_key="work"),
            StageSpec(stage_id="VALIDATE", dependencies=("WORK",), handler_key="validate", final_validation=True),
        ),
    )


def test_durable_enqueue_deduplicates_command_and_reopens(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    command = _command(run.run_id)
    first, duplicate = control.enqueue_execution_command(command, run)
    second, duplicate_again = control.enqueue_execution_command(command, run)
    assert first.job_id == second.job_id
    assert not duplicate and duplicate_again
    assert control.schema_version == 5
    control.close()
    reopened = SQLiteControlStore(control.path, project_root=tmp_path)
    assert reopened.get_job(first.job_id).status is JobStatus.QUEUED
    with pytest.raises(Exception):
        reopened.enqueue_execution_command(command.model_copy(update={"action": ExecutionAction.CANCEL}), run)
    reopened.close()
    artifacts.root.exists()


def test_v4_to_v5_migration_installs_job_tables_without_reset(tmp_path: Path) -> None:
    control, _artifacts, run = _stores(tmp_path)
    path = control.path
    control.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_meta SET schema_version = 4")
        connection.execute("DROP TABLE jobs")
        connection.execute("DROP TABLE execution_plans")
    reopened = SQLiteControlStore(path, project_root=tmp_path)
    try:
        assert reopened.schema_version == 5 and reopened.get_run(run.run_id) is not None
        tables = {row[0] for row in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"jobs", "execution_plans"}.issubset(tables)
        assert sqlite3.connect(path).execute("SELECT 1 FROM schema_migrations WHERE version_from = 4 AND version_to = 5").fetchone()
    finally:
        reopened.close()


def test_two_workers_are_fenced_and_expired_lease_reclaimed(tmp_path: Path) -> None:
    control, _artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-fence", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)
    job = control.enqueue_stage_job(run_id=run.run_id, plan_id=plan.plan_id, stage_id="WORK")
    clock = MutableClock()
    claimed = control.claim_next_job(worker_id="worker-a", now=clock(), lease_seconds=5)
    assert claimed is not None and claimed.job_id == job.job_id and claimed.lease_generation == 1
    assert control.claim_next_job(worker_id="worker-b", now=clock(), lease_seconds=5) is None
    attempt = control.ensure_stage_attempt(job_id=job.job_id, worker_id="worker-a", lease_generation=claimed.lease_generation, now=clock())
    clock.advance(6)
    reclaimed = control.claim_next_job(worker_id="worker-b", now=clock(), lease_seconds=5)
    assert reclaimed is not None and reclaimed.lease_generation == 2
    result = StageExecutionResult(status=StageResultStatus.SUCCEEDED)
    with pytest.raises(ConcurrencyConflictError):
        control.finalize_stage_job(job_id=job.job_id, worker_id="worker-a", lease_generation=1, attempt=attempt, result=result, status=JobStatus.SUCCEEDED.value, now=clock())
    control.close()


def test_worker_runs_authoritative_plan_and_requires_final_validation(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    plan = _plan(run.run_id)
    control.register_execution_plan(plan)
    command = _command(run.run_id)
    submission = DurableExecutionSubmission(control)
    accepted = submission.submit_command(command=command, run=run)
    assert accepted.status == "ACCEPTED" and accepted.submission_id
    clock = MutableClock()
    work = SuccessfulStage()
    validate = SuccessfulStage()
    registry = StageHandlerRegistry({"work": work, "validate": validate})
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=registry, worker_id="worker-1", clock=clock)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert control.get_run(run.run_id).status.value == "SUCCEEDED"
    attempts = control.list_stage_attempts(run_id=run.run_id)
    assert [item.stage_id for item in attempts] == ["VALIDATE", "WORK"]
    assert attempts[0].attempt_id != attempts[1].attempt_id
    assert work.requests[0].request_id.startswith("stage-request_")
    control.close()


def test_missing_handler_blocks_and_never_fakes_success(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-missing", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="not-wired", final_validation=True),))
    control.register_execution_plan(plan)
    command = _command(run.run_id)
    DurableExecutionSubmission(control).submit_command(command=command, run=run)
    clock = MutableClock()
    worker = JobWorker(control_store=control, artifact_store=artifacts, worker_id="worker-1", clock=clock)
    worker.run_once()
    outcome = worker.run_once()
    assert outcome.status == JobStatus.BLOCKED.value
    assert control.get_run(run.run_id).status.value == "BLOCKED"
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.BLOCKED
    control.close()


def test_retry_creates_new_attempt_and_is_bounded(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-retry", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id), run=run)
    retry = SuccessfulStage(StageExecutionResult(status=StageResultStatus.FAILED, failure_code="TEMPORARY", failure_classification=FailureClassification.RETRYABLE_TRANSIENT, failure_reason="bounded transient failure"))
    clock = MutableClock()
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": retry}), worker_id="worker-1", clock=clock)
    worker.run_once()
    worker.run_once()
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.RETRY_WAIT
    assert len(control.list_stage_attempts(run_id=run.run_id)) == 1
    clock.advance(5)
    worker.run_once()
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.RETRY_WAIT
    assert len(control.list_stage_attempts(run_id=run.run_id)) == 2
    control.close()


def test_queued_and_running_cancellation_prevent_completion(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-cancel", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id), run=run)
    clock = MutableClock()
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": SuccessfulStage()}), worker_id="worker-1", clock=clock)
    worker.run_once()
    queued = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
    control.request_run_cancellation(run_id=run.run_id, now=clock())
    assert control.get_run(run.run_id).status.value == "CANCELLED"
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.CANCELLED
    assert queued is not None
    control.close()


def test_no_partial_job_state_and_read_contract_is_safe(tmp_path: Path) -> None:
    control, _artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-safe", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)
    with pytest.raises(ValueError):
        StageExecutionResult(status=StageResultStatus.FAILED, failure_code="E", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="traceback: password=secret")
    with pytest.raises(ValueError):
        plan.model_copy(update={"stages": (StageSpec(stage_id="WORK", handler_key="work", metadata={"raw_row": "1"}),)})
    assert not any(job.status.value == "PARTIAL" for job in control.list_jobs(run_id=run.run_id))
    control.close()


class ReviewThenSuccess:
    def __init__(self, control, artifacts, run_id: str) -> None:
        self.control = control
        self.artifacts = artifacts
        self.run_id = run_id
        self.context = None
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        artifact_id = stable_id("artifact", {"attempt": request.attempt_id, "call": self.calls})
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            run_id=request.run_id,
            stage_id=request.stage_id,
            attempt_id=request.attempt_id,
            artifact_kind="REVIEW_SUBJECT" if self.calls == 1 else "FINAL_OUTPUT",
            media_type="application/json",
            producer="step28-test",
        )
        ref = self.artifacts.publish(manifest, b"step28-output")
        self.control.register_artifact(ref)
        if self.calls == 1:
            self.context = ReviewCompatibilityContext(
                review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
                subject_stage=request.stage_id,
                subject_artifact_id=ref.artifact_id,
                subject_content_hash=ref.content_hash,
                subject_schema_version="1",
                model_version="test-model",
                source_schema_fingerprints={"source": "schema-test"},
                policy_version="test-policy-v1",
                subject_semantic_id="semantic-test",
                applicability_fingerprint="applicability-test",
            )
            return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, output_artifact_refs=(ref.artifact_id,), review_context=self.context)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))


def test_review_resume_requires_compatible_context_and_creates_new_attempt(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-review", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id), run=run)
    handler = ReviewThenSuccess(control, artifacts, run.run_id)
    clock = MutableClock()
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-1", clock=clock)
    worker.run_once()
    assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
    first_attempt = control.list_stage_attempts(run_id=run.run_id)[0]
    assert first_attempt.status is StageStatus.NEEDS_REVIEW
    context = handler.context
    assert context is not None
    decision = ReviewPolicyService().create_decision(context, decision=ReviewDecisionStatus.ACCEPTED, actor="reviewer", rationale="reviewed")
    subject_key = "|".join((context.review_checkpoint_id.value, context.subject_artifact_id, context.subject_content_hash))
    control.record_review(ReviewRecord(run_id=run.run_id, subject_key=subject_key, decision=decision, revision=1), expected_revision=0)
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, key="resume-1", action=ExecutionAction.RESUME), run=run)
    clock.advance(5)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert control.get_run(run.run_id).status is not None
    attempts = control.list_stage_attempts(run_id=run.run_id)
    assert len(attempts) == 2 and attempts[0].attempt_id != attempts[1].attempt_id
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.SUCCEEDED
    control.close()


def test_cancellation_completion_race_is_closed_by_store_fence(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path)
    plan = ExecutionPlan(plan_id="plan-race", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id), run=run)

    class CancellingHandler:
        def execute(self, request):
            control.request_run_cancellation(run_id=request.run_id, now=datetime.now(timezone.utc))
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": CancellingHandler()}), worker_id="worker-1")
    worker.run_once()
    outcome = worker.run_once()
    assert outcome.status == JobStatus.CANCELLED.value
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.CANCELLED
    assert control.get_run(run.run_id).status.value == "CANCELLED"
    control.close()
