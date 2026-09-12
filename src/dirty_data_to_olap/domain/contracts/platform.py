"""Project-owned contracts for the local-first data platform.

The platform contracts describe storage identity, durable metadata and
lifecycle policy.  They intentionally reference existing project artifacts by
ID and content hash instead of redefining the meaning of canonical, OLAP or
validation contracts.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from enum import Enum
from typing import Any, BinaryIO, Mapping

from pydantic import Field, field_validator, model_validator

from .source import _SourceModel, stable_digest, utc_now


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")


def _token(value: str, *, field_name: str) -> str:
    if not _TOKEN.fullmatch(value) or ".." in value or "\\" in value:
        raise ValueError(f"unsafe {field_name}")
    return value


def _hash(value: str) -> str:
    normalized = value.lower().removeprefix("sha256:")
    if not _HASH.fullmatch(normalized):
        raise ValueError("content hash must be a SHA-256 hexadecimal digest")
    return normalized


def normalize_logical_key(value: str) -> str:
    """Normalize a storage key without applying host filesystem semantics."""

    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or value.startswith("\\")
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or not _LOGICAL_KEY.fullmatch(value)
    ):
        raise ValueError("storage key must be a normalized relative POSIX key")
    return value


class ArtifactStorageMode(str, Enum):
    MANAGED = "MANAGED"
    EXTERNAL = "EXTERNAL"


class ArtifactPublicationState(str, Enum):
    RESERVED = "RESERVED"
    WRITING = "WRITING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    TOMBSTONED = "TOMBSTONED"


class ArtifactIntegrityState(str, Enum):
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    HASH_MISMATCH = "HASH_MISMATCH"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    UNREADABLE = "UNREADABLE"
    EXTERNAL_UNAVAILABLE = "EXTERNAL_UNAVAILABLE"
    NOT_CHECKED = "NOT_CHECKED"


class RetentionClass(str, Enum):
    EPHEMERAL = "EPHEMERAL"
    RUN_SCOPED = "RUN_SCOPED"
    CACHE = "CACHE"
    PERSISTENT = "PERSISTENT"
    PINNED_GATE_EVIDENCE = "PINNED_GATE_EVIDENCE"


class RunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"
    SKIPPED = "SKIPPED"


class CacheEntryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class GateEvidenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class CleanupAction(str, Enum):
    DELETE_REFERENCE = "DELETE_REFERENCE"
    DELETE_BLOB_IF_UNREFERENCED = "DELETE_BLOB_IF_UNREFERENCED"
    INVALIDATE_CACHE_POINTER = "INVALIDATE_CACHE_POINTER"
    RETAIN_EXTERNAL_REFERENCE = "RETAIN_EXTERNAL_REFERENCE"


class _PlatformModel(_SourceModel):
    """Base for platform contracts with the repository's strict defaults."""


class ArtifactDependency(_PlatformModel):
    artifact_id: str = Field(min_length=1)
    upstream_artifact_id: str = Field(min_length=1)
    expected_content_hash: str
    relationship_kind: str = Field(min_length=1)

    _validate_hash = field_validator("expected_content_hash")(_hash)


class ArtifactManifest(_PlatformModel):
    """The durable envelope used before and after artifact publication."""

    artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    storage_mode: ArtifactStorageMode = ArtifactStorageMode.MANAGED
    logical_key: str | None = None
    storage_key: str | None = None
    external_locator: str | None = None
    content_hash: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    upstream_artifact_refs: tuple[ArtifactDependency, ...] = ()
    source_snapshot_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    retention_class: RetentionClass = RetentionClass.RUN_SCOPED
    sensitivity_ref: str | None = None
    publication_state: ArtifactPublicationState = ArtifactPublicationState.RESERVED
    created_at: datetime = Field(default_factory=utc_now)
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("artifact_id", "run_id", "stage_id", "attempt_id", "artifact_kind", "producer")
    @classmethod
    def validate_tokens(cls, value: str, info: Any) -> str:
        return _token(value, field_name=info.field_name)

    @field_validator("logical_key", "storage_key")
    @classmethod
    def validate_keys(cls, value: str | None) -> str | None:
        return None if value is None else normalize_logical_key(value)

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value)

    @model_validator(mode="after")
    def validate_storage_shape(self) -> "ArtifactManifest":
        if self.storage_mode is ArtifactStorageMode.EXTERNAL and self.external_locator is None:
            raise ValueError("external artifacts require an external locator")
        if self.storage_mode is ArtifactStorageMode.MANAGED and self.external_locator is not None:
            raise ValueError("managed artifacts cannot carry an external locator")
        if self.publication_state is ArtifactPublicationState.PUBLISHED:
            if self.content_hash is None or self.byte_size is None:
                raise ValueError("published artifacts require verified size and content hash")
        return self


