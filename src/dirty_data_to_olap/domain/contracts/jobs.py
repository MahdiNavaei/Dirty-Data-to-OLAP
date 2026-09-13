"""Project-owned durable job and stage-boundary contracts for Step28.

The queue is an implementation detail.  These contracts deliberately model
the durable control-plane identities and never carry source rows, provider
objects, credentials or arbitrary exception text.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .canonical import ReviewCheckpoint, ReviewCompatibilityContext
from .source import _SourceModel, stable_digest, utc_now


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SAFE_META_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_SECRET = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|credential|raw[_-]?dsn|connection[_-]?string)")
_UNSAFE_META = re.compile(r"(?i)(raw[_-]?row|stack[_-]?trace|traceback|exception[_-]?text|request[_-]?body)")


def _identity(value: str) -> str:
    if not _ID.fullmatch(value) or ".." in value or "\\" in value:
        raise ValueError("job identity has an unsafe shape")
    return value


def _safe_metadata(value: Mapping[str, str]) -> Mapping[str, str]:
    clean: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key)
        item_text = str(item)
        if not _SAFE_META_KEY.fullmatch(key_text) or _SECRET.search(key_text) or _UNSAFE_META.search(key_text):
            raise ValueError("job metadata contains an unsafe key")
        if len(item_text) > 512 or "\n" in item_text or "\r" in item_text or _SECRET.search(item_text) or _UNSAFE_META.search(item_text):
            raise ValueError("job metadata contains unsafe or oversized content")
        clean[key_text] = item_text
    return clean


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"


class JobKind(str, Enum):
    COMMAND = "COMMAND"
    STAGE = "STAGE"


class FailureClassification(str, Enum):
    RETRYABLE_TRANSIENT = "RETRYABLE_TRANSIENT"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    BLOCKED_PREREQUISITE = "BLOCKED_PREREQUISITE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CANCELLED = "CANCELLED"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"


class StageResultStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageSpec(_SourceModel):
    """One run-scoped projection of the authoritative architecture DAG."""

    stage_id: str = Field(min_length=1, max_length=128)
    required: bool = True
    dependencies: tuple[str, ...] = ()
    handler_key: str = Field(min_length=1, max_length=128)
    review_checkpoint: ReviewCheckpoint | None = None
    final_validation: bool = False
    policy_config_fingerprint: str = Field(default="step28-policy-v1", min_length=1, max_length=256)
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("stage_id", "handler_key")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _identity(value)

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not _ID.fullmatch(item) for item in value):
            raise ValueError("stage dependencies must be unique safe identities")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return _safe_metadata(value)


class ExecutionPlan(_SourceModel):
    """Durable run plan compiled from the existing stage_graph.yml DAG."""

    plan_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    graph_source: str = Field(default="docs/architecture/specs/stage_graph.yml", min_length=1, max_length=256)
    graph_version: str = Field(default="v1_runtime_stage_dag", min_length=1, max_length=128)
    stages: tuple[StageSpec, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("plan_id", "run_id")
    @classmethod
    def validate_plan_ids(cls, value: str) -> str:
        return _identity(value)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        ids = [stage.stage_id for stage in self.stages]
        if len(set(ids)) != len(ids):
            raise ValueError("execution plan stage identities must be unique")
        known = set(ids)
        for stage in self.stages:
            if stage.stage_id in stage.dependencies or any(dep not in known for dep in stage.dependencies):
                raise ValueError("execution plan contains an invalid dependency")
        pending = {stage.stage_id: set(stage.dependencies) for stage in self.stages}
        resolved: set[str] = set()
        while pending:
            ready = {stage_id for stage_id, deps in pending.items() if deps <= resolved}
            if not ready:
                raise ValueError("execution plan must be acyclic")
            resolved.update(ready)
            for stage_id in ready:
                pending.pop(stage_id)
        if sum(stage.final_validation for stage in self.stages) > 1:
            raise ValueError("execution plan may have at most one final validation stage")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version", "created_at"}))

    def stage(self, stage_id: str) -> StageSpec | None:
        return next((stage for stage in self.stages if stage.stage_id == stage_id), None)


class JobRecord(_SourceModel):
    """Safe durable job projection suitable for Step29 read/query consumers."""

    job_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    job_kind: JobKind
    command_id: str | None = Field(default=None, max_length=256)
    action: str | None = Field(default=None, max_length=32)
    stage_id: str | None = Field(default=None, max_length=128)
    parent_job_id: str | None = Field(default=None, max_length=128)
    plan_id: str | None = Field(default=None, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    status: JobStatus = JobStatus.QUEUED
    priority: int = Field(default=0, ge=-100, le=100)
    created_at: datetime = Field(default_factory=utc_now)
    available_at: datetime = Field(default_factory=utc_now)
    lease_owner: str | None = Field(default=None, max_length=128)
    lease_generation: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    delivery_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_classification: FailureClassification | None = None
    failure_reason: str | None = Field(default=None, max_length=512)
    cancellation_requested: bool = False
    cancellation_requested_at: datetime | None = None
    result_refs: tuple[str, ...] = ()
    review_context: ReviewCompatibilityContext | None = None
    metadata: Mapping[str, str] = Field(default_factory=dict)
    revision: int = Field(default=0, ge=0)

    @field_validator("job_id", "run_id")
    @classmethod
    def validate_job_ids(cls, value: str) -> str:
        return _identity(value)

    @field_validator("command_id", "stage_id", "parent_job_id", "plan_id", "attempt_id", "lease_owner")
    @classmethod
    def validate_optional_ids(cls, value: str | None) -> str | None:
        return None if value is None else _identity(value)

    @field_validator("metadata")
    @classmethod
    def validate_job_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return _safe_metadata(value)

    @field_validator("failure_reason")
    @classmethod
    def validate_failure_reason(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value or _SECRET.search(value) or len(value) > 512):
            raise ValueError("failure reason must be short, safe metadata")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> "JobRecord":
        if self.job_kind is JobKind.COMMAND and self.command_id is None:
            raise ValueError("command jobs require command_id")
        if self.job_kind is JobKind.STAGE and (self.stage_id is None or self.plan_id is None):
            raise ValueError("stage jobs require stage_id and plan_id")
        return self


class JobLease(_SourceModel):
    job_id: str
    worker_id: str
    lease_generation: int = Field(ge=1)
    leased_at: datetime
    expires_at: datetime

    _validate_job_id = field_validator("job_id", "worker_id")(_identity)


class StageExecutionRequest(_SourceModel):
    """Typed request crossing the local worker/stage boundary."""

    request_id: str
    job_id: str
    run_id: str
    stage_id: str
    attempt_id: str
    plan_id: str
    configuration_fingerprint: str = Field(min_length=1)
    policy_config_fingerprint: str = Field(min_length=1)
    input_artifact_refs: tuple[str, ...] = ()
    cancellation_token_id: str = Field(min_length=1)
    metadata: Mapping[str, str] = Field(default_factory=dict)

    _validate_request_ids = field_validator("request_id", "job_id", "run_id", "stage_id", "attempt_id", "plan_id", "cancellation_token_id")(_identity)

    @field_validator("metadata")
    @classmethod
    def validate_request_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return _safe_metadata(value)


class StageExecutionResult(_SourceModel):
    """Typed result returned by a project-owned stage executor."""

    status: StageResultStatus
    output_artifact_refs: tuple[str, ...] = ()
    failure_code: str | None = Field(default=None, max_length=128)
    failure_classification: FailureClassification | None = None
    failure_reason: str | None = Field(default=None, max_length=512)
    review_context: ReviewCompatibilityContext | None = None
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_result_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return _safe_metadata(value)

    @field_validator("failure_reason")
    @classmethod
    def validate_result_reason(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value or _SECRET.search(value) or len(value) > 512):
            raise ValueError("failure reason must be safe metadata")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> "StageExecutionResult":
        if self.status is StageResultStatus.NEEDS_REVIEW and self.review_context is None:
            raise ValueError("NEEDS_REVIEW requires an authoritative review context")
        if self.status in {StageResultStatus.BLOCKED, StageResultStatus.FAILED} and not (self.failure_code and self.failure_classification):
            raise ValueError("blocked or failed stage results require a classified failure")
        if self.failure_classification is FailureClassification.NEEDS_REVIEW and self.status is not StageResultStatus.NEEDS_REVIEW:
            raise ValueError("NEEDS_REVIEW classification must use NEEDS_REVIEW status")
        return self


class RetryPolicy(_SourceModel):
    max_retries: int = Field(default=2, ge=0, le=10)
    base_delay_seconds: int = Field(default=1, ge=0, le=3600)
    max_delay_seconds: int = Field(default=60, ge=0, le=86400)

    def delay_for(self, retry_count: int) -> int:
        return min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, retry_count - 1)))


__all__ = [
    "ExecutionPlan",
    "FailureClassification",
    "JobKind",
    "JobLease",
    "JobRecord",
    "JobStatus",
    "RetryPolicy",
    "StageExecutionRequest",
    "StageExecutionResult",
    "StageResultStatus",
    "StageSpec",
]
