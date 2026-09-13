from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendService, Principal
from dirty_data_to_olap.application.jobs import (
    BoundedWorkerPool,
    DurableExecutionSubmission,
    InjectedWorkerCrash,
    JobWorker,
    StageHandlerRegistry,
)
from dirty_data_to_olap.application.platform import GateEvidenceService
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.canonical import (
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecisionStatus,
    review_subject_key,
)
from dirty_data_to_olap.domain.contracts.jobs import (
    DeliveryPhase,
    ExecutionPlan,
    ExecutionPlanSelection,
    FailureClassification,
    JobStatus,
    ReplaySafety,
    StageExecutionResult,
    StageResultStatus,
    StageSelectionDecision,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactManifest,
    ArtifactStorageMode,
    RetentionClass,
    RunRecord,
    RunStatus,
    StageStatus,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.domain.contracts.source import stable_id


AUTH = {"X-Local-Principal": "step28-reviewer"}


class Clock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _bundle(tmp_path: Path, *, g6: bool = True):
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id="run-step28-repair", project_id="step28", configuration_fingerprint="cfg"))
    if g6:
        source = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "step22-reference-run" / "validation" / "validation_report.json"
        payload = source.read_bytes()
        payload_json = json.loads(payload.decode("utf-8"))
        report = ValidationReport.model_validate({key: value for key, value in payload_json.items() if key != "content_hash"})
        report_ref = artifacts.publish(
            ArtifactManifest(
                artifact_id=stable_id("repair-g6-report", {"run_id": run.run_id}),
                run_id=run.run_id,
                stage_id="VALIDATION_RECONCILIATION",
                attempt_id="repair-g6-attempt",
                artifact_kind="ValidationReport",
                media_type="application/json",
                producer="step28-integrity-repair-test",
                retention_class=RetentionClass.PINNED_GATE_EVIDENCE,
                storage_mode=ArtifactStorageMode.MANAGED,
            ),
            payload,
        )
        control.register_artifact(report_ref)
        GateEvidenceService().record_from_validation_report(
            gate_id="G6_DATA_CORRECTNESS",
            platform_run_id=run.run_id,
            report=report,
            report_artifact=report_ref,
            verified_content_commit="b" * 40,
            control_store=control,
            artifact_store=artifacts,
        )
    return control, artifacts, run


def _command(run_id: str, key: str, action: ExecutionAction = ExecutionAction.SUBMIT) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=stable_id("repair-command", {"run": run_id, "key": key, "action": action.value}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"repair:{run_id}:{action.value}",
        idempotency_key=key,
        request_fingerprint=("c" if action is ExecutionAction.SUBMIT else "d") * 64,
        principal_subject="repair-test",
        principal_source="STEP28_REPAIR_TEST",
    )


def _register_plan(control: SQLiteControlStore, run_id: str, stages: tuple[StageSpec, ...]) -> ExecutionPlan:
    conditional = tuple(stage for stage in stages if stage.conditional)
    selection = None if not conditional else ExecutionPlanSelection(
        run_id=run_id,
        policy_ref="step28-test-selection",
        scope="step28-test",
        scope_fingerprint="step28-test-scope",
        decisions=tuple(
            StageSelectionDecision(
                stage_id=stage.stage_id,
                selected=stage.selected,
                policy_ref="step28-test-selection",
                reason=stage.selection_reason,
                scope="step28-test",
                scope_fingerprint="step28-test-scope",
            )
            for stage in conditional
        ),
    )
    plan = ExecutionPlan(plan_id=stable_id("repair-plan", {"run": run_id, "stages": [stage.stage_id for stage in stages]}), run_id=run_id, stages=stages, selection=selection)
    control.register_execution_plan(plan)
    return plan