class ArtifactRef(_PlatformModel):
    """Stable logical reference; a host path is never its identity."""

    artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    content_hash: str
    byte_size: int = Field(ge=0)
    storage_mode: ArtifactStorageMode
    logical_key: str | None = None
    storage_key: str | None = None
    external_locator: str | None = None
    producer: str = Field(min_length=1)
    retention_class: RetentionClass
    sensitivity_ref: str | None = None
    publication_state: ArtifactPublicationState
    created_at: datetime
    provenance_refs: tuple[str, ...] = ()

    _validate_hash = field_validator("content_hash")(_hash)

    @property
    def content_sha256(self) -> str:
        return self.content_hash

    @model_validator(mode="after")
    def reference_shape(self) -> "ArtifactRef":
        if self.storage_mode is ArtifactStorageMode.MANAGED and not self.storage_key:
            raise ValueError("managed artifact references require a storage key")
        if self.storage_mode is ArtifactStorageMode.EXTERNAL and not self.external_locator:
            raise ValueError("external artifact references require a locator")
        return self


class ArtifactPublication(_PlatformModel):
    artifact_id: str = Field(min_length=1)
    content_hash: str
    byte_size: int = Field(ge=0)
    storage_key: str | None = None
    state: ArtifactPublicationState
    idempotent: bool = False
    published_at: datetime = Field(default_factory=utc_now)

    _validate_hash = field_validator("content_hash")(_hash)


class ArtifactIntegrityResult(_PlatformModel):
    artifact_id: str = Field(min_length=1)
    state: ArtifactIntegrityState
    expected_content_hash: str
    actual_content_hash: str | None = None
    expected_byte_size: int = Field(ge=0)
    actual_byte_size: int | None = Field(default=None, ge=0)
    checked_at: datetime = Field(default_factory=utc_now)
    detail: str = Field(min_length=1)

    _validate_expected_hash = field_validator("expected_content_hash")(_hash)

    @field_validator("actual_content_hash")
    @classmethod
    def validate_actual_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value)


class RunRecord(_PlatformModel):
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    status: RunStatus = RunStatus.CREATED
    configuration_fingerprint: str = Field(min_length=1)
    git_content_commit: str | None = None
    root_artifact_refs: tuple[str, ...] = ()
    source_snapshot_refs: tuple[str, ...] = ()
    gate_refs: tuple[str, ...] = ()
    latest_stage_refs: Mapping[str, str] = Field(default_factory=dict)
    revision: int = Field(default=0, ge=0)
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("run_id", "project_id")
    @classmethod
    def validate_identity(cls, value: str, info: Any) -> str:
        return _token(value, field_name=info.field_name)


class StageAttemptRecord(_PlatformModel):
    attempt_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_artifact_refs: tuple[str, ...] = ()
    output_artifact_refs: tuple[str, ...] = ()
    failure_code: str | None = None
    failure_reason: str | None = None
    policy_config_fingerprint: str = Field(min_length=1)
    resource_budget_ref: str | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator("attempt_id", "run_id", "stage_id")
    @classmethod
    def validate_identity(cls, value: str, info: Any) -> str:
        return _token(value, field_name=info.field_name)


