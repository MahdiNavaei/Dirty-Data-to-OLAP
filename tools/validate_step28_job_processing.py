"""Behavioral Step28 validator for the disposable SQLite job runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission, JobWorker, StageHandlerRegistry
from dirty_data_to_olap.application.platform import ConcurrencyConflictError
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    FailureClassification,
    JobStatus,
    RetryPolicy,
    StageExecutionResult,
    StageResultStatus,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import RunRecord
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


def main() -> int:
    scenarios = 0
    with tempfile.TemporaryDirectory(prefix="step28-validator-") as raw:
        root = Path(raw)
        control, artifacts, run = run_case(root, "durable")
        assert control.schema_version == 5
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
        assert reopened.schema_version == 5 and reopened.get_job(first.job_id) is not None
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
    print(f"STEP28_VALIDATOR=PASS scenarios={scenarios}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
