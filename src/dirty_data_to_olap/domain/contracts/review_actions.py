"""Typed, server-enforced human review action contracts.

Review decisions remain the execution-guard contract.  These records describe
the operation a reviewer performed around that decision without turning
labels, locks, or overrides into guard-satisfying decision states.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from .canonical import ReviewCheckpoint, ReviewDecisionStatus, ReviewCompatibilityContext
from .source import _SourceModel, stable_digest, utc_now


class ReviewAction(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    OVERRIDE = "OVERRIDE"
    LABEL = "LABEL"
    LOCK = "LOCK"
    DEFER = "DEFER"


class ReviewOverrideTarget(str, Enum):
    RELATIONSHIP_DISPOSITION = "RELATIONSHIP_DISPOSITION"
    IDENTITY_MEMBERSHIP = "IDENTITY_MEMBERSHIP"
    ANALYTICAL_MEASURE_SEMANTICS = "ANALYTICAL_MEASURE_SEMANTICS"
    MATERIALIZATION_TARGET = "MATERIALIZATION_TARGET"


class ReviewOverrideValue(str, Enum):
    RETAIN_CANDIDATE = "RETAIN_CANDIDATE"
    REQUIRE_REVISION = "REQUIRE_REVISION"
    EXCLUDE_CANDIDATE = "EXCLUDE_CANDIDATE"
    KEEP_SEPARATE = "KEEP_SEPARATE"
    MERGE_REVIEW_REQUIRED = "MERGE_REVIEW_REQUIRED"
    REQUIRE_LINKAGE_EVIDENCE = "REQUIRE_LINKAGE_EVIDENCE"
    NON_ADDITIVE = "NON_ADDITIVE"
    DUCKDB_LOCAL = "DUCKDB_LOCAL"


class ReviewLabelNamespace(str, Enum):
    EVIDENCE = "EVIDENCE"
    IDENTITY = "IDENTITY"
    ANALYTICAL = "ANALYTICAL"
    MATERIALIZATION = "MATERIALIZATION"


class ReviewLabelValue(str, Enum):
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    DO_NOT_MERGE = "DO_NOT_MERGE"
    DOMAIN_REVIEWED = "DOMAIN_REVIEWED"
    QUARANTINED = "QUARANTINED"
    NON_ADDITIVE = "NON_ADDITIVE"
    CONTROLLED_TARGET = "CONTROLLED_TARGET"


class ReviewActionApplicability(_SourceModel):
    action: ReviewAction
    available: bool
    reason_if_unavailable: str | None = None
    required_fields: tuple[str, ...] = ()
    requires_confirmation: bool = False
    downstream_effect: str = Field(min_length=1)
    supported_override_targets: tuple[ReviewOverrideTarget, ...] = ()
    supported_override_replacements: tuple[ReviewOverrideValue, ...] = ()


class ReviewOverridePayload(_SourceModel):
    target: ReviewOverrideTarget
    replacement: ReviewOverrideValue
    old_value_ref: str = Field(min_length=1, max_length=256)
    evidence_ref: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def target_value_is_bounded(self) -> "ReviewOverridePayload":
        allowed = {
            ReviewOverrideTarget.RELATIONSHIP_DISPOSITION: {
                ReviewOverrideValue.RETAIN_CANDIDATE,
                ReviewOverrideValue.REQUIRE_REVISION,
                ReviewOverrideValue.EXCLUDE_CANDIDATE,
            },
            ReviewOverrideTarget.IDENTITY_MEMBERSHIP: {
                ReviewOverrideValue.KEEP_SEPARATE,
                ReviewOverrideValue.MERGE_REVIEW_REQUIRED,
                ReviewOverrideValue.REQUIRE_LINKAGE_EVIDENCE,
            },
            ReviewOverrideTarget.ANALYTICAL_MEASURE_SEMANTICS: {ReviewOverrideValue.NON_ADDITIVE, ReviewOverrideValue.REQUIRE_REVISION},
            ReviewOverrideTarget.MATERIALIZATION_TARGET: {ReviewOverrideValue.DUCKDB_LOCAL, ReviewOverrideValue.REQUIRE_REVISION},
        }
        if self.replacement not in allowed[self.target]:
            raise ValueError("override replacement is not permitted for the selected target")
        return self


class ReviewOverrideProposal(_SourceModel):
    """Immutable, review-required replacement proposal consumed by runtime."""

    schema_version: str = "prompt04-review-override-v1"
    original_subject_artifact_id: str = Field(min_length=1)
    original_subject_content_hash: str = Field(min_length=1)
    checkpoint: ReviewCheckpoint
    subject_stage: str = Field(min_length=1)
    target: ReviewOverrideTarget
    replacement: ReviewOverrideValue
    old_value_ref: str = Field(min_length=1, max_length=256)
    evidence_ref: str | None = Field(default=None, max_length=256)
    policy_version: str = Field(min_length=1)
    state: str = Field(pattern=r"^REVIEW_REQUIRED$")

    @model_validator(mode="after")
    def bounded_runtime_target(self) -> "ReviewOverrideProposal":
        if self.target is not ReviewOverrideTarget.RELATIONSHIP_DISPOSITION:
            raise ValueError("only relationship disposition overrides are runtime-supported in Prompt04-R1")
        if self.replacement not in {ReviewOverrideValue.RETAIN_CANDIDATE, ReviewOverrideValue.EXCLUDE_CANDIDATE}:
            raise ValueError("only retain/exclude relationship dispositions are runtime-supported in Prompt04-R1")
        if self.old_value_ref != self.original_subject_artifact_id:
            raise ValueError("override old_value_ref must bind the original subject artifact")
        return self


class ReviewLabelPayload(_SourceModel):
    namespace: ReviewLabelNamespace
    value: ReviewLabelValue

    @model_validator(mode="after")
    def namespace_value_is_bounded(self) -> "ReviewLabelPayload":
        allowed = {
            ReviewLabelNamespace.EVIDENCE: {ReviewLabelValue.NEEDS_EVIDENCE, ReviewLabelValue.DOMAIN_REVIEWED, ReviewLabelValue.QUARANTINED},
            ReviewLabelNamespace.IDENTITY: {ReviewLabelValue.DO_NOT_MERGE, ReviewLabelValue.DOMAIN_REVIEWED, ReviewLabelValue.QUARANTINED},
            ReviewLabelNamespace.ANALYTICAL: {ReviewLabelValue.NON_ADDITIVE, ReviewLabelValue.DOMAIN_REVIEWED},
            ReviewLabelNamespace.MATERIALIZATION: {ReviewLabelValue.CONTROLLED_TARGET, ReviewLabelValue.DOMAIN_REVIEWED},
        }
        if self.value not in allowed[self.namespace]:
            raise ValueError("label value is not permitted in the selected namespace")
        return self


class ReviewLockPayload(_SourceModel):
    scope: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
    confirm: bool = False


class ReviewActionRecord(_SourceModel):
    action_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    checkpoint: ReviewCheckpoint
    subject_artifact_id: str = Field(min_length=1)
    subject_content_hash: str = Field(min_length=1)
    applicability_fingerprint: str = Field(min_length=1)
    action: ReviewAction
    action_payload_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_revision: int = Field(ge=0)
    resulting_revision: int = Field(ge=1)
    principal: str = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=2000)
    recorded_at: datetime = Field(default_factory=utc_now)
    original_decision: ReviewDecisionStatus | None = None
    resulting_decision: ReviewDecisionStatus | None = None
    resulting_subject_artifact_id: str | None = None
    override_target: ReviewOverrideTarget | None = None
    override_replacement: ReviewOverrideValue | None = None
    label_namespace: ReviewLabelNamespace | None = None
    label_value: ReviewLabelValue | None = None
    lock_scope: str | None = None
    downstream_effect: str = Field(min_length=1)
    next_required_action: str = Field(min_length=1)
    execution_eligible: bool = False


class ReviewActionHistoryRecord(_SourceModel):
    history_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    action: ReviewActionRecord
    revision: int = Field(ge=1)
    recorded_at: datetime = Field(default_factory=utc_now)


class ReviewActionState(_SourceModel):
    run_id: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    action_revision: int = Field(default=0, ge=0)
    locked: bool = False
    lock_scope: str | None = None
    lock_context_fingerprint: str | None = None
    labels: tuple[str, ...] = ()
    current_subject_artifact_id: str = Field(min_length=1)
    current_subject_content_hash: str = Field(min_length=1)
    last_action_id: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class ReviewActionResult(_SourceModel):
    action_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    checkpoint: ReviewCheckpoint
    subject_artifact_id: str = Field(min_length=1)
    action: ReviewAction
    replayed: bool = False
    previous_revision: int = Field(ge=0)
    resulting_revision: int = Field(ge=1)
    resulting_decision: ReviewDecisionStatus | None = None
    resulting_subject_artifact_id: str | None = None
    subject_state: str = Field(min_length=1)
    guard_satisfied: bool = False
    execution_eligible: bool = False
    downstream_effect: str = Field(min_length=1)
    next_required_action: str = Field(min_length=1)
    lock_scope: str | None = None
    labels: tuple[str, ...] = ()


def action_payload_fingerprint(payload: object) -> str:
    """Hash typed action payloads without persisting raw request bodies."""

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return stable_digest(payload)


__all__ = [
    "ReviewAction",
    "ReviewActionApplicability",
    "ReviewActionHistoryRecord",
    "ReviewActionRecord",
    "ReviewActionResult",
    "ReviewActionState",
    "ReviewLabelNamespace",
    "ReviewLabelPayload",
    "ReviewLabelValue",
    "ReviewLockPayload",
    "ReviewOverridePayload",
    "ReviewOverrideProposal",
    "ReviewOverrideTarget",
    "ReviewOverrideValue",
    "action_payload_fingerprint",
]
