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
from .product import ProductPolicyBinding
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


class ReplaySafety(str, Enum):
    """The durable contract used when a handler delivery outcome is unknown."""

    REPLAY_SAFE = "REPLAY_SAFE"
    RECONCILIATION_REQUIRED_ON_UNKNOWN = "RECONCILIATION_REQUIRED_ON_UNKNOWN"


class DeliveryPhase(str, Enum):
    """Durable delivery markers surrounding an external stage handler call."""

    NOT_STARTED = "NOT_STARTED"
    ATTEMPT_CREATED = "ATTEMPT_CREATED"
    HANDLER_DELIVERY_STARTED = "HANDLER_DELIVERY_STARTED"
    RESULT_RECORDED = "RESULT_RECORDED"
    FINALIZED = "FINALIZED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class PlanPreparationStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class ExecutionPlanPhase(str, Enum):
    """Durable lifecycle of a plan while runtime truth becomes available."""

    BOOTSTRAP = "BOOTSTRAP"
    SOURCE_RESOLVED = "SOURCE_RESOLVED"
    COMPLETE = "COMPLETE"


class ExecutionPlanIntent(_SourceModel):
    """Bounded caller intent; it contains no selection authority."""

    product_policy_id: str = Field(default="order", pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")
    product_policy_version: str = Field(default="order-product-v1", min_length=1, max_length=128)
    product_policy_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    cross_source_mapping_requested: bool | None = None
    entity_resolution_requested: bool | None = None
    optional_semantic_evidence_enabled: bool = False
    learned_evidence_enabled: bool = False

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version"}))


class StageSelectionDecision(_SourceModel):
    """Run-specific policy evidence for one conditional stage."""

    stage_id: str = Field(min_length=1, max_length=128)
    selected: bool
    policy_ref: str = Field(min_length=1, max_length=256)
    evidence_ref: str | None = Field(default=None, max_length=256)
    reason: str = Field(min_length=1, max_length=512)
    scope: str = Field(min_length=1, max_length=256)
    scope_fingerprint: str = Field(min_length=1, max_length=256)

    @field_validator("stage_id")
    @classmethod
    def validate_stage_id(cls, value: str) -> str:
        return _identity(value)


class ExecutionPlanSelection(_SourceModel):
    """Explicit selection evidence bound to one run and its planning scope."""

    run_id: str = Field(min_length=1, max_length=128)
    policy_ref: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=256)
    scope_fingerprint: str = Field(min_length=1, max_length=256)
    decisions: tuple[StageSelectionDecision, ...] = ()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _identity(value)

    @model_validator(mode="after")
    def unique_stage_decisions(self) -> "ExecutionPlanSelection":
        stage_ids = [item.stage_id for item in self.decisions]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("execution plan selection decisions must be unique by stage")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version"}))

    def decision_for(self, stage_id: str) -> StageSelectionDecision | None:
        return next((item for item in self.decisions if item.stage_id == stage_id), None)


class ExecutionPlanPreparation(_SourceModel):
    """Truthful result of the application-owned plan preparation boundary."""

    run_id: str = Field(min_length=1, max_length=128)
    status: PlanPreparationStatus
    plan_id: str | None = Field(default=None, max_length=128)
    selection_fingerprint: str = Field(min_length=1, max_length=256)
    unresolved_stage_ids: tuple[str, ...] = ()
    planning_phase: ExecutionPlanPhase = ExecutionPlanPhase.COMPLETE
    detail: str = Field(min_length=1, max_length=512)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _identity(value)

    @model_validator(mode="after")
    def ready_has_plan(self) -> "ExecutionPlanPreparation":
        if self.status is PlanPreparationStatus.READY and not self.plan_id:
            raise ValueError("ready plan preparation requires a plan identity")
        if self.status is PlanPreparationStatus.BLOCKED and not self.unresolved_stage_ids:
            raise ValueError("blocked plan preparation requires unresolved stage identities")
        return self


