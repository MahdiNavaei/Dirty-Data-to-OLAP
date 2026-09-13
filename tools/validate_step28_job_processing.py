"""Behavioral Step28 validator for the disposable SQLite job runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendService
from dirty_data_to_olap.application.jobs import BoundedWorkerPool, DurableExecutionSubmission, InjectedWorkerCrash, JobWorker, StageHandlerRegistry, load_authoritative_execution_plan
from dirty_data_to_olap.application.platform import ConcurrencyConflictError
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext, ReviewDecisionStatus, review_subject_key
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    FailureClassification,
    JobStatus,
    RetryPolicy,
    ReplaySafety,
    StageExecutionResult,
    StageResultStatus,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, ArtifactStorageMode, RetentionClass, RunRecord, RunStatus
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.application.platform import GateEvidenceService
from dirty_data_to_olap.application.backend import Principal

from dirty_data_to_olap.domain.contracts.source import stable_id


def command(run_id: str, key: str, action: ExecutionAction = ExecutionAction.SUBMIT) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=stable_id("execution-command", {"run_id": run_id, "key": key, "action": action.value}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"step28:{run_id}:{action.value}",
        idempotency_key=key,
        request_fingerprint=("a" if action is ExecutionAction.SUBMIT else "b") * 64,
        principal_subject="validator",
        principal_source="STEP28_VALIDATOR",
    )


def run_case(root: Path, name: str) -> tuple[SQLiteControlStore, LocalArtifactStore, RunRecord]:
    control = SQLiteControlStore(root / f"{name}.sqlite", project_root=root)
    artifacts = LocalArtifactStore(root / f"{name}-artifacts", project_root=root)
    run = control.create_run(RunRecord(run_id=f"run-{name}", project_id="step28", configuration_fingerprint="cfg"))
    return control, artifacts, run


def seed_g6(root: Path, control: SQLiteControlStore, artifacts: LocalArtifactStore, run: RunRecord) -> None:
    source = ROOT / "workspace" / "runs" / "step22-reference-run" / "validation" / "validation_report.json"
    payload = source.read_bytes()
    payload_json = json.loads(payload.decode("utf-8"))
    report = ValidationReport.model_validate({key: value for key, value in payload_json.items() if key != "content_hash"})
    ref = artifacts.publish(
        ArtifactManifest(
            artifact_id=stable_id("validator-g6-report", {"run_id": run.run_id}),
            run_id=run.run_id,
            stage_id="VALIDATION_RECONCILIATION",
            attempt_id="validator-g6-attempt",
            artifact_kind="ValidationReport",
            media_type="application/json",
            producer="step28-validator",
            storage_mode=ArtifactStorageMode.MANAGED,
            retention_class=RetentionClass.PINNED_GATE_EVIDENCE,
        ),
        payload,
    )
    control.register_artifact(ref)
    GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=run.run_id, report=report, report_artifact=ref, verified_content_commit="c" * 40, control_store=control, artifact_store=artifacts)


def extended_scenarios(root: Path) -> int:
    scenarios = 0
    loaded = load_authoritative_execution_plan(ROOT, run_id="loader-run")
    checkpoints = {stage.review_checkpoint for stage in loaded.stages if stage.review_checkpoint is not None}
    assert checkpoints == {
        ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
        ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY,
        ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN,
        ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN,
    }
    assert any(not stage.selected and stage.conditional for stage in loaded.stages)
    scenarios += 1

    control, artifacts, run = run_case(root, "review-interop")
    seed_g6(root, control, artifacts, run)
    plan = ExecutionPlan(plan_id="plan-review-interop", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    control.register_execution_plan(plan)

    class ReviewHandler:
        def __init__(self) -> None:
            self.calls = 0
            self.context = None

        def execute(self, request):
            self.calls += 1
            ref = artifacts.publish(ArtifactManifest(artifact_id=stable_id("validator-review-subject", {"attempt": request.attempt_id}), run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_kind="ReviewSubject", media_type="application/json", producer="step28-validator"), b"subject")
            control.register_artifact(ref)
            if self.calls == 1:
                self.context = ReviewCompatibilityContext(review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, subject_stage=request.stage_id, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, subject_schema_version="1.0", model_version="validator-model", source_schema_fingerprints={"source": "validator-schema"}, policy_version="validator-policy", subject_semantic_id="validator-semantic", applicability_fingerprint="validator-app")
                return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, output_artifact_refs=(ref.artifact_id,), review_context=self.context)
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    handler = ReviewHandler()
    DurableExecutionSubmission(control).submit_command(command=command(run.run_id, "submit"), run=run)
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="interop-worker")
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
    context = handler.context
    assert context is not None
    client = TestClient(create_app(BackendService(control_store=control, artifact_store=artifacts)), raise_server_exceptions=False)
    reviewed = client.post(f"/api/v1/runs/{run.run_id}/reviews/{context.review_checkpoint_id.value}", headers={"X-Local-Principal": "validator-reviewer", "Idempotency-Key": "validator-review"}, json={"subject_artifact_id": context.subject_artifact_id, "subject_content_hash": context.subject_content_hash, "decision": "ACCEPTED", "rationale": "validator review", "expected_revision": 0})
    assert reviewed.status_code == 200, reviewed.text
    assert control.get_current_review(run_id=run.run_id, subject_key=review_subject_key(context)) is not None
    DurableExecutionSubmission(control).submit_command(command=command(run.run_id, "resume", ExecutionAction.RESUME), run=run)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert len(control.list_stage_attempts(run_id=run.run_id)) == 2
    scenarios += 4
    control.close()

    def replay_case(name: str, safety: ReplaySafety, fault_point: str) -> tuple[str, int]:
        replay_control, replay_artifacts, replay_run = run_case(root, name)
        seed_g6(root, replay_control, replay_artifacts, replay_run)
        replay_plan = ExecutionPlan(plan_id=f"plan-{name}", run_id=replay_run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True, replay_safety=safety),))
        replay_control.register_execution_plan(replay_plan)
        DurableExecutionSubmission(replay_control).submit_command(command=command(replay_run.run_id, "submit"), run=replay_run)
        class Counter:
            calls = 0
            def execute(self, _request):
                self.calls += 1
                return StageExecutionResult(status=StageResultStatus.SUCCEEDED)
        handler = Counter()
        fired = False
        def inject(point, _job, _attempt):
            nonlocal fired
            if point == fault_point and not fired:
                fired = True
                raise InjectedWorkerCrash(fault_point)
        first = JobWorker(control_store=replay_control, artifact_store=replay_artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="replay-a", lease_seconds=5)
        first.run_once()
        crashing = JobWorker(control_store=replay_control, artifact_store=replay_artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="replay-a", lease_seconds=5, fault_injector=inject)
        try:
            crashing.run_once()
        except InjectedWorkerCrash:
            pass
        time.sleep(5.1)
        outcome = JobWorker(control_store=replay_control, artifact_store=replay_artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="replay-b", lease_seconds=5).run_once()
        status = replay_control.get_stage_job(run_id=replay_run.run_id, stage_id="WORK").status.value
        replay_control.close()
        return status, handler.calls

    assert replay_case("replay-safe", ReplaySafety.REPLAY_SAFE, "after_handler_returns_before_result_record") == (JobStatus.SUCCEEDED.value, 2)
    assert replay_case("replay-unknown", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, "after_handler_delivery_marker") == (JobStatus.FAILED.value, 0)
    assert replay_case("replay-before-marker", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, "before_handler_delivery_marker") == (JobStatus.SUCCEEDED.value, 1)
    assert replay_case("replay-result-record", ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN, "after_result_record_before_finalization") == (JobStatus.SUCCEEDED.value, 1)
    scenarios += 4

    cancel, cancel_artifacts, cancel_run = run_case(root, "cooperative-cancel")
    cancel_plan = ExecutionPlan(plan_id="plan-cooperative-cancel", run_id=cancel_run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
    cancel.register_execution_plan(cancel_plan)
    DurableExecutionSubmission(cancel).submit_command(command=command(cancel_run.run_id, "submit"), run=cancel_run)
    observed = []
    class Cooperative:
        def execute_with_context(self, _request, probe):
            observed.append(probe.is_cancelled())
            cancel.request_run_cancellation(run_id=cancel_run.run_id, now=datetime.now(timezone.utc))
            observed.append(probe.is_cancelled())
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)
    cancel_worker = JobWorker(control_store=cancel, artifact_store=cancel_artifacts, executor=StageHandlerRegistry({"work": Cooperative()}), worker_id="cancel-worker")
    cancel_worker.run_once()
    assert cancel_worker.run_once().status == JobStatus.CANCELLED.value and observed == [False, True] and cancel.get_stage_job(run_id=cancel_run.run_id, stage_id="WORK").status is JobStatus.CANCELLED
    scenarios += 1
    cancel.close()

    pool_control = SQLiteControlStore(root / "backpressure.sqlite", project_root=root)
    pool_artifacts = LocalArtifactStore(root / "backpressure-artifacts", project_root=root)
    pool_runs = [pool_control.create_run(RunRecord(run_id=f"pool-run-{index}", project_id="step28", configuration_fingerprint="cfg")) for index in range(2)]
    for pool_run in pool_runs:
        pool_plan = ExecutionPlan(plan_id=f"pool-plan-{pool_run.run_id}", run_id=pool_run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", source_scope="source-a"),))
        pool_control.register_execution_plan(pool_plan)
        DurableExecutionSubmission(pool_control).submit_command(command=command(pool_run.run_id, "submit"), run=pool_run)
    active = 0
    maximum = 0
    lock = threading.Lock()
    class Slow:
        def execute(self, _request):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)
    pool = BoundedWorkerPool(tuple(JobWorker(control_store=pool_control, artifact_store=pool_artifacts, executor=StageHandlerRegistry({"work": Slow()}), worker_id=f"pool-{index}") for index in range(2)), max_workers=2, max_jobs_per_pump=12, max_active_per_source=1)
    pool.pump()
    assert maximum == 1 and all(pool_control.get_stage_job(run_id=pool_run.run_id, stage_id="WORK").status is JobStatus.SUCCEEDED for pool_run in pool_runs)
    scenarios += 1
    pool_control.close()

    negative, negative_artifacts, negative_run = run_case(root, "g6-negative")
    negative_plan = ExecutionPlan(plan_id="plan-g6-negative", run_id=negative_run.run_id, stages=(StageSpec(stage_id="FINAL", handler_key="final", final_validation=True),))
    negative.register_execution_plan(negative_plan)
    DurableExecutionSubmission(negative).submit_command(command=command(negative_run.run_id, "submit"), run=negative_run)
    negative_worker = JobWorker(control_store=negative, artifact_store=negative_artifacts, executor=StageHandlerRegistry({"final": type("Final", (), {"execute": lambda _self, _request: StageExecutionResult(status=StageResultStatus.SUCCEEDED)})()}), worker_id="g6-negative-worker")
    negative_worker.run_once()
    negative_worker.run_once()
    assert negative.get_stage_job(run_id=negative_run.run_id, stage_id="FINAL").status is JobStatus.SUCCEEDED and negative.get_run(negative_run.run_id).status is RunStatus.BLOCKED
    scenarios += 1
    negative.close()
    return scenarios


def main() -> int:
    scenarios = 0
    with tempfile.TemporaryDirectory(prefix="step28-validator-") as raw:
        root = Path(raw)
        control, artifacts, run = run_case(root, "durable")
        assert control.schema_version == 6
        scenarios += 1
        plan = ExecutionPlan(
            plan_id="plan-durable",
            run_id=run.run_id,
            stages=(
                StageSpec(stage_id="WORK", handler_key="work"),
                StageSpec(stage_id="VALIDATE", dependencies=("WORK",), handler_key="validate", final_validation=True),
            ),
        )
        control.register_execution_plan(plan)
        first, duplicate = control.enqueue_execution_command(command("run-durable", "submit"), run)
        second, duplicate_again = control.enqueue_execution_command(command("run-durable", "submit"), run)
        assert not duplicate and duplicate_again and first.job_id == second.job_id
        scenarios += 1
        worker = JobWorker(
            control_store=control,
            artifact_store=artifacts,
            executor=StageHandlerRegistry({"work": StageHandlerRegistry(), "validate": StageHandlerRegistry()}),
            worker_id="validator-worker",
        )
        # The two explicit handlers are intentionally absent: this proves the
        # runtime records BLOCKED rather than inventing SUCCEEDED.
        assert worker.run_once().status == JobStatus.SUCCEEDED.value
        assert worker.run_once().status == JobStatus.BLOCKED.value
        assert control.get_run(run.run_id).status.value == "BLOCKED"
        scenarios += 2
        control.close()
        reopened = SQLiteControlStore(control.path, project_root=root)
        assert reopened.schema_version == 6 and reopened.get_job(first.job_id) is not None
        scenarios += 1
        reopened.close()

        fence, fence_artifacts, fence_run = run_case(root, "fence")
        fence_plan = ExecutionPlan(plan_id="plan-fence", run_id=fence_run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
        fence.register_execution_plan(fence_plan)
        stage_job = fence.enqueue_stage_job(run_id=fence_run.run_id, plan_id=fence_plan.plan_id, stage_id="WORK")
        now = datetime.now(timezone.utc)
        claim_a = fence.claim_next_job(worker_id="a", now=now, lease_seconds=2)
        assert claim_a is not None and fence.claim_next_job(worker_id="b", now=now, lease_seconds=2) is None
        attempt = fence.ensure_stage_attempt(job_id=stage_job.job_id, worker_id="a", lease_generation=claim_a.lease_generation, now=now)
        claim_b = fence.claim_next_job(worker_id="b", now=now + timedelta(seconds=3), lease_seconds=2)
        assert claim_b is not None and claim_b.lease_generation == 2
        with __import__("contextlib").suppress(ConcurrencyConflictError):
            fence.finalize_stage_job(
                job_id=stage_job.job_id,
                worker_id="a",
                lease_generation=1,
                attempt=attempt,
                result=StageExecutionResult(status=StageResultStatus.SUCCEEDED),
                status=JobStatus.SUCCEEDED.value,
                now=now + timedelta(seconds=3),
            )
            raise AssertionError("stale worker finalized a reclaimed job")
        scenarios += 3
        fence.close()

        retry, retry_artifacts, retry_run = run_case(root, "retry")
        retry_plan = ExecutionPlan(plan_id="plan-retry", run_id=retry_run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),))
        retry.register_execution_plan(retry_plan)
        DurableExecutionSubmission(retry).submit_command(command=command(retry_run.run_id, "submit"), run=retry_run)
        failed = StageExecutionResult(status=StageResultStatus.FAILED, failure_code="TEMPORARY", failure_classification=FailureClassification.RETRYABLE_TRANSIENT, failure_reason="bounded transient")
        retry_worker = JobWorker(control_store=retry, artifact_store=retry_artifacts, executor=StageHandlerRegistry({"work": type("Retry", (), {"execute": lambda _self, _request: failed})()}), retry_policy=RetryPolicy(max_retries=1), worker_id="retry-worker")
        retry_worker.run_once()
        retry_worker.run_once()
        assert retry.get_stage_job(run_id=retry_run.run_id, stage_id="WORK").status is JobStatus.RETRY_WAIT
        scenarios += 1
        retry.close()

        cancel, cancel_artifacts, cancel_run = run_case(root, "cancel")
        DurableExecutionSubmission(cancel).submit_command(command=command(cancel_run.run_id, "submit"), run=cancel_run)
        cancel.request_run_cancellation(run_id=cancel_run.run_id, now=datetime.now(timezone.utc))
        assert cancel.get_run(cancel_run.run_id).status.value == "CANCELLED"
        assert cancel.get_job(stable_id("job", {"command_id": command(cancel_run.run_id, "submit").command_id})).status is JobStatus.CANCELLED
        scenarios += 1
        cancel.close()
        scenarios += extended_scenarios(root)
    print(f"STEP28_VALIDATOR=PASS scenarios={scenarios}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