class StagedDatasetPart(_PlatformModel):
    part_id: str = Field(min_length=1)
    logical_key: str
    artifact_ref: ArtifactRef
    row_count: int | None = Field(default=None, ge=0)
    schema_fingerprint: str = Field(min_length=1)
    sampling_scope: str | None = None

    _validate_key = field_validator("logical_key")(normalize_logical_key)


class StagedDatasetManifest(_PlatformModel):
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(min_length=1)
    format: str = Field(pattern=r"^(parquet|json|text)$")
    parts: tuple[StagedDatasetPart, ...] = Field(min_length=1)
    row_count: int | None = Field(default=None, ge=0)
    schema_metadata_ref: str | None = None
    sampling_scope: str | None = None
    provenance_refs: tuple[str, ...] = ()
    retention_class: RetentionClass = RetentionClass.RUN_SCOPED

    @field_validator("dataset_id", "dataset_version", "run_id", "source_id", "snapshot_id", "table_id")
    @classmethod
    def validate_identity(cls, value: str, info: Any) -> str:
        return _token(value, field_name=info.field_name)

    @model_validator(mode="after")
    def unique_parts(self) -> "StagedDatasetManifest":
        if len({part.part_id for part in self.parts}) != len(self.parts):
            raise ValueError("staged dataset part IDs must be unique")
        return self


class CacheInputRef(_PlatformModel):
    artifact_id: str = Field(min_length=1)
    content_hash: str

    _validate_hash = field_validator("content_hash")(_hash)


class CacheKey(_PlatformModel):
    stage_id: str = Field(min_length=1)
    component_id: str = Field(min_length=1)
    input_artifacts: tuple[CacheInputRef, ...] = ()
    contract_versions: Mapping[str, str] = Field(default_factory=dict)
    policy_version: str = Field(min_length=1)
    configuration_fingerprint: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    seed: int | None = None
    domain_scope_fingerprint: str | None = None
    review_subject_artifact_id: str | None = None
    review_subject_content_hash: str | None = None
    review_applicability_fingerprint: str | None = None

    @property
    def key_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class CacheEntry(_PlatformModel):
    cache_key_hash: str
    cache_key: CacheKey
    output_artifact_id: str = Field(min_length=1)
    output_content_hash: str
    status: CacheEntryStatus = CacheEntryStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    invalidated_reason: str | None = None

    _validate_output_hash = field_validator("output_content_hash")(_hash)

    @model_validator(mode="after")
    def key_matches(self) -> "CacheEntry":
        if self.cache_key_hash != self.cache_key.key_hash:
            raise ValueError("cache entry key hash does not match its key")
        return self


class RetentionPolicy(_PlatformModel):
    retention_class: RetentionClass
    max_age_seconds: int | None = Field(default=None, ge=0)
    pin: bool = False
    reason: str = Field(min_length=1)


class CleanupCandidate(_PlatformModel):
    artifact_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    retention_class: RetentionClass
    age_seconds: int = Field(ge=0)
    dependent_artifact_ids: tuple[str, ...] = ()
    pinned: bool = False
    byte_size: int = Field(ge=0)
    planned_action: CleanupAction


class CleanupPlan(_PlatformModel):
    plan_id: str = Field(min_length=1)
    run_id: str | None = None
    candidates: tuple[CleanupCandidate, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    dry_run: bool = True
    policy_ref: str = Field(min_length=1)


class CleanupAuthorization(_PlatformModel):
    plan_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    authorized: bool = False
    authorized_at: datetime = Field(default_factory=utc_now)


class CleanupResult(_PlatformModel):
    plan_id: str = Field(min_length=1)
    executed: bool
    deleted_artifact_ids: tuple[str, ...] = ()
    retained_artifact_ids: tuple[str, ...] = ()
    rejected_artifact_ids: tuple[str, ...] = ()
    detail: str = Field(min_length=1)


class ResourceBudget(_PlatformModel):
    max_worker_slots: int = Field(default=1, ge=1)
    memory_budget_mb: int | None = Field(default=None, gt=0)
    disk_budget_bytes: int | None = Field(default=None, gt=0)
    temporary_space_budget_bytes: int | None = Field(default=None, gt=0)
    max_staged_bytes: int | None = Field(default=None, gt=0)
    max_artifact_bytes: int | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)


