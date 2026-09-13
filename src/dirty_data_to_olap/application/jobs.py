"""Durable Step28 execution boundary and local worker.

This module is intentionally small: SQLite is the authoritative queue and
the worker is an at-least-once consumer.  A worker lease is a capability to
attempt a fenced state transition, not ownership of the truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol

from dirty_data_to_olap.application.platform import (
    ArtifactStorePort,
    ConcurrencyConflictError,
    ControlStorePort,
    PlatformError,
)
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.domain.contracts.api import ExecutionCommand, ExecutionAction, SubmissionResult
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    FailureClassification,
    JobKind,
    JobRecord,
    JobStatus,
    RetryPolicy,
    StageExecutionRequest,
    StageExecutionResult,
    StageResultStatus,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactIntegrityState,
    ArtifactPublicationState,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
)
from dirty_data_to_olap.domain.contracts.source import stable_id, utc_now


class StageExecutorPort(Protocol):
    """Project-owned typed stage executor; no provider-native types cross it."""

    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        ...


StageHandlerPort = StageExecutorPort
FaultInjector = Callable[[str, JobRecord, StageAttemptRecord | None], None]


class InjectedWorkerCrash(RuntimeError):
    """Deterministic test-only crash signal; durable state remains unreconciled."""


class MissingStageHandler:
    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        return StageExecutionResult(
            status=StageResultStatus.BLOCKED,
            failure_code="STAGE_HANDLER_UNAVAILABLE",
            failure_classification=FailureClassification.BLOCKED_PREREQUISITE,
            failure_reason="no project-owned handler is registered for this stage",
            metadata={"stage_id": request.stage_id},
        )


class StageHandlerRegistry(StageExecutorPort):
    """Explicit handler registry.  An absent handler is BLOCKED, never success."""

    def __init__(self, handlers: Mapping[str, StageExecutorPort] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def register(self, handler_key: str, handler: StageExecutorPort) -> None:
        if not handler_key or handler_key in self._handlers:
            raise ValueError("handler key must be non-empty and not already registered")
        self._handlers[handler_key] = handler

    def has_handler(self, handler_key: str) -> bool:
        return handler_key in self._handlers

    def execute_for(self, handler_key: str, request: StageExecutionRequest) -> StageExecutionResult:
        return StageExecutionResult.model_validate(self._handlers.get(handler_key, MissingStageHandler()).execute(request))

    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        return self.execute_for(request.stage_id, request)


class DurableExecutionSubmission:
    """Step27 submission port backed by the same durable SQLite control store."""

    def __init__(self, control_store: ControlStorePort) -> None:
        self.control_store = control_store

    def submit_command(self, *, command: ExecutionCommand, run: RunRecord) -> SubmissionResult:
        job, _duplicate = self.control_store.enqueue_execution_command(command, run)
        return SubmissionResult(
            run_id=run.run_id,
            command_id=command.command_id,
            status="ACCEPTED",
            submission_id=job.job_id,
            detail="durably accepted by the SQLite control store",
            accepted_by="sqlite-control-store",
        )


def _safe_now(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class WorkerOutcome:
    job_id: str | None
    status: str
    detail: str


class JobWorker:
    """One bounded at-least-once worker iteration."""

    def __init__(
        self,
        *,
        control_store: ControlStorePort,
        artifact_store: ArtifactStorePort,
        executor: StageExecutorPort | None = None,
        worker_id: str = "worker-1",
        lease_seconds: int = 30,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        if not worker_id or lease_seconds < 1:
            raise ValueError("worker_id and a positive lease are required")
        self.control_store = control_store
        self.artifact_store = artifact_store
        self.executor = executor or StageHandlerRegistry()
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock = clock
        self.fault_injector = fault_injector
        self.review_policy = ReviewPolicyService()

    def run_once(self) -> WorkerOutcome:
        now = _safe_now(self.clock)
        job = self.control_store.claim_next_job(worker_id=self.worker_id, now=now, lease_seconds=self.lease_seconds)
        if job is None:
            return WorkerOutcome(None, "IDLE", "no eligible durable job")
        self._inject_fault("after_claim", job, None)
        if job.job_kind is JobKind.COMMAND:
            return self._run_command(job, now)
        return self._run_stage(job, now)

    def _run_command(self, job: JobRecord, now: datetime) -> WorkerOutcome:
        action = ExecutionAction(job.action or "submit")
        if action is ExecutionAction.CANCEL:
            self.control_store.request_run_cancellation(run_id=job.run_id, now=now)
            stored = self.control_store.finalize_command_job(
                job_id=job.job_id,
                worker_id=self.worker_id,
                lease_generation=job.lease_generation,
                status=JobStatus.SUCCEEDED.value,
                now=now,
                detail="run cancellation was durably recorded",
            )
            return WorkerOutcome(stored.job_id, stored.status.value, "cancellation recorded")
        if action is ExecutionAction.RESUME:
            return self._resume_command(job, now)
        plan = self.control_store.get_execution_plan(job.run_id)
        if plan is None:
            self._set_run_status(job.run_id, RunStatus.BLOCKED)
            stored = self.control_store.finalize_command_job(
                job_id=job.job_id,
                worker_id=self.worker_id,
                lease_generation=job.lease_generation,
                status=JobStatus.BLOCKED.value,
                now=now,
                detail="no durable execution plan is registered",
                failure_code="EXECUTION_PLAN_UNAVAILABLE",
                failure_classification=FailureClassification.BLOCKED_PREREQUISITE.value,
            )
            return WorkerOutcome(stored.job_id, stored.status.value, "execution plan unavailable")
        scheduled = self._schedule_ready(plan, parent_job_id=job.job_id)
        if not scheduled:
            self._set_run_status(job.run_id, RunStatus.BLOCKED)
            stored = self.control_store.finalize_command_job(
                job_id=job.job_id,
                worker_id=self.worker_id,
                lease_generation=job.lease_generation,
                status=JobStatus.BLOCKED.value,
                now=now,
                detail="execution plan has no runnable root stage",
                failure_code="NO_RUNNABLE_ROOT_STAGE",
                failure_classification=FailureClassification.BLOCKED_PREREQUISITE.value,
            )
            return WorkerOutcome(stored.job_id, stored.status.value, "no runnable root stage")
        stored = self.control_store.finalize_command_job(
            job_id=job.job_id,
            worker_id=self.worker_id,
            lease_generation=job.lease_generation,
            status=JobStatus.SUCCEEDED.value,
            now=now,
            detail=f"scheduled {scheduled} authoritative stage boundary jobs",
        )
        return WorkerOutcome(stored.job_id, stored.status.value, "execution plan scheduled")

    def _resume_command(self, job: JobRecord, now: datetime) -> WorkerOutcome:
        run = self.control_store.get_run(job.run_id)
        plan = self.control_store.get_execution_plan(job.run_id)
        if run is None or plan is None:
            return self._finalize_command_blocked(job, now, "resume requires an existing durable execution plan", "EXECUTION_PLAN_UNAVAILABLE")
        if run.status in {RunStatus.CANCELLED, RunStatus.SUCCEEDED}:
            return self._finalize_command_blocked(job, now, "terminal runs cannot be resumed", "ILLEGAL_RESUME_TERMINAL_RUN")
        jobs = self.control_store.list_jobs(run_id=run.run_id, limit=10000)
        resumed = 0
        blocked_reason = "no resumable stage is currently authorized"
        for candidate in jobs:
            if candidate.job_kind is not JobKind.STAGE:
                continue
            if candidate.status is JobStatus.NEEDS_REVIEW:
                context = candidate.review_context
                if context is None:
                    blocked_reason = "review context is unavailable"
                    continue
                subject_key = "|".join((context.review_checkpoint_id.value, context.subject_artifact_id, context.subject_content_hash))
                current = self.control_store.get_current_review(run_id=run.run_id, subject_key=subject_key)
                if current is None or current.decision.decision not in {ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED}:
                    blocked_reason = "compatible accepted review is required before resume"
                    continue
                try:
                    self.review_policy.require_compatible(current.decision, context)
                except ReviewCompatibilityError:
                    blocked_reason = "current review is stale or incompatible"
                    continue
                self.control_store.resume_job(job_id=candidate.job_id, now=now)
                resumed += 1
            elif candidate.status is JobStatus.BLOCKED:
                stage = plan.stage(candidate.stage_id or "")
                if stage is None or not self._handler_available(stage.handler_key):
                    blocked_reason = "blocked prerequisite or stage capability remains unavailable"
                    continue
                if not self._dependencies_succeeded(plan, stage.stage_id):
                    blocked_reason = "stage prerequisites are not resolved"
                    continue
                self.control_store.resume_job(job_id=candidate.job_id, now=now)
                resumed += 1
            elif candidate.status is JobStatus.FAILED:
                if candidate.failure_classification is FailureClassification.UNKNOWN_SIDE_EFFECT:
                    blocked_reason = "unknown side effect outcome requires reconciliation; blind retry is forbidden"
                    continue
                self.control_store.resume_job(job_id=candidate.job_id, now=now)
                resumed += 1
        if resumed:
            self._set_run_status(run.run_id, RunStatus.RUNNING)
            stored = self.control_store.finalize_command_job(
                job_id=job.job_id,
                worker_id=self.worker_id,
                lease_generation=job.lease_generation,
                status=JobStatus.SUCCEEDED.value,
                now=now,
                detail=f"authorized {resumed} new stage attempt(s)",
            )
            return WorkerOutcome(stored.job_id, stored.status.value, "resume authorized")
        return self._finalize_command_blocked(job, now, blocked_reason, "RESUME_NOT_AUTHORIZED")

    def _finalize_command_blocked(self, job: JobRecord, now: datetime, detail: str, code: str) -> WorkerOutcome:
        stored = self.control_store.finalize_command_job(
            job_id=job.job_id,
            worker_id=self.worker_id,
            lease_generation=job.lease_generation,
            status=JobStatus.BLOCKED.value,
            now=now,
            detail=detail,
            failure_code=code,
            failure_classification=FailureClassification.BLOCKED_PREREQUISITE.value,
        )
        return WorkerOutcome(stored.job_id, stored.status.value, detail)

    def _run_stage(self, job: JobRecord, now: datetime) -> WorkerOutcome:
        plan = self.control_store.get_execution_plan(job.run_id)
        stage = None if plan is None else plan.stage(job.stage_id or "")
        attempt = self.control_store.ensure_stage_attempt(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, now=now)
        self._inject_fault("after_stage_attempt", job, attempt)
        if plan is None or stage is None:
            result = StageExecutionResult(
                status=StageResultStatus.BLOCKED,
                failure_code="EXECUTION_PLAN_UNAVAILABLE",
                failure_classification=FailureClassification.BLOCKED_PREREQUISITE,
                failure_reason="stage plan is unavailable at worker start",
            )
            stored = self._finalize(job, attempt, result, now)
            return WorkerOutcome(stored.job_id, stored.status.value, "stage plan unavailable")
        if not self._dependencies_succeeded(plan, stage.stage_id):
            result = StageExecutionResult(
                status=StageResultStatus.BLOCKED,
                failure_code="PREREQUISITE_NOT_SUCCEEDED",
                failure_classification=FailureClassification.BLOCKED_PREREQUISITE,
                failure_reason="one or more authoritative stage prerequisites are not succeeded",
            )
            stored = self._finalize(job, attempt, result, now)
            self._set_run_status(job.run_id, RunStatus.BLOCKED)
            return WorkerOutcome(stored.job_id, stored.status.value, "stage prerequisite blocked")
        current_run = self.control_store.get_run(job.run_id)
        if job.cancellation_requested or current_run is None or current_run.status is RunStatus.CANCELLED:
            result = StageExecutionResult(status=StageResultStatus.CANCELLED, failure_code="CANCELLATION_REQUESTED", failure_classification=FailureClassification.CANCELLED, failure_reason="run cancellation was observed before stage execution")
            stored = self._finalize(job, attempt, result, now)
            return WorkerOutcome(stored.job_id, stored.status.value, "stage cancelled before execution")
        self._set_run_status(job.run_id, RunStatus.RUNNING)
        try:
            refreshed = self.control_store.heartbeat_job(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, now=now, lease_seconds=self.lease_seconds)
            request = StageExecutionRequest(
                request_id=stable_id("stage-request", {"job_id": job.job_id, "attempt_id": attempt.attempt_id}),
                job_id=job.job_id,
                run_id=job.run_id,
                stage_id=stage.stage_id,
                attempt_id=attempt.attempt_id,
                plan_id=plan.plan_id,
                configuration_fingerprint=current_run.configuration_fingerprint,
                policy_config_fingerprint=stage.policy_config_fingerprint,
                input_artifact_refs=attempt.input_artifact_refs,
                cancellation_token_id=stable_id("cancel-token", {"run_id": job.run_id, "job_id": job.job_id, "generation": refreshed.lease_generation}),
                metadata={"handler_key": stage.handler_key},
            )
            result = self._execute(stage.handler_key, request)
            self._inject_fault("after_artifact_publication", job, attempt)
            after = _safe_now(self.clock)
            refreshed = self.control_store.heartbeat_job(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, now=after, lease_seconds=self.lease_seconds)
            del refreshed
            if result.status in {StageResultStatus.SUCCEEDED, StageResultStatus.NEEDS_REVIEW}:
                try:
                    self._verify_outputs(job, attempt, result)
                except (KeyError, OSError, PlatformError):
                    result = StageExecutionResult(
                        status=StageResultStatus.FAILED,
                        failure_code="ARTIFACT_INTEGRITY_FAILED",
                        failure_classification=FailureClassification.TERMINAL_FAILURE,
                        failure_reason="stage output was not durably registered and verified",
                    )
        except InjectedWorkerCrash:
            raise
        except ConcurrencyConflictError:
            raise
        except PlatformError:
            raise
        except Exception:
            result = StageExecutionResult(
                status=StageResultStatus.FAILED,
                failure_code="STAGE_OUTCOME_UNKNOWN",
                failure_classification=FailureClassification.UNKNOWN_SIDE_EFFECT,
                failure_reason="stage execution outcome is unknown after worker delivery",
            )
        self._inject_fault("before_stage_finalization", job, attempt)
        stored = self._finalize(job, attempt, result, _safe_now(self.clock))
        self._advance_after_stage(plan, stage.stage_id, stored)
        return WorkerOutcome(stored.job_id, stored.status.value, f"stage {stage.stage_id} finalized")

    def _execute(self, handler_key: str, request: StageExecutionRequest) -> StageExecutionResult:
        if isinstance(self.executor, StageHandlerRegistry):
            return self.executor.execute_for(handler_key, request)
        result = self.executor.execute(request)
        return StageExecutionResult.model_validate(result)

    def _inject_fault(self, point: str, job: JobRecord, attempt: StageAttemptRecord | None) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point, job, attempt)

    def _handler_available(self, handler_key: str) -> bool:
        return not isinstance(self.executor, StageHandlerRegistry) or self.executor.has_handler(handler_key)

    def _dependencies_succeeded(self, plan: ExecutionPlan, stage_id: str) -> bool:
        stage = plan.stage(stage_id)
        if stage is None:
            return False
        return all((dependency := self.control_store.get_stage_job(run_id=plan.run_id, stage_id=dependency_id)) is not None and dependency.status is JobStatus.SUCCEEDED for dependency_id in stage.dependencies)

    def _schedule_ready(self, plan: ExecutionPlan, *, parent_job_id: str | None) -> int:
        count = 0
        for stage in plan.stages:
            if not stage.dependencies or self._dependencies_succeeded(plan, stage.stage_id):
                existing = self.control_store.get_stage_job(run_id=plan.run_id, stage_id=stage.stage_id)
                if existing is None:
                    self.control_store.enqueue_stage_job(run_id=plan.run_id, plan_id=plan.plan_id, stage_id=stage.stage_id, parent_job_id=parent_job_id, available_at=_safe_now(self.clock))
                    count += 1
        return count

    def _verify_outputs(self, job: JobRecord, attempt: StageAttemptRecord, result: StageExecutionResult) -> None:
        for artifact_id in result.output_artifact_refs:
            artifact = self.control_store.get_artifact(artifact_id)
            if artifact is None or artifact.run_id != job.run_id or artifact.stage_id != job.stage_id or artifact.attempt_id != attempt.attempt_id:
                raise PlatformError("stage output is not registered to this run, stage and attempt")
            if artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
                raise PlatformError("stage output is not published")
            stored = self.artifact_store.stat(artifact)
            integrity = self.artifact_store.verify(artifact)
            if stored != artifact or integrity.state is not ArtifactIntegrityState.VERIFIED:
                raise PlatformError("stage output failed artifact integrity verification")

    def _finalize(self, job: JobRecord, attempt: StageAttemptRecord, result: StageExecutionResult, now: datetime) -> JobRecord:
        attempt_failure = {
            "failure_code": result.failure_code,
            "failure_reason": result.failure_reason,
        }
        if result.status is StageResultStatus.SUCCEEDED:
            job_status = JobStatus.SUCCEEDED
            attempt_status = StageStatus.SUCCEEDED
        elif result.status is StageResultStatus.NEEDS_REVIEW:
            if result.review_context is None:
                raise PlatformError("review result has no authoritative context")
            self.control_store.register_review_subject_context(run_id=job.run_id, context=result.review_context)
            job_status = JobStatus.NEEDS_REVIEW
            attempt_status = StageStatus.NEEDS_REVIEW
        elif result.status is StageResultStatus.BLOCKED:
            job_status = JobStatus.BLOCKED
            attempt_status = StageStatus.BLOCKED
        elif result.status is StageResultStatus.CANCELLED:
            job_status = JobStatus.CANCELLED
            attempt_status = StageStatus.CANCELLED
        else:
            attempt_status = StageStatus.FAILED
            if result.failure_classification is FailureClassification.RETRYABLE_TRANSIENT and job.retry_count < self.retry_policy.max_retries:
                job_status = JobStatus.RETRY_WAIT
                retry_count = job.retry_count + 1
                delay = self.retry_policy.delay_for(retry_count)
                available_at = now + timedelta(seconds=delay)
            else:
                job_status = JobStatus.FAILED
                retry_count = job.retry_count
                available_at = now
            return self.control_store.finalize_stage_job(
                job_id=job.job_id,
                worker_id=self.worker_id,
                lease_generation=job.lease_generation,
                attempt=attempt.model_copy(update={"status": attempt_status, "finished_at": now, "output_artifact_refs": (), **attempt_failure}),
                result=result,
                status=job_status.value,
                now=now,
                retry_count=retry_count,
                available_at=available_at,
            )
        return self.control_store.finalize_stage_job(
            job_id=job.job_id,
            worker_id=self.worker_id,
            lease_generation=job.lease_generation,
            attempt=attempt.model_copy(update={"status": attempt_status, "finished_at": now, "output_artifact_refs": result.output_artifact_refs if job_status in {JobStatus.SUCCEEDED, JobStatus.NEEDS_REVIEW} else (), **attempt_failure}),
            result=result,
            status=job_status.value,
            now=now,
        )

    def _advance_after_stage(self, plan: ExecutionPlan, stage_id: str, job: JobRecord) -> None:
        if job.status is JobStatus.SUCCEEDED:
            self._schedule_ready(plan, parent_job_id=job.job_id)
            self._maybe_complete_run(plan)
        elif job.status is JobStatus.NEEDS_REVIEW:
            self._set_run_status(job.run_id, RunStatus.NEEDS_REVIEW)
        elif job.status is JobStatus.BLOCKED:
            self._set_run_status(job.run_id, RunStatus.BLOCKED)
        elif job.status is JobStatus.FAILED:
            self._set_run_status(job.run_id, RunStatus.FAILED)
        elif job.status is JobStatus.CANCELLED:
            self._set_run_status(job.run_id, RunStatus.CANCELLED)

    def _maybe_complete_run(self, plan: ExecutionPlan) -> None:
        required = [stage for stage in plan.stages if stage.required]
        final = [stage for stage in required if stage.final_validation]
        if not final:
            return
        jobs = {job.stage_id: job for job in self.control_store.list_jobs(run_id=plan.run_id, limit=10000) if job.stage_id}
        if all(jobs.get(stage.stage_id) is not None and jobs[stage.stage_id].status is JobStatus.SUCCEEDED for stage in required) and jobs[final[0].stage_id].status is JobStatus.SUCCEEDED:
            run = self.control_store.get_run(plan.run_id)
            if run is not None and run.status not in {RunStatus.CANCELLED, RunStatus.FAILED}:
                self.control_store.update_run(run.model_copy(update={"status": RunStatus.SUCCEEDED}), expected_revision=run.revision)

    def _set_run_status(self, run_id: str, status: RunStatus) -> None:
        run = self.control_store.get_run(run_id)
        if run is None or run.status in {RunStatus.CANCELLED, RunStatus.SUCCEEDED} and status is not RunStatus.CANCELLED:
            return
        if run.status is status:
            return
        try:
            self.control_store.update_run(run.model_copy(update={"status": status}), expected_revision=run.revision)
        except ConcurrencyConflictError:
            latest = self.control_store.get_run(run_id)
            if latest is None or latest.status is not status:
                raise


class BoundedWorkerPool:
    """A bounded local pump; no unbounded task/thread creation is permitted."""

    def __init__(self, workers: tuple[JobWorker, ...], *, max_workers: int = 1, max_jobs_per_pump: int = 100, max_active_per_run: int = 1, max_active_per_source: int = 1) -> None:
        if not workers or max_workers < 1 or max_jobs_per_pump < 1 or max_active_per_run < 1 or max_active_per_source < 1:
            raise ValueError("a worker pool needs positive bounded limits")
        self.workers = workers[:max_workers]
        self.max_workers = max_workers
        self.max_jobs_per_pump = max_jobs_per_pump
        self.max_active_per_run = max_active_per_run
        self.max_active_per_source = max_active_per_source

    def pump(self) -> tuple[WorkerOutcome, ...]:
        outcomes: list[WorkerOutcome] = []
        for index in range(self.max_jobs_per_pump):
            worker = self.workers[index % len(self.workers)]
            outcome = worker.run_once()
            outcomes.append(outcome)
            if outcome.status == "IDLE":
                break
        return tuple(outcomes)


def load_authoritative_execution_plan(project_root: str | Path, *, run_id: str, plan_id: str | None = None) -> ExecutionPlan:
    """Project the existing architecture DAG into a run-scoped durable plan.

    This is intentionally a loader, not a second hard-coded DAG.  The
    architecture YAML remains the semantic source of stage dependencies;
    handler wiring and review policy are supplied by the project boundary.
    """

    import yaml

    path = Path(project_root) / "docs" / "architecture" / "specs" / "stage_graph.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    stages = []
    for raw in document.get("stages", []):
        stage_id = str(raw["stage_id"])
        stages.append(
            StageSpec(
                stage_id=stage_id,
                required=bool(raw.get("required", True)),
                dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
                handler_key=stage_id,
                final_validation=stage_id == "VALIDATION_RECONCILIATION",
                metadata={"graph_source": "stage_graph.yml"},
            )
        )
    if not stages:
        raise PlatformError("authoritative stage graph contains no stages")
    return ExecutionPlan(
        plan_id=plan_id or stable_id("execution-plan", {"run_id": run_id, "graph": str(path)}),
        run_id=run_id,
        graph_source="docs/architecture/specs/stage_graph.yml",
        graph_version=str(document.get("scope", "v1_runtime_stage_dag")),
        stages=tuple(stages),
    )


__all__ = [
    "BoundedWorkerPool",
    "DurableExecutionSubmission",
    "FaultInjector",
    "InjectedWorkerCrash",
    "JobWorker",
    "load_authoritative_execution_plan",
    "MissingStageHandler",
    "StageExecutorPort",
    "StageHandlerPort",
    "StageHandlerRegistry",
    "WorkerOutcome",
]