def test_review_worker_backend_api_resume_uses_one_authoritative_subject_key(tmp_path: Path) -> None:
    control, artifacts, run = _bundle(tmp_path)
    _register_plan(control, run.run_id, (StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))

    class ReviewThenSuccess:
        def __init__(self) -> None:
            self.calls = 0
            self.context: ReviewCompatibilityContext | None = None

        def execute(self, request):
            self.calls += 1
            ref = artifacts.publish(
                ArtifactManifest(
                    artifact_id=stable_id("repair-subject", {"attempt": request.attempt_id}),
                    run_id=request.run_id,
                    stage_id=request.stage_id,
                    attempt_id=request.attempt_id,
                    artifact_kind="ReviewSubject",
                    media_type="application/json",
                    producer="step28-review-test",
                ),
                b"review-subject",
            )
            control.register_artifact(ref)
            if self.calls == 1:
                self.context = ReviewCompatibilityContext(
                    review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
                    subject_stage=request.stage_id,
                    subject_artifact_id=ref.artifact_id,
                    subject_content_hash=ref.content_hash,
                    subject_schema_version="1.0",
                    model_version="repair-model-v1",
                    source_schema_fingerprints={"source": "repair-schema-v1"},
                    policy_version="repair-policy-v1",
                    subject_semantic_id="repair-semantic-v1",
                    applicability_fingerprint="repair-applicability-v1",
                )
                return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, output_artifact_refs=(ref.artifact_id,), review_context=self.context)
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    handler = ReviewThenSuccess()
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    clock = Clock()
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="repair-worker", clock=clock)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
    context = handler.context
    assert context is not None

    backend = BackendService(control_store=control, artifact_store=artifacts)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    response = client.post(
        f"/api/v1/runs/{run.run_id}/reviews/{context.review_checkpoint_id.value}",
        headers={**AUTH, "Idempotency-Key": "repair-review-1"},
        json={"subject_artifact_id": context.subject_artifact_id, "subject_content_hash": context.subject_content_hash, "decision": "ACCEPTED", "rationale": "authoritative repair review", "expected_revision": 0},
    )
    assert response.status_code == 200, response.text
    assert control.get_current_review(run_id=run.run_id, subject_key=review_subject_key(context)) is not None

    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "resume", ExecutionAction.RESUME), run=run)
    clock.advance(60)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert handler.calls == 2
    assert len(control.list_stage_attempts(run_id=run.run_id)) == 2
    assert control.get_run(run.run_id).status is RunStatus.SUCCEEDED
    control.close()


@pytest.mark.parametrize("field", ["subject_semantic_id", "applicability_fingerprint", "policy_version", "subject_content_hash"])
def test_changed_authoritative_review_context_never_resumes_old_decision(tmp_path: Path, field: str) -> None:
    control, artifacts, run = _bundle(tmp_path)
    _register_plan(control, run.run_id, (StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))

    class NeedsReview:
        def __init__(self) -> None:
            self.context = None

        def execute(self, request):
            ref = artifacts.publish(ArtifactManifest(artifact_id=stable_id("repair-change-subject", {"attempt": request.attempt_id}), run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_kind="ReviewSubject", media_type="application/json", producer="step28-change-test"), b"subject")
            control.register_artifact(ref)
            self.context = ReviewCompatibilityContext(review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, subject_stage=request.stage_id, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, subject_schema_version="1.0", model_version="model", source_schema_fingerprints={"source": "schema"}, policy_version="policy-v1", subject_semantic_id="semantic-v1", applicability_fingerprint="app-v1")
            return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, output_artifact_refs=(ref.artifact_id,), review_context=self.context)

    handler = NeedsReview()
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    clock = Clock()
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="change-worker", clock=clock)
    worker.run_once()
    worker.run_once()
    context = handler.context
    assert context is not None
    BackendService(control_store=control, artifact_store=artifacts).review(run_id=run.run_id, checkpoint=context.review_checkpoint_id, subject_artifact_id=context.subject_artifact_id, subject_content_hash=context.subject_content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale="accepted before mutation", expected_revision=0, principal=Principal(subject="reviewer", scopes=frozenset({"reviews:write"})), idempotency_key="change-review")
    changed = context.model_copy(update={field: f"changed-{field}"})
    control.get_review_subject_context = lambda **_kwargs: changed
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "resume", ExecutionAction.RESUME), run=run)
    clock.advance(60)
    outcome = worker.run_once()
    assert outcome.status == JobStatus.BLOCKED.value
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.NEEDS_REVIEW
    assert len(control.list_stage_attempts(run_id=run.run_id)) == 1
    control.close()