class StageSpec(_SourceModel):
    """One run-scoped projection of the authoritative architecture DAG."""

    stage_id: str = Field(min_length=1, max_length=128)
    required: bool = True
    conditional: bool = False
    selected: bool = True
    selection_reason: str = Field(default="selected by the authoritative execution plan", min_length=1, max_length=256)
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    conditional_dependencies: tuple[str, ...] = ()
    handler_key: str = Field(min_length=1, max_length=128)
    review_checkpoint: ReviewCheckpoint | None = None
    required_review_checkpoint: ReviewCheckpoint | None = None
    replay_safety: ReplaySafety = ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN
    source_scope: str | None = Field(default=None, min_length=1, max_length=256)
    final_validation: bool = False
    policy_config_fingerprint: str = Field(default="step28-policy-v1", min_length=1, max_length=256)
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("stage_id", "handler_key")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _identity(value)

    @field_validator("dependencies", "optional_dependencies", "conditional_dependencies")
    @classmethod
    def validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not _ID.fullmatch(item) for item in value):
            raise ValueError("stage dependencies must be unique safe identities")
        return value

    @model_validator(mode="after")
    def validate_selection(self) -> "StageSpec":
        if self.conditional and not self.selection_reason.strip():
            raise ValueError("conditional stage selection must carry an explicit reason")
        if self.required and not self.selected:
            raise ValueError("required stages cannot be omitted from an execution plan")
        return self

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
    selection: ExecutionPlanSelection | None = None
    success_guard_required: bool = False
    planning_phase: ExecutionPlanPhase = ExecutionPlanPhase.COMPLETE
    planning_intent: ExecutionPlanIntent | None = None
    product_policy: ProductPolicyBinding | None = None
    pending_stage_ids: tuple[str, ...] = ()
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("plan_id", "run_id")
    @classmethod
    def validate_plan_ids(cls, value: str) -> str:
        return _identity(value)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        if self.product_policy is not None and self.planning_intent is not None:
            if self.product_policy.product_id != self.planning_intent.product_policy_id or self.product_policy.version != self.planning_intent.product_policy_version:
                raise ValueError("execution plan product policy does not match its planning intent")
            if self.planning_intent.product_policy_fingerprint is not None and self.product_policy.content_fingerprint != self.planning_intent.product_policy_fingerprint:
                raise ValueError("execution plan product policy fingerprint does not match its planning intent")
        ids = [stage.stage_id for stage in self.stages]
        if len(set(ids)) != len(ids):
            raise ValueError("execution plan stage identities must be unique")
        if len(set(self.pending_stage_ids)) != len(self.pending_stage_ids) or any(not _ID.fullmatch(item) for item in self.pending_stage_ids):
            raise ValueError("pending plan stage identities must be unique safe identities")
        if self.planning_phase is ExecutionPlanPhase.COMPLETE and self.pending_stage_ids:
            raise ValueError("complete execution plans cannot retain pending stage decisions")
        known = set(ids)
        conditional_ids = {stage.stage_id for stage in self.stages if stage.conditional}
        if conditional_ids and self.selection is None:
            raise ValueError("conditional execution stages require explicit run-specific selection evidence")
        if self.selection is not None:
            if self.selection.run_id != self.run_id:
                raise ValueError("execution plan selection is bound to a different run")
            selected_ids = {item.stage_id for item in self.selection.decisions}
            if selected_ids != conditional_ids:
                raise ValueError("execution plan selection must resolve every conditional stage exactly once")
            if any(
                item.policy_ref != self.selection.policy_ref
                or item.scope != self.selection.scope
                or item.scope_fingerprint != self.selection.scope_fingerprint
                for item in self.selection.decisions
            ):
                raise ValueError("stage selection decisions must use the enclosing policy and scope")
            for stage in self.stages:
                if not stage.conditional:
                    continue
                decision = self.selection.decision_for(stage.stage_id)
                if decision is None or decision.selected != stage.selected or decision.reason != stage.selection_reason:
                    raise ValueError("stage selection does not match its authoritative selection evidence")
        for stage in self.stages:
            if stage.stage_id in stage.dependencies or any(dep not in known for dep in stage.dependencies):
                raise ValueError("execution plan contains an invalid dependency")
            if stage.selected:
                for dependency_id in stage.dependencies:
                    dependency = self.stage(dependency_id)
                    if dependency is None or not dependency.selected:
                        raise ValueError("selected stages cannot depend on an unselected hard dependency")
                if stage.required_review_checkpoint is not None:
                    checkpoint_stage = next(
                        (candidate for candidate in self.stages if candidate.review_checkpoint is stage.required_review_checkpoint),
                        None,
                    )
                    if checkpoint_stage is None or not checkpoint_stage.selected:
                        raise ValueError("selected stages require a selected review checkpoint guard")
            for dependency_id in stage.conditional_dependencies:
                if dependency_id not in known:
                    raise ValueError("conditional dependencies must be represented in the execution plan")
        pending = {
            stage.stage_id: set(
                dep for dep in (*stage.dependencies, *stage.optional_dependencies, *stage.conditional_dependencies) if dep in known
            )
            for stage in self.stages
        }
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
        if self.success_guard_required and not any(stage.final_validation and stage.required and stage.selected for stage in self.stages):
            raise ValueError("successful execution plans require a selected final validation stage")
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
    review_contexts: tuple[ReviewCompatibilityContext, ...] = ()
    delivery_phase: DeliveryPhase = DeliveryPhase.NOT_STARTED
    replay_safety: ReplaySafety = ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN
    source_scope: str | None = None
    durable_result: Mapping[str, Any] | None = None
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
    review_contexts: tuple[ReviewCompatibilityContext, ...] = ()
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
        if self.status is StageResultStatus.NEEDS_REVIEW and self.review_context is None and not self.review_contexts:
            raise ValueError("NEEDS_REVIEW requires an authoritative review context")
        if self.review_context is not None and self.review_contexts and self.review_context not in self.review_contexts:
            raise ValueError("singular review context must be one of the explicit review contexts")
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
    "ExecutionPlanIntent",
    "ExecutionPlanPreparation",
    "ExecutionPlanSelection",
    "ExecutionPlanPhase",
    "DeliveryPhase",
    "FailureClassification",
    "JobKind",
    "JobLease",
    "JobRecord",
    "JobStatus",
    "RetryPolicy",
    "ReplaySafety",
    "PlanPreparationStatus",
    "StageExecutionRequest",
    "StageExecutionResult",
    "StageResultStatus",
    "StageSelectionDecision",
    "StageSpec",
]
