"""Ports and provider-neutral services for the local data platform.

These protocols keep application code independent from SQLite, pathlib and
object-storage SDKs.  The local adapters live under ``adapters.platform``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BufferedIOBase
from typing import BinaryIO, Iterable, Protocol, Sequence, runtime_checkable

from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactDependency,
    ArtifactIntegrityResult,
    ArtifactIntegrityState,
    ArtifactManifest,
    ArtifactRef,
    ArtifactPublication,
    ArtifactPublicationState,
    CacheEntry,
    CacheKey,
    CapabilityQuery,
    CapabilityRecord,
    CleanupAuthorization,
    CleanupDeletionPermit,
    CleanupPlan,
    CleanupResult,
    GateEvidence,
    GateEvidenceStatus,
    IntegrityScanResult,
    ReproducibilityManifest,
    ResourceBudget,
    RunRecord,
    StageAttemptRecord,
    StagedDatasetManifest,
)
from dirty_data_to_olap.domain.contracts.api import IdempotencyRecord, ReviewHistoryRecord, ReviewRecord
from dirty_data_to_olap.domain.contracts.canonical import ReviewCompatibilityContext, ReviewDecision
from dirty_data_to_olap.domain.contracts.review_actions import ReviewActionHistoryRecord, ReviewActionRecord, ReviewActionState
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, JobRecord, StageExecutionResult
from dirty_data_to_olap.domain.contracts.validation import ValidationReport


class PlatformError(RuntimeError):
    """Base error for explicit platform failures."""


class ArtifactConflictError(PlatformError):
    """An immutable logical artifact identity was reused with new content."""


class ArtifactIntegrityError(PlatformError):
    """A claimed or stored artifact hash/size did not verify."""


class PathConfinementError(PlatformError):
    """A logical or external location leaves its configured boundary."""


class UnsupportedSchemaVersionError(PlatformError):
    """The control store declares a schema newer than this adapter supports."""


class ConcurrencyConflictError(PlatformError):
    """A compare-and-swap update observed a different row revision."""


class StaleDependencyError(PlatformError):
    """A dependency was resolved at a hash different from its pinned hash."""


class CleanupAuthorizationError(PlatformError):
    """Cleanup was attempted without a validated plan authorization."""


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Logical artifact operations; arbitrary host filesystem access is absent."""

    def reserve(self, manifest: ArtifactManifest) -> ArtifactPublication:
        ...

    def publish(self, manifest: ArtifactManifest, payload: bytes | BinaryIO) -> ArtifactRef:
        ...

    def register_external(self, manifest: ArtifactManifest, locator: str) -> ArtifactRef:
        ...

    def open(self, artifact: ArtifactRef | str) -> BinaryIO:
        ...

    def read(self, artifact: ArtifactRef | str) -> bytes:
        ...

    def exists(self, artifact: ArtifactRef | str) -> bool:
        ...

    def verify(self, artifact: ArtifactRef | str) -> ArtifactIntegrityResult:
        ...

    def stat(self, artifact: ArtifactRef | str) -> ArtifactRef:
        ...

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None, limit: int | None = None, offset: int = 0) -> tuple[ArtifactRef, ...]:
        ...

    def delete(self, artifact: ArtifactRef | str, permit: CleanupDeletionPermit) -> None:
        ...


