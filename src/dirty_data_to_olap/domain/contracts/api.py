"""Project-owned contracts for the Step27 backend boundary.

These contracts are transport-neutral.  HTTP framework models live at the edge;
these types describe the safe metadata exchanged between the backend service,
the control store and an execution submission port.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

from pydantic import Field, field_validator

from .canonical import ReviewDecision
from .source import _SourceModel, stable_digest, utc_now


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SECRET = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|credential)")


def _safe_key(value: str) -> str:
    if not _KEY.fullmatch(value) or ".." in value or "\\" in value:
        raise ValueError("idempotency key has an unsafe shape")
    return value


class IdempotencyRecord(_SourceModel):
    """Safe replay metadata; no raw request body is persisted."""

    scope: str = Field(min_length=1, max_length=256)
    key: str = Field(min_length=1, max_length=128)
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_status: int = Field(ge=100, le=599)
    response_body: Mapping[str, Any] = Field(default_factory=dict)
    resource_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    _validate_key = field_validator("key")(_safe_key)

    @property
    def identity(self) -> str:
        return f"{self.scope}:{self.key}"


class ReviewRecord(_SourceModel):
    """A current review projection with an immutable revision number."""

    run_id: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    decision: ReviewDecision
    revision: int = Field(ge=1)
    recorded_at: datetime = Field(default_factory=utc_now)


class ReviewHistoryRecord(_SourceModel):
    """One append-only review action."""

    history_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    decision: ReviewDecision
    revision: int = Field(ge=1)
    recorded_at: datetime = Field(default_factory=utc_now)


class SubmissionResult(_SourceModel):
    """Truthful handoff result; QUEUED is intentionally not a V1 state."""

    run_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(ACCEPTED|REJECTED|UNAVAILABLE|CONFLICT|REVIEW_REQUIRED|BLOCKED)$")
    submission_id: str | None = None
    detail: str = Field(min_length=1)
    accepted_by: str | None = None


def idempotency_fingerprint(payload: Mapping[str, Any]) -> str:
    """Hash semantic request fields only, with deterministic JSON encoding."""

    return stable_digest(payload)


def safe_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    """Reject secret-shaped metadata before it reaches the control store."""

    result: dict[str, str] = {}
    for key, value in metadata.items():
        if _SECRET.search(str(key)) or _SECRET.search(str(value)):
            raise ValueError("metadata contains a restricted secret-shaped field")
        result[str(key)] = str(value)
    return result


__all__ = [
    "IdempotencyRecord",
    "ReviewHistoryRecord",
    "ReviewRecord",
    "SubmissionResult",
    "idempotency_fingerprint",
    "safe_metadata",
]