@pytest.mark.parametrize(
    "point,replay_safety,expected_status,expected_calls",
    [
        ("before_handler_delivery_marker", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, JobStatus.SUCCEEDED, 1),
        ("after_handler_delivery_marker", ReplaySafety.REPLAY_SAFE, JobStatus.SUCCEEDED, 1),
        ("after_handler_delivery_marker", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, JobStatus.FAILED, 0),
        ("after_handler_returns_before_result_record", ReplaySafety.REPLAY_SAFE, JobStatus.SUCCEEDED, 2),
        ("after_handler_returns_before_result_record", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, JobStatus.FAILED, 1),
        ("after_result_record_before_finalization", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, JobStatus.SUCCEEDED, 1),
    ],
)
def test_delivery_fault_points_are_durable_and_replay_safe(
    tmp_path: Path,
    point: str,
    replay_safety: ReplaySafety,
    expected_status: JobStatus,
    expected_calls: int,
) -> None:
    control, artifacts, run = _bundle(tmp_path)
    _register_plan(control, run.run_id, (StageSpec(stage_id="WORK", handler_key="work", final_validation=True, replay_safety=replay_safety),))
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    clock = Clock()

    class Counter:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _request):
            self.calls += 1
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    handler = Counter()
    crashed = False

    def inject(candidate_point, _job, _attempt):
        nonlocal crashed
        if candidate_point == point and not crashed:
            crashed = True
            raise InjectedWorkerCrash(point)

    first = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="fault-a", lease_seconds=2, clock=clock)
    first.run_once()
    crashing = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="fault-a", lease_seconds=2, clock=clock, fault_injector=inject)
    with pytest.raises(InjectedWorkerCrash):
        crashing.run_once()
    job_before = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
    assert job_before is not None
    assert job_before.delivery_phase in {DeliveryPhase.ATTEMPT_CREATED, DeliveryPhase.HANDLER_DELIVERY_STARTED, DeliveryPhase.RESULT_RECORDED}
    clock.advance(3)
    recovered = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="fault-b", lease_seconds=2, clock=clock)
    assert recovered.run_once().status == expected_status.value
    assert handler.calls == expected_calls
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is expected_status
    control.close()


