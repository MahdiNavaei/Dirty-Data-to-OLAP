"""Deterministic, review-only fusion of project-owned evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from dirty_data_to_olap.domain.contracts.applied_ml import AppliedMLResult
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult, RelationshipCandidate
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    Conflict,
    ConflictType,
    DecisionExplanation,
    DecisionState,
    DeclaredConstraintInput,
    DomainAssertion,
    EvidenceBundle,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceFusionCompleteness,
    EvidenceFusionInputs,
    EvidenceFusionRequest,
    EvidenceLineageReference,
    EvidencePresenceState,
    EvidenceReliabilityState,
    EvidenceRole,
    FusionArtifactReference,
    FusionEvidenceItem,
    FusionFailure,
    FusionFailureKind,
    FusionScore,
    FusionSubjectKind,
    NormalizedEvidenceSignal,
    ProducerEvidenceStatus,
    ProducerResultState,
    RelationshipDecision,
    SemanticMappingDecision,
    fusion_conflict_id,
    fusion_decision_id,
    fusion_input_fingerprint,
    fusion_signal_id,
)
from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult
from dirty_data_to_olap.domain.contracts.semantic_ai import (
    LLMEvidence,
    SemanticEvidenceResult,
    SemanticSupportState,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, DeclaredConstraint


_WEIGHTS = {
    "declared": 1.0,
    "coverage": 1.0,
    "uniqueness": 1.0,
    "type": 0.75,
    "rank": 0.5,
    "quality": 0.5,
}


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("fusion input must be a project-owned contract or mapping")


def _subject_relationship(item: Mapping[str, Any]) -> str:
    return "rel:" + item["from_table"] + ":" + ",".join(item["from_columns"]) + "->" + item["to_table"] + ":" + ",".join(item["to_columns"])


def _subject_mapping(item: Mapping[str, Any]) -> str:
    left = (item["source_id"], item["source_column_id"])
    right = (item["target_source_id"], item["target_column_id"])
    return "map:" + "<->".join(":".join(part) for part in sorted((left, right)))


def _producer_state(value: Any) -> ProducerResultState:
    raw = getattr(value, "status", None)
    name = getattr(raw, "value", str(raw)).upper()
    if name in {"COMPLETE", "EXECUTED_EXPERIMENTAL"}:
        return ProducerResultState.COMPLETE
    if name in {"INCOMPLETE", "INSUFFICIENT_DATA"}:
        return ProducerResultState.INCOMPLETE
    if name in {"FAILED"}:
        return ProducerResultState.FAILED
    if name in {"SKIPPED", "SKIPPED_NOT_CONFIGURED"}:
        return ProducerResultState.SKIPPED
    if name in {"UNAVAILABLE"}:
        return ProducerResultState.UNAVAILABLE
    return ProducerResultState.INCOMPLETE


class EvidenceFusionService:
    """Fuse aggregate evidence without owning source access or acceptance."""

    def __init__(self, *, artifact_root: Path | None = None) -> None:
        self.artifact_root = artifact_root

    @staticmethod
    def load_policy(kind: str = "relationship", *, policy_root: Path = Path("policies/evidence-fusion")):
        """Load the committed policy identity without requiring a YAML runtime dependency."""
        from dirty_data_to_olap.domain.contracts.evidence_fusion import FusionPolicyReference, FusionPolicyStatus
        filename = "mapping_fusion_v1.yml" if kind == "mapping" else "relationship_fusion_v1.yml"
        path = policy_root / filename
        content = path.read_bytes()
        policy_id = "mapping-fusion-v1" if kind == "mapping" else "relationship-fusion-v1"
        return FusionPolicyReference(policy_id=policy_id, version="1.0", status=FusionPolicyStatus.UNCALIBRATED, content_hash=hashlib.sha256(content).hexdigest())

    def fuse(
        self,
        request: EvidenceFusionRequest,
        inputs: EvidenceFusionInputs | None = None,
        *,
        profile_result: ProfileResult | None = None,
        quality_result: QualityResult | None = None,
        dependency_result: DependencyResult | None = None,
        schema_match_result: SchemaMatchResult | None = None,
        applied_ml_result: AppliedMLResult | None = None,
        semantic_results: Iterable[SemanticEvidenceResult | LLMEvidence] = (),
        source_catalogs: Iterable[SourceCatalog] = (),
    ) -> Any:
        """Return an EvidenceFusionResult and optionally publish deterministic JSON.

        The explicit result arguments are the integration boundary for Steps
        08--16.  ``FusionEvidenceInputs`` is used for aggregate observations
        that have already been normalized by an upstream project-owned stage.
        """
        from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionResult

        supplied = inputs or EvidenceFusionInputs()
        statuses = list(supplied.producer_statuses)
        items = list(supplied.evidence_items)
        relationships = [_dump(item) for item in supplied.relationship_candidates]
        mappings = [_dump(item) for item in supplied.mapping_candidates]
        failures: list[FusionFailure] = []

        if profile_result is not None:
            statuses.append(self._status("profiling", EvidenceFamily.PROFILE, profile_result))
        if quality_result is not None:
            statuses.append(self._status("quality", EvidenceFamily.QUALITY, quality_result))
        if dependency_result is not None:
            statuses.append(self._status("dependency", EvidenceFamily.DEPENDENCY, dependency_result))
            relationships.extend(_dump(item) for item in dependency_result.relationship_candidates)
            items.extend(self._dependency_items(dependency_result))
        items.extend(self._declared_items(supplied.declared_constraints))
        catalog_constraints = tuple(constraint for catalog in source_catalogs for constraint in catalog.declared_constraints)
        items.extend(self._declared_items(catalog_constraints))
        items.extend(self._assertion_items(supplied.domain_assertions))
        if schema_match_result is not None:
            statuses.append(self._status("schema-matching", EvidenceFamily.SCHEMA_MATCHING, schema_match_result))
            mappings.extend(_dump(item) for item in schema_match_result.candidates)
            items.extend(self._schema_items(schema_match_result))
        if applied_ml_result is not None:
            statuses.append(self._status("applied-ml", EvidenceFamily.APPLIED_ML, applied_ml_result))
            items.extend(self._ml_items(applied_ml_result))
        for result in semantic_results:
            if isinstance(result, SemanticEvidenceResult):
                statuses.append(self._semantic_status(result))
                if result.evidence is not None:
                    items.extend(self._semantic_items(result.evidence))
            else:
                items.extend(self._semantic_items(result))

        statuses = self._unique_statuses(statuses)
        if request.cross_source_mapping_scope and not any(item.family is EvidenceFamily.SCHEMA_MATCHING and item.state is ProducerResultState.COMPLETE for item in statuses):
            failures.append(FusionFailure(failure_id="fusion-required-schema-matching", kind=FusionFailureKind.REQUIRED_PRODUCER_FAILED, detail="cross-source mapping requires a COMPLETE schema-matching result", subject_id=None))
        for family in (EvidenceFamily.PROFILE, EvidenceFamily.DEPENDENCY, EvidenceFamily.QUALITY):
            matching = [item for item in statuses if item.family is family]
            if not matching:
                failures.append(FusionFailure(failure_id="fusion-missing-" + family.value.lower(), kind=FusionFailureKind.INPUT_INCOMPLETE, detail=f"required {family.value} producer result was not supplied"))
            elif matching[0].state is not ProducerResultState.COMPLETE:
                failures.append(FusionFailure(failure_id="fusion-required-" + family.value.lower(), kind=FusionFailureKind.REQUIRED_PRODUCER_FAILED, detail=f"required {family.value} producer is {matching[0].state.value}"))

        collision = self._collision(items)
        if collision:
            failures.append(collision)
        by_subject = self._signals(items, request, failures)
        self._scope_failures(by_subject, failures)
        relationships = self._bounded(relationships, request.max_relationship_candidates)
        mappings = self._bounded(mappings, request.max_mapping_candidates)
        all_conflicts: list[Conflict] = []
        bundles: list[EvidenceBundle] = []
        relationship_decisions: list[RelationshipDecision] = []
        mapping_decisions: list[SemanticMappingDecision] = []

        for candidate in sorted(relationships, key=lambda item: str(item.get("candidate_id", ""))):
            candidate_id = str(candidate.get("candidate_id", ""))
            if not candidate_id:
                failures.append(FusionFailure(failure_id="fusion-invalid-relationship", kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="relationship candidate has no stable candidate_id"))
                continue
            subject = _subject_relationship(candidate)
            signals = tuple(by_subject.get(subject, ()))[: request.max_evidence_per_subject]
            conflicts = self._conflicts_for_relationship(candidate, subject, signals, relationships, tuple(supplied.declared_constraints) + catalog_constraints)
            all_conflicts.extend(conflicts[: request.max_conflicts_per_subject])
            bundles.append(self._bundle(subject, FusionSubjectKind.RELATIONSHIP, f"{candidate['from_table']} -> {candidate['to_table']}", signals, conflicts))
            relationship_decisions.append(self._relationship_decision(candidate, subject, signals, conflicts, request, statuses))

        for candidate in sorted(mappings, key=lambda item: str(item.get("candidate_id", ""))):
            candidate_id = str(candidate.get("candidate_id", ""))
            if not candidate_id:
                failures.append(FusionFailure(failure_id="fusion-invalid-mapping", kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="mapping candidate has no stable candidate_id"))
                continue
            subject = _subject_mapping(candidate)
            signals = tuple(by_subject.get(subject, ()))[: request.max_evidence_per_subject]
            conflicts = self._conflicts_for_mapping(candidate, subject, signals, mappings)
            all_conflicts.extend(conflicts[: request.max_conflicts_per_subject])
            bundles.append(self._bundle(subject, FusionSubjectKind.MAPPING, f"{candidate['source_column_id']} <-> {candidate['target_column_id']}", signals, conflicts))
            mapping_decisions.append(self._mapping_decision(candidate, subject, signals, conflicts, request, statuses))

        all_conflicts = self._unique_conflicts(all_conflicts)
        for decision in relationship_decisions:
            related = tuple(item.conflict_id for item in all_conflicts if item.subject_id == decision.subject_id)
            if related:
                relationship_decisions[relationship_decisions.index(decision)] = decision.model_copy(update={"conflict_refs": related})
        for decision in mapping_decisions:
            related = tuple(item.conflict_id for item in all_conflicts if item.subject_id == decision.subject_id)
            if related:
                mapping_decisions[mapping_decisions.index(decision)] = decision.model_copy(update={"conflict_refs": related})
        if len(relationship_decisions) + len(mapping_decisions) > request.max_total_decisions:
            failures.append(FusionFailure(failure_id="fusion-total-decision-bound", kind=FusionFailureKind.INPUT_INCOMPLETE, detail="fusion decision bound was exceeded"))
        required_failed = bool(failures) and any(item.kind in {FusionFailureKind.INPUT_INCOMPLETE, FusionFailureKind.REQUIRED_PRODUCER_FAILED, FusionFailureKind.EVIDENCE_ID_COLLISION, FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH} for item in failures)
        completeness = EvidenceFusionCompleteness.INCOMPLETE_REQUIRED_EVIDENCE if required_failed else EvidenceFusionCompleteness.COMPLETE_REVIEW_READY
        if any(item.kind in {FusionFailureKind.EVIDENCE_ID_COLLISION, FusionFailureKind.POLICY_INVALID} for item in failures):
            completeness = EvidenceFusionCompleteness.FAILED
        result = EvidenceFusionResult(request=request, relationships=tuple(relationship_decisions), mappings=tuple(mapping_decisions), conflicts=tuple(all_conflicts), bundles=tuple(sorted(bundles, key=lambda item: item.bundle_id)), signals=tuple(sorted((signal for values in by_subject.values() for signal in values), key=lambda item: item.signal_id)), failures=tuple(failures), completeness=completeness, policy=request.policy, forwarded_repair_proposal_refs=())
        return self._publish(result)

    def _status(self, producer_id: str, family: EvidenceFamily, result: Any) -> ProducerEvidenceStatus:
        identity = getattr(result, "request", None) or getattr(result, "profile_request", None)
        result_id = getattr(identity, "request_id", None) or getattr(identity, "quality_run_id", None) or producer_id + "-result"
        return ProducerEvidenceStatus(producer_id=producer_id, family=family, state=_producer_state(result), result_id=str(result_id), detail="project-owned producer result")

    def _semantic_status(self, result: SemanticEvidenceResult) -> ProducerEvidenceStatus:
        state = {SemanticSupportState.CANDIDATE_ONLY: ProducerResultState.COMPLETE, SemanticSupportState.SKIPPED: ProducerResultState.SKIPPED, SemanticSupportState.UNAVAILABLE: ProducerResultState.UNAVAILABLE, SemanticSupportState.PRIVACY_BLOCKED: ProducerResultState.PRIVACY_BLOCKED, SemanticSupportState.FAILED: ProducerResultState.FAILED}[result.state]
        return ProducerEvidenceStatus(producer_id="semantic-ai", family=EvidenceFamily.SEMANTIC_AI, state=state, result_id=result.request_id, detail=result.failure.detail if result.failure else "semantic evidence state")

    def _dependency_items(self, result: DependencyResult) -> list[FusionEvidenceItem]:
        output: list[FusionEvidenceItem] = []
        for evidence in result.inclusion_dependencies:
            subject = "rel:" + evidence.left_table_id + ":" + ",".join(evidence.left_columns) + "->" + evidence.right_table_id + ":" + ",".join(evidence.right_columns)
            base = dict(producer_id="dependency", family=EvidenceFamily.DEPENDENCY, role=EvidenceRole.DIRECT_OBSERVATION, scope_id=evidence.source_id + ":" + evidence.snapshot_id, observation_scope=EvidenceReliabilityState.FULL if all(evidence.observation_scope.complete_by_table.values()) else EvidenceReliabilityState.BOUNDED, source_ids=(evidence.source_id,), snapshot_ids=(evidence.snapshot_id,), correlation_group=evidence.evidence_id)
            output.extend((FusionEvidenceItem(evidence_id=evidence.evidence_id + ":coverage", subject_id=subject, metric_name="inclusion_coverage", metric_value=evidence.coverage_ratio, metric_semantics="matched distinct non-null left values divided by eligible left values", direction=EvidenceDirection.SUPPORTS, score_bearing=True, **base), FusionEvidenceItem(evidence_id=evidence.evidence_id + ":orphan", subject_id=subject, metric_name="orphan_ratio", metric_value=evidence.violation_ratio, metric_semantics="left values without a target match divided by eligible left values", direction=EvidenceDirection.CONTRADICTS, score_bearing=True, **base), FusionEvidenceItem(evidence_id=evidence.evidence_id + ":uniqueness", subject_id=subject, metric_name="target_uniqueness", metric_value=evidence.target_uniqueness_ratio, metric_semantics="unique target values divided by observed target values", direction=EvidenceDirection.SUPPORTS, score_bearing=True, **base), FusionEvidenceItem(evidence_id=evidence.evidence_id + ":type", subject_id=subject, metric_name="type_compatibility", metric_value=1.0 if evidence.type_compatible else 0.0, metric_semantics="project-owned physical/logical type compatibility boolean", direction=EvidenceDirection.SUPPORTS if evidence.type_compatible else EvidenceDirection.CONTRADICTS, score_bearing=True, **base)))
            if evidence.low_cardinality_risk:
                output.append(FusionEvidenceItem(evidence_id=evidence.evidence_id + ":low-cardinality", subject_id=subject, metric_name="low_cardinality_risk", metric_value=None, metric_semantics="provider flagged a tiny domain trap", direction=EvidenceDirection.CONTRADICTS, score_bearing=False, **base))
        return output

    def _schema_items(self, result: SchemaMatchResult) -> list[FusionEvidenceItem]:
        scores = {item.score_id: item for item in result.scores}
        output: list[FusionEvidenceItem] = []
        for candidate in result.candidates:
            data = _dump(candidate)
            subject = _subject_mapping(data)
            for ref in sorted(data.get("score_refs", ())):
                score = scores.get(ref)
                if score is None:
                    continue
                output.append(FusionEvidenceItem(evidence_id=score.score_id, subject_id=subject, producer_id="schema-matching:" + score.matcher.name.lower(), family=EvidenceFamily.SCHEMA_MATCHING, role=EvidenceRole.DIRECT_OBSERVATION, metric_name="matcher_rank", metric_value=float(score.rank), metric_semantics="matcher-specific ordinal rank; native score is not averaged", direction=EvidenceDirection.SUPPORTS, scope_id=score.source_column_id + ":" + score.target_column_id, observation_scope=EvidenceReliabilityState.SAMPLED if score.observation_scope.reduced_scope else EvidenceReliabilityState.FULL, source_ids=tuple(score.observation_scope.source_ids), snapshot_ids=tuple(score.observation_scope.snapshot_ids), correlation_group=score.score_id, score_bearing=True))
        return output

    def _ml_items(self, result: AppliedMLResult) -> list[FusionEvidenceItem]:
        return [FusionEvidenceItem(evidence_id=item.evidence_id, subject_id="candidate:" + item.candidate_id, producer_id="applied-ml", family=EvidenceFamily.APPLIED_ML, role=EvidenceRole.DERIVED_INTERPRETATION, metric_name="learned_ranking_score", metric_value=item.ranking_score, metric_semantics="uncalibrated learned ranking; derived from upstream aggregate features", direction=EvidenceDirection.CONTEXT, scope_id=item.model_id, observation_scope=EvidenceReliabilityState.BOUNDED, derived_from_refs=item.input_evidence_refs, correlation_group=item.model_id, score_bearing=False, qualitative_text="learned evidence is auxiliary and not independently scored") for item in result.learned_evidence]

    def _semantic_items(self, evidence: LLMEvidence) -> list[FusionEvidenceItem]:
        output: list[FusionEvidenceItem] = []
        subject = evidence.subject_refs[0] if len(evidence.subject_refs) == 1 else "semantic:" + stable_digest(evidence.subject_refs)[:24]
        for item in evidence.hypotheses:
            direction = EvidenceDirection.SUPPORTS if item.kind.value == "SUPPORTS_HYPOTHESIS" else EvidenceDirection.CONTRADICTS if item.kind.value == "CONTRADICTS_HYPOTHESIS" else EvidenceDirection.CONTEXT
            output.append(FusionEvidenceItem(evidence_id=item.hypothesis_id, subject_id=subject, producer_id="semantic-ai", family=EvidenceFamily.SEMANTIC_AI, role=EvidenceRole.DERIVED_INTERPRETATION, metric_name="semantic_hypothesis", metric_value=None, metric_semantics="qualitative provider hypothesis; no numeric conversion", direction=direction, scope_id=evidence.context_manifest.input_fingerprint, observation_scope=EvidenceReliabilityState.BOUNDED, source_ids=(), snapshot_ids=(), derived_from_refs=evidence.context_manifest.allowed_provider_evidence_refs, correlation_group=evidence.evidence_id, score_bearing=False, qualitative_text=item.statement))
        return output

    @staticmethod
    def _declared_items(constraints: tuple[Any, ...]) -> list[FusionEvidenceItem]:
        output: list[FusionEvidenceItem] = []
        for item in constraints:
            if isinstance(item, DeclaredConstraintInput):
                data = item.model_dump(mode="json")
                data["from_table"] = data.pop("from_table")
                data["to_table"] = data.pop("to_table")
                data["from_columns"] = tuple(data.pop("from_columns"))
                data["to_columns"] = tuple(data.pop("to_columns"))
                data["constraint_id"] = data["constraint_id"]
            elif isinstance(item, DeclaredConstraint):
                data = {"constraint_id": "declared:" + stable_digest(item.model_dump(mode="json"))[:24], "constraint_type": item.constraint_type, "source_id": item.source_id, "snapshot_id": "catalog", "from_table": item.table_id, "from_columns": item.columns, "to_table": item.referenced_table_id or item.referenced_table_name or "unknown", "to_columns": item.referenced_columns, "scope_id": "catalog:" + item.source_id}
            else:
                continue
            if data["constraint_type"].upper() != "FOREIGN_KEY":
                continue
            subject = "rel:" + data["from_table"] + ":" + ",".join(data["from_columns"]) + "->" + data["to_table"] + ":" + ",".join(data["to_columns"])
            output.append(FusionEvidenceItem(evidence_id=data["constraint_id"], subject_id=subject, producer_id="declared-metadata", family=EvidenceFamily.DECLARED_CONSTRAINT, role=EvidenceRole.DECLARED_METADATA, metric_name="declared_foreign_key", metric_value=1.0, metric_semantics="source-declared foreign-key metadata; not proof of snapshot validity", direction=EvidenceDirection.SUPPORTS, scope_id=data["scope_id"], observation_scope=EvidenceReliabilityState.FULL, source_ids=(data["source_id"],), snapshot_ids=(data["snapshot_id"],), correlation_group=data["constraint_id"], score_bearing=True))
        return output

    @staticmethod
    def _assertion_items(assertions: tuple[DomainAssertion, ...]) -> list[FusionEvidenceItem]:
        return [FusionEvidenceItem(evidence_id=item.assertion_id, subject_id=item.subject_id, producer_id="domain-assertion", family=EvidenceFamily.DOMAIN_ASSERTION, role=EvidenceRole.HUMAN_OR_DOMAIN_ASSERTION, metric_name="domain_assertion", metric_value=None, metric_semantics="explicit human/domain assertion; no fabricated probability", direction=EvidenceDirection.CONTEXT, scope_id=item.scope_id, observation_scope=EvidenceReliabilityState.FULL, source_ids=item.source_ids, snapshot_ids=item.snapshot_ids, derived_from_refs=item.evidence_refs, correlation_group=item.assertion_id, score_bearing=False, qualitative_text=item.statement) for item in assertions if item.status.upper() in {"PROPOSED", "ACCEPTED", "REVIEW_REQUIRED", "CONFLICTED"}]

    @staticmethod
    def _scope_failures(by_subject: dict[str, list[NormalizedEvidenceSignal]], failures: list[FusionFailure]) -> None:
        for subject, signals in sorted(by_subject.items()):
            snapshots: dict[str, set[str]] = {}
            for signal in signals:
                for source_id in signal.source_ids:
                    snapshots.setdefault(source_id, set()).update(signal.snapshot_ids)
            if any(len(values) > 1 for values in snapshots.values()):
                failures.append(FusionFailure(failure_id="fusion-scope-" + stable_digest(subject)[:24], kind=FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH, detail="one subject combines incompatible snapshots for a source", subject_id=subject, evidence_refs=tuple(item.evidence_id for item in signals)))

    def _signals(self, items: list[FusionEvidenceItem], request: EvidenceFusionRequest, failures: list[FusionFailure]) -> dict[str, list[NormalizedEvidenceSignal]]:
        output: dict[str, list[NormalizedEvidenceSignal]] = {}
        seen: dict[str, str] = {}
        for item in sorted(items, key=lambda value: (value.subject_id, value.evidence_id, value.metric_name)):
            if item.subject_id not in request.relationship_candidate_ids and item.subject_id not in request.mapping_candidate_ids and not item.subject_id.startswith(("rel:", "map:")):
                # Candidate IDs are accepted for callers that index their own subjects;
                # otherwise only canonical directional/symmetric IDs are eligible.
                continue
            if item.presence is not EvidencePresenceState.OBSERVED:
                continue
            payload = json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            prior = seen.get(item.evidence_id)
            if prior is not None and prior != payload:
                failures.append(FusionFailure(failure_id="fusion-collision-" + stable_digest(item.evidence_id)[:24], kind=FusionFailureKind.EVIDENCE_ID_COLLISION, detail="one evidence_id claimed materially different payloads", evidence_refs=(item.evidence_id,)))
                continue
            seen[item.evidence_id] = payload
            normalized, method = self._normalize(item)
            signal = NormalizedEvidenceSignal(signal_id=fusion_signal_id(item.evidence_id, item.subject_id, item.metric_name), subject_id=item.subject_id, evidence_id=item.evidence_id, producer_id=item.producer_id, family=item.family, role=item.role, raw_metric_name=item.metric_name, raw_metric_value=item.metric_value, raw_metric_semantics=item.metric_semantics, normalization_method=method, normalization_version="evidence-fusion-v1", normalized_value=normalized, direction=item.direction, presence=item.presence, reliability=item.observation_scope, scope_id=item.scope_id, source_ids=item.source_ids, snapshot_ids=item.snapshot_ids, derived_from_refs=item.derived_from_refs, correlation_group=item.correlation_group, score_bearing=item.score_bearing)
            output.setdefault(item.subject_id, []).append(signal)
        return output

    @staticmethod
    def _normalize(item: FusionEvidenceItem) -> tuple[float | None, str]:
        if not item.score_bearing or item.metric_value is None:
            return None, "qualitative_only_v1"
        if item.metric_name == "matcher_rank":
            return 1.0 / (1.0 + item.metric_value), "ordinal_rank_presence_v1"
        if item.metric_name in {"inclusion_coverage", "orphan_ratio", "target_uniqueness", "type_compatibility"}:
            return float(item.metric_value), "bounded_01_identity_v1"
        return float(item.metric_value), "declared_policy_bounded_01_v1"

    def _conflicts_for_relationship(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], all_candidates: list[Mapping[str, Any]], declared: tuple[DeclaredConstraintInput, ...]) -> list[Conflict]:
        conflicts = self._structural_conflicts(subject, signals)
        matching_declared = []
        for item in declared:
            if isinstance(item, DeclaredConstraintInput):
                matches = item.from_table == candidate.get("from_table") and item.to_table == candidate.get("to_table") and item.from_columns == tuple(candidate.get("from_columns", ())) and item.to_columns == tuple(candidate.get("to_columns", ()))
                identifier = item.constraint_id
            elif isinstance(item, DeclaredConstraint):
                matches = item.table_id == candidate.get("from_table") and (item.referenced_table_id or item.referenced_table_name) == candidate.get("to_table") and item.columns == tuple(candidate.get("from_columns", ())) and item.referenced_columns == tuple(candidate.get("to_columns", ()))
                identifier = "declared:" + stable_digest(item.model_dump(mode="json"))[:24]
            else:
                matches = False
                identifier = ""
            if matches:
                matching_declared.append(identifier)
        contradictions = tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS)
        if matching_declared and contradictions:
            conflicts.append(self._conflict(ConflictType.DECLARED_DATA_CONFLICT, subject, tuple(matching_declared), contradictions, "declared FK metadata is retained while observed data contradicts it", "declared_fk_vs_observed_data"))
        targets = {(item.get("to_table"), tuple(item.get("to_columns", ()))) for item in all_candidates if item.get("from_table") == candidate.get("from_table") and tuple(item.get("from_columns", ())) == tuple(candidate.get("from_columns", ()))}
        if len(targets) > 1:
            conflicts.append(self._conflict(ConflictType.MULTIPLE_TARGET_AMBIGUITY, subject, (str(candidate.get("candidate_id")),), tuple(str(item.get("candidate_id")) for item in all_candidates), "more than one bounded target is plausible for this source endpoint", "multiple_target_requires_review"))
        return conflicts

    def _conflicts_for_mapping(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], all_candidates: list[Mapping[str, Any]]) -> list[Conflict]:
        conflicts = self._structural_conflicts(subject, signals)
        semantic = tuple(item.evidence_id for item in signals if item.family is EvidenceFamily.SEMANTIC_AI and item.direction is EvidenceDirection.SUPPORTS)
        type_bad = tuple(item.evidence_id for item in signals if item.raw_metric_name == "type_compatibility" and item.direction is EvidenceDirection.CONTRADICTS)
        if semantic and type_bad:
            conflicts.append(self._conflict(ConflictType.TYPE_SEMANTIC_CONFLICT, subject, semantic, type_bad, "semantic evidence suggests equivalence but type evidence is incompatible", "semantic_vs_type"))
        plausible = [item for item in all_candidates if item.get("source_id") == candidate.get("source_id") and item.get("source_column_id") == candidate.get("source_column_id")]
        if len({(item.get("target_source_id"), item.get("target_column_id")) for item in plausible}) > 1:
            conflicts.append(self._conflict(ConflictType.MULTIPLE_TARGET_AMBIGUITY, subject, (str(candidate.get("candidate_id")),), tuple(str(item.get("candidate_id")) for item in plausible), "one source column has multiple plausible mapping targets", "multiple_mapping_targets_require_review"))
        return conflicts

    def _structural_conflicts(self, subject: str, signals: tuple[NormalizedEvidenceSignal, ...]) -> list[Conflict]:
        output: list[Conflict] = []
        support = tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.SUPPORTS)
        contradiction = tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS)
        semantic = tuple(item.evidence_id for item in signals if item.family is EvidenceFamily.SEMANTIC_AI and item.direction is EvidenceDirection.SUPPORTS)
        if semantic and contradiction:
            output.append(self._conflict(ConflictType.SEMANTIC_STRUCTURAL_CONFLICT, subject, semantic, contradiction, "qualitative semantic support conflicts with structural evidence", "semantic_structural_disagreement"))
        if any(item.raw_metric_name == "type_compatibility" and item.direction is EvidenceDirection.CONTRADICTS for item in signals) and (semantic or any(item.family is EvidenceFamily.SCHEMA_MATCHING for item in signals)):
            output.append(self._conflict(ConflictType.TYPE_SEMANTIC_CONFLICT, subject, semantic or support, tuple(item.evidence_id for item in signals if item.raw_metric_name == "type_compatibility"), "semantic or matcher evidence does not erase incompatible type evidence", "type_semantic_compatibility"))
        by_metric: dict[str, list[NormalizedEvidenceSignal]] = {}
        for item in signals:
            by_metric.setdefault(item.raw_metric_name, []).append(item)
        for metric, values in by_metric.items():
            sampled = [item for item in values if item.reliability is EvidenceReliabilityState.SAMPLED]
            full = [item for item in values if item.reliability is EvidenceReliabilityState.FULL]
            if sampled and full and any((a.normalized_value or 0) >= 0.8 and (b.normalized_value or 0) <= 0.5 for a in sampled for b in full):
                output.append(self._conflict(ConflictType.SAMPLE_FULLSCAN_CONFLICT, subject, tuple(item.evidence_id for item in sampled), tuple(item.evidence_id for item in full), f"sampled and full observations disagree for {metric}; observations are retained", "sample_vs_full_observation"))
        return output

    @staticmethod
    def _conflict(kind: ConflictType, subject: str, supporting: tuple[str, ...], contradicting: tuple[str, ...], explanation: str, rule: str) -> Conflict:
        refs = tuple(sorted(set(supporting + contradicting)))
        return Conflict(conflict_id=fusion_conflict_id(subject, kind, refs), conflict_type=kind, subject_id=subject, supporting_evidence_refs=tuple(sorted(set(supporting))), contradicting_evidence_refs=tuple(sorted(set(contradicting))), explanation=explanation, policy_rule=rule, scope_id="subject:" + stable_digest(subject)[:20], provenance="application.evidence_fusion:evidence-fusion-v1")

    def _score(self, signals: tuple[NormalizedEvidenceSignal, ...]) -> FusionScore:
        grouped: dict[str, NormalizedEvidenceSignal] = {}
        for signal in signals:
            if signal.score_bearing and signal.normalized_value is not None:
                grouped.setdefault(signal.correlation_group, signal)
        contributions: dict[str, float] = {}
        for group, signal in sorted(grouped.items()):
            weight = _WEIGHTS["coverage"] if signal.raw_metric_name in {"inclusion_coverage", "orphan_ratio"} else _WEIGHTS["uniqueness"] if signal.raw_metric_name == "target_uniqueness" else _WEIGHTS["type"] if signal.raw_metric_name == "type_compatibility" else _WEIGHTS["rank"] if signal.raw_metric_name == "matcher_rank" else _WEIGHTS["declared"] if signal.family is EvidenceFamily.DECLARED_CONSTRAINT else _WEIGHTS["quality"]
            signed = signal.normalized_value if signal.direction is EvidenceDirection.SUPPORTS else -signal.normalized_value if signal.direction is EvidenceDirection.CONTRADICTS else 0.0
            contributions[signal.signal_id] = round(weight * signed, 12)
        observed = float(sum(self._weight_for(signal) for signal in grouped.values()))
        expected_names = {"inclusion_coverage", "target_uniqueness", "type_compatibility"} if any(signal.raw_metric_name in {"inclusion_coverage", "target_uniqueness", "type_compatibility", "orphan_ratio"} for signal in signals) else {"matcher_rank", "type_compatibility"} if any(signal.raw_metric_name == "matcher_rank" for signal in signals) else set()
        expected_weight = sum(self._weight_for_name(name) for name in expected_names)
        eligible = float(max(observed, expected_weight))
        value = round(sum(contributions.values()) / eligible, 12) if contributions and eligible else None
        return FusionScore(value=value, eligible_weight=eligible, observed_weight=observed, evidence_coverage=round(observed / eligible, 12) if eligible else 0.0, sufficient=bool(contributions), contributions=contributions)

    @staticmethod
    def _weight_for_name(name: str) -> float:
        return _WEIGHTS["coverage"] if name in {"inclusion_coverage", "orphan_ratio"} else _WEIGHTS["uniqueness"] if name == "target_uniqueness" else _WEIGHTS["type"] if name == "type_compatibility" else _WEIGHTS["rank"]

    def _weight_for(self, signal: NormalizedEvidenceSignal) -> float:
        return _WEIGHTS["declared"] if signal.family is EvidenceFamily.DECLARED_CONSTRAINT else self._weight_for_name(signal.raw_metric_name)

    def _band(self, score: FusionScore, conflicts: list[Conflict]) -> Any:
        from dirty_data_to_olap.domain.contracts.evidence_fusion import ConfidenceBand
        if conflicts:
            return ConfidenceBand.CONFLICTED
        if not score.sufficient:
            return ConfidenceBand.INSUFFICIENT
        if (score.value or 0) >= 0.75:
            return ConfidenceBand.HIGH
        if (score.value or 0) >= 0.4:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.LOW

    def _relationship_decision(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], request: EvidenceFusionRequest, statuses: list[ProducerEvidenceStatus]) -> RelationshipDecision:
        score = self._score(signals)
        required = {EvidenceFamily.PROFILE, EvidenceFamily.DEPENDENCY, EvidenceFamily.QUALITY}
        missing = tuple("producer:" + family.value.lower() for family in sorted(required - {item.family for item in statuses}, key=lambda value: value.value)) + tuple("producer:" + item.producer_id for item in statuses if item.state not in {ProducerResultState.COMPLETE})
        state = DecisionState.INCOMPLETE_REQUIRED_EVIDENCE if any(item.family in required and item.state is not ProducerResultState.COMPLETE for item in statuses) or not all(any(item.family is family for item in statuses) for family in required) else DecisionState.REVIEW_REQUIRED
        explanation = self._explanation(signals, conflicts, missing, score)
        fingerprint = fusion_input_fingerprint(subject, signals, request.policy)
        return RelationshipDecision(decision_id=fusion_decision_id(subject, fingerprint, request.policy), candidate_id=str(candidate["candidate_id"]), subject_id=subject, from_table=str(candidate["from_table"]), from_columns=tuple(candidate["from_columns"]), to_table=str(candidate["to_table"]), to_columns=tuple(candidate["to_columns"]), proposed_cardinality=str(candidate.get("proposed_cardinality", "MANY_TO_ONE")), score=score, confidence_band=self._band(score, conflicts), decision_state=state, policy=request.policy, supporting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.SUPPORTS), contradicting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS), missing_evidence_refs=missing, unavailable_evidence_refs=tuple("producer:" + item.producer_id for item in statuses if item.state in {ProducerResultState.UNAVAILABLE, ProducerResultState.SKIPPED}), conflict_refs=tuple(item.conflict_id for item in conflicts), explanation=explanation, input_evidence_fingerprint=fingerprint, provenance="application.evidence_fusion:evidence-fusion-v1")

    def _mapping_decision(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], request: EvidenceFusionRequest, statuses: list[ProducerEvidenceStatus]) -> SemanticMappingDecision:
        score = self._score(signals)
        required = {EvidenceFamily.SCHEMA_MATCHING} if request.cross_source_mapping_scope else set()
        missing = tuple("producer:" + family.value.lower() for family in sorted(required - {item.family for item in statuses}, key=lambda value: value.value)) + tuple("producer:" + item.producer_id for item in statuses if item.state not in {ProducerResultState.COMPLETE})
        state = DecisionState.INCOMPLETE_REQUIRED_EVIDENCE if any(item.family is EvidenceFamily.SCHEMA_MATCHING and item.state is not ProducerResultState.COMPLETE for item in statuses) else DecisionState.REVIEW_REQUIRED
        fingerprint = fusion_input_fingerprint(subject, signals, request.policy)
        return SemanticMappingDecision(decision_id=fusion_decision_id(subject, fingerprint, request.policy), candidate_id=str(candidate["candidate_id"]), subject_id=subject, source_id=str(candidate["source_id"]), source_column_id=str(candidate["source_column_id"]), target_source_id=str(candidate["target_source_id"]), target_column_id=str(candidate["target_column_id"]), score=score, confidence_band=self._band(score, conflicts), decision_state=state, policy=request.policy, supporting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.SUPPORTS), contradicting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS), missing_evidence_refs=missing, unavailable_evidence_refs=tuple("producer:" + item.producer_id for item in statuses if item.state in {ProducerResultState.UNAVAILABLE, ProducerResultState.SKIPPED}), conflict_refs=tuple(item.conflict_id for item in conflicts), explanation=self._explanation(signals, conflicts, missing, score), input_evidence_fingerprint=fingerprint, provenance="application.evidence_fusion:evidence-fusion-v1")

    @staticmethod
    def _explanation(signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], missing: tuple[str, ...], score: FusionScore) -> DecisionExplanation:
        supports = tuple(f"{item.evidence_id}: {item.raw_metric_name}={item.raw_metric_value!r} ({item.raw_metric_semantics}); contribution={score.contributions.get(item.signal_id, 0)}" for item in signals if item.direction is EvidenceDirection.SUPPORTS)
        contradicts = tuple(f"{item.evidence_id}: {item.raw_metric_name}={item.raw_metric_value!r} ({item.raw_metric_semantics}); contribution={score.contributions.get(item.signal_id, 0)}" for item in signals if item.direction is EvidenceDirection.CONTRADICTS)
        limitations = tuple(missing) + tuple(f"conflict {item.conflict_id}: {item.explanation}" for item in conflicts) + tuple(f"{item.evidence_id}: qualitative/derived evidence is visible but not score-bearing" for item in signals if not item.score_bearing)
        reconstruction = (f"score={score.value!r} semantics={score.score_semantics} eligible_weight={score.eligible_weight} observed_weight={score.observed_weight} coverage={score.evidence_coverage}", "decision remains REVIEW_REQUIRED until the later review checkpoint and G5 evaluation")
        return DecisionExplanation(supports=supports, contradicts=contradicts, limitations=limitations, reconstruction=reconstruction)

    @staticmethod
    def _bundle(subject: str, kind: FusionSubjectKind, hypothesis: str, signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict]) -> EvidenceBundle:
        lineage = tuple(EvidenceLineageReference(evidence_id=item.evidence_id, producer_id=item.producer_id, family=item.family, source_ids=item.source_ids, snapshot_ids=item.snapshot_ids, scope_id=item.scope_id, observation_scope=item.reliability.value, correlation_group=item.correlation_group, derived_from_refs=item.derived_from_refs) for item in signals)
        return EvidenceBundle(bundle_id="fusion_bundle_" + stable_digest((subject, tuple(item.signal_id for item in signals)))[:32], subject_id=subject, subject_kind=kind, hypothesis=hypothesis, lineage=lineage, signals=signals, supporting_evidence_refs=tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.SUPPORTS), contradicting_evidence_refs=tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS), missing_evidence_refs=(), unavailable_evidence_refs=())

    @staticmethod
    def _collision(items: list[FusionEvidenceItem]) -> FusionFailure | None:
        seen: dict[str, str] = {}
        for item in items:
            payload = json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            if item.evidence_id in seen and seen[item.evidence_id] != payload:
                return FusionFailure(failure_id="fusion-collision-" + stable_digest(item.evidence_id)[:24], kind=FusionFailureKind.EVIDENCE_ID_COLLISION, detail="one evidence_id claimed materially different evidence", evidence_refs=(item.evidence_id,))
            seen[item.evidence_id] = payload
        return None

    @staticmethod
    def _unique_statuses(values: list[ProducerEvidenceStatus]) -> list[ProducerEvidenceStatus]:
        output: dict[str, ProducerEvidenceStatus] = {}
        for item in values:
            output.setdefault(item.producer_id, item)
        return [output[key] for key in sorted(output)]

    @staticmethod
    def _unique_conflicts(values: list[Conflict]) -> list[Conflict]:
        output = {item.conflict_id: item for item in values}
        return [output[key] for key in sorted(output)]

    @staticmethod
    def _bounded(values: list[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        return sorted(values, key=lambda item: str(item.get("candidate_id", "")))[:limit]

    def _publish(self, result: Any) -> Any:
        if self.artifact_root is None:
            return result
        root = self.artifact_root.resolve()
        project_root = Path.cwd().resolve()
        try:
            root.relative_to(project_root)
        except ValueError:
            raise ValueError("fusion artifact root must remain under the project root") from None
        root.mkdir(parents=True, exist_ok=True)
        payload = result.model_dump(mode="json", exclude={"artifacts"})
        serialized = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        target = root / "evidence_fusion" / "manifests" / f"{result.request.execution_context_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=".fusion-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        location = str(target.relative_to(project_root)).replace("\\", "/")
        artifact = FusionArtifactReference(artifact_id="fusion-artifact-" + stable_digest(serialized)[:24], artifact_type="evidence_fusion_result", location=location, content_hash=hashlib.sha256(serialized).hexdigest())
        return result.model_copy(update={"artifacts": (artifact,)})
