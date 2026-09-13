"""Step27 backend application boundary.

The service exposes control-plane operations over existing project ports.  It
does not run data engines, execute SQL, or decide domain semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol

from dirty_data_to_olap.application.platform import (
    ArtifactStorePort,
    ConcurrencyConflictError,
    ControlStorePort,
    PlatformError,
)
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.visualization import VisualizationInputError, VisualizationService
from dirty_data_to_olap.domain.contracts.api import (
    IdempotencyRecord,
    ReviewHistoryRecord,
    ReviewRecord,
    SubmissionResult,
    idempotency_fingerprint,
    safe_metadata,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecisionStatus,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactIntegrityState,
    ArtifactPublicationState,
    ArtifactRef,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.domain.contracts.visualization import (
    ValidationReportVisualization,
    ValidationReportVisualizationBinding,
    VisualizationGraph,
    VisualizationScope,
)
from dirty_data_to_olap.domain.contracts.source import stable_id, utc_now


@dataclass(frozen=True)
class Principal:
    """Trusted caller context supplied by the transport/composition boundary."""

    subject: str
    scopes: frozenset[str]
    source: str = "TRUSTED_PRINCIPAL"


class ExecutionSubmissionPort(Protocol):
    """The Step28 handoff; implementations own heavy execution elsewhere."""

    def submit_run(self, *, run: RunRecord, principal: Principal) -> SubmissionResult:
        ...

    def cancel_run(self, *, run: RunRecord, principal: Principal) -> SubmissionResult:
        ...

    def resume_run(self, *, run: RunRecord, principal: Principal) -> SubmissionResult:
        ...


class BackendError(RuntimeError):
    """Safe, transport-independent application error."""

    def __init__(self, code: str, message: str, *, status: int, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        super().__init__(message)


class UnavailableExecutionSubmission:
    """Explicit reference behavior until Step28 supplies a durable executor."""

    @staticmethod
    def _result(run: RunRecord, detail: str) -> SubmissionResult:
        return SubmissionResult(run_id=run.run_id, status="UNAVAILABLE", detail=detail)

    def submit_run(self, *, run: RunRecord, principal: Principal) -> SubmissionResult:
        return self._result(run, "no execution backend is configured; Step28 owns durable execution")

    def cancel_run(self, *, run: RunRecord, principal: Principal) -> SubmissionResult:
        return self._result(run, "no execution backend is configured; cancellation is not accepted")

    def resume_run(self, *, run: RunRecord, principal: Principal) -> SubmissionResult:
        return self._result(run, "no execution backend is configured; resume is not accepted")


@dataclass(frozen=True)
class PageResult:
    items: tuple[Any, ...]
    page_size: int
    offset: int
    next_offset: int | None
    order_by: str


def _safe_key(key: str) -> str:
    try:
        IdempotencyRecord(
            scope="validation",
            key=key,
            request_fingerprint="0" * 64,
            response_status=200,
        )
    except ValueError as exc:
        raise BackendError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key has an unsafe shape", status=400) from exc
    return key


def _safe_filter(value: str | None, *, name: str) -> str | None:
    if value is not None and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) or ".." in value):
        raise BackendError("INVALID_FILTER", f"{name} filter has an unsafe or unsupported shape", status=400)
    return value


class BackendService:
    """Use-case service for the bounded V1 backend/control-plane surface."""

    def __init__(
        self,
        *,
        control_store: ControlStorePort,
        artifact_store: ArtifactStorePort,
        execution: ExecutionSubmissionPort | None = None,
        configuration_fingerprint: str | None = None,
        max_page_size: int = 100,
        max_projection_bytes: int = 4_000_000,
    ) -> None:
        if max_page_size < 1 or max_page_size > 1000:
            raise ValueError("max_page_size must be between 1 and 1000")
        if max_projection_bytes < 1:
            raise ValueError("max_projection_bytes must be positive")
        self.control_store = control_store
        self.artifact_store = artifact_store
        self.execution = execution or UnavailableExecutionSubmission()
        self.configuration_fingerprint = configuration_fingerprint
        self.max_page_size = max_page_size
        self.max_projection_bytes = max_projection_bytes
        self.review_policy = ReviewPolicyService()
        self._mutation_lock = RLock()

    def _require_scope(self, principal: Principal, scope: str) -> None:
        if not principal.subject or scope not in principal.scopes:
            raise BackendError("FORBIDDEN", "caller is not authorized for this operation", status=403)

    def _page(self, items: tuple[Any, ...], *, page_size: int, offset: int, order_by: str) -> PageResult:
        if page_size < 1 or page_size > self.max_page_size or offset < 0:
            raise BackendError("INVALID_PAGE", "page_size exceeds the bounded API limit or offset is invalid", status=400)
        has_more = len(items) > page_size
        visible = items[:page_size]
        return PageResult(
            items=visible,
            page_size=page_size,
            offset=offset,
            next_offset=offset + page_size if has_more else None,
            order_by=order_by,
        )

    @staticmethod
    def _idempotent_response(record: IdempotencyRecord) -> dict[str, Any]:
        return dict(record.response_body)

    def _existing_idempotency(self, *, scope: str, key: str, fingerprint: str) -> IdempotencyRecord | None:
        existing = self.control_store.get_idempotency(scope=scope, key=key)
        if existing is not None and existing.request_fingerprint != fingerprint:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409)
        return existing

    def create_run(
        self,
        *,
        project_id: str,
        configuration_fingerprint: str,
        git_content_commit: str | None,
        metadata: Mapping[str, str],
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[RunRecord, bool]:
        self._require_scope(principal, "runs:write")
        key = _safe_key(idempotency_key)
        try:
            safe = safe_metadata(metadata)
        except ValueError as exc:
            raise BackendError("INVALID_METADATA", "run metadata contains a restricted field", status=422) from exc
        request = {
            "project_id": project_id,
            "configuration_fingerprint": configuration_fingerprint,
            "git_content_commit": git_content_commit,
            "metadata": dict(sorted(safe.items())),
        }
        fingerprint = idempotency_fingerprint(request)
        scope = f"run-create:{principal.subject}"
        with self._mutation_lock:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is not None:
                body = self._idempotent_response(existing)
                return RunRecord.model_validate(body["run"]), True
            if self.configuration_fingerprint is not None and configuration_fingerprint != self.configuration_fingerprint:
                raise BackendError("CONFIGURATION_CONFLICT", "run configuration does not match the configured local platform", status=409)
            run = RunRecord(
                # A distinct idempotency key represents a new run command;
                # the key participates in resource identity but not in the
                # semantic fingerprint used for changed-request detection.
                run_id=stable_id("run", {"scope": scope, "key": key, "request": request}),
                project_id=project_id,
                configuration_fingerprint=configuration_fingerprint,
                git_content_commit=git_content_commit,
                metadata=safe,
            )
            try:
                run = self.control_store.create_run(run)
                stored = IdempotencyRecord(
                    scope=scope,
                    key=key,
                    request_fingerprint=fingerprint,
                    response_status=201,
                    response_body={"run": run.model_dump(mode="json")},
                    resource_id=run.run_id,
                )
                self.control_store.record_idempotency(stored)
            except BackendError:
                raise
            except PlatformError as exc:
                raise BackendError("RUN_CREATION_REJECTED", "run could not be persisted", status=409) from exc
            return run, False

    def get_run(self, run_id: str) -> RunRecord:
        run = self.control_store.get_run(run_id)
        if run is None:
            raise BackendError("RUN_NOT_FOUND", "run was not found", status=404)
        return run

    def list_runs(self, *, project_id: str | None, status: str | None, page_size: int, offset: int) -> PageResult:
        project_id = _safe_filter(project_id, name="project_id")
        if status is not None:
            try:
                RunStatus(status)
            except ValueError as exc:
                raise BackendError("INVALID_RUN_STATUS", "status filter is not a supported run status", status=400) from exc
        rows = self.control_store.list_runs(project_id=project_id, status=status, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="created_at,run_id")

    def list_attempts(self, *, run_id: str, stage_id: str | None, status: str | None, page_size: int, offset: int) -> PageResult:
        self.get_run(run_id)
        stage_id = _safe_filter(stage_id, name="stage_id")
        if status is not None:
            try:
                StageStatus(status)
            except ValueError as exc:
                raise BackendError("INVALID_STAGE_STATUS", "status filter is not a supported stage status", status=400) from exc
        rows = self.control_store.list_stage_attempts(run_id=run_id, stage_id=stage_id, status=status, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="stage_id,attempt_number,attempt_id")

    def get_attempt(self, *, run_id: str, attempt_id: str) -> StageAttemptRecord:
        attempt = self.control_store.get_stage_attempt(attempt_id)
        if attempt is None or attempt.run_id != run_id:
            raise BackendError("ATTEMPT_NOT_FOUND", "stage attempt was not found", status=404)
        return attempt

    def _subject_key(self, context: ReviewCompatibilityContext) -> str:
        return "|".join(
            (
                context.review_checkpoint_id.value,
                context.subject_artifact_id,
                context.subject_content_hash,
                context.subject_semantic_id,
                context.applicability_fingerprint,
            )
        )

    def _check_review_subject(self, *, run_id: str, context: ReviewCompatibilityContext) -> str:
        subject = self.control_store.get_artifact(context.subject_artifact_id)
        if subject is None:
            raise BackendError("REVIEW_SUBJECT_NOT_REGISTERED", "review subject is not a registered project artifact", status=404)
        if subject.run_id != run_id or subject.content_hash != context.subject_content_hash:
            raise BackendError("REVIEW_SUBJECT_MISMATCH", "review subject is not bound to this run and content hash", status=409)
        if subject.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise BackendError("REVIEW_SUBJECT_NOT_CONSUMABLE", "review subject is not a published artifact", status=409)
        return self._subject_key(context)

    def _record_review_idempotency(self, *, scope: str, key: str, fingerprint: str, record: ReviewRecord) -> None:
        self.control_store.record_idempotency(
            IdempotencyRecord(
                scope=scope,
                key=key,
                request_fingerprint=fingerprint,
                response_status=200,
                response_body={"review": record.model_dump(mode="json")},
                resource_id=record.decision.review_decision_id,
            )
        )

    def review(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        context: ReviewCompatibilityContext,
        decision: ReviewDecisionStatus,
        rationale: str,
        expected_revision: int,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ReviewRecord, bool]:
        self._require_scope(principal, "reviews:write")
        if context.review_checkpoint_id is not checkpoint:
            raise BackendError("WRONG_REVIEW_CHECKPOINT", "review context checkpoint does not match the API resource", status=422)
        if expected_revision < 0:
            raise BackendError("INVALID_REVIEW_REVISION", "expected_revision must be non-negative", status=422)
        if decision in {ReviewDecisionStatus.INVALIDATED}:
            raise BackendError("INVALID_REVIEW_ACTION", "invalidated is a lifecycle result, not a generic review action", status=422)
        key = _safe_key(idempotency_key)
        run = self.get_run(run_id)
        subject_key = self._check_review_subject(run_id=run_id, context=context)
        scope = f"review:{run_id}:{subject_key}:{principal.subject}"
        request = {
            "run_id": run_id,
            "checkpoint": checkpoint.value,
            "context": context.model_dump(mode="json"),
            "decision": decision.value,
            "rationale": rationale,
            "expected_revision": expected_revision,
        }
        fingerprint = idempotency_fingerprint(request)
        with self._mutation_lock:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is not None:
                return ReviewRecord.model_validate(self._idempotent_response(existing)["review"]), True
            current = self.control_store.get_current_review(run_id=run_id, subject_key=subject_key)
            actual_revision = 0 if current is None else current.revision
            if actual_revision != expected_revision:
                raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409)
            try:
                review_decision = self.review_policy.create_decision(
                    context,
                    decision=decision,
                    actor=principal.subject,
                    actor_source=principal.source,
                    rationale=rationale,
                )
                record = ReviewRecord(run_id=run_id, subject_key=subject_key, decision=review_decision, revision=expected_revision + 1)
                record = self.control_store.record_review(record, expected_revision=expected_revision)
                self._record_review_idempotency(scope=scope, key=key, fingerprint=fingerprint, record=record)
            except BackendError:
                raise
            except ConcurrencyConflictError as exc:
                raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409) from exc
            except (ValueError, PlatformError) as exc:
                raise BackendError("REVIEW_REJECTED", "review action could not be recorded", status=422) from exc
            return record, False

    def invalidate_review(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        context: ReviewCompatibilityContext,
        reason: str,
        expected_revision: int,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ReviewRecord, bool]:
        self._require_scope(principal, "reviews:write")
        if context.review_checkpoint_id is not checkpoint:
            raise BackendError("WRONG_REVIEW_CHECKPOINT", "review context checkpoint does not match the API resource", status=422)
        key = _safe_key(idempotency_key)
        subject_key = self._check_review_subject(run_id=run_id, context=context)
        scope = f"review-invalidate:{run_id}:{subject_key}:{principal.subject}"
        fingerprint = idempotency_fingerprint({"run_id": run_id, "checkpoint": checkpoint.value, "context": context.model_dump(mode="json"), "reason": reason, "expected_revision": expected_revision})
        with self._mutation_lock:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is not None:
                return ReviewRecord.model_validate(self._idempotent_response(existing)["review"]), True
            current = self.control_store.get_current_review(run_id=run_id, subject_key=subject_key)
            if current is None:
                raise BackendError("REVIEW_NOT_FOUND", "review subject has no current decision", status=404)
            if current.revision != expected_revision:
                raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409)
            if not reason.strip():
                raise BackendError("INVALID_REVIEW_REASON", "review invalidation requires a reason", status=422)
            try:
                invalidated = self.review_policy.invalidate(current.decision, reason)
                record = ReviewRecord(run_id=run_id, subject_key=subject_key, decision=invalidated, revision=expected_revision + 1)
                record = self.control_store.record_review(record, expected_revision=expected_revision)
                self._record_review_idempotency(scope=scope, key=key, fingerprint=fingerprint, record=record)
            except ConcurrencyConflictError as exc:
                raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409) from exc
            except (ValueError, PlatformError) as exc:
                raise BackendError("REVIEW_REJECTED", "review invalidation could not be recorded", status=422) from exc
            return record, False

    def list_reviews(self, *, run_id: str, subject_key: str | None, page_size: int, offset: int) -> PageResult:
        self.get_run(run_id)
        rows = self.control_store.list_review_history(run_id=run_id, subject_key=subject_key, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="subject_key,revision")

    def _verified_artifact(self, *, run_id: str, artifact_id: str) -> ArtifactRef:
        artifact = self.control_store.get_artifact(artifact_id)
        if artifact is None or artifact.run_id != run_id:
            raise BackendError("ARTIFACT_NOT_FOUND", "artifact was not found", status=404)
        if artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise BackendError("ARTIFACT_NOT_CONSUMABLE", "artifact is not published and consumable", status=409)
        try:
            stored = self.artifact_store.stat(artifact)
            integrity = self.artifact_store.verify(artifact)
        except (KeyError, PlatformError, OSError) as exc:
            raise BackendError("ARTIFACT_INTEGRITY_FAILED", "registered artifact could not be verified", status=409) from exc
        if stored != artifact or integrity.state is not ArtifactIntegrityState.VERIFIED:
            raise BackendError("ARTIFACT_INTEGRITY_FAILED", "registered artifact could not be verified", status=409)
        return artifact

    def artifact(self, *, run_id: str, artifact_id: str) -> ArtifactRef:
        return self._verified_artifact(run_id=run_id, artifact_id=artifact_id)

    def list_artifacts(self, *, run_id: str, stage_id: str | None, artifact_kind: str | None, page_size: int, offset: int) -> PageResult:
        self.get_run(run_id)
        stage_id = _safe_filter(stage_id, name="stage_id")
        artifact_kind = _safe_filter(artifact_kind, name="artifact_kind")
        rows = self.control_store.list_artifacts(run_id=run_id, stage_id=stage_id, artifact_kind=artifact_kind, limit=page_size + 1, offset=offset)
        # The listing is metadata-only, but every returned reference is still
        # checked against the control/artifact stores before it is exposed.
        verified = tuple(self._verified_artifact(run_id=run_id, artifact_id=item.artifact_id) for item in rows)
        return self._page(verified, page_size=page_size, offset=offset, order_by="artifact_id")

    def register_artifact(self, *, run_id: str, artifact_id: str, principal: Principal) -> ArtifactRef:
        self._require_scope(principal, "artifacts:write")
        artifact = self._verified_artifact(run_id=run_id, artifact_id=artifact_id) if self.control_store.get_artifact(artifact_id) else None
        if artifact is not None:
            return artifact
        try:
            candidate = self.artifact_store.stat(artifact_id)
        except (KeyError, PlatformError) as exc:
            raise BackendError("ARTIFACT_NOT_FOUND", "artifact was not found in the configured artifact store", status=404) from exc
        if candidate.run_id != run_id or candidate.storage_mode.value != "MANAGED":
            raise BackendError("ARTIFACT_SCOPE_REJECTED", "artifact is outside the requested project run scope", status=403)
        if candidate.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise BackendError("ARTIFACT_NOT_CONSUMABLE", "only published artifacts can be registered", status=409)
        try:
            if self.artifact_store.verify(candidate).state is not ArtifactIntegrityState.VERIFIED:
                raise BackendError("ARTIFACT_INTEGRITY_FAILED", "artifact content could not be verified", status=409)
            return self.control_store.register_artifact(candidate)
        except BackendError:
            raise
        except PlatformError as exc:
            raise BackendError("ARTIFACT_REGISTRATION_REJECTED", "artifact registration was rejected", status=409) from exc

    def artifact_payload(self, *, run_id: str, artifact_id: str, principal: Principal) -> bytes:
        """Generic content serving is intentionally denied at this boundary."""

        self._require_scope(principal, "artifacts:read")
        artifact = self._verified_artifact(run_id=run_id, artifact_id=artifact_id)
        raise BackendError("ARTIFACT_PAYLOAD_NOT_EXPOSED", "generic artifact payload access is not exposed by the V1 API", status=403)

    def _read_verified_json(self, *, run_id: str, artifact_id: str) -> tuple[ArtifactRef, dict[str, Any]]:
        artifact = self._verified_artifact(run_id=run_id, artifact_id=artifact_id)
        if artifact.byte_size > self.max_projection_bytes:
            raise BackendError("PROJECTION_TOO_LARGE", "trusted projection exceeds the bounded API payload limit", status=413)
        try:
            payload = json.loads(self.artifact_store.read(artifact).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError, PlatformError) as exc:
            raise BackendError("ARTIFACT_PAYLOAD_INVALID", "registered artifact payload is not a valid project contract", status=409) from exc
        if not isinstance(payload, dict):
            raise BackendError("ARTIFACT_PAYLOAD_INVALID", "registered artifact payload is not a valid project contract", status=409)
        return artifact, payload

    def validation_view(self, *, run_id: str, artifact_id: str, visualization_id: str) -> ValidationReportVisualization:
        artifact, payload = self._read_verified_json(run_id=run_id, artifact_id=artifact_id)
        if artifact.artifact_kind != "ValidationReport":
            raise BackendError("WRONG_ARTIFACT_KIND", "artifact is not a ValidationReport", status=422)
        declared_hash = payload.pop("content_hash", None)
        try:
            report = ValidationReport.model_validate(payload)
            if declared_hash is not None and declared_hash != report.content_hash:
                raise ValueError("validation report hash mismatch")
            run = self.get_run(run_id)
            scope = VisualizationScope(
                visualization_id=visualization_id,
                visualization_version=VisualizationService.visualization_version,
                project_id=run.project_id,
                run_id=run_id,
                snapshot_id=report.bindings.source_snapshot_id,
                stage_id=artifact.stage_id,
                scope_id=artifact.artifact_id,
                scope_semantics="authoritative registered ValidationReport projection",
                provenance_refs=tuple(sorted(report.provenance_refs)),
            )
            binding = ValidationReportVisualizationBinding.from_report(report)
            return VisualizationService().build_validation_from_report(visualization_id=visualization_id, scope=scope, report=report, binding=binding)
        except (ValueError, VisualizationInputError) as exc:
            raise BackendError("VALIDATION_PROJECTION_REJECTED", "ValidationReport could not be projected through the trusted Step26 path", status=409) from exc

    def visualization_graph(self, *, run_id: str, artifact_id: str) -> VisualizationGraph:
        artifact, payload = self._read_verified_json(run_id=run_id, artifact_id=artifact_id)
        if artifact.artifact_kind != "VisualizationGraph":
            raise BackendError("WRONG_ARTIFACT_KIND", "artifact is not a VisualizationGraph", status=422)
        try:
            graph = VisualizationGraph.model_validate(payload)
        except ValueError as exc:
            raise BackendError("VISUALIZATION_PROJECTION_REJECTED", "visualization artifact does not validate as the Step26 contract", status=409) from exc
        if graph.scope.run_id != run_id:
            raise BackendError("VISUALIZATION_SCOPE_REJECTED", "visualization scope is not bound to this run", status=409)
        return graph

    def _submission(
        self,
        *,
        action: str,
        run_id: str,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[SubmissionResult, bool]:
        self._require_scope(principal, "runs:write")
        key = _safe_key(idempotency_key)
        run = self.get_run(run_id)
        scope = f"execution:{action}:{run_id}:{principal.subject}"
        fingerprint = idempotency_fingerprint({"action": action, "run_id": run_id})
        with self._mutation_lock:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is not None:
                return SubmissionResult.model_validate(self._idempotent_response(existing)["submission"]), True
            try:
                result = {
                    "submit": self.execution.submit_run,
                    "cancel": self.execution.cancel_run,
                    "resume": self.execution.resume_run,
                }[action](run=run, principal=principal)
                result = SubmissionResult.model_validate(result)
            except BackendError:
                raise
            except Exception as exc:
                raise BackendError("EXECUTION_UNAVAILABLE", "execution backend did not accept the command", status=503, retryable=True) from exc
            self.control_store.record_idempotency(
                IdempotencyRecord(
                    scope=scope,
                    key=key,
                    request_fingerprint=fingerprint,
                    response_status=503 if result.status == "UNAVAILABLE" else 202,
                    response_body={"submission": result.model_dump(mode="json")},
                    resource_id=result.submission_id or run_id,
                )
            )
            return result, False

    def submit(self, *, run_id: str, principal: Principal, idempotency_key: str) -> tuple[SubmissionResult, bool]:
        return self._submission(action="submit", run_id=run_id, principal=principal, idempotency_key=idempotency_key)

    def cancel(self, *, run_id: str, principal: Principal, idempotency_key: str) -> tuple[SubmissionResult, bool]:
        return self._submission(action="cancel", run_id=run_id, principal=principal, idempotency_key=idempotency_key)

    def resume(self, *, run_id: str, principal: Principal, idempotency_key: str) -> tuple[SubmissionResult, bool]:
        return self._submission(action="resume", run_id=run_id, principal=principal, idempotency_key=idempotency_key)


__all__ = [
    "BackendError",
    "BackendService",
    "ExecutionSubmissionPort",
    "PageResult",
    "Principal",
    "UnavailableExecutionSubmission",
]
