"""Reusable stage-scoped review and compatibility policy service."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from dirty_data_to_olap.domain.contracts.canonical import (
    ReviewCompatibilityContext,
    ReviewDecision,
    ReviewDecisionStatus,
)
from dirty_data_to_olap.domain.contracts.source import stable_id, utc_now


class ReviewCompatibilityError(ValueError):
    """A review artifact cannot authorize the requested downstream subject."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("incompatible review decision: " + ",".join(self.errors))


class ReviewPolicyService:
    """The single semantic authority for review decisions and replay."""

    def create_decision(
        self,
        context: ReviewCompatibilityContext,
        *,
        decision: ReviewDecisionStatus,
        actor: str,
        actor_source: str = "PROJECT_REVIEWER",
        rationale: str,
        reviewed_at: datetime | None = None,
    ) -> ReviewDecision:
        decision = ReviewDecisionStatus(decision)
        return ReviewDecision(
            review_decision_id=stable_id("rdec", {"context": context.model_dump(mode="json"), "decision": decision.value, "actor": actor, "rationale": rationale}),
            review_checkpoint_id=context.review_checkpoint_id,
            subject_stage=context.subject_stage,
            subject_artifact_id=context.subject_artifact_id,
            subject_content_hash=context.subject_content_hash,
            subject_schema_version=context.subject_schema_version,
            model_version=context.model_version,
            source_schema_fingerprints=dict(context.source_schema_fingerprints),
            policy_version=context.policy_version,
            domain_assertion_refs=tuple(sorted(context.domain_assertion_refs)),
            subject_semantic_id=context.subject_semantic_id,
            applicability_fingerprint=context.applicability_fingerprint,
            decision=decision,
            reviewed_at=reviewed_at or utc_now(),
            actor=actor,
            actor_source=actor_source,
            rationale=rationale,
        )

    def require_compatible(self, decision: ReviewDecision, context: ReviewCompatibilityContext) -> ReviewDecision:
        errors = decision.compatibility_errors(context)
        if errors:
            raise ReviewCompatibilityError(errors)
        return decision

    def invalidate(self, decision: ReviewDecision, reason: str) -> ReviewDecision:
        if not reason.strip():
            raise ValueError("invalidation requires a reason")
        return decision.model_copy(update={"decision": ReviewDecisionStatus.INVALIDATED, "invalidation_reason": reason})