class WorkspaceAllocation(_PlatformModel):
    run_id: str = Field(min_length=1)
    workspace_key: str
    artifact_key_prefix: str
    staging_key_prefix: str
    resource_budget: ResourceBudget

    _validate_workspace_key = field_validator("workspace_key", "artifact_key_prefix", "staging_key_prefix")(normalize_logical_key)


class LocalPlatformConfig(_PlatformModel):
    project_root: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)
    artifact_root: str = Field(min_length=1)
    staging_root: str = Field(min_length=1)
    control_store_path: str = Field(min_length=1)
    cache_root: str = Field(min_length=1)
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    retention_defaults: tuple[RetentionPolicy, ...] = ()

    @field_validator("project_root", "workspace_root", "artifact_root", "staging_root", "control_store_path", "cache_root")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if not os.path.isabs(value) or "\x00" in value:
            raise ValueError("platform roots must be explicit absolute paths")
        return os.path.normpath(value)

    @property
    def configuration_fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    @property
    def config_fingerprint(self) -> str:
        return self.configuration_fingerprint


class GateEvidence(_PlatformModel):
    gate_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: GateEvidenceStatus
    eligible: bool
    validation_report_artifact_id: str = Field(min_length=1)
    validation_report_content_hash: str
    policy_version: str = Field(min_length=1)
    verified_content_commit: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=utc_now)
    provenance_refs: tuple[str, ...] = ()

    _validate_report_hash = field_validator("validation_report_content_hash")(_hash)


class CapabilityQuery(_PlatformModel):
    capability_id: str = Field(min_length=1)
    requested_version: str | None = None


class CapabilityRecord(_PlatformModel):
    capability_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(REFERENCE_TESTED|CONTRACT_DEFINED|FUTURE_NOT_EXECUTED|UNAVAILABLE)$")
    implementation: str = Field(min_length=1)
    version: str = Field(min_length=1)
    checked_at: datetime = Field(default_factory=utc_now)
    detail: str = Field(min_length=1)


class DependencyResolution(_PlatformModel):
    artifact_id: str = Field(min_length=1)
    dependencies: tuple[ArtifactDependency, ...] = ()
    stale_dependency_ids: tuple[str, ...] = ()
    status: str = Field(pattern=r"^(RESOLVED|STALE_DEPENDENCY)$")


class IntegrityScanResult(_PlatformModel):
    run_id: str = Field(min_length=1)
    checked_artifact_ids: tuple[str, ...] = ()
    results: tuple[ArtifactIntegrityResult, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)

    @property
    def failed(self) -> bool:
        return any(item.state is not ArtifactIntegrityState.VERIFIED for item in self.results)


class ReproducibilityManifest(_PlatformModel):
    run_id: str = Field(min_length=1)
    configuration_fingerprint: str = Field(min_length=1)
    git_content_commit: str | None = None
    source_snapshot_refs: tuple[str, ...] = ()
    root_artifact_refs: tuple[ArtifactRef, ...] = ()
    gate_refs: tuple[str, ...] = ()
    control_schema_version: int
    generated_at: datetime = Field(default_factory=utc_now)


# Compatibility aliases for the architecture vocabulary.  They are aliases,
# not competing lifecycle models.
RunManifest = RunRecord
RunState = RunRecord
StageAttempt = StageAttemptRecord
ArtifactReference = ArtifactRef
ArtifactMetadata = ArtifactManifest
ArtifactEnvelope = ArtifactManifest
ArtifactIntegrityStatus = ArtifactIntegrityState


def artifact_payload_bytes(payload: bytes | bytearray | memoryview | BinaryIO) -> bytes:
    """Read a bounded adapter payload without introducing executable formats."""

    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    if not hasattr(payload, "read"):
        raise TypeError("artifact payload must be bytes-like or a binary stream")
    value = payload.read()
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("artifact stream must return bytes")
    return bytes(value)
