"""Reusable stage-scoped review and compatibility policy service."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from dirty_data_to_olap.domain.contracts.canonical import (
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecision,
    ReviewDecisionStatus,
    ReviewSkipAuthorization,
)
from dirty_data_to_olap.domain.contracts.evidence_fusion import RelationshipDecision, SemanticMappingDecision
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
        skip_authorization: ReviewSkipAuthorization | None = None,
    ) -> ReviewDecision:
        decision = ReviewDecisionStatus(decision)
        if decision is ReviewDecisionStatus.SKIPPED:
            if skip_authorization is None:
                raise ValueError("SKIPPED requires explicit versioned policy authorization")
            if skip_authorization.checkpoint is not context.review_checkpoint_id or skip_authorization.applicability_fingerprint != context.applicability_fingerprint:
                raise ValueError("skip authorization does not bind the guarded checkpoint/applicability")
        elif skip_authorization is not None:
            raise ValueError("skip authorization is only valid for SKIPPED decisions")
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
            skip_authorization=skip_authorization,
        )

    def require_compatible(self, decision: ReviewDecision, context: ReviewCompatibilityContext) -> ReviewDecision:
        errors = decision.compatibility_errors(context)
        if errors:
            raise ReviewCompatibilityError(errors)
        return decision

    def evidence_context(self, decision: RelationshipDecision | SemanticMappingDecision, domain_assertion_refs: tuple[str, ...]) -> ReviewCompatibilityContext:
        if isinstance(decision, RelationshipDecision):
            scope = {
                "from": {"table": decision.from_table, "columns": decision.from_columns},
                "to": {"table": decision.to_table, "columns": decision.to_columns},
            }
        else:
            scope = {
                "source": {"source_id": decision.source_id, "column_id": decision.source_column_id},
                "target": {"source_id": decision.target_source_id, "column_id": decision.target_column_id},
            }
        content_hash = stable_id("evidence-content", decision.model_dump(mode="json"))
        policy_version = f"{decision.policy.policy_id}:{decision.policy.version}"
        applicability = stable_id("evidence-applicability", {"decision": decision.decision_id, "content": content_hash, "input": decision.input_evidence_fingerprint, "scope": scope})
        return ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
            subject_stage="EVIDENCE_FUSION",
            subject_artifact_id=decision.decision_id,
            subject_content_hash=content_hash,
            subject_schema_version=decision.schema_version,
            model_version=f"evidence-fusion:{policy_version}",
            source_schema_fingerprints={"input": decision.input_evidence_fingerprint, "scope": stable_id("evidence-scope", scope)},
            policy_version=policy_version,
            domain_assertion_refs=tuple(sorted(domain_assertion_refs)),
            subject_semantic_id=decision.subject_id,
            applicability_fingerprint=applicability,
        )

    def invalidate(self, decision: ReviewDecision, reason: str) -> ReviewDecision:
        if not reason.strip():
            raise ValueError("invalidation requires a reason")
        return decision.model_copy(update={"decision": ReviewDecisionStatus.INVALIDATED, "invalidation_reason": reason})