@runtime_checkable
class ControlStorePort(Protocol):
    """Durable typed metadata boundary; raw datasets never cross this port."""

    @property
    def schema_version(self) -> int:
        ...

    def create_run(self, run: RunRecord) -> RunRecord:
        ...

    def create_run_with_idempotency(self, run: RunRecord, idempotency: IdempotencyRecord) -> tuple[RunRecord, bool]:
        """Atomically create a run and its completed replay record."""
        ...

    def get_run(self, run_id: str) -> RunRecord | None:
        ...

    def list_runs(self, *, project_id: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[RunRecord, ...]:
        ...

    def update_run(self, run: RunRecord, *, expected_revision: int) -> RunRecord:
        ...

    def create_stage_attempt(self, attempt: StageAttemptRecord) -> StageAttemptRecord:
        ...

    def get_stage_attempt(self, attempt_id: str) -> StageAttemptRecord | None:
        ...

    def list_stage_attempts(self, *, run_id: str, stage_id: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[StageAttemptRecord, ...]:
        ...

    def update_stage_attempt(self, attempt: StageAttemptRecord, *, expected_revision: int) -> StageAttemptRecord:
        ...

    # Step28 durable control-plane boundary.  These methods are deliberately
    # expressed in project-owned types; an OSS queue may deliver a wake-up,
    # but it is not authoritative for identity, leases or lifecycle state.
    def register_execution_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        ...

    def get_execution_plan(self, run_id: str) -> ExecutionPlan | None:
        ...

    def advance_execution_plan(self, plan: ExecutionPlan, *, expected_content_hash: str) -> ExecutionPlan:
        """CAS-advance a phased plan after its owning runtime stage succeeds."""
        ...

    def enqueue_execution_command(self, command: Any, run: RunRecord) -> tuple[JobRecord, bool]:
        ...

    def enqueue_stage_job(self, *, run_id: str, plan_id: str, stage_id: str, parent_job_id: str | None = None, available_at: datetime | None = None) -> JobRecord:
        ...

    def get_job(self, job_id: str) -> JobRecord | None:
        ...

    def get_stage_job(self, *, run_id: str, stage_id: str) -> JobRecord | None:
        ...

    def list_jobs(self, *, run_id: str, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[JobRecord, ...]:
        ...

    def claim_next_job(self, *, worker_id: str, now: datetime, lease_seconds: int = 30, max_active_per_run: int = 1, max_active_per_source: int = 1) -> JobRecord | None:
        ...

    def heartbeat_job(self, *, job_id: str, worker_id: str, lease_generation: int, now: datetime, lease_seconds: int = 30) -> JobRecord:
        ...

    def ensure_stage_attempt(self, *, job_id: str, worker_id: str, lease_generation: int, now: datetime) -> StageAttemptRecord:
        ...

    def mark_handler_delivery_started(self, *, job_id: str, worker_id: str, lease_generation: int, now: datetime) -> JobRecord:
        ...

    def record_stage_result(self, *, job_id: str, worker_id: str, lease_generation: int, attempt: StageAttemptRecord, result: StageExecutionResult, now: datetime) -> JobRecord:
        ...

    def finalize_stage_job(self, *, job_id: str, worker_id: str, lease_generation: int, attempt: StageAttemptRecord, result: StageExecutionResult, status: str, now: datetime, retry_count: int = 0, available_at: datetime | None = None) -> JobRecord:
        ...

    def finalize_command_job(self, *, job_id: str, worker_id: str, lease_generation: int, status: str, now: datetime, detail: str, failure_code: str | None = None, failure_classification: str | None = None) -> JobRecord:
        ...

    def request_run_cancellation(self, *, run_id: str, now: datetime) -> RunRecord:
        ...

    def resume_job(self, *, job_id: str, now: datetime) -> JobRecord:
        ...

    def register_artifact(self, artifact: ArtifactRef) -> ArtifactRef:
        ...

    def register_artifact_with_dependencies(self, artifact: ArtifactRef, dependencies: Sequence[ArtifactDependency]) -> ArtifactRef:
        ...

    def get_artifact(self, artifact_id: str) -> ArtifactRef | None:
        ...

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None, limit: int | None = None, offset: int = 0) -> tuple[ArtifactRef, ...]:
        ...

    def get_current_review(self, *, run_id: str, subject_key: str) -> ReviewRecord | None:
        ...

    def record_review(self, record: ReviewRecord, *, expected_revision: int) -> ReviewRecord:
        ...

    def record_review_with_idempotency(self, record: ReviewRecord, *, expected_revision: int, idempotency: IdempotencyRecord) -> tuple[ReviewRecord, bool]:
        """Atomically apply a review CAS mutation and its replay record."""
        ...

    def record_review_with_action_state_with_idempotency(
        self,
        record: ReviewRecord,
        state: ReviewActionState,
        *,
        expected_review_revision: int,
        expected_action_revision: int,
        idempotency: IdempotencyRecord,
    ) -> tuple[ReviewRecord, ReviewActionState, bool]:
        """Atomically persist a legacy review and synchronize its lifecycle state."""
        ...

    def list_review_history(self, *, run_id: str, subject_key: str | None = None, limit: int = 100, offset: int = 0) -> tuple[ReviewHistoryRecord, ...]:
        ...

    def get_review_action_state(self, *, run_id: str, subject_key: str) -> ReviewActionState | None:
        ...

    def record_review_action_with_idempotency(self, record: ReviewActionRecord, state: ReviewActionState, *, expected_revision: int, idempotency: IdempotencyRecord) -> tuple[ReviewActionRecord, ReviewActionState, bool]:
        """Atomically persist one consequential action and its replay record."""
        ...

    def record_review_and_action_with_idempotency(
        self,
        review: ReviewRecord | None,
        action: ReviewActionRecord,
        state: ReviewActionState,
        *,
        expected_review_revision: int,
        expected_action_revision: int,
        idempotency: IdempotencyRecord,
        requeue_checkpoint: str | None = None,
        requeue_review_contexts: tuple[ReviewCompatibilityContext, ...] | None = None,
    ) -> tuple[ReviewRecord | None, ReviewActionRecord, ReviewActionState, bool]:
        """Atomically persist a decision, action state/history and replay fence."""
        ...

    def record_review_invalidation_with_lifecycle(
        self,
        review: ReviewRecord,
        state: ReviewActionState,
        *,
        expected_review_revision: int,
        expected_action_revision: int,
        idempotency: IdempotencyRecord,
        requeue_checkpoint: str,
    ) -> tuple[ReviewRecord, ReviewActionState, bool]:
        """Atomically invalidate review, clear lock state and requeue descendants."""
        ...

    def list_review_subject_contexts(self, *, run_id: str, checkpoint: str | None = None) -> tuple[ReviewCompatibilityContext, ...]:
        ...

    def list_review_action_history(self, *, run_id: str, subject_key: str | None = None, limit: int = 100, offset: int = 0) -> tuple[ReviewActionHistoryRecord, ...]:
        ...

    def get_idempotency(self, *, scope: str, key: str) -> IdempotencyRecord | None:
        ...

    def record_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        ...

    def reserve_idempotency(self, record: IdempotencyRecord) -> tuple[IdempotencyRecord, bool]:
        """Durably reserve a replay key before an external command delivery."""
        ...

    def complete_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        ...

    def mark_idempotency_unknown(self, record: IdempotencyRecord) -> IdempotencyRecord:
        ...

    def register_review_subject_context(self, *, run_id: str, context: ReviewCompatibilityContext) -> ReviewCompatibilityContext:
        """Register context created by trusted project contract producers."""
        ...

    def get_review_subject_context(self, *, run_id: str, checkpoint: str, artifact_id: str) -> ReviewCompatibilityContext | None:
        ...

    def get_dependents(self, artifact_id: str) -> tuple[ArtifactRef, ...]:
        ...

    def resolve_dependencies(self, artifact_id: str) -> tuple[ArtifactDependency, ...]:
        ...

    def record_cache_entry(self, entry: CacheEntry) -> CacheEntry:
        ...

    def get_cache_entry(self, cache_key: CacheKey) -> CacheEntry | None:
        ...

    def invalidate_cache_entry(self, cache_key: CacheKey, *, reason: str) -> None:
        ...

    def record_gate_evidence(self, evidence: GateEvidence, *, validation_report: ValidationReport, artifact_store: ArtifactStorePort) -> GateEvidence:
        ...

    def get_gate_evidence(self, gate_id: str, *, run_id: str | None = None) -> GateEvidence | None:
        ...

    def record_staged_dataset(self, manifest: StagedDatasetManifest) -> StagedDatasetManifest:
        ...

    def get_staged_dataset(self, run_id: str, dataset_id: str, dataset_version: str) -> StagedDatasetManifest | None:
        ...

    def mark_artifact_tombstoned(self, artifact_id: str, *, plan_id: str, reason: str) -> None:
        ...

    def build_reproducibility_manifest(self, run_id: str, *, artifact_store: ArtifactStorePort) -> ReproducibilityManifest:
        ...

    def record_audit_event(self, event_type: str, *, run_id: str | None = None, artifact_id: str | None = None, status: str, content_hash: str | None = None, detail: str) -> None:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class StagingStorePort(Protocol):
    """Versioned staged-dataset organization over the artifact boundary."""

    def publish_part(self, manifest: StagedDatasetManifest, part_id: str, payload: bytes | BinaryIO, *, attempt_id: str, producer: str) -> object:
        ...

    def register_manifest(self, manifest: StagedDatasetManifest) -> StagedDatasetManifest:
        ...


@runtime_checkable
class CapabilityRegistryPort(Protocol):
    def get(self, query: CapabilityQuery) -> CapabilityRecord:
        ...


class PlatformIntegrityService:
    """Run-bounded integrity verification over registered artifacts only."""

    def scan(self, run_id: str, *, control_store: ControlStorePort, artifact_store: ArtifactStorePort, artifact_stores: Sequence[ArtifactStorePort] = ()) -> IntegrityScanResult:
        artifacts = control_store.list_artifacts(run_id=run_id)
        stores = (artifact_store, *artifact_stores)
        results = []
        for artifact in artifacts:
            for store in stores:
                try:
                    results.append(store.verify(artifact))
                    break
                except KeyError:
                    continue
            else:
                results.append(
                    ArtifactIntegrityResult(
                        artifact_id=artifact.artifact_id,
                        state=ArtifactIntegrityState.MISSING,
                        expected_content_hash=artifact.content_hash,
                        expected_byte_size=artifact.byte_size,
                        detail="artifact reference is not available in the configured stores",
                    )
                )
            result = results[-1]
            record_event = getattr(control_store, "record_audit_event", None)
            if callable(record_event) and result.state is not ArtifactIntegrityState.VERIFIED:
                record_event(
                    "artifact_verification_failed",
                    run_id=artifact.run_id,
                    artifact_id=artifact.artifact_id,
                    status=result.state.value,
                    content_hash=artifact.content_hash,
                    detail=result.detail,
                )
        return IntegrityScanResult(
            run_id=run_id,
            checked_artifact_ids=tuple(artifact.artifact_id for artifact in artifacts),
            results=tuple(results),
        )


class PlatformCacheService:
    """Resolve cache entries only after exact key and content verification."""

    def resolve(self, cache_key: CacheKey, *, control_store: ControlStorePort, artifact_store: ArtifactStorePort, artifact_stores: Sequence[ArtifactStorePort] = ()) -> ArtifactRef | None:
        entry = control_store.get_cache_entry(cache_key)
        if entry is None or entry.status.value != "ACTIVE" or entry.cache_key_hash != cache_key.key_hash:
            return None
        artifact = control_store.get_artifact(entry.output_artifact_id)
        if artifact is None or artifact.content_hash != entry.output_content_hash:
            control_store.invalidate_cache_entry(cache_key, reason="output metadata is missing or hash-bound output changed")
            return None
        integrity = None
        for store in (artifact_store, *artifact_stores):
            try:
                integrity = store.verify(artifact)
                break
            except KeyError:
                continue
        if integrity is None:
            integrity = ArtifactIntegrityResult(
                artifact_id=artifact.artifact_id,
                state=ArtifactIntegrityState.MISSING,
                expected_content_hash=artifact.content_hash,
                expected_byte_size=artifact.byte_size,
                detail="cached artifact reference is unavailable in the configured stores",
            )
        if integrity.state is not ArtifactIntegrityState.VERIFIED:
            control_store.invalidate_cache_entry(cache_key, reason=f"output integrity is {integrity.state.value}")
            return None
        return artifact


class GateEvidenceService:
    """Persist G6 only from a verified, typed ValidationReport artifact."""

    @staticmethod
    def _decode_report(payload: bytes) -> ValidationReport:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError("registered ValidationReport bytes are not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ArtifactIntegrityError("registered ValidationReport payload must be a JSON object")
        declared_hash = value.pop("content_hash", None)
        try:
            report = ValidationReport.model_validate(value)
        except Exception as exc:
            raise ArtifactIntegrityError("registered ValidationReport does not validate as the project contract") from exc
        if declared_hash is not None and declared_hash != report.content_hash:
            raise ArtifactIntegrityError("registered ValidationReport semantic hash does not match its payload")
        return report

    def record_from_validation_report(
        self,
        *,
        gate_id: str,
        platform_run_id: str,
        report: ValidationReport,
        report_artifact: ArtifactRef,
        verified_content_commit: str,
        control_store: ControlStorePort,
        artifact_store: ArtifactStorePort,
        provenance_refs: Sequence[str] = (),
    ) -> GateEvidence:
        if gate_id != "G6_DATA_CORRECTNESS":
            raise PlatformError("the ValidationReport-backed platform receipt is scoped to G6_DATA_CORRECTNESS")
        registered = control_store.get_artifact(report_artifact.artifact_id)
        if registered is None:
            raise PlatformError("gate evidence requires the exact registered ValidationReport artifact")
        if registered != report_artifact:
            raise ArtifactConflictError("gate evidence artifact reference is not the registered immutable reference")
        if registered.artifact_kind != "ValidationReport":
            raise PlatformError("gate evidence requires artifact_kind=ValidationReport")
        if registered.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise PlatformError("gate evidence requires a published ValidationReport")
        try:
            stored = artifact_store.stat(registered)
        except (KeyError, PlatformError) as exc:
            raise ArtifactIntegrityError("gate evidence artifact is not present in the configured artifact store") from exc
        if stored != registered:
            raise ArtifactConflictError("gate evidence artifact metadata differs between control and artifact stores")
        integrity = artifact_store.verify(registered)
        if integrity.state is not ArtifactIntegrityState.VERIFIED:
            raise ArtifactIntegrityError("gate evidence requires verified ValidationReport bytes")
        parsed = self._decode_report(artifact_store.read(registered))
        if parsed != report:
            raise ArtifactIntegrityError("typed ValidationReport does not correspond to the registered artifact bytes")
        refs = tuple(dict.fromkeys((*provenance_refs, f"validation-report:{report.report_id}", f"validation-run:{report.run_id}")))
        evidence = GateEvidence(
            gate_id=gate_id,
            run_id=platform_run_id,
            status=GateEvidenceStatus(report.g6_status.value),
            eligible=report.g6_eligible,
            validation_report_artifact_id=registered.artifact_id,
            validation_report_run_id=report.run_id,
            validation_report_id=report.report_id,
            validation_report_content_hash=registered.content_hash,
            policy_version=report.policy.policy_version,
            verified_content_commit=verified_content_commit,
            provenance_refs=refs,
        )
        return control_store.record_gate_evidence(evidence, validation_report=report, artifact_store=artifact_store)


class PlatformLifecycleService:
    """Plan-first cleanup and durable tombstone coordination."""

    _ACTIVE_RUN_STATES = {"CREATED", "RUNNING", "NEEDS_REVIEW"}

    def build_cleanup_plan(self, *, control_store: ControlStorePort, run_id: str | None, policy: Sequence, now: datetime | None = None) -> CleanupPlan:
        from dirty_data_to_olap.domain.contracts.platform import (
            CleanupAction,
            CleanupCandidate,
            CleanupPlan,
            RetentionClass,
        )

        checked_at = now or datetime.now(timezone.utc)
        policy_ref = policy[0].reason if policy else "platform-default"
        run = control_store.get_run(run_id) if run_id else None
        if run is not None and run.status.value in self._ACTIVE_RUN_STATES:
            plan = CleanupPlan(plan_id=f"cleanup_{run_id}_active", run_id=run_id, policy_ref=policy_ref, dry_run=True)
            record_event = getattr(control_store, "record_audit_event", None)
            if callable(record_event):
                record_event("cleanup_planned", run_id=run_id, status="ACTIVE_RUN_PROTECTED", detail=plan.plan_id)
            return plan
        policies = {item.retention_class: item for item in policy if item.retention_class is not RetentionClass.PERSISTENT}
        candidates: list[CleanupCandidate] = []
        for artifact in control_store.list_artifacts(run_id=run_id):
            if artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
                continue
            if artifact.retention_class.value in {"PERSISTENT", "PINNED_GATE_EVIDENCE"}:
                continue
            retention = policies.get(artifact.retention_class)
            if retention is None or retention.pin or retention.max_age_seconds is None:
                continue
            age = max(0, int((checked_at - artifact.created_at).total_seconds()))
            if age < retention.max_age_seconds:
                continue
            dependents = control_store.get_dependents(artifact.artifact_id)
            if dependents or artifact.retention_class is RetentionClass.PINNED_GATE_EVIDENCE or artifact.storage_mode.value == "EXTERNAL":
                continue
            candidates.append(CleanupCandidate(
                artifact_id=artifact.artifact_id,
                expected_content_hash=artifact.content_hash,
                reason=retention.reason,
                retention_class=artifact.retention_class,
                age_seconds=age,
                dependent_artifact_ids=tuple(item.artifact_id for item in dependents),
                pinned=False,
                byte_size=artifact.byte_size,
                planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED,
            ))
        plan = CleanupPlan(
            plan_id=f"cleanup_{run_id or 'platform'}_{int(checked_at.timestamp())}",
            run_id=run_id,
            candidates=tuple(candidates),
            created_at=checked_at,
            dry_run=True,
            policy_ref=policy_ref,
        )
        record_event = getattr(control_store, "record_audit_event", None)
        if callable(record_event):
            record_event("cleanup_planned", run_id=run_id, status="DRY_RUN", detail=f"{plan.plan_id}; candidates={len(plan.candidates)}")
        return plan

    def execute_cleanup(self, plan: CleanupPlan, authorization: CleanupAuthorization, *, control_store: ControlStorePort, artifact_store: ArtifactStorePort, artifact_stores: Sequence[ArtifactStorePort] = ()) -> CleanupResult:
        if plan.dry_run:
            result = CleanupResult(plan_id=plan.plan_id, executed=False, retained_artifact_ids=tuple(item.artifact_id for item in plan.candidates), detail="dry-run plan did not delete anything")
            record_event = getattr(control_store, "record_audit_event", None)
            if callable(record_event):
                record_event("cleanup_executed", run_id=plan.run_id, status="DRY_RUN", detail=plan.plan_id)
            return result
        if (
            not authorization.authorized
            or authorization.plan_id != plan.plan_id
            or authorization.plan_content_hash != plan.content_hash
        ):
            raise CleanupAuthorizationError("cleanup requires authorization for the exact plan")
        deleted: list[str] = []
        retained: list[str] = []
        rejected: list[str] = []
        for candidate in plan.candidates:
            artifact = control_store.get_artifact(candidate.artifact_id)
            run = control_store.get_run(artifact.run_id) if artifact is not None else None
            dependents = tuple(sorted(item.artifact_id for item in control_store.get_dependents(candidate.artifact_id))) if artifact is not None else ()
            selected_store = None
            if artifact is not None:
                for candidate_store in (artifact_store, *artifact_stores):
                    try:
                        stored_reference = candidate_store.stat(artifact)
                    except (KeyError, PlatformError):
                        continue
                    if stored_reference == artifact:
                        selected_store = candidate_store
                        break
            if (
                artifact is None
                or selected_store is None
                or (plan.run_id is not None and artifact.run_id != plan.run_id)
                or artifact.content_hash != candidate.expected_content_hash
                or artifact.byte_size != candidate.byte_size
                or artifact.retention_class is not candidate.retention_class
                or candidate.pinned
                or tuple(sorted(candidate.dependent_artifact_ids)) != dependents
                or artifact.retention_class.value == "PINNED_GATE_EVIDENCE"
                or (run is not None and run.status.value in self._ACTIVE_RUN_STATES)
                or dependents
            ):
                rejected.append(candidate.artifact_id)
                continue
            try:
                integrity = selected_store.verify(artifact)
            except (KeyError, PlatformError):
                rejected.append(candidate.artifact_id)
                continue
            if integrity.state is not ArtifactIntegrityState.VERIFIED:
                rejected.append(candidate.artifact_id)
                continue
            if artifact.storage_mode.value == "EXTERNAL" or candidate.planned_action.value == "RETAIN_EXTERNAL_REFERENCE":
                retained.append(candidate.artifact_id)
                continue
            try:
                permit = CleanupDeletionPermit(
                    plan_id=plan.plan_id,
                    plan_content_hash=plan.content_hash,
                    run_id=plan.run_id,
                    artifact_id=candidate.artifact_id,
                    expected_content_hash=candidate.expected_content_hash,
                    expected_byte_size=candidate.byte_size,
                    retention_class=candidate.retention_class,
                    planned_action=candidate.planned_action,
                    dependent_artifact_ids=candidate.dependent_artifact_ids,
                )
                selected_store.delete(artifact, permit)
                control_store.mark_artifact_tombstoned(candidate.artifact_id, plan_id=plan.plan_id, reason=candidate.reason)
            except PlatformError:
                rejected.append(candidate.artifact_id)
            else:
                deleted.append(candidate.artifact_id)
        result = CleanupResult(
            plan_id=plan.plan_id,
            executed=True,
            deleted_artifact_ids=tuple(deleted),
            retained_artifact_ids=tuple(retained),
            rejected_artifact_ids=tuple(rejected),
            detail="cleanup execution completed with per-artifact safety checks",
        )
        record_event = getattr(control_store, "record_audit_event", None)
        if callable(record_event):
            record_event("cleanup_executed", run_id=plan.run_id, status="COMPLETED", detail=f"{plan.plan_id}; deleted={len(deleted)}; rejected={len(rejected)}")
        return result
