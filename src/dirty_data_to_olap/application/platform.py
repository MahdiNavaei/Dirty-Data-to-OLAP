"""Ports and provider-neutral services for the local data platform.

These protocols keep application code independent from SQLite, pathlib and
object-storage SDKs.  The local adapters live under ``adapters.platform``.
"""

from __future__ import annotations

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
    CleanupPlan,
    CleanupResult,
    GateEvidence,
    IntegrityScanResult,
    ReproducibilityManifest,
    ResourceBudget,
    RunRecord,
    StageAttemptRecord,
    StagedDatasetManifest,
)


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

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None) -> tuple[ArtifactRef, ...]:
        ...

    def delete(self, artifact: ArtifactRef | str, authorization: CleanupAuthorization) -> None:
        ...


@runtime_checkable
class ControlStorePort(Protocol):
    """Durable typed metadata boundary; raw datasets never cross this port."""

    @property
    def schema_version(self) -> int:
        ...

    def create_run(self, run: RunRecord) -> RunRecord:
        ...

    def get_run(self, run_id: str) -> RunRecord | None:
        ...

    def update_run(self, run: RunRecord, *, expected_revision: int) -> RunRecord:
        ...

    def create_stage_attempt(self, attempt: StageAttemptRecord) -> StageAttemptRecord:
        ...

    def get_stage_attempt(self, attempt_id: str) -> StageAttemptRecord | None:
        ...

    def update_stage_attempt(self, attempt: StageAttemptRecord, *, expected_revision: int) -> StageAttemptRecord:
        ...

    def register_artifact(self, artifact: ArtifactRef) -> ArtifactRef:
        ...

    def register_artifact_with_dependencies(self, artifact: ArtifactRef, dependencies: Sequence[ArtifactDependency]) -> ArtifactRef:
        ...

    def get_artifact(self, artifact_id: str) -> ArtifactRef | None:
        ...

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None) -> tuple[ArtifactRef, ...]:
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

    def record_gate_evidence(self, evidence: GateEvidence) -> GateEvidence:
        ...

    def get_gate_evidence(self, gate_id: str, *, run_id: str | None = None) -> GateEvidence | None:
        ...

    def record_staged_dataset(self, manifest: StagedDatasetManifest) -> StagedDatasetManifest:
        ...

    def get_staged_dataset(self, dataset_id: str, dataset_version: str) -> StagedDatasetManifest | None:
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

    def execute_cleanup(self, plan: CleanupPlan, authorization: CleanupAuthorization, *, control_store: ControlStorePort, artifact_store: ArtifactStorePort) -> CleanupResult:
        if plan.dry_run:
            result = CleanupResult(plan_id=plan.plan_id, executed=False, retained_artifact_ids=tuple(item.artifact_id for item in plan.candidates), detail="dry-run plan did not delete anything")
            record_event = getattr(control_store, "record_audit_event", None)
            if callable(record_event):
                record_event("cleanup_executed", run_id=plan.run_id, status="DRY_RUN", detail=plan.plan_id)
            return result
        if not authorization.authorized or authorization.plan_id != plan.plan_id:
            raise CleanupAuthorizationError("cleanup requires authorization for the exact plan")
        deleted: list[str] = []
        retained: list[str] = []
        rejected: list[str] = []
        for candidate in plan.candidates:
            artifact = control_store.get_artifact(candidate.artifact_id)
            run = control_store.get_run(artifact.run_id) if artifact is not None else None
            if artifact is None or artifact.retention_class.value == "PINNED_GATE_EVIDENCE" or (run is not None and run.status.value in self._ACTIVE_RUN_STATES) or control_store.get_dependents(candidate.artifact_id):
                rejected.append(candidate.artifact_id)
                continue
            if artifact.storage_mode.value == "EXTERNAL" or candidate.planned_action.value == "RETAIN_EXTERNAL_REFERENCE":
                retained.append(candidate.artifact_id)
                continue
            try:
                artifact_store.delete(artifact, authorization)
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
