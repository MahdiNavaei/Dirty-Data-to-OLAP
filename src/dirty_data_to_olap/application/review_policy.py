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
from dirty_data_to_olap.domain.contracts.analytical import CompiledPlan, GeneratedSQL, AnalyticalPlan, TargetConfig
from dirty_data_to_olap.domain.contracts.source import stable_digest
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

    def evidence_context(
        self,
        decision: RelationshipDecision | SemanticMappingDecision,
        domain_assertion_refs: tuple[str, ...],
        *,
        subject_content_hash: str | None = None,
    ) -> ReviewCompatibilityContext:
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
        # The default keeps the semantic policy fingerprint useful for pure
        # domain callers.  Durable review subjects must override it with the
        # immutable artifact-store hash at the persistence boundary.
        semantic_content_hash = stable_id("evidence-content", decision.model_dump(mode="json"))
        content_hash = subject_content_hash or semantic_content_hash
        policy_version = f"{decision.policy.policy_id}:{decision.policy.version}"
        applicability = stable_id("evidence-applicability", {"decision": decision.decision_id, "content": semantic_content_hash, "input": decision.input_evidence_fingerprint, "scope": scope})
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

    def analytical_plan_context(self, plan: AnalyticalPlan, *, subject_content_hash: str | None = None) -> ReviewCompatibilityContext:
        """Bind analytical review to the exact plan and finalized canonical model."""

        semantic_scope = {
            "canonical_model_id": plan.canonical_model_id,
            "canonical_model_content_hash": plan.canonical_model_content_hash,
            "facts": plan.materialized_fact_ids,
            "dimensions": plan.materialized_dimension_ids,
            "grains": plan.grain_spec_ids,
            "measures": plan.measure_spec_ids,
            "dimension_spec_content_hashes": dict(sorted(plan.dimension_spec_content_hashes.items())),
            "fact_spec_content_hashes": dict(sorted(plan.fact_spec_content_hashes.items())),
            "grain_spec_content_hashes": dict(sorted(plan.grain_spec_content_hashes.items())),
            "measure_spec_content_hashes": dict(sorted(plan.measure_spec_content_hashes.items())),
        }
        applicability = stable_id("analytical-review-applicability", {
            "plan_id": plan.plan_id,
            "plan_content_hash": plan.content_hash,
            "canonical_model_id": plan.canonical_model_id,
            "canonical_model_content_hash": plan.canonical_model_content_hash,
            "binding": plan.input_binding_content_hash,
            "policy": plan.policy_version,
            "spec_package": plan.analytical_spec_package_hash,
        })
        return ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN,
            subject_stage="ANALYTICAL_PLANNING",
            subject_artifact_id=plan.plan_id,
            # Domain callers retain the semantic model hash by default. The
            # durable review boundary supplies the immutable artifact-store
            # hash so the API can bind the assertion to published bytes.
            subject_content_hash=subject_content_hash or plan.content_hash,
            subject_schema_version=plan.schema_version,
            model_version=plan.plan_version,
            source_schema_fingerprints={
                "canonical_model_id": plan.canonical_model_id,
                "canonical_model_content_hash": plan.canonical_model_content_hash,
                "canonical_model_fingerprint": plan.canonical_model_fingerprint,
                "analytical_spec_package_hash": plan.analytical_spec_package_hash,
                **dict(plan.source_schema_fingerprints),
            },
            policy_version=plan.policy_version,
            domain_assertion_refs=tuple(sorted(plan.domain_assertion_refs)),
            subject_semantic_id=stable_id("analytical-plan-semantic", semantic_scope),
            applicability_fingerprint=applicability,
        )

    def materialization_context(
        self,
        compiled_plan: CompiledPlan,
        generated_sql: GeneratedSQL,
        target_config: TargetConfig,
        *,
        subject_content_hash: str | None = None,
    ) -> ReviewCompatibilityContext:
        """Bind materialization approval to compiled SQL and controlled target."""

        subject_hash = stable_digest({
            "compiled_plan_hash": compiled_plan.content_hash,
            "generated_sql_hash": generated_sql.sql_hash,
            "target_config_fingerprint": target_config.config_fingerprint,
        })
        return ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN,
            subject_stage="COMPILATION",
            subject_artifact_id=compiled_plan.compiled_plan_id,
            # Domain callers retain the semantic binding by default. The
            # durable review boundary overrides this with the compiled-plan
            # artifact-store hash.
            subject_content_hash=subject_content_hash or subject_hash,
            subject_schema_version=compiled_plan.schema_version,
            model_version=compiled_plan.compiler_version,
            source_schema_fingerprints={
                "canonical_model_id": compiled_plan.canonical_model_id,
                "canonical_model_content_hash": compiled_plan.canonical_model_content_hash,
                "plan_content_hash": compiled_plan.plan_content_hash,
                "compiled_plan_content_hash": compiled_plan.content_hash,
                "generated_sql_hash": generated_sql.sql_hash,
                "target_config_fingerprint": target_config.config_fingerprint,
            },
            policy_version="materialization-policy-v1",
            domain_assertion_refs=tuple(sorted(compiled_plan.domain_assertion_refs)),
            subject_semantic_id=stable_id("materialization-semantic", {
                "plan_id": compiled_plan.plan_id,
                "compiled_plan_id": compiled_plan.compiled_plan_id,
                "dialect": compiled_plan.dialect,
                "target_type": target_config.target_type,
            }),
            applicability_fingerprint=stable_id("materialization-applicability", {
                "compiled_plan": compiled_plan.content_hash,
                "sql": generated_sql.sql_hash,
                "target": target_config.config_fingerprint,
            }),
        )
