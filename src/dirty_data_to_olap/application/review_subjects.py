"""Trusted derivation of real Step28 review-checkpoint subjects.

Only registered, verified project artifacts cross this boundary.  The
builders remain the semantic authority; this module only selects and decodes
their typed inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService
from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, PlatformError
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import AnalyticalPlan, CompiledPlan, GeneratedSQL, TargetConfig
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityProposal, CanonicalModelHypothesis
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionResult, RelationshipDecision, SemanticMappingDecision
from dirty_data_to_olap.domain.contracts.platform import ArtifactIntegrityState, ArtifactPublicationState, ArtifactRef
from dirty_data_to_olap.domain.contracts.source import stable_digest


@dataclass(frozen=True)
class ReviewSubjectDerivation:
    contexts: tuple[ReviewCompatibilityContext, ...] = ()
    unresolved_subject_ids: tuple[str, ...] = ()
    detail: str = ""


class ReviewSubjectDerivationPort(Protocol):
    """Application boundary for deriving checkpoint subjects from artifacts."""

    def derive(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        upstream_artifacts: tuple[ArtifactRef, ...],
    ) -> ReviewSubjectDerivation:
        ...


class ReviewCheckpointSubjectResolver:
    """Decode verified upstream contracts and call existing review builders."""

    def __init__(self, control_store: ControlStorePort, artifact_store: ArtifactStorePort) -> None:
        self.control_store = control_store
        self.artifact_store = artifact_store
        self.review_policy = ReviewPolicyService()
        self.canonical_finalization = CanonicalFinalizationService(self.review_policy)

    def derive(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        upstream_artifacts: tuple[ArtifactRef, ...],
    ) -> ReviewSubjectDerivation:
        verified: list[tuple[ArtifactRef, object]] = []
        unresolved: list[str] = []
        for supplied in upstream_artifacts:
            try:
                artifact, payload = self._read_verified(run_id, supplied)
            except (KeyError, OSError, PlatformError, ValueError):
                unresolved.append(supplied.artifact_id)
                continue
            verified.append((artifact, payload))

        if checkpoint is ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS:
            return self._evidence_contexts(verified, unresolved)
        if checkpoint is ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY:
            return self._identity_contexts(run_id, verified, unresolved)
        if checkpoint is ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN:
            contexts = tuple(
                self.review_policy.analytical_plan_context(payload)
                for artifact, payload in verified
                if artifact.artifact_kind == "AnalyticalPlan" and isinstance(payload, AnalyticalPlan) and payload.plan_id == artifact.artifact_id
            )
            return ReviewSubjectDerivation(contexts=contexts, unresolved_subject_ids=tuple(unresolved))
        if checkpoint is ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN:
            return self._materialization_contexts(verified, unresolved)
        return ReviewSubjectDerivation(unresolved_subject_ids=tuple(unresolved), detail="unsupported review checkpoint")

    def _evidence_contexts(
        self,
        verified: list[tuple[ArtifactRef, object]],
        unresolved: list[str],
    ) -> ReviewSubjectDerivation:
        domain_refs = tuple(sorted(artifact.artifact_id for artifact, _payload in verified if artifact.artifact_kind == "DomainAssertion"))
        contexts: list[ReviewCompatibilityContext] = []
        for artifact, payload in verified:
            if artifact.artifact_kind == "RelationshipDecision" and isinstance(payload, RelationshipDecision):
                if payload.decision_id != artifact.artifact_id:
                    unresolved.append(artifact.artifact_id)
                    continue
                contexts.append(self.review_policy.evidence_context(payload, domain_refs))
            elif artifact.artifact_kind == "SemanticMappingDecision" and isinstance(payload, SemanticMappingDecision):
                if payload.decision_id != artifact.artifact_id:
                    unresolved.append(artifact.artifact_id)
                    continue
                contexts.append(self.review_policy.evidence_context(payload, domain_refs))
            elif artifact.artifact_kind == "EvidenceFusionResult" and isinstance(payload, EvidenceFusionResult):
                # A container with more than one decision is never collapsed
                # into a fabricated single review subject.  Producers must
                # publish decision artifacts when individual review is needed.
                unresolved.extend(item.decision_id for item in (*payload.relationships, *payload.mappings))
        return ReviewSubjectDerivation(
            contexts=self._unique_contexts(contexts),
            unresolved_subject_ids=tuple(sorted(set(unresolved))),
            detail="evidence decisions are derived from typed decision artifacts",
        )

    def _identity_contexts(
        self,
        run_id: str,
        verified: list[tuple[ArtifactRef, object]],
        unresolved: list[str],
    ) -> ReviewSubjectDerivation:
        hypotheses = {
            artifact.artifact_id: payload
            for artifact, payload in verified
            if artifact.artifact_kind == "CanonicalModelHypothesis" and isinstance(payload, CanonicalModelHypothesis) and payload.artifact_id == artifact.artifact_id
        }
        proposals = [
            (artifact, payload)
            for artifact, payload in verified
            if artifact.artifact_kind == "CanonicalIdentityProposal" and isinstance(payload, CanonicalIdentityProposal) and payload.proposal_id == artifact.artifact_id
        ]
        er_results = {
            artifact.artifact_id: payload
            for artifact, payload in verified
            if artifact.artifact_kind == "EntityResolutionResult" and isinstance(payload, EntityResolutionResult)
        }
        contexts: list[ReviewCompatibilityContext] = []
        for artifact, proposal in proposals:
            hypothesis = hypotheses.get(proposal.hypothesis_artifact_id)
            if hypothesis is None:
                # The graph names the proposal as the direct dependency, while
                # the proposal explicitly binds the hypothesis it was derived
                # from. Resolve that typed reference by identity and verify it
                # through the same control/artifact boundary before use.
                hypothesis_artifact = self.control_store.get_artifact(proposal.hypothesis_artifact_id)
                if hypothesis_artifact is not None:
                    try:
                        candidate_artifact, candidate = self._read_verified(run_id, hypothesis_artifact)
                        if candidate_artifact.artifact_kind == "CanonicalModelHypothesis" and isinstance(candidate, CanonicalModelHypothesis) and candidate.artifact_id == candidate_artifact.artifact_id:
                            hypothesis = candidate
                    except (KeyError, OSError, PlatformError, ValueError):
                        pass
            if hypothesis is None:
                unresolved.append(artifact.artifact_id)
                continue
            er_hashes: dict[str, str] = {}
            for er_ref in proposal.er_result_refs:
                result = er_results.get(er_ref)
                if result is None:
                    unresolved.append(artifact.artifact_id)
                    break
                er_hashes[result.spec.entity_family] = stable_digest(result.model_dump(mode="json"))
            else:
                try:
                    contexts.append(self.canonical_finalization.identity_context(hypothesis, proposal, er_hashes))
                except ValueError:
                    unresolved.append(artifact.artifact_id)
        return ReviewSubjectDerivation(
            contexts=self._unique_contexts(contexts),
            unresolved_subject_ids=tuple(sorted(set(unresolved))),
            detail="canonical identity context is derived from hypothesis, proposal and ER artifacts",
        )

    def _materialization_contexts(
        self,
        verified: list[tuple[ArtifactRef, object]],
        unresolved: list[str],
    ) -> ReviewSubjectDerivation:
        compiled = {
            payload.compiled_plan_id: (artifact, payload)
            for artifact, payload in verified
            if artifact.artifact_kind == "CompiledPlan" and isinstance(payload, CompiledPlan) and payload.compiled_plan_id == artifact.artifact_id
        }
        generated = {
            payload.generated_sql_id: (artifact, payload)
            for artifact, payload in verified
            if artifact.artifact_kind == "GeneratedSQL" and isinstance(payload, GeneratedSQL) and payload.generated_sql_id == artifact.artifact_id
        }
        targets = [
            (artifact, payload)
            for artifact, payload in verified
            if artifact.artifact_kind == "TargetConfig" and isinstance(payload, TargetConfig)
        ]
        contexts: list[ReviewCompatibilityContext] = []
        for compiled_artifact, compiled_plan in compiled.values():
            generated_item = generated.get(compiled_plan.generated_sql_id)
            if generated_item is None:
                unresolved.append(compiled_plan.compiled_plan_id)
                continue
            generated_artifact, sql = generated_item
            if (
                compiled_artifact.run_id != generated_artifact.run_id
                or compiled_artifact.stage_id != "COMPILATION"
                or generated_artifact.stage_id != "COMPILATION"
                or compiled_artifact.attempt_id != generated_artifact.attempt_id
                or compiled_plan.generated_sql_id != sql.generated_sql_id
                or compiled_plan.generated_sql_hash != sql.sql_hash
                or compiled_plan.plan_id != sql.plan_id
                or compiled_plan.plan_content_hash != sql.plan_content_hash
            ):
                unresolved.append(compiled_plan.compiled_plan_id)
                continue
            matching_targets = [
                (target_artifact, target)
                for target_artifact, target in targets
                if target_artifact.run_id == compiled_artifact.run_id
                and target_artifact.stage_id == "COMPILATION"
                and target_artifact.attempt_id == compiled_artifact.attempt_id
                and compiled_plan.target_config_fingerprint == target.config_fingerprint
            ]
            if not matching_targets:
                unresolved.append(compiled_plan.compiled_plan_id)
                continue
            for _target_artifact, target in matching_targets:
                # ReviewPolicyService remains the semantic authority.  The
                # transport hashes are independently checked above and by
                # _read_verified; they are deliberately not substituted for
                # the builder's semantic review hash.
                contexts.append(self.review_policy.materialization_context(compiled_plan, sql, target))
        return ReviewSubjectDerivation(
            contexts=self._unique_contexts(contexts),
            unresolved_subject_ids=tuple(sorted(set(unresolved))),
            detail="materialization context is derived from compiled plan, SQL and target config",
        )

    def _read_verified(self, run_id: str, supplied: ArtifactRef) -> tuple[ArtifactRef, object]:
        artifact = self.control_store.get_artifact(supplied.artifact_id)
        if artifact is None or artifact != supplied or artifact.run_id != run_id or artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise PlatformError("review input artifact is not the exact registered published reference")
        if self.artifact_store.stat(artifact) != artifact or self.artifact_store.verify(artifact).state is not ArtifactIntegrityState.VERIFIED:
            raise PlatformError("review input artifact failed integrity verification")
        payload = json.loads(self.artifact_store.read(artifact).decode("utf-8"))
        contract_types = {
            "RelationshipDecision": RelationshipDecision,
            "SemanticMappingDecision": SemanticMappingDecision,
            "EvidenceFusionResult": EvidenceFusionResult,
            "CanonicalModelHypothesis": CanonicalModelHypothesis,
            "CanonicalIdentityProposal": CanonicalIdentityProposal,
            "EntityResolutionResult": EntityResolutionResult,
            "AnalyticalPlan": AnalyticalPlan,
            "CompiledPlan": CompiledPlan,
            "GeneratedSQL": GeneratedSQL,
            "TargetConfig": TargetConfig,
        }
        contract = contract_types.get(artifact.artifact_kind)
        return artifact, contract.model_validate(payload) if contract is not None else payload

    @staticmethod
    def _unique_contexts(contexts: list[ReviewCompatibilityContext]) -> tuple[ReviewCompatibilityContext, ...]:
        by_key = {
            (context.review_checkpoint_id, context.subject_artifact_id, context.subject_content_hash, context.subject_semantic_id, context.applicability_fingerprint): context
            for context in contexts
        }
        return tuple(by_key[key] for key in sorted(by_key, key=lambda item: tuple(str(value) for value in item)))


__all__ = ["ReviewCheckpointSubjectResolver", "ReviewSubjectDerivation", "ReviewSubjectDerivationPort"]