def test_cooperative_cancellation_probe_stops_multi_checkpoint_handler(tmp_path: Path) -> None:
    control, artifacts, run = _bundle(tmp_path, g6=False)
    _register_plan(control, run.run_id, (StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    observed: list[bool] = []

    class Cooperative:
        def execute_with_context(self, _request, probe):
            observed.append(probe.is_cancelled())
            control.request_run_cancellation(run_id=run.run_id, now=datetime.now(timezone.utc))
            observed.append(probe.is_cancelled())
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": Cooperative()}), worker_id="cancel-worker")
    worker.run_once()
    outcome = worker.run_once()
    assert outcome.status == JobStatus.CANCELLED.value
    assert observed == [False, True]
    assert control.get_run(run.run_id).status is RunStatus.CANCELLED
    assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.CANCELLED
    assert control.list_stage_attempts(run_id=run.run_id)[0].status is StageStatus.CANCELLED
    assert control.list_artifacts(run_id=run.run_id, stage_id="WORK") == ()
    control.close()


def test_bounded_pool_enforces_per_source_admission(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    runs = [control.create_run(RunRecord(run_id=f"run-source-{index}", project_id="step28", configuration_fingerprint="cfg")) for index in range(2)]
    for run in runs:
        plan = ExecutionPlan(plan_id=f"plan-{run.run_id}", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", source_scope="authoritative-source", final_validation=False),))
        control.register_execution_plan(plan)
        DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    lock = threading.Lock()
    active = 0
    max_active = 0

    class Slow:
        def execute(self, _request):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    workers = tuple(JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": Slow()}), worker_id=f"pool-{index}") for index in range(2))
    pool = BoundedWorkerPool(workers, max_workers=2, max_jobs_per_pump=12, max_active_per_run=1, max_active_per_source=1)
    outcomes = pool.pump()
    assert outcomes and max_active == 1
    assert all(control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.SUCCEEDED for run in runs)
    control.close()


def test_optional_and_conditional_plan_selection_is_isolated(tmp_path: Path) -> None:
    control, artifacts, run = _bundle(tmp_path)
    plan = _register_plan(
        control,
        run.run_id,
        (
            StageSpec(stage_id="REQUIRED", handler_key="required", final_validation=True),
            StageSpec(stage_id="OPTIONAL_UNSELECTED", handler_key="optional", required=False, conditional=True, selected=False, selection_reason="capability not selected"),
            StageSpec(stage_id="OPTIONAL_DEPENDENCY_ABSENT", handler_key="optional", required=False, optional_dependencies=("MISSING",)),
            StageSpec(stage_id="REQUIRED_FAILURE", handler_key="fail", required=False, conditional=True, selected=False, selection_reason="failure branch not selected"),
        ),
    )
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)

    class Success:
        def execute(self, _request):
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    class Failure:
        def execute(self, _request):
            return StageExecutionResult(status=StageResultStatus.FAILED, failure_code="OPTIONAL_FAILURE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="selected optional stage failed")

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"required": Success(), "optional": Failure(), "fail": Failure()}), worker_id="optional-worker")
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert control.get_stage_job(run_id=run.run_id, stage_id="OPTIONAL_UNSELECTED") is None
    for _ in range(4):
        worker.run_once()
    assert control.get_stage_job(run_id=run.run_id, stage_id="OPTIONAL_DEPENDENCY_ABSENT").status is JobStatus.FAILED
    assert control.get_run(run.run_id).status is RunStatus.SUCCEEDED
    assert plan.stage("OPTIONAL_UNSELECTED").selection_reason == "capability not selected"
    control.close()


def test_final_validation_job_without_typed_g6_cannot_succeed(tmp_path: Path) -> None:
    control, artifacts, run = _bundle(tmp_path, g6=False)
    _register_plan(control, run.run_id, (StageSpec(stage_id="FINAL", handler_key="final", final_validation=True),))
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)

    class Final:
        def execute(self, _request):
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"final": Final()}), worker_id="g6-worker")
    worker.run_once()
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert control.get_run(run.run_id).status is RunStatus.BLOCKED
    assert control.get_stage_job(run_id=run.run_id, stage_id="FINAL").status is JobStatus.SUCCEEDED
    control.close()


def test_v5_to_v6_migration_retains_plan_job_and_attempt_rows(tmp_path: Path) -> None:
    control, artifacts, run = _bundle(tmp_path)
    plan = _register_plan(control, run.run_id, (StageSpec(stage_id="WORK", handler_key="work"),))
    job = control.enqueue_stage_job(run_id=run.run_id, plan_id=plan.plan_id, stage_id="WORK")
    claimed = control.claim_next_job(worker_id="migration-worker", now=datetime.now(timezone.utc), lease_seconds=30)
    assert claimed is not None
    attempt = control.ensure_stage_attempt(job_id=job.job_id, worker_id="migration-worker", lease_generation=claimed.lease_generation, now=datetime.now(timezone.utc))
    path = control.path
    control.close()
    import sqlite3
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_meta SET schema_version = 5")
    reopened = SQLiteControlStore(path, project_root=tmp_path)
    assert reopened.schema_version == 6
    assert reopened.get_execution_plan(run.run_id) == plan
    assert reopened.get_job(job.job_id).attempt_id == attempt.attempt_id
    assert reopened.get_stage_attempt(attempt.attempt_id).attempt_id == attempt.attempt_id
    assert reopened.get_job(job.job_id).delivery_phase is DeliveryPhase.ATTEMPT_CREATED
    reopened.close()
    artifacts.root.exists()
