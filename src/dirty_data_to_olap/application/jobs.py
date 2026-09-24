"""Durable Step28 execution boundary and local worker.

This module is intentionally small: SQLite is the authoritative queue and
the worker is an at-least-once consumer.  A worker lease is a capability to
attempt a fenced state transition, not ownership of the truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Callable, Mapping, Protocol, Sequence

from dirty_data_to_olap.application.platform import (
    ArtifactStorePort,
    ConcurrencyConflictError,
    ControlStorePort,
    GateEvidenceService,
    PlatformError,
)
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.application.review_subjects import ReviewSubjectDerivationPort, ReviewCheckpointSubjectResolver
from dirty_data_to_olap.domain.contracts.api import ExecutionCommand, ExecutionAction, SubmissionResult
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewDecisionStatus, review_subject_key
from dirty_data_to_olap.domain.contracts.jobs import (
    DeliveryPhase,
    ExecutionPlan,
    ExecutionPlanIntent,
    ExecutionPlanPhase,
    ExecutionPlanSelection,
    FailureClassification,
    JobKind,
    JobRecord,
    JobStatus,
    RetryPolicy,
    ReplaySafety,
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
    GateEvidenceStatus,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.domain.contracts.source import stable_id, utc_now
from dirty_data_to_olap.observability import TelemetryClient, classify_error, safe_exception_detail


class StageExecutorPort(Protocol):
    """Project-owned typed stage executor; no provider-native types cross it."""

    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        ...


class ExecutionPlanAdvancerPort(Protocol):
    """Durable coordinator that expands a phased plan from completed runtime truth."""

    def advance_after_stage(self, *, run_id: str, completed_stage_id: str) -> ExecutionPlan:
        ...


class CancellationProbePort(Protocol):
    """Live durable cancellation observation kept outside the typed request."""

    def is_cancelled(self) -> bool:
        ...


class CancellationAwareStageExecutorPort(Protocol):
    def execute_with_context(self, request: StageExecutionRequest, cancellation_probe: CancellationProbePort) -> StageExecutionResult:
        ...


StageHandlerPort = StageExecutorPort
FaultInjector = Callable[[str, JobRecord, StageAttemptRecord | None], None]


class InjectedWorkerCrash(RuntimeError):
    """Deterministic test-only crash signal; durable state remains unreconciled."""


class DurableCancellationProbe:
    def __init__(self, control_store: ControlStorePort, *, run_id: str, job_id: str) -> None:
        self.control_store = control_store
        self.run_id = run_id
        self.job_id = job_id

    def is_cancelled(self) -> bool:
        run = self.control_store.get_run(self.run_id)
        job = self.control_store.get_job(self.job_id)
        return run is None or run.status is RunStatus.CANCELLED or job is None or job.cancellation_requested


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

    def execute_for(self, handler_key: str, request: StageExecutionRequest, cancellation_probe: CancellationProbePort | None = None) -> StageExecutionResult:
        handler = self._handlers.get(handler_key, MissingStageHandler())
        if cancellation_probe is not None and hasattr(handler, "execute_with_context"):
            result = handler.execute_with_context(request, cancellation_probe)
        else:
            result = handler.execute(request)
        return StageExecutionResult.model_validate(result)

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


class ExecutionPlanSelectionError(ValueError):
    """The authoritative graph cannot be compiled without run-specific selection."""

    def __init__(self, unresolved_stage_ids: tuple[str, ...], detail: str = "conditional stage selection is unresolved") -> None:
        self.unresolved_stage_ids = unresolved_stage_ids
        super().__init__(detail)


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
        max_active_per_run: int = 1,
        max_active_per_source: int = 1,
        review_subject_deriver: ReviewSubjectDerivationPort | None = None,
        plan_advancer: ExecutionPlanAdvancerPort | None = None,
        telemetry: TelemetryClient | None = None,
    ) -> None:
        if not worker_id or lease_seconds < 1 or max_active_per_run < 1 or max_active_per_source < 1:
            raise ValueError("worker_id and a positive lease are required")
        self.control_store = control_store
        self.artifact_store = artifact_store
        self.executor = executor or StageHandlerRegistry()
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock = clock
        self.fault_injector = fault_injector
        self.max_active_per_run = max_active_per_run
        self.max_active_per_source = max_active_per_source
        self.review_policy = ReviewPolicyService()
        self.review_subject_deriver = review_subject_deriver or ReviewCheckpointSubjectResolver(control_store, artifact_store)
        self.plan_advancer = plan_advancer
        self.telemetry = telemetry or TelemetryClient()

    def run_once(self) -> WorkerOutcome:
        now = _safe_now(self.clock)
        job = self.control_store.claim_next_job(worker_id=self.worker_id, now=now, lease_seconds=self.lease_seconds, max_active_per_run=self.max_active_per_run, max_active_per_source=self.max_active_per_source)
        if job is None:
            self.telemetry.metric("ddo_worker_activity_total", 1, labels={"activity": "idle"})
            return WorkerOutcome(None, "IDLE", "no eligible durable job")
        correlation = self.telemetry.context(run_id=job.run_id, job_id=job.job_id, stage_id=job.stage_id, command_id=job.command_id)
        self.telemetry.operation(event_name="job.claimed", component="worker", operation="claim", correlation=correlation, status=job.status.value, details={"job_kind": job.job_kind.value, "delivery_count": job.delivery_count})
        self.telemetry.metric("ddo_job_lifecycle_total", 1, labels={"job_kind": job.job_kind.value, "job_status": job.status.value})
        self.telemetry.queue_snapshot(self.control_store.list_jobs(run_id=job.run_id, limit=10_000))
        self._inject_fault("after_claim", job, None)
        if job.job_kind is JobKind.COMMAND:
            outcome = self._run_command(job, now)
            self.telemetry.operation(event_name="command.finalized", component="worker", operation="command", correlation=correlation, status=outcome.status, details={"detail": outcome.detail})
            return outcome
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
                contexts = candidate.review_contexts or ((candidate.review_context,) if candidate.review_context is not None else ())
                if not contexts:
                    blocked_reason = "review context is unavailable"
                    continue
                all_compatible = True
                for context in contexts:
                    authoritative = self.control_store.get_review_subject_context(
                        run_id=run.run_id,
                        checkpoint=context.review_checkpoint_id.value,
                        artifact_id=context.subject_artifact_id,
                    )
                    if authoritative is None:
                        blocked_reason = "authoritative review context is unavailable"
                        all_compatible = False
                        break
                    subject_artifact = self.control_store.get_artifact(context.subject_artifact_id)
                    if subject_artifact is None or subject_artifact.run_id != run.run_id:
                        blocked_reason = "review subject artifact changed or is unavailable"
                        all_compatible = False
                        break
                    try:
                        integrity = self.artifact_store.verify(subject_artifact)
                        stored_artifact = self.artifact_store.stat(subject_artifact)
                    except (KeyError, OSError, PlatformError):
                        blocked_reason = "review subject artifact changed or is unavailable"
                        all_compatible = False
                        break
                    if stored_artifact != subject_artifact or integrity.state is not ArtifactIntegrityState.VERIFIED:
                        blocked_reason = "review subject artifact changed or is unavailable"
                        all_compatible = False
                        break
                    current = self.control_store.get_current_review(run_id=run.run_id, subject_key=review_subject_key(authoritative))
                    if current is None or current.decision.decision not in {ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED}:
                        blocked_reason = "compatible accepted review is required before resume"
                        all_compatible = False
                        break
                    try:
                        self.review_policy.require_compatible(current.decision, authoritative)
                    except ReviewCompatibilityError:
                        blocked_reason = "current review is stale or incompatible"
                        all_compatible = False
                        break
                if not all_compatible:
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
            self.telemetry.operation(
                event_name="review.resumed",
                component="worker",
                operation="resume",
                correlation=self.telemetry.context(run_id=run.run_id),
                status="SUCCEEDED",
                details={"resumed_jobs": resumed},
            )
            self._set_run_status(run.run_id, RunStatus.RUNNING, allow_terminal_reactivation=True)
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
        correlation = self.telemetry.context(run_id=job.run_id, stage_id=job.stage_id, job_id=job.job_id, attempt_id=attempt.attempt_id)
        self.telemetry.operation(event_name="stage.attempt_created", component="worker", operation="attempt", correlation=correlation, status=attempt.status.value, details={"attempt_number": attempt.attempt_number})
        refreshed_job = self.control_store.get_job(job.job_id)
        if refreshed_job is None:
            raise PlatformError("stage job disappeared after attempt creation")
        job = refreshed_job
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

        if job.delivery_phase is DeliveryPhase.RESULT_RECORDED:
            if job.durable_result is None:
                result = StageExecutionResult(status=StageResultStatus.FAILED, failure_code="RECONCILIATION_REQUIRED", failure_classification=FailureClassification.UNKNOWN_SIDE_EFFECT, failure_reason="durable result marker has no durable result payload")
            else:
                result = StageExecutionResult.model_validate(job.durable_result)
            stored = self._finalize_after_record(job, attempt, result, plan, stage)
            return WorkerOutcome(stored.job_id, stored.status.value, f"stage {stage.stage_id} finalized from durable result")

        if job.delivery_phase is DeliveryPhase.RECONCILIATION_REQUIRED:
            result = StageExecutionResult(status=StageResultStatus.FAILED, failure_code="RECONCILIATION_REQUIRED", failure_classification=FailureClassification.UNKNOWN_SIDE_EFFECT, failure_reason="durable handler outcome requires reconciliation")
            stored = self._finalize(job, attempt, result, _safe_now(self.clock))
            self._advance_after_stage(plan, stage.stage_id, stored)
            return WorkerOutcome(stored.job_id, stored.status.value, "handler outcome requires reconciliation")

        if stage.review_checkpoint is not None:
            result = self._review_stage_result(job, stage)
            self.control_store.record_stage_result(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, attempt=attempt, result=result, now=_safe_now(self.clock))
            attempt = self.control_store.get_stage_attempt(attempt.attempt_id) or attempt.model_copy(update={"revision": attempt.revision + 1, "delivery_phase": DeliveryPhase.RESULT_RECORDED.value})
            stored = self._finalize(job, attempt, result, _safe_now(self.clock))
            self._advance_after_stage(plan, stage.stage_id, stored)
            return WorkerOutcome(stored.job_id, stored.status.value, f"review checkpoint {stage.stage_id} finalized")

        if job.delivery_phase is DeliveryPhase.HANDLER_DELIVERY_STARTED and job.replay_safety is not ReplaySafety.REPLAY_SAFE:
            result = StageExecutionResult(
                status=StageResultStatus.FAILED,
                failure_code="RECONCILIATION_REQUIRED",
                failure_classification=FailureClassification.UNKNOWN_SIDE_EFFECT,
                failure_reason="handler delivery began without a durable result; blind replay is forbidden",
            )
            self.control_store.record_stage_result(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, attempt=attempt, result=result, now=_safe_now(self.clock))
            attempt = self.control_store.get_stage_attempt(attempt.attempt_id) or attempt.model_copy(update={"revision": attempt.revision + 1, "delivery_phase": DeliveryPhase.RESULT_RECORDED.value})
            stored = self._finalize(job, attempt, result, _safe_now(self.clock))
            self._advance_after_stage(plan, stage.stage_id, stored)
            return WorkerOutcome(stored.job_id, stored.status.value, "unknown handler outcome requires reconciliation")

        self._set_run_status(job.run_id, RunStatus.RUNNING)
        probe = DurableCancellationProbe(self.control_store, run_id=job.run_id, job_id=job.job_id)
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
            self._inject_fault("before_handler_delivery_marker", job, attempt)
            self.control_store.mark_handler_delivery_started(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, now=_safe_now(self.clock))
            self.telemetry.operation(event_name="stage.delivery_started", component="worker", operation="handler_delivery", correlation=correlation, status="STARTED")
            attempt = self.control_store.get_stage_attempt(attempt.attempt_id) or attempt.model_copy(update={"revision": attempt.revision + 1, "delivery_phase": DeliveryPhase.HANDLER_DELIVERY_STARTED.value})
            self._inject_fault("after_handler_delivery_marker", job, attempt)
            result = self._execute(stage.handler_key, request, probe)
            self._inject_fault("after_handler_returns_before_result_record", job, attempt)
            self._inject_fault("after_artifact_publication", job, attempt)
            after = _safe_now(self.clock)
            refreshed = self.control_store.heartbeat_job(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, now=after, lease_seconds=self.lease_seconds)
            del refreshed
            if probe.is_cancelled():
                result = StageExecutionResult(status=StageResultStatus.CANCELLED, failure_code="CANCELLATION_REQUESTED", failure_classification=FailureClassification.CANCELLED, failure_reason="cooperative cancellation was observed by the stage boundary")
            elif result.status in {StageResultStatus.SUCCEEDED, StageResultStatus.NEEDS_REVIEW}:
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
        except Exception as exc:
            result = StageExecutionResult(
                status=StageResultStatus.FAILED,
                failure_code="STAGE_OUTCOME_UNKNOWN",
                failure_classification=FailureClassification.UNKNOWN_SIDE_EFFECT,
                failure_reason="stage execution outcome is unknown after worker delivery",
                metadata={"error_type": type(exc).__name__, "error_detail": safe_exception_detail(exc)},
            )
        self.control_store.record_stage_result(job_id=job.job_id, worker_id=self.worker_id, lease_generation=job.lease_generation, attempt=attempt, result=result, now=_safe_now(self.clock))
        self.telemetry.operation(event_name="stage.result_recorded", component="worker", operation="result_record", correlation=correlation, status=result.status.value, error_class=classify_error(result.failure_code, result.failure_classification.value if result.failure_classification else None) if result.failure_code else None, details={"output_artifact_count": len(result.output_artifact_refs)})
        attempt = self.control_store.get_stage_attempt(attempt.attempt_id) or attempt.model_copy(update={"revision": attempt.revision + 1, "delivery_phase": DeliveryPhase.RESULT_RECORDED.value})
        self._inject_fault("after_result_record_before_finalization", job, attempt)
        self._inject_fault("before_stage_finalization", job, attempt)
        stored = self._finalize(job, attempt, result, _safe_now(self.clock))
        self._advance_after_stage(plan, stage.stage_id, stored)
        return WorkerOutcome(stored.job_id, stored.status.value, f"stage {stage.stage_id} finalized")

    def _execute(self, handler_key: str, request: StageExecutionRequest, cancellation_probe: CancellationProbePort | None = None) -> StageExecutionResult:
        if isinstance(self.executor, StageHandlerRegistry):
            return self.executor.execute_for(handler_key, request, cancellation_probe)
        if cancellation_probe is not None and hasattr(self.executor, "execute_with_context"):
            result = self.executor.execute_with_context(request, cancellation_probe)
        else:
            result = self.executor.execute(request)
        return StageExecutionResult.model_validate(result)

    def _inject_fault(self, point: str, job: JobRecord, attempt: StageAttemptRecord | None) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point, job, attempt)

    def _finalize_after_record(self, job: JobRecord, attempt: StageAttemptRecord, result: StageExecutionResult, plan: ExecutionPlan, stage: StageSpec) -> JobRecord:
        self._inject_fault("after_result_record_before_finalization", job, attempt)
        self._inject_fault("before_stage_finalization", job, attempt)
        stored = self._finalize(job, attempt, result, _safe_now(self.clock))
        self._advance_after_stage(plan, stage.stage_id, stored)
        return stored

    def _review_stage_result(self, job: JobRecord, stage: StageSpec) -> StageExecutionResult:
        checkpoint = stage.review_checkpoint
        if checkpoint is None:
            raise PlatformError("review result requested for a non-review stage")
        upstream: list = []
        for dependency_id in (*stage.dependencies, *stage.conditional_dependencies):
            dependency = self.control_store.get_stage_job(run_id=job.run_id, stage_id=dependency_id)
            if dependency is None or dependency.status is not JobStatus.SUCCEEDED:
                continue
            for artifact_id in dependency.result_refs:
                artifact = self.control_store.get_artifact(artifact_id)
                if artifact is not None:
                    upstream.append(artifact)
        derivation = self.review_subject_deriver.derive(
            run_id=job.run_id,
            checkpoint=checkpoint,
            upstream_artifacts=tuple(upstream),
        )
        contexts = derivation.contexts
        if not contexts and job.review_context is not None and job.review_context.review_checkpoint_id is checkpoint:
            # A resumed legacy job may already carry a context.  New real
            # checkpoint reaches use the typed derivation above and never
            # require a handler to register an arbitrary context.
            contexts = (job.review_context,)
        if not contexts:
            return StageExecutionResult(
                status=StageResultStatus.BLOCKED,
                failure_code="REVIEW_CONTEXT_UNAVAILABLE",
                failure_classification=FailureClassification.BLOCKED_PREREQUISITE,
                failure_reason=derivation.detail or "authoritative review context could not be derived from typed upstream artifacts",
            )
        if any(context.review_checkpoint_id is not checkpoint for context in contexts):
            return StageExecutionResult(
                status=StageResultStatus.BLOCKED,
                failure_code="REVIEW_CONTEXT_INVALID",
                failure_classification=FailureClassification.BLOCKED_PREREQUISITE,
                failure_reason="registered review context names a different checkpoint",
            )
        for context in contexts:
            self.control_store.register_review_subject_context(run_id=job.run_id, context=context)
        accepted = True
        for context in contexts:
            current = self.control_store.get_current_review(run_id=job.run_id, subject_key=review_subject_key(context))
            if current is None or current.decision.decision not in {ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED}:
                accepted = False
                break
            try:
                self.review_policy.require_compatible(current.decision, context)
            except ReviewCompatibilityError:
                accepted = False
                break
        if accepted:
            self.telemetry.operation(
                event_name="review.satisfied",
                component="worker",
                operation="review_checkpoint",
                correlation=self.telemetry.context(run_id=job.run_id, stage_id=stage.stage_id, job_id=job.job_id),
                status="SUCCEEDED",
                details={"checkpoint": checkpoint.value, "context_count": len(contexts)},
            )
            self.telemetry.metric("ddo_review_lifecycle_total", 1, labels={"review_checkpoint": checkpoint.value, "result_class": "SUCCEEDED"})
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)
        if len(contexts) == 1:
            self.telemetry.operation(
                event_name="review.waiting",
                component="worker",
                operation="review_checkpoint",
                correlation=self.telemetry.context(run_id=job.run_id, stage_id=stage.stage_id, job_id=job.job_id),
                status="NEEDS_REVIEW",
                details={"checkpoint": checkpoint.value},
            )
            self.telemetry.metric("ddo_review_lifecycle_total", 1, labels={"review_checkpoint": checkpoint.value, "result_class": "NEEDS_REVIEW"})
            return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, review_context=contexts[0])
        self.telemetry.operation(
            event_name="review.waiting",
            component="worker",
            operation="review_checkpoint",
            correlation=self.telemetry.context(run_id=job.run_id, stage_id=stage.stage_id, job_id=job.job_id),
            status="NEEDS_REVIEW",
            details={"checkpoint": checkpoint.value, "context_count": len(contexts)},
        )
        self.telemetry.metric("ddo_review_lifecycle_total", 1, labels={"review_checkpoint": checkpoint.value, "result_class": "NEEDS_REVIEW"})
        return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, review_contexts=contexts)

    def _handler_available(self, handler_key: str) -> bool:
        return not isinstance(self.executor, StageHandlerRegistry) or self.executor.has_handler(handler_key)

    def _dependencies_succeeded(self, plan: ExecutionPlan, stage_id: str) -> bool:
        stage = plan.stage(stage_id)
        if stage is None:
            return False
        if not all((dependency := self.control_store.get_stage_job(run_id=plan.run_id, stage_id=dependency_id)) is not None and dependency.status is JobStatus.SUCCEEDED for dependency_id in stage.dependencies):
            return False
        for dependency_id in stage.conditional_dependencies:
            dependency = self.control_store.get_stage_job(run_id=plan.run_id, stage_id=dependency_id)
            dependency_stage = plan.stage(dependency_id)
            if dependency_stage is not None and dependency_stage.selected and (dependency is None or dependency.status is not JobStatus.SUCCEEDED):
                return False
        return True

    def _schedule_ready(self, plan: ExecutionPlan, *, parent_job_id: str | None) -> int:
        count = 0
        for stage in plan.stages:
            if not stage.selected:
                continue
            if self._dependencies_succeeded(plan, stage.stage_id):
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
            contexts = result.review_contexts or ((result.review_context,) if result.review_context is not None else ())
            if not contexts:
                raise PlatformError("review result has no authoritative context")
            for context in contexts:
                self.control_store.register_review_subject_context(run_id=job.run_id, context=context)
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
            stored = self.control_store.finalize_stage_job(
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
            self._observe_finalization(job, attempt, result, stored, retry_count=retry_count)
            return stored
        stored = self.control_store.finalize_stage_job(
            job_id=job.job_id,
            worker_id=self.worker_id,
            lease_generation=job.lease_generation,
            attempt=attempt.model_copy(update={"status": attempt_status, "finished_at": now, "output_artifact_refs": result.output_artifact_refs if job_status in {JobStatus.SUCCEEDED, JobStatus.NEEDS_REVIEW} else (), **attempt_failure}),
            result=result,
            status=job_status.value,
            now=now,
        )
        self._observe_finalization(job, attempt, result, stored, retry_count=stored.retry_count)
        return stored

    def _observe_finalization(self, job: JobRecord, attempt: StageAttemptRecord, result: StageExecutionResult, stored: JobRecord, *, retry_count: int) -> None:
        correlation = self.telemetry.context(run_id=job.run_id, stage_id=job.stage_id, job_id=job.job_id, attempt_id=attempt.attempt_id)
        error_class = classify_error(result.failure_code, result.failure_classification.value if result.failure_classification else None) if result.failure_code else None
        duration = None
        if attempt.started_at is not None and attempt.finished_at is not None:
            duration = max(0.0, (attempt.finished_at - attempt.started_at).total_seconds())
        self.telemetry.operation(event_name="job.finalized", component="worker", operation="finalize", correlation=correlation, status=stored.status.value, error_class=error_class, retry_count=retry_count, details={"delivery_phase": stored.delivery_phase.value, "failure_code": result.failure_code})
        self.telemetry.metric("ddo_job_lifecycle_total", 1, labels={"job_kind": job.job_kind.value, "job_status": stored.status.value})
        if duration is not None:
            self.telemetry.metric("ddo_job_execution_duration_seconds", duration, labels={"result_class": self.telemetry.result_class(stored.status.value)})
        if stored.status is JobStatus.RETRY_WAIT:
            self.telemetry.metric("ddo_job_retries_total", 1, labels={"retry_reason_class": error_class.value if error_class else "UNKNOWN"})
            self.telemetry.operation(event_name="job.retry_scheduled", component="worker", operation="retry", correlation=correlation, status=stored.status.value, error_class=error_class, retry_count=retry_count)
        if stored.status is JobStatus.NEEDS_REVIEW:
            contexts = result.review_contexts or ((result.review_context,) if result.review_context is not None else ())
            checkpoints = {context.review_checkpoint_id.value for context in contexts}
            for checkpoint in checkpoints or {"OTHER"}:
                self.telemetry.metric("ddo_review_lifecycle_total", 1, labels={"review_checkpoint": checkpoint, "result_class": "NEEDS_REVIEW"})

    def _advance_after_stage(self, plan: ExecutionPlan, stage_id: str, job: JobRecord) -> None:
        if job.status is JobStatus.SUCCEEDED:
            active_plan = plan
            if self.plan_advancer is not None:
                active_plan = self.plan_advancer.advance_after_stage(run_id=job.run_id, completed_stage_id=stage_id)
            # A source-resolved plan may still carry a pending ER branch while
            # its already-authorized source/evidence prefix continues.  Only a
            # bootstrap plan with no source truth is unrunnable as a whole.
            if active_plan.pending_stage_ids and (
                active_plan.planning_phase is ExecutionPlanPhase.BOOTSTRAP
                or (active_plan.planning_phase is ExecutionPlanPhase.SOURCE_RESOLVED and stage_id == "CANONICAL_HYPOTHESES")
            ):
                self._set_run_status(job.run_id, RunStatus.BLOCKED)
                return
            self._schedule_ready(active_plan, parent_job_id=job.job_id)
            self._maybe_complete_run(active_plan)
        elif job.status is JobStatus.NEEDS_REVIEW:
            self._set_run_status(job.run_id, RunStatus.NEEDS_REVIEW)
        elif job.status is JobStatus.BLOCKED:
            if (stage := plan.stage(stage_id)) is not None and stage.required:
                self._set_run_status(job.run_id, RunStatus.BLOCKED)
        elif job.status is JobStatus.FAILED:
            if (stage := plan.stage(stage_id)) is not None and stage.required:
                self._set_run_status(job.run_id, RunStatus.FAILED)
        elif job.status is JobStatus.CANCELLED:
            self._set_run_status(job.run_id, RunStatus.CANCELLED)

    def _maybe_complete_run(self, plan: ExecutionPlan) -> None:
        required = [stage for stage in plan.stages if stage.required and stage.selected]
        final = [stage for stage in required if stage.final_validation]
        if not final:
            return
        jobs = {job.stage_id: job for job in self.control_store.list_jobs(run_id=plan.run_id, limit=10000) if job.stage_id}
        if not all(jobs.get(stage.stage_id) is not None and jobs[stage.stage_id].status is JobStatus.SUCCEEDED for stage in required):
            return
        if not self._valid_g6_evidence(plan.run_id):
            self._set_run_status(plan.run_id, RunStatus.BLOCKED)
            return
        run = self.control_store.get_run(plan.run_id)
        if run is not None and run.status not in {RunStatus.CANCELLED, RunStatus.FAILED}:
            self.control_store.update_run(run.model_copy(update={"status": RunStatus.SUCCEEDED}), expected_revision=run.revision)

    def _valid_g6_evidence(self, run_id: str) -> bool:
        evidence = self.control_store.get_gate_evidence("G6_DATA_CORRECTNESS", run_id=run_id)
        if evidence is None or evidence.run_id != run_id or evidence.status is not GateEvidenceStatus.PASS or not evidence.eligible:
            return False
        artifact = self.control_store.get_artifact(evidence.validation_report_artifact_id)
        if artifact is None or artifact.run_id != run_id or artifact.artifact_kind != "ValidationReport" or artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
            return False
        if artifact.content_hash != evidence.validation_report_content_hash:
            return False
        try:
            if self.artifact_store.stat(artifact) != artifact or self.artifact_store.verify(artifact).state is not ArtifactIntegrityState.VERIFIED:
                return False
            report = GateEvidenceService._decode_report(self.artifact_store.read(artifact))
        except (KeyError, OSError, PlatformError, ValueError):
            return False
        return (
            isinstance(report, ValidationReport)
            and report.run_id == evidence.validation_report_run_id
            and report.report_id == evidence.validation_report_id
            and report.g6_status.value == evidence.status.value
            and report.g6_eligible is True
        )

    def _set_run_status(self, run_id: str, status: RunStatus, *, allow_terminal_reactivation: bool = False) -> None:
        run = self.control_store.get_run(run_id)
        if run is None or run.status in {RunStatus.CANCELLED, RunStatus.SUCCEEDED} and status is not RunStatus.CANCELLED:
            return
        if run.status in {RunStatus.FAILED, RunStatus.BLOCKED} and status is RunStatus.RUNNING and not allow_terminal_reactivation:
            return
        if run.status is status:
            return
        self.telemetry.metric("ddo_run_lifecycle_total", 1, labels={"run_status": status.value})
        self.telemetry.operation(
            event_name="run.status_changed",
            component="worker",
            operation="run_status",
            correlation=self.telemetry.context(run_id=run_id),
            status=status.value,
        )
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
        self._shutdown_requested = Event()
        for worker in self.workers:
            worker.max_active_per_run = max_active_per_run
            worker.max_active_per_source = max_active_per_source

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested.is_set()

    def request_shutdown(self) -> None:
        """Stop admitting new claims after the current bounded batch."""

        self._shutdown_requested.set()

    def pump(self) -> tuple[WorkerOutcome, ...]:
        outcomes: list[WorkerOutcome] = []
        with ThreadPoolExecutor(max_workers=len(self.workers)) as executor:
            while len(outcomes) < self.max_jobs_per_pump:
                if self._shutdown_requested.is_set():
                    break
                batch = self.workers[: min(len(self.workers), self.max_jobs_per_pump - len(outcomes))]
                batch_outcomes = tuple(executor.map(lambda worker: worker.run_once(), batch))
                outcomes.extend(batch_outcomes)
                if all(outcome.status == "IDLE" for outcome in batch_outcomes):
                    break
        return tuple(outcomes)


def load_authoritative_execution_plan(
    project_root: str | Path,
    *,
    run_id: str,
    selection: ExecutionPlanSelection | None = None,
    plan_id: str | None = None,
    stage_ids: Sequence[str] | None = None,
    planning_phase: ExecutionPlanPhase = ExecutionPlanPhase.COMPLETE,
    planning_intent: ExecutionPlanIntent | None = None,
    pending_stage_ids: Sequence[str] = (),
    revision: int = 0,
    success_guard_required: bool = True,
) -> ExecutionPlan:
    """Project the existing architecture DAG into a run-scoped durable plan.

    This is intentionally a loader, not a second hard-coded DAG.  The
    architecture YAML remains the semantic source of stage dependencies;
    handler wiring and review policy are supplied by the project boundary.
    """

    import yaml

    path = Path(project_root) / "docs" / "architecture" / "specs" / "stage_graph.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    all_raw_stages = tuple(document.get("stages", []))
    requested_stage_ids = None if stage_ids is None else {str(stage_id) for stage_id in stage_ids}
    all_stage_ids = {str(raw["stage_id"]) for raw in all_raw_stages}
    if requested_stage_ids is not None and not requested_stage_ids <= all_stage_ids:
        raise ExecutionPlanSelectionError(tuple(sorted(requested_stage_ids - all_stage_ids)), "execution plan requested an unknown graph stage")
    graph_stages = tuple(raw for raw in all_raw_stages if requested_stage_ids is None or str(raw["stage_id"]) in requested_stage_ids)
    conditional_stage_ids = tuple(str(raw["stage_id"]) for raw in graph_stages if bool(raw.get("conditional", False)))
    if conditional_stage_ids and selection is None:
        raise ExecutionPlanSelectionError(conditional_stage_ids)
    if selection is not None and selection.run_id != run_id:
        raise ExecutionPlanSelectionError(conditional_stage_ids, "execution selection is bound to a different run")
    decisions = {} if selection is None else {item.stage_id: item for item in selection.decisions}
    unknown = tuple(sorted(set(decisions) - set(conditional_stage_ids)))
    missing = tuple(stage_id for stage_id in conditional_stage_ids if stage_id not in decisions) if selection is not None else ()
    required_unselected = tuple(
        str(raw["stage_id"])
        for raw in graph_stages
        if bool(raw.get("conditional", False)) and bool(raw.get("required", True)) and raw.get("stage_id") in decisions and not decisions[str(raw["stage_id"])].selected
    )
    if unknown or missing or required_unselected:
        unresolved = tuple(sorted(set(unknown) | set(missing) | set(required_unselected)))
        detail = "execution selection must resolve every conditional stage exactly once"
        if required_unselected:
            detail = "required conditional stages cannot be excluded by the execution selection"
        raise ExecutionPlanSelectionError(unresolved, detail)
    stages = []
    for raw in graph_stages:
        stage_id = str(raw["stage_id"])
        conditional = bool(raw.get("conditional", False))
        required = bool(raw.get("required", True))
        decision = decisions.get(stage_id)
        checkpoint = raw.get("review_checkpoint_id")
        conditional_dependencies = tuple(dict.fromkeys(
            str(item.get("dependency")) for item in raw.get("conditional_dependencies", []) if isinstance(item, dict) and item.get("dependency")
        ))
        selected = True if not conditional else decision.selected
        stages.append(
            StageSpec(
                stage_id=stage_id,
                required=required,
                conditional=conditional,
                selected=selected,
                selection_reason=("unconditional stage selected" if not conditional else decision.reason),
                dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
                optional_dependencies=tuple(str(item) for item in raw.get("optional_dependencies", [])),
                conditional_dependencies=conditional_dependencies,
                handler_key=stage_id,
                review_checkpoint=ReviewCheckpoint(str(checkpoint)) if checkpoint else None,
                required_review_checkpoint=ReviewCheckpoint(str(raw["required_review_checkpoint"])) if raw.get("required_review_checkpoint") else None,
                final_validation=stage_id == "VALIDATION_RECONCILIATION",
                source_scope=str(raw["source_scope"]) if raw.get("source_scope") else None,
                metadata={
                    "graph_source": "stage_graph.yml",
                    **({} if decision is None else {
                        "selection_policy_ref": decision.policy_ref,
                        "selection_scope": decision.scope,
                        "selection_scope_fingerprint": decision.scope_fingerprint,
                        **({"selection_evidence_ref": decision.evidence_ref} if decision.evidence_ref else {}),
                    }),
                },
            )
        )
    if not stages:
        raise PlatformError("authoritative stage graph contains no stages")
    return ExecutionPlan(
        plan_id=plan_id or stable_id("execution-plan", {"run_id": run_id, "graph": str(path), "selection": selection.content_hash if selection is not None else "bootstrap"}),
        run_id=run_id,
        graph_source="docs/architecture/specs/stage_graph.yml",
        graph_version=str(document.get("scope", "v1_runtime_stage_dag")),
        stages=tuple(stages),
        selection=selection,
        success_guard_required=success_guard_required,
        planning_phase=planning_phase,
        planning_intent=planning_intent,
        pending_stage_ids=tuple(pending_stage_ids),
        revision=revision,
    )


__all__ = [
    "BoundedWorkerPool",
    "CancellationAwareStageExecutorPort",
    "CancellationProbePort",
    "DurableCancellationProbe",
    "DurableExecutionSubmission",
    "ExecutionPlanAdvancerPort",
    "ExecutionPlanSelectionError",
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
