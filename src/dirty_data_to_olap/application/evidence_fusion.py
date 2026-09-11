"""Deterministic, policy-driven, review-only evidence fusion."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from dirty_data_to_olap.domain.contracts.applied_ml import AppliedMLResult
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    Conflict, ConflictType, DecisionExplanation, DecisionState, DeclaredConstraintInput,
    DomainAssertion, EvidenceBundle, EvidenceDirection, EvidenceFamily,
    EvidenceFusionCompleteness, EvidenceFusionInputs, EvidenceFusionRequest,
    EvidenceLineageReference, EvidencePresenceState, EvidenceReliabilityState,
    EvidenceRole, FusionArtifactReference, FusionEvidenceItem, FusionFailure,
    ExpectedProducerResult, FusionFailureKind, FusionPolicyReference, FusionScoringDimension,
    FusionSnapshotBinding, FusionSubjectBinding, FusionScore, FusionSubjectKind,
    NormalizedEvidenceSignal, ProducerEvidenceStatus, ProducerResultState,
    RelationshipDecision, SemanticMappingDecision, fusion_conflict_id,
    fusion_decision_id, fusion_input_fingerprint, fusion_signal_id,
)
from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult, SchemaMatchSignalFamily
from dirty_data_to_olap.domain.contracts.semantic_ai import LLMEvidence, SemanticEvidenceResult, SemanticSupportState
from dirty_data_to_olap.domain.contracts.source import DeclaredConstraint, SourceCatalog, stable_digest


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("fusion input must be a project-owned contract or mapping")


def _relationship_subject(item: Mapping[str, Any]) -> str:
    return "rel:" + str(item["from_table"]) + ":" + ",".join(item["from_columns"]) + "->" + str(item["to_table"]) + ":" + ",".join(item["to_columns"])


def _mapping_subject(item: Mapping[str, Any]) -> str:
    left = (str(item["source_id"]), str(item["source_column_id"]))
    right = (str(item["target_source_id"]), str(item["target_column_id"]))
    return "map:" + "<->".join(":".join(part) for part in sorted((left, right)))


def _producer_state(value: Any) -> ProducerResultState:
    raw = getattr(value, "status", None)
    if raw is None:
        raw = getattr(value, "completeness", None)
    name = getattr(raw, "value", str(raw)).upper()
    if name in {"COMPLETE", "EXECUTED_EXPERIMENTAL"}:
        return ProducerResultState.COMPLETE
    if name in {"INCOMPLETE", "INSUFFICIENT_DATA"}:
        return ProducerResultState.INCOMPLETE
    if name == "FAILED":
        return ProducerResultState.FAILED
    if name in {"SKIPPED", "SKIPPED_NOT_CONFIGURED"}:
        return ProducerResultState.SKIPPED
    if name == "NOT_CONFIGURED":
        return ProducerResultState.NOT_CONFIGURED
    if name == "UNAVAILABLE":
        return ProducerResultState.UNAVAILABLE
    if name == "PRIVACY_BLOCKED":
        return ProducerResultState.PRIVACY_BLOCKED
    return ProducerResultState.INCOMPLETE


class EvidenceFusionService:
    """Fuse project-owned aggregate evidence without source access or acceptance."""

    def __init__(self, *, artifact_root: Path | None = None, policy_root: Path = Path("policies/evidence-fusion")) -> None:
        self.artifact_root = artifact_root
        self.policy_root = policy_root

    @staticmethod
    def load_policy(kind: str = "relationship", *, policy_root: Path = Path("policies/evidence-fusion")) -> FusionPolicyReference:
        """Load the stdlib-JSON authoritative policy and bind its exact bytes."""
        filename = "mapping_fusion_v1.json" if kind == "mapping" else "relationship_fusion_v1.json"
        path = policy_root / filename
        content = path.read_bytes()
        data = json.loads(content.decode("utf-8"))
        automation = data.get("automation", {})
        return FusionPolicyReference(
            policy_id=data["policy_id"], version=str(data["version"]), status=data["status"],
            score_semantics=data["score_semantics"], automation_enabled=bool(automation.get("enabled", False)),
            auto_accept_enabled=bool(automation.get("auto_accept", False)), auto_reject_enabled=bool(automation.get("auto_reject", False)),
            requires_g5_for_automation=bool(automation.get("requires_g5", True)), automation=automation,
            content_hash=hashlib.sha256(content).hexdigest(),
            subject_kind=data.get("subject_kind"), normalization_rules=tuple(data.get("normalization_rules", ())),
            scoring_dimensions=tuple(data.get("scoring_dimensions", ())), band_policy=tuple(data.get("band_policy", ())),
            conflict_rules=tuple(data.get("conflict_rules", ())), required_producer_families=tuple(data.get("required_producer_families", ())),
            cross_source_mapping_requires_schema_matching=bool(data.get("cross_source_mapping_requires_schema_matching", True)),
        )

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
        profile_results: Iterable[ProfileResult] = (),
        quality_results: Iterable[QualityResult] = (),
        dependency_results: Iterable[DependencyResult] = (),
        schema_match_results: Iterable[SchemaMatchResult] = (),
        applied_ml_results: Iterable[AppliedMLResult] = (),
    ) -> Any:
        from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionResult

        supplied = inputs or EvidenceFusionInputs()
        failures: list[FusionFailure] = []
        profiles = list(supplied.profile_results) + list(profile_results) + ([profile_result] if profile_result else [])
        qualities = list(supplied.quality_results) + list(quality_results) + ([quality_result] if quality_result else [])
        dependencies = list(supplied.dependency_results) + list(dependency_results) + ([dependency_result] if dependency_result else [])
        schemas = list(supplied.schema_match_results) + list(schema_match_results) + ([schema_match_result] if schema_match_result else [])
        mls = list(supplied.applied_ml_results) + list(applied_ml_results) + ([applied_ml_result] if applied_ml_result else [])
        semantics = list(supplied.semantic_results) + list(semantic_results)
        catalogs = list(supplied.source_catalogs) + list(source_catalogs)
        statuses = list(supplied.producer_statuses)
        items = list(supplied.evidence_items)
        relationships = [_dump(item) for item in supplied.relationship_candidates]
        mappings = [_dump(item) for item in supplied.mapping_candidates]

        for result in profiles:
            statuses.append(self._status("profiling", EvidenceFamily.PROFILE, result))
        for result in qualities:
            statuses.append(self._status("quality", EvidenceFamily.QUALITY, result))
        for result in dependencies:
            statuses.append(self._status("dependency", EvidenceFamily.DEPENDENCY, result))
            relationships.extend(_dump(item) for item in result.relationship_candidates)
            items.extend(self._dependency_items(result))
        for result in schemas:
            statuses.append(self._status("schema-matching", EvidenceFamily.SCHEMA_MATCHING, result))
            mappings.extend(_dump(item) for item in result.candidates)
            items.extend(self._schema_items(result, request.policy))
        for result in mls:
            statuses.append(self._status("applied-ml", EvidenceFamily.APPLIED_ML, result))
        for result in semantics:
            if isinstance(result, SemanticEvidenceResult):
                statuses.append(self._semantic_status(result))
            elif isinstance(result, LLMEvidence):
                statuses.append(ProducerEvidenceStatus(producer_id="semantic-ai", family=EvidenceFamily.SEMANTIC_AI, state=ProducerResultState.COMPLETE, result_id=result.evidence_id, detail="direct semantic evidence"))

        items.extend(self._declared_items(supplied.declared_constraints))
        items.extend(self._declared_items(tuple(c for catalog in catalogs for c in catalog.declared_constraints)))
        items.extend(self._assertion_items(supplied.domain_assertions))
        statuses = self._unique_statuses(statuses, failures)
        policy_ok = self._policy_valid(request.policy)
        if not policy_ok:
            failures.append(FusionFailure(failure_id="fusion-policy-invalid", kind=FusionFailureKind.POLICY_INVALID, detail="Step17 requires a typed uncalibrated policy with declared dimensions and automation disabled"))

        relationships = self._bounded(relationships, request.max_relationship_candidates, "relationship", failures)
        mappings = self._bounded(mappings, request.max_mapping_candidates, "mapping", failures)
        if len(relationships) + len(mappings) > request.max_total_decisions:
            failures.append(FusionFailure(failure_id="fusion-total-decision-bound", kind=FusionFailureKind.BOUNDED_INPUT_DISCARDED, detail="max_total_decisions discarded material candidates"))
            keep = request.max_total_decisions
            relationships, mappings = relationships[:keep], mappings[: max(0, keep - len(relationships))]
        selected_subjects = self._candidate_subjects(relationships, mappings)
        selected_kind = FusionSubjectKind.MAPPING if mappings and not relationships else FusionSubjectKind.RELATIONSHIP
        if relationships and mappings:
            failures.append(FusionFailure(failure_id="fusion-mixed-subject-request", kind=FusionFailureKind.POLICY_INVALID, detail="one fusion request cannot contain both relationship and mapping candidates"))
        if request.policy.subject_kind is not None and ((relationships and request.policy.subject_kind is not FusionSubjectKind.RELATIONSHIP) or (mappings and request.policy.subject_kind is not FusionSubjectKind.MAPPING)):
            failures.append(FusionFailure(failure_id="fusion-policy-subject-kind", kind=FusionFailureKind.POLICY_INVALID, detail="request candidates and policy subject kind do not match"))
        bound_items = self._bind_items(items, selected_subjects, failures)
        bound_items.extend(self._candidate_items(relationships, mappings))
        topologies = self._candidate_topologies(relationships, mappings)
        bound_items.extend(self._profile_items(profiles, topologies))
        quality_items, repair_refs = self._quality_items(qualities, topologies)
        bound_items.extend(quality_items)
        for result in mls:
            bound_items.extend(self._ml_items(result, selected_subjects, failures))
        for result in semantics:
            bound_items.extend(self._semantic_result_items(result, selected_subjects, supplied.subject_bindings, failures))

        if request.cross_source_mapping_scope and not any(item.family is EvidenceFamily.SCHEMA_MATCHING and item.state is ProducerResultState.COMPLETE for item in statuses):
            failures.append(FusionFailure(failure_id="fusion-required-schema-matching", kind=FusionFailureKind.REQUIRED_PRODUCER_FAILED, detail="cross-source mapping requires a COMPLETE schema-matching result"))
        for family in request.policy.required_producer_families or (EvidenceFamily.PROFILE, EvidenceFamily.DEPENDENCY, EvidenceFamily.QUALITY):
            matching = [item for item in statuses if item.family is family]
            if not matching:
                failures.append(FusionFailure(failure_id="fusion-missing-" + family.value.lower(), kind=FusionFailureKind.INPUT_INCOMPLETE, detail=f"required {family.value} producer result was not supplied"))
            elif any(item.state is not ProducerResultState.COMPLETE for item in matching):
                failures.append(FusionFailure(failure_id="fusion-required-" + family.value.lower(), kind=FusionFailureKind.REQUIRED_PRODUCER_FAILED, detail=f"required {family.value} producer is incomplete or failed"))
        self._expected_results(request, statuses, failures)
        by_subject = self._signals(bound_items, request, failures)
        self._scope_failures(by_subject, failures)

        conflicts: list[Conflict] = []
        bundles: list[EvidenceBundle] = []
        relationship_decisions: list[RelationshipDecision] = []
        mapping_decisions: list[SemanticMappingDecision] = []
        for candidate in relationships:
            subject = _relationship_subject(candidate)
            local = self._subject_signals(by_subject, subject, request, failures)
            scope_failure = self._candidate_scope_failure(candidate, subject, local)
            if scope_failure:
                failures.append(scope_failure)
            local_conflicts = self._conflicts_for_relationship(candidate, subject, local, relationships, tuple(supplied.declared_constraints) + tuple(c for catalog in catalogs for c in catalog.declared_constraints), request.policy)
            if len(local_conflicts) > request.max_conflicts_per_subject:
                failures.append(FusionFailure(failure_id="fusion-conflict-bound-" + stable_digest(subject)[:20], kind=FusionFailureKind.BOUNDED_INPUT_DISCARDED, detail="max_conflicts_per_subject discarded material conflicts", subject_id=subject))
            local_conflicts = local_conflicts[:request.max_conflicts_per_subject]
            conflicts.extend(local_conflicts)
            missing, unavailable = self._subject_missing(subject, local, statuses, request.policy)
            bundles.append(self._bundle(subject, FusionSubjectKind.RELATIONSHIP, f"{candidate['from_table']} -> {candidate['to_table']}", local, local_conflicts, missing, unavailable))
            relationship_decisions.append(self._relationship_decision(candidate, subject, local, local_conflicts, request, statuses, relationships, missing, policy_ok))
        for candidate in mappings:
            subject = _mapping_subject(candidate)
            local = self._subject_signals(by_subject, subject, request, failures)
            scope_failure = self._candidate_scope_failure(candidate, subject, local)
            if scope_failure:
                failures.append(scope_failure)
            local_conflicts = self._conflicts_for_mapping(candidate, subject, local, mappings, request.policy)
            if len(local_conflicts) > request.max_conflicts_per_subject:
                failures.append(FusionFailure(failure_id="fusion-conflict-bound-" + stable_digest(subject)[:20], kind=FusionFailureKind.BOUNDED_INPUT_DISCARDED, detail="max_conflicts_per_subject discarded material conflicts", subject_id=subject))
            local_conflicts = local_conflicts[:request.max_conflicts_per_subject]
            conflicts.extend(local_conflicts)
            missing, unavailable = self._subject_missing(subject, local, statuses, request.policy)
            bundles.append(self._bundle(subject, FusionSubjectKind.MAPPING, f"{candidate['source_column_id']} <-> {candidate['target_column_id']}", local, local_conflicts, missing, unavailable))
            mapping_decisions.append(self._mapping_decision(candidate, subject, local, local_conflicts, request, statuses, mappings, missing, policy_ok))
        conflicts = self._unique_conflicts(conflicts)
        conflict_by_subject = {item.subject_id: tuple(c.conflict_id for c in conflicts if c.subject_id == item.subject_id) for item in (*relationship_decisions, *mapping_decisions)}
        relationship_decisions = [item.model_copy(update={"conflict_refs": conflict_by_subject.get(item.subject_id, ())}) for item in relationship_decisions]
        mapping_decisions = [item.model_copy(update={"conflict_refs": conflict_by_subject.get(item.subject_id, ())}) for item in mapping_decisions]
        subject_incomplete = any(item.decision_state is DecisionState.INCOMPLETE_REQUIRED_EVIDENCE for item in (*relationship_decisions, *mapping_decisions)) or any(any(ref.startswith(("subject:dimension:", "subject:matcher_evidence")) for ref in bundle.missing_evidence_refs) for bundle in bundles)
        required_failed = subject_incomplete or any(item.kind in {FusionFailureKind.INPUT_INCOMPLETE, FusionFailureKind.REQUIRED_PRODUCER_FAILED, FusionFailureKind.EVIDENCE_ID_COLLISION, FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH, FusionFailureKind.STALE_EVIDENCE, FusionFailureKind.UNSUPPORTED_SCORE_SEMANTICS, FusionFailureKind.BOUNDED_INPUT_DISCARDED} for item in failures)
        completeness = EvidenceFusionCompleteness.INCOMPLETE_REQUIRED_EVIDENCE if required_failed else EvidenceFusionCompleteness.COMPLETE_REVIEW_READY
        if any(item.kind in {FusionFailureKind.POLICY_INVALID, FusionFailureKind.EVIDENCE_ID_COLLISION} for item in failures):
            completeness = EvidenceFusionCompleteness.FAILED
        result = EvidenceFusionResult(request=request, relationships=tuple(relationship_decisions), mappings=tuple(mapping_decisions), conflicts=tuple(conflicts), bundles=tuple(sorted(bundles, key=lambda item: item.bundle_id)), signals=tuple(sorted((s for values in by_subject.values() for s in values), key=lambda item: item.signal_id)), failures=tuple(failures), completeness=completeness, policy=request.policy, forwarded_repair_proposal_refs=tuple(sorted(repair_refs)))
        return self._publish(result)

    @staticmethod
    def _policy_valid(policy: FusionPolicyReference) -> bool:
        return policy.status.value == "UNCALIBRATED" and not policy.automation_enabled and not policy.auto_accept_enabled and not policy.auto_reject_enabled and bool(policy.scoring_dimensions) and bool(policy.normalization_rules)

    @staticmethod
    def _status(producer_id: str, family: EvidenceFamily, result: Any) -> ProducerEvidenceStatus:
        identity = getattr(result, "request", None) or getattr(result, "profile_request", None)
        result_id = getattr(identity, "request_id", None) or getattr(identity, "profile_request_id", None) or getattr(result, "quality_run_id", None) or producer_id + "-result"
        source_ids: tuple[str, ...] = ()
        snapshot_map: dict[str, str] = {}
        value = getattr(result, "source_id", None)
        if value:
            source_ids = (str(value),)
        scope = getattr(result, "observation_scope", None)
        if scope is not None:
            source_ids = tuple(getattr(scope, "source_ids", source_ids))
            snapshots = getattr(scope, "snapshot_ids", {})
            if isinstance(snapshots, Mapping):
                snapshot_map = {str(k): str(v) for k, v in snapshots.items()}
            elif getattr(scope, "source_id", None) and getattr(scope, "snapshot_id", None):
                source_ids = (str(scope.source_id),)
                snapshot_map = {str(scope.source_id): str(scope.snapshot_id)}
        if not snapshot_map and getattr(result, "snapshot_id", None):
            snapshot_map = {source_ids[0] if source_ids else producer_id: str(result.snapshot_id)}
        if not source_ids and identity is not None and getattr(identity, "source_id", None):
            source_ids = (str(identity.source_id),)
            if getattr(identity, "snapshot_id", None):
                snapshot_map = {str(identity.source_id): str(identity.snapshot_id)}
        return ProducerEvidenceStatus(producer_id=producer_id, family=family, state=_producer_state(result), result_id=str(result_id), detail="project-owned producer result", source_ids=source_ids, snapshot_by_source=snapshot_map, input_fingerprint=stable_digest(result))

    @staticmethod
    def _semantic_status(result: SemanticEvidenceResult) -> ProducerEvidenceStatus:
        state = {SemanticSupportState.CANDIDATE_ONLY: ProducerResultState.COMPLETE, SemanticSupportState.SKIPPED: ProducerResultState.SKIPPED, SemanticSupportState.UNAVAILABLE: ProducerResultState.UNAVAILABLE, SemanticSupportState.PRIVACY_BLOCKED: ProducerResultState.PRIVACY_BLOCKED, SemanticSupportState.FAILED: ProducerResultState.FAILED}[result.state]
        return ProducerEvidenceStatus(producer_id="semantic-ai", family=EvidenceFamily.SEMANTIC_AI, state=state, result_id=result.request_id, detail=result.failure.detail if result.failure else "semantic evidence state")

    @staticmethod
    def _unique_statuses(values: list[ProducerEvidenceStatus], failures: list[FusionFailure]) -> list[ProducerEvidenceStatus]:
        grouped: dict[tuple[str, str, tuple[tuple[str, str], ...]], list[ProducerEvidenceStatus]] = {}
        for item in values:
            key = (item.family.value, item.result_id, tuple(sorted(item.snapshot_by_source.items())))
            grouped.setdefault(key, []).append(item)
        output: list[ProducerEvidenceStatus] = []
        for key in sorted(grouped):
            distinct = {stable_digest(item) for item in grouped[key]}
            if len(distinct) > 1:
                failures.append(FusionFailure(failure_id="fusion-status-conflict-" + stable_digest(key)[:24], kind=FusionFailureKind.STALE_EVIDENCE, detail="duplicate producer-result identity has conflicting state, producer or content fingerprint", evidence_refs=(key[1],)))
                output.extend(sorted(grouped[key], key=lambda item: stable_digest(item)))
            else:
                output.append(grouped[key][0])
        return output

    @staticmethod
    def _expected_results(request: EvidenceFusionRequest, statuses: list[ProducerEvidenceStatus], failures: list[FusionFailure]) -> None:
        expectations = list(request.expected_producer_results)
        for expected in expectations:
            matches = [item for item in statuses if item.family is expected.family and item.producer_id == expected.producer_id and item.result_id == expected.result_id]
            if not matches:
                failures.append(FusionFailure(failure_id="fusion-stale-" + stable_digest(expected)[:24], kind=FusionFailureKind.STALE_EVIDENCE, detail=f"expected producer result {expected.family.value}/{expected.producer_id}/{expected.result_id} was not supplied", evidence_refs=(expected.result_id,)))
                continue
            if expected.source_ids and any(tuple(item.source_ids) != tuple(expected.source_ids) for item in matches):
                failures.append(FusionFailure(failure_id="fusion-stale-scope-" + stable_digest(expected)[:24], kind=FusionFailureKind.STALE_EVIDENCE, detail="expected producer result source scope does not match", evidence_refs=(expected.result_id,)))
            if expected.snapshot_by_source and any(dict(item.snapshot_by_source) != dict(expected.snapshot_by_source) for item in matches):
                failures.append(FusionFailure(failure_id="fusion-stale-snapshot-" + stable_digest(expected)[:24], kind=FusionFailureKind.STALE_EVIDENCE, detail="expected producer result snapshot scope does not match", evidence_refs=(expected.result_id,)))
        for family_key, result_id in request.expected_producer_result_ids.items():
            matches = [item for item in statuses if item.family.value == family_key.upper() or item.producer_id == family_key]
            if not any(item.result_id == result_id for item in matches):
                failures.append(FusionFailure(failure_id="fusion-stale-" + stable_digest((family_key, result_id))[:24], kind=FusionFailureKind.STALE_EVIDENCE, detail=f"expected producer result {family_key}={result_id} was not supplied", evidence_refs=(result_id,)))

    @staticmethod
    def _candidate_subjects(relationships: list[Mapping[str, Any]], mappings: list[Mapping[str, Any]]) -> dict[str, str]:
        return {str(item["candidate_id"]): _relationship_subject(item) for item in relationships} | {str(item["candidate_id"]): _mapping_subject(item) for item in mappings}

    @staticmethod
    def _bounded(values: list[Mapping[str, Any]], limit: int, kind: str, failures: list[FusionFailure]) -> list[Mapping[str, Any]]:
        ordered = sorted(values, key=lambda item: str(item.get("candidate_id", "")))
        if len(ordered) > limit:
            failures.append(FusionFailure(failure_id="fusion-" + kind + "-bound", kind=FusionFailureKind.BOUNDED_INPUT_DISCARDED, detail=f"max_{kind}_candidates discarded material candidates", evidence_refs=tuple(str(item.get("candidate_id", "")) for item in ordered[limit:])))
        return ordered[:limit]

    @staticmethod
    def _bind_items(items: list[FusionEvidenceItem], subjects: Mapping[str, str], failures: list[FusionFailure]) -> list[FusionEvidenceItem]:
        output = []
        for item in items:
            if item.subject_id in subjects:
                output.append(item.model_copy(update={"subject_id": subjects[item.subject_id]}))
            elif item.subject_id in subjects.values() or item.subject_id.startswith(("rel:", "map:")):
                output.append(item)
            else:
                failures.append(FusionFailure(failure_id="fusion-item-binding-" + stable_digest(item.evidence_id)[:20], kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="evidence item subject is not one of the selected candidates", evidence_refs=(item.evidence_id,)))
        return output

    @staticmethod
    def _candidate_items(relationships: list[Mapping[str, Any]], mappings: list[Mapping[str, Any]]) -> list[FusionEvidenceItem]:
        output = []
        for item in relationships:
            source_id, snapshot_id = item.get("source_id"), item.get("snapshot_id")
            output.append(FusionEvidenceItem(evidence_id="candidate:" + str(item["candidate_id"]), subject_id=_relationship_subject(item), producer_id="dependency", family=EvidenceFamily.CANDIDATE_CONTAINER, role=EvidenceRole.HYPOTHESIS_CONTAINER, metric_name="relationship_candidate", metric_value=None, metric_semantics="candidate wrapper; never independently scored", direction=EvidenceDirection.CONTEXT, scope_id=str(snapshot_id or "candidate"), observation_scope=EvidenceReliabilityState.BOUNDED, source_ids=(str(source_id),) if source_id else (), snapshot_ids=(str(snapshot_id),) if snapshot_id else (), snapshot_by_source={str(source_id): str(snapshot_id)} if source_id and snapshot_id else {}, correlation_group="candidate:" + str(item["candidate_id"]), score_bearing=False))
        for item in mappings:
            source_snapshot = item.get("source_snapshot_id") or item.get("snapshot_id")
            target_snapshot = item.get("target_snapshot_id")
            snapshot_map = {}
            if source_snapshot:
                snapshot_map[str(item["source_id"])] = str(source_snapshot)
            if target_snapshot:
                snapshot_map[str(item["target_source_id"])] = str(target_snapshot)
            output.append(FusionEvidenceItem(evidence_id="candidate:" + str(item["candidate_id"]), subject_id=_mapping_subject(item), producer_id="schema-matching", family=EvidenceFamily.CANDIDATE_CONTAINER, role=EvidenceRole.HYPOTHESIS_CONTAINER, metric_name="mapping_candidate", metric_value=None, metric_semantics="candidate wrapper; never independently scored", direction=EvidenceDirection.CONTEXT, scope_id="candidate:" + str(item["candidate_id"]), observation_scope=EvidenceReliabilityState.BOUNDED, source_ids=(str(item["source_id"]), str(item["target_source_id"])), snapshot_ids=tuple(snapshot_map.values()), snapshot_by_source=snapshot_map, correlation_group="candidate:" + str(item["candidate_id"]), score_bearing=False))
        return output

    @staticmethod
    def _candidate_topologies(relationships: list[Mapping[str, Any]], mappings: list[Mapping[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
        output: dict[str, tuple[dict[str, Any], ...]] = {}
        for item in relationships:
            subject = _relationship_subject(item)
            source = {"source_id": str(item.get("source_id", "")), "snapshot_id": str(item.get("snapshot_id", "")), "table_id": str(item["from_table"]), "column_ids": tuple(item["from_columns"])}
            target = {"source_id": str(item.get("source_id", "")), "snapshot_id": str(item.get("snapshot_id", "")), "table_id": str(item["to_table"]), "column_ids": tuple(item["to_columns"])}
            output[subject] = (source, target)
        for item in mappings:
            subject = _mapping_subject(item)
            output[subject] = (
                {"source_id": str(item["source_id"]), "snapshot_id": str(item.get("source_snapshot_id") or item.get("snapshot_id") or ""), "table_id": str(item.get("source_table_id") or ""), "column_ids": (str(item["source_column_id"]),)},
                {"source_id": str(item["target_source_id"]), "snapshot_id": str(item.get("target_snapshot_id") or ""), "table_id": str(item.get("target_table_id") or ""), "column_ids": (str(item["target_column_id"]),)},
            )
        return output

    def _dependency_items(self, result: DependencyResult) -> list[FusionEvidenceItem]:
        output: list[FusionEvidenceItem] = []
        for evidence in result.inclusion_dependencies:
            subject = "rel:" + evidence.left_table_id + ":" + ",".join(evidence.left_columns) + "->" + evidence.right_table_id + ":" + ",".join(evidence.right_columns)
            snapshot_map = {evidence.source_id: evidence.snapshot_id}
            base = dict(producer_id="dependency", family=EvidenceFamily.DEPENDENCY, role=EvidenceRole.DIRECT_OBSERVATION, scope_id=evidence.source_id + ":" + evidence.snapshot_id, observation_scope=EvidenceReliabilityState.FULL if all(evidence.observation_scope.complete_by_table.values()) else EvidenceReliabilityState.BOUNDED, source_ids=(evidence.source_id,), snapshot_ids=(evidence.snapshot_id,), snapshot_by_source=snapshot_map, correlation_group=evidence.evidence_id)
            output.extend((FusionEvidenceItem(evidence_id=evidence.evidence_id + ":coverage", subject_id=subject, metric_name="inclusion_coverage", metric_value=evidence.coverage_ratio, metric_semantics="matched distinct non-null left values divided by eligible left values", direction=EvidenceDirection.SUPPORTS, score_dimension_id="inclusion", dependency_group="inclusion", score_bearing=True, **base), FusionEvidenceItem(evidence_id=evidence.evidence_id + ":orphan", subject_id=subject, metric_name="orphan_ratio", metric_value=evidence.violation_ratio, metric_semantics="left values without a target match divided by eligible left values", direction=EvidenceDirection.CONTRADICTS, score_dimension_id="inclusion", dependency_group="inclusion", score_bearing=True, **base), FusionEvidenceItem(evidence_id=evidence.evidence_id + ":uniqueness", subject_id=subject, metric_name="target_uniqueness", metric_value=evidence.target_uniqueness_ratio, metric_semantics="unique target values divided by observed target values", direction=EvidenceDirection.SUPPORTS, score_dimension_id="target_uniqueness", dependency_group="target_uniqueness", score_bearing=True, **base), FusionEvidenceItem(evidence_id=evidence.evidence_id + ":type", subject_id=subject, metric_name="type_compatibility", metric_value=1.0 if evidence.type_compatible else 0.0, metric_semantics="project-owned type compatibility boolean", direction=EvidenceDirection.SUPPORTS if evidence.type_compatible else EvidenceDirection.CONTRADICTS, score_dimension_id="type_compatibility", dependency_group="type_compatibility", score_bearing=True, **base)))
            if evidence.low_cardinality_risk:
                output.append(FusionEvidenceItem(evidence_id=evidence.evidence_id + ":low-cardinality", subject_id=subject, producer_id="dependency", family=EvidenceFamily.DEPENDENCY, role=EvidenceRole.DIRECT_OBSERVATION, metric_name="low_cardinality_risk", metric_value=None, metric_semantics="provider flagged a tiny domain trap", direction=EvidenceDirection.CONTRADICTS, scope_id=evidence.source_id + ":" + evidence.snapshot_id, observation_scope=EvidenceReliabilityState.FULL, source_ids=(evidence.source_id,), snapshot_ids=(evidence.snapshot_id,), snapshot_by_source=snapshot_map, correlation_group=evidence.evidence_id, score_bearing=False))
        return output

    @staticmethod
    def _matcher_family_key(matcher_id: str, matcher_name: str) -> str:
        value = (matcher_name + " " + matcher_id).lower()
        if "coma" in value:
            return "coma"
        if "cupid" in value:
            return "cupid"
        return "other-" + stable_digest(value)[:12]

    def _schema_items(self, result: SchemaMatchResult, policy: FusionPolicyReference) -> list[FusionEvidenceItem]:
        scores = {item.score_id: item for item in result.scores}
        schema_signals = {item.signal_id: item for item in result.signals}
        dimensions = {item.dimension_id for item in policy.scoring_dimensions}
        output: list[FusionEvidenceItem] = []
        for candidate in result.candidates:
            data = _dump(candidate)
            subject = _mapping_subject(data)
            scope_map = {str(k): str(v) for k, v in candidate.observation_scope.snapshot_ids.items()}
            common = dict(observation_scope=EvidenceReliabilityState.SAMPLED if candidate.observation_scope.reduced_scope else EvidenceReliabilityState.FULL, source_ids=tuple(scope_map), snapshot_ids=tuple(scope_map.values()), snapshot_by_source=scope_map, correlation_group="schema-candidate:" + str(candidate.candidate_id))
            for ref in sorted(data.get("score_refs", ())):
                score = scores.get(ref)
                if score is None:
                    continue
                family_key = self._matcher_family_key(score.matcher.matcher_id, score.matcher.name)
                metric_name = "matcher_rank:" + family_key
                dimension_id = "matcher:" + family_key + ":rank"
                score_bearing = dimension_id in dimensions
                output.append(FusionEvidenceItem(evidence_id=score.score_id, subject_id=subject, producer_id="schema-matching:" + family_key, family=EvidenceFamily.SCHEMA_MATCHING, role=EvidenceRole.DIRECT_OBSERVATION, metric_name=metric_name, metric_value=float(score.rank), metric_semantics=f"{score.matcher.name} matcher rank; native score is not averaged", direction=EvidenceDirection.SUPPORTS, score_dimension_id=dimension_id if score_bearing else None, dependency_group="matcher:" + family_key, score_bearing=score_bearing, qualitative_text=None if score_bearing else "matcher family is visible but not configured as a numeric policy dimension", scope_id=score.source_column_id + ":" + score.target_column_id, observation_scope=EvidenceReliabilityState.SAMPLED if score.observation_scope.reduced_scope else EvidenceReliabilityState.FULL, source_ids=tuple(scope_map), snapshot_ids=tuple(scope_map.values()), snapshot_by_source=scope_map, correlation_group=score.score_id))
            for ref in sorted(data.get("signal_refs", ())):
                signal = schema_signals.get(ref)
                if signal is None:
                    continue
                is_structural = signal.family is SchemaMatchSignalFamily.SCHEMA_STRUCTURAL and signal.value is not None
                direction = EvidenceDirection.SUPPORTS if is_structural and signal.value >= 0.5 else EvidenceDirection.CONTRADICTS if is_structural else EvidenceDirection.CONTEXT
                output.append(FusionEvidenceItem(evidence_id=signal.signal_id, subject_id=subject, producer_id="schema-matching:signal", family=EvidenceFamily.SCHEMA_MATCHING, role=EvidenceRole.DIRECT_OBSERVATION, metric_name="type_compatibility" if is_structural else "schema_signal:" + signal.family.value.lower(), metric_value=signal.value, metric_semantics=signal.semantics, direction=direction, score_dimension_id="type_compatibility" if is_structural and "type_compatibility" in dimensions else None, dependency_group="type_compatibility" if is_structural else "schema_signal:" + signal.family.value.lower(), score_bearing=is_structural and "type_compatibility" in dimensions, derived_from_refs=signal.evidence_refs, qualitative_text=None if is_structural else signal.semantics, scope_id=subject, **common))
        return output

    @staticmethod
    def _declared_items(constraints: tuple[Any, ...]) -> list[FusionEvidenceItem]:
        output = []
        for item in constraints:
            if isinstance(item, DeclaredConstraintInput):
                data = item.model_dump(mode="json")
                if data["constraint_type"].upper() != "FOREIGN_KEY":
                    continue
                binding = item.snapshot_binding
                source_id = data["source_id"]
                snapshot_map = {} if binding is FusionSnapshotBinding.NOT_APPLICABLE_SCHEMA_METADATA else ({source_id: data["snapshot_id"]} if data.get("snapshot_id") else {})
                subject = "rel:" + data["from_table"] + ":" + ",".join(data["from_columns"]) + "->" + data["to_table"] + ":" + ",".join(data["to_columns"])
                output.append(FusionEvidenceItem(evidence_id=data["constraint_id"], subject_id=subject, producer_id="declared-metadata", family=EvidenceFamily.DECLARED_CONSTRAINT, role=EvidenceRole.DECLARED_METADATA, metric_name="declared_foreign_key", metric_value=1.0, metric_semantics="declared foreign-key metadata; not snapshot observation", direction=EvidenceDirection.SUPPORTS, scope_id=data["scope_id"], observation_scope=EvidenceReliabilityState.FULL, source_ids=(source_id,), snapshot_ids=tuple(snapshot_map.values()), snapshot_by_source=snapshot_map, snapshot_binding=binding, score_dimension_id="declared_constraint", dependency_group="declared_constraint", correlation_group=data["constraint_id"], score_bearing=True))
            elif isinstance(item, DeclaredConstraint) and item.constraint_type.upper() == "FOREIGN_KEY":
                data = item.model_dump(mode="json")
                subject = "rel:" + item.table_id + ":" + ",".join(item.columns) + "->" + str(item.referenced_table_id or item.referenced_table_name) + ":" + ",".join(item.referenced_columns)
                digest = stable_digest(data)[:24]
                output.append(FusionEvidenceItem(evidence_id="declared:" + digest, subject_id=subject, producer_id="declared-metadata", family=EvidenceFamily.DECLARED_CONSTRAINT, role=EvidenceRole.DECLARED_METADATA, metric_name="declared_foreign_key", metric_value=1.0, metric_semantics="declared catalog foreign-key metadata; not snapshot observation", direction=EvidenceDirection.SUPPORTS, scope_id="catalog:" + item.source_id, observation_scope=EvidenceReliabilityState.FULL, source_ids=(item.source_id,), snapshot_ids=(), snapshot_by_source={}, snapshot_binding=FusionSnapshotBinding.NOT_APPLICABLE_SCHEMA_METADATA, score_dimension_id="declared_constraint", dependency_group="declared_constraint", correlation_group="declared:" + digest, score_bearing=True))
        return output

    @staticmethod
    def _assertion_items(assertions: tuple[DomainAssertion, ...]) -> list[FusionEvidenceItem]:
        return [FusionEvidenceItem(evidence_id=item.assertion_id, subject_id=item.subject_id, producer_id="domain-assertion", family=EvidenceFamily.DOMAIN_ASSERTION, role=EvidenceRole.HUMAN_OR_DOMAIN_ASSERTION, metric_name="domain_assertion", metric_value=None, metric_semantics="explicit qualitative domain assertion", direction=EvidenceDirection.CONTEXT, scope_id=item.scope_id, observation_scope=EvidenceReliabilityState.FULL, source_ids=item.source_ids, snapshot_ids=item.snapshot_ids, snapshot_by_source=dict(zip(item.source_ids, item.snapshot_ids)), correlation_group=item.assertion_id, score_bearing=False, qualitative_text=item.statement) for item in assertions]

    @staticmethod
    def _endpoint_matches(profile: Any, endpoint: Mapping[str, Any]) -> bool:
        return bool(endpoint.get("table_id")) and profile.source_id == endpoint["source_id"] and profile.snapshot_id == endpoint["snapshot_id"] and profile.table_id == endpoint["table_id"] and (not hasattr(profile, "column_id") or profile.column_id in endpoint["column_ids"])

    def _profile_items(self, results: list[ProfileResult], topologies: Mapping[str, tuple[Mapping[str, Any], ...]]) -> list[FusionEvidenceItem]:
        output = []
        for result in results:
            for profile in (*result.tables, *result.columns):
                for subject, endpoints in topologies.items():
                    data = profile.model_dump(mode="json")
                    if not any(self._endpoint_matches(profile, endpoint) for endpoint in endpoints):
                        continue
                    status = getattr(profile.status, "value", str(profile.status))
                    presence = EvidencePresenceState.OBSERVED if status == "COMPLETE" and profile.observation_scope.completeness.value != "NOT_OBSERVED" else EvidencePresenceState.INCOMPLETE
                    scope = profile.observation_scope
                    output.append(FusionEvidenceItem(evidence_id=f"profile:{profile.profile_id}:{stable_digest(subject)[:12]}", subject_id=subject, producer_id="profiling", family=EvidenceFamily.PROFILE, role=EvidenceRole.DIRECT_OBSERVATION, metric_name="column_profile" if hasattr(profile, "column_id") else "table_profile", metric_value=None, metric_semantics="bounded profile context; no independent numeric vote", direction=EvidenceDirection.CONTEXT, presence=presence, scope_id=profile.profile_id, observation_scope=EvidenceReliabilityState.FULL if scope.profiling_mode.value == "FULL" else EvidenceReliabilityState.SAMPLED, source_ids=(profile.source_id,), snapshot_ids=(profile.snapshot_id,), snapshot_by_source={profile.source_id: profile.snapshot_id}, correlation_group=profile.profile_id, score_bearing=False, qualitative_text=json.dumps({"profile_id": profile.profile_id, "table_id": profile.table_id, "column_id": data.get("column_id"), "rows_profiled": data.get("rows_profiled", data.get("rows_observed"))}, sort_keys=True)))
            for subject, endpoints in topologies.items():
                if not any(self._endpoint_matches(profile, endpoint) for profile in (*result.tables, *result.columns) for endpoint in endpoints):
                    output.append(self._not_observed_marker("profile", subject, EvidenceFamily.PROFILE, endpoints, "profile result has no exact selected endpoint observation"))
        return output

    @staticmethod
    def _quality_matches(issue: Any, endpoint: Mapping[str, Any]) -> bool:
        return bool(endpoint.get("table_id")) and issue.source_id == endpoint["source_id"] and issue.snapshot_id == endpoint["snapshot_id"] and issue.table_id == endpoint["table_id"] and (not issue.column_ids or set(issue.column_ids).issubset(set(endpoint["column_ids"])))

    @staticmethod
    def _not_observed_marker(prefix: str, subject: str, family: EvidenceFamily, endpoints: tuple[Mapping[str, Any], ...], text: str) -> FusionEvidenceItem:
        source_map = {str(item["source_id"]): str(item["snapshot_id"]) for item in endpoints if item.get("source_id") and item.get("snapshot_id")}
        return FusionEvidenceItem(evidence_id=f"{prefix}:not-observed:{stable_digest((subject, source_map))[:16]}", subject_id=subject, producer_id=prefix, family=family, role=EvidenceRole.DIRECT_OBSERVATION, metric_name=prefix + "_observation", metric_value=None, metric_semantics="selected subject was not observed by this producer result", direction=EvidenceDirection.CONTEXT, presence=EvidencePresenceState.NOT_OBSERVED, scope_id=subject, observation_scope=EvidenceReliabilityState.UNKNOWN, source_ids=tuple(source_map), snapshot_ids=tuple(source_map.values()), snapshot_by_source=source_map, correlation_group=prefix + ":" + subject, score_bearing=False, qualitative_text=text)

    def _quality_items(self, results: list[QualityResult], topologies: Mapping[str, tuple[Mapping[str, Any], ...]]) -> tuple[list[FusionEvidenceItem], set[str]]:
        output, repairs = [], set()
        for result in results:
            for issue in result.issues:
                for subject, endpoints in topologies.items():
                    if not any(self._quality_matches(issue, endpoint) for endpoint in endpoints):
                        continue
                    issue_refs = tuple(ref.evidence_id for ref in issue.evidence_refs) + issue.declared_constraint_refs + issue.domain_assertion_refs
                    repairs.update(issue.repair_proposal_refs)
                    output.append(FusionEvidenceItem(evidence_id=f"quality:{issue.issue_id}:{stable_digest(subject)[:12]}", subject_id=subject, producer_id="quality", family=EvidenceFamily.QUALITY, role=EvidenceRole.DIRECT_OBSERVATION, metric_name="quality_issue:" + issue.issue_type, metric_value=issue.affected_ratio, metric_semantics=issue.measurement_semantics.value, direction=EvidenceDirection.CONTRADICTS if issue.status.value in {"OPEN", "INCONCLUSIVE"} else EvidenceDirection.CONTEXT, scope_id=issue.issue_id, observation_scope=EvidenceReliabilityState.FULL if issue.observation_scope.completeness.value == "FULLY_OBSERVED" else EvidenceReliabilityState.BOUNDED, source_ids=(issue.source_id,), snapshot_ids=(issue.snapshot_id,), snapshot_by_source={issue.source_id: issue.snapshot_id}, correlation_group=issue.issue_id, score_bearing=False, derived_from_refs=(issue.issue_id,) + issue_refs + issue.repair_proposal_refs, qualitative_text=json.dumps({"issue_id": issue.issue_id, "dimension": issue.quality_dimension.value, "severity": issue.severity.value, "affected_ratio": issue.affected_ratio, "repair_refs": issue.repair_proposal_refs}, sort_keys=True)))
            for proposal in result.repair_proposals:
                for subject, endpoints in topologies.items():
                    if not any(result.source_id == endpoint.get("source_id") and result.snapshot_id == endpoint.get("snapshot_id") and endpoint.get("table_id") == proposal.table_id and (not proposal.column_ids or set(proposal.column_ids).issubset(set(endpoint.get("column_ids", ())))) for endpoint in endpoints):
                        continue
                    repairs.add(proposal.proposal_id)
            for subject, endpoints in topologies.items():
                matching_endpoints = tuple(endpoint for endpoint in endpoints if endpoint.get("source_id") == result.source_id and endpoint.get("snapshot_id") == result.snapshot_id)
                if not matching_endpoints:
                    continue
                output.append(self._quality_coverage_item(result, subject, matching_endpoints))
        return output, repairs

    @staticmethod
    def _quality_coverage_item(result: QualityResult, subject: str, endpoints: tuple[Mapping[str, Any], ...]) -> FusionEvidenceItem:
        evaluations = tuple(result.rule_evaluations)
        summaries = tuple(result.dimension_summaries)
        applicability = {item.applicability.value for item in evaluations}
        semantics = {item.measurement_semantics.value for item in evaluations} | {item.measurement_semantics.value for item in summaries}
        if "INCONCLUSIVE" in applicability or "INCONCLUSIVE" in {item.status.value for item in summaries} or "INCONCLUSIVE" in semantics:
            state = "INCONCLUSIVE"
            presence = EvidencePresenceState.OBSERVED
        elif "INSUFFICIENT_EVIDENCE" in applicability:
            state = "INSUFFICIENT_EVIDENCE"
            presence = EvidencePresenceState.OBSERVED
        elif "NOT_APPLICABLE" in applicability:
            state = "NOT_APPLICABLE"
            presence = EvidencePresenceState.OBSERVED
        elif "APPLICABLE" in applicability or any(item.status.value == "MEASURED" for item in summaries):
            state = "EVALUATED_CLEAN"
            presence = EvidencePresenceState.OBSERVED
        else:
            state = "NOT_EVALUATED"
            presence = EvidencePresenceState.OBSERVED
        source_map = {str(item["source_id"]): str(item["snapshot_id"]) for item in endpoints if item.get("source_id") and item.get("snapshot_id")}
        detail = {"quality_run_id": result.quality_run_id, "coverage_state": state, "applicability": sorted(applicability), "measurement_semantics": sorted(semantics), "rule_evaluations": len(evaluations), "dimension_summaries": len(summaries)}
        return FusionEvidenceItem(evidence_id=f"quality:coverage:{result.quality_run_id}:{stable_digest(subject)[:12]}", subject_id=subject, producer_id="quality", family=EvidenceFamily.QUALITY, role=EvidenceRole.DIRECT_OBSERVATION, metric_name="quality_coverage:" + state.lower(), metric_value=None, metric_semantics="quality coverage state is explicit and is not inferred from issue absence", direction=EvidenceDirection.CONTEXT, presence=presence, scope_id=result.quality_run_id, observation_scope=EvidenceReliabilityState.FULL if any(item.measurement_semantics.value.startswith("EXACT_ON_FULL") for item in summaries) else EvidenceReliabilityState.BOUNDED, source_ids=tuple(source_map), snapshot_ids=tuple(source_map.values()), snapshot_by_source=source_map, correlation_group=result.quality_run_id, score_bearing=False, qualitative_text=json.dumps(detail, sort_keys=True))

    def _ml_items(self, result: AppliedMLResult, subjects: Mapping[str, str], failures: list[FusionFailure]) -> list[FusionEvidenceItem]:
        output = []
        for item in result.learned_evidence:
            subject = subjects.get(item.candidate_id)
            if subject is None:
                failures.append(FusionFailure(failure_id="fusion-ml-binding-" + stable_digest(item.candidate_id)[:20], kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="learned evidence candidate_id is not in selected candidates", evidence_refs=(item.evidence_id,)))
                continue
            output.append(FusionEvidenceItem(evidence_id=item.evidence_id, subject_id=subject, producer_id="applied-ml", family=EvidenceFamily.APPLIED_ML, role=EvidenceRole.DERIVED_INTERPRETATION, metric_name="learned_ranking_score", metric_value=item.ranking_score, metric_semantics="uncalibrated learned ranking; auxiliary only", direction=EvidenceDirection.CONTEXT, scope_id=item.model_id, observation_scope=EvidenceReliabilityState.BOUNDED, derived_from_refs=item.input_evidence_refs, correlation_group=item.model_id, score_bearing=False, qualitative_text="derived ML evidence; not independently scored"))
        return output

    def _semantic_result_items(self, result: SemanticEvidenceResult | LLMEvidence, subjects: Mapping[str, str], bindings: tuple[Any, ...], failures: list[FusionFailure]) -> list[FusionEvidenceItem]:
        evidence = result.evidence if isinstance(result, SemanticEvidenceResult) else result
        if evidence is None:
            return []
        explicit = [_dump(item) for item in bindings if isinstance(item, FusionSubjectBinding)]
        bound_subjects: set[str] = set()
        for upstream in evidence.subject_refs:
            candidate = next((item for item in explicit if item["upstream_subject_ref"] == upstream), None)
            if candidate is not None:
                if candidate["candidate_id"] not in subjects or candidate["fusion_subject_id"] != subjects[candidate["candidate_id"]]:
                    failures.append(FusionFailure(failure_id="fusion-semantic-binding-" + stable_digest(upstream)[:20], kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="semantic subject binding is unknown or inconsistent", evidence_refs=(evidence.evidence_id,)))
                    return []
                bound_subjects.add(candidate["fusion_subject_id"])
            elif upstream in subjects:
                bound_subjects.add(subjects[upstream])
            elif upstream in subjects.values():
                bound_subjects.add(upstream)
            else:
                failures.append(FusionFailure(failure_id="fusion-semantic-binding-" + stable_digest(upstream)[:20], kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="semantic evidence requires a known subject binding", evidence_refs=(evidence.evidence_id,)))
                return []
        if not bound_subjects:
            failures.append(FusionFailure(failure_id="fusion-semantic-binding-empty", kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="semantic evidence has no bindable subject", evidence_refs=(evidence.evidence_id,)))
            return []
        if len(bound_subjects) != 1:
            failures.append(FusionFailure(failure_id="fusion-semantic-binding-multi" + stable_digest(tuple(sorted(bound_subjects)))[:20], kind=FusionFailureKind.SUBJECT_BINDING_ERROR, detail="semantic evidence references multiple independently bound subjects", evidence_refs=(evidence.evidence_id,)))
            return []
        bound_subject = next(iter(bound_subjects))
        output = []
        for hypothesis in evidence.hypotheses:
            direction = EvidenceDirection.SUPPORTS if hypothesis.kind.value == "SUPPORTS_HYPOTHESIS" else EvidenceDirection.CONTRADICTS if hypothesis.kind.value == "CONTRADICTS_HYPOTHESIS" else EvidenceDirection.CONTEXT
            output.append(FusionEvidenceItem(evidence_id=hypothesis.hypothesis_id, subject_id=bound_subject, producer_id="semantic-ai", family=EvidenceFamily.SEMANTIC_AI, role=EvidenceRole.DERIVED_INTERPRETATION, metric_name="semantic_hypothesis", metric_value=None, metric_semantics="qualitative provider hypothesis; no numeric conversion", direction=direction, scope_id=evidence.context_manifest.input_fingerprint, observation_scope=EvidenceReliabilityState.BOUNDED, derived_from_refs=evidence.context_manifest.allowed_provider_evidence_refs, correlation_group=evidence.evidence_id, score_bearing=False, qualitative_text=hypothesis.statement))
        return output

    @staticmethod
    def _subject_signals(by_subject: dict[str, list[NormalizedEvidenceSignal]], subject: str, request: EvidenceFusionRequest, failures: list[FusionFailure]) -> tuple[NormalizedEvidenceSignal, ...]:
        values = by_subject.get(subject, [])
        if len(values) > request.max_evidence_per_subject:
            failures.append(FusionFailure(failure_id="fusion-evidence-bound-" + stable_digest(subject)[:20], kind=FusionFailureKind.BOUNDED_INPUT_DISCARDED, detail="max_evidence_per_subject discarded material evidence", subject_id=subject))
        return tuple(values[:request.max_evidence_per_subject])

    def _signals(self, items: list[FusionEvidenceItem], request: EvidenceFusionRequest, failures: list[FusionFailure]) -> dict[str, list[NormalizedEvidenceSignal]]:
        output: dict[str, list[NormalizedEvidenceSignal]] = {}
        seen: dict[str, str] = {}
        dimensions = {item.dimension_id: item for item in request.policy.scoring_dimensions}
        metrics = {metric: item for item in request.policy.scoring_dimensions for metric in item.metric_names}
        rules = {item.rule_id: item for item in request.policy.normalization_rules}
        for item in sorted(items, key=lambda value: (value.subject_id, value.evidence_id, value.metric_name)):
            if item.subject_id not in request.relationship_candidate_ids and item.subject_id not in request.mapping_candidate_ids and not item.subject_id.startswith(("rel:", "map:")):
                continue
            payload = json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            prior = seen.get(item.evidence_id)
            if prior is not None and prior != payload:
                failures.append(FusionFailure(failure_id="fusion-collision-" + stable_digest(item.evidence_id)[:24], kind=FusionFailureKind.EVIDENCE_ID_COLLISION, detail="one evidence_id claimed materially different payloads", evidence_refs=(item.evidence_id,)))
                continue
            seen[item.evidence_id] = payload
            dimension = dimensions.get(item.score_dimension_id or "") or metrics.get(item.metric_name)
            score_bearing = item.score_bearing and item.presence is EvidencePresenceState.OBSERVED
            normalized: float | None = None
            method = "qualitative_only_v1"
            dimension_id = dimension.dimension_id if dimension else None
            if score_bearing:
                if dimension is None:
                    failures.append(FusionFailure(failure_id="fusion-unsupported-" + stable_digest(item.metric_name)[:20], kind=FusionFailureKind.UNSUPPORTED_SCORE_SEMANTICS, detail=f"score-bearing metric {item.metric_name} is not declared by policy", subject_id=item.subject_id, evidence_refs=(item.evidence_id,)))
                    score_bearing = False
                else:
                    rule = rules.get(dimension.normalization_rule_id)
                    if rule is None or item.metric_name not in rule.metric_names:
                        failures.append(FusionFailure(failure_id="fusion-unsupported-" + stable_digest(item.metric_name)[:20], kind=FusionFailureKind.UNSUPPORTED_SCORE_SEMANTICS, detail=f"metric {item.metric_name} has no policy-authorized normalization", subject_id=item.subject_id, evidence_refs=(item.evidence_id,)))
                        score_bearing = False
                    elif item.metric_value is None or not (-1 <= float(item.metric_value) <= 1):
                        failures.append(FusionFailure(failure_id="fusion-unsupported-value-" + stable_digest(item.evidence_id)[:20], kind=FusionFailureKind.UNSUPPORTED_SCORE_SEMANTICS, detail="score-bearing evidence value is outside policy bounds", subject_id=item.subject_id, evidence_refs=(item.evidence_id,)))
                        score_bearing = False
                    elif rule.method.startswith("ordinal"):
                        normalized, method = 1.0 / (1.0 + float(item.metric_value)), rule.method
                    elif rule.method.startswith("compatibility_strength"):
                        normalized, method = (2.0 * float(item.metric_value)) - 1.0, rule.method
                    elif rule.method.startswith("identity"):
                        normalized, method = float(item.metric_value), rule.method
                    else:
                        failures.append(FusionFailure(failure_id="fusion-unsupported-rule-" + stable_digest(rule.rule_id)[:20], kind=FusionFailureKind.UNSUPPORTED_SCORE_SEMANTICS, detail=f"normalization rule {rule.rule_id} is not executable by Step17", subject_id=item.subject_id, evidence_refs=(item.evidence_id,)))
                        score_bearing = False
            signal = NormalizedEvidenceSignal(signal_id=fusion_signal_id(item.evidence_id, item.subject_id, item.metric_name), subject_id=item.subject_id, evidence_id=item.evidence_id, producer_id=item.producer_id, family=item.family, role=item.role, raw_metric_name=item.metric_name, raw_metric_value=item.metric_value, raw_metric_semantics=item.metric_semantics, normalization_method=method, normalization_version="step17-fusion-v2", normalized_value=normalized, direction=item.direction, presence=item.presence, reliability=item.observation_scope, scope_id=item.scope_id, source_ids=item.source_ids, snapshot_ids=item.snapshot_ids, snapshot_by_source=item.snapshot_by_source, snapshot_binding=item.snapshot_binding, derived_from_refs=item.derived_from_refs, correlation_group=item.correlation_group, score_dimension_id=dimension_id, dependency_group=item.dependency_group or (dimension.dependency_group if dimension else None), score_bearing=score_bearing)
            output.setdefault(item.subject_id, []).append(signal)
        return output

    @staticmethod
    def _normalize_dimension(signals: tuple[NormalizedEvidenceSignal, ...], dimension: FusionScoringDimension) -> float | None:
        values = [item for item in signals if item.score_bearing and item.normalized_value is not None and item.score_dimension_id == dimension.dimension_id]
        if not values:
            return None
        if dimension.dimension_id == "inclusion":
            coverage = next((item for item in values if item.raw_metric_name == "inclusion_coverage"), None)
            if coverage is not None:
                return coverage.normalized_value
            orphan = next((item for item in values if item.raw_metric_name == "orphan_ratio"), None)
            return 1.0 - float(orphan.normalized_value) if orphan is not None else None
        return values[0].normalized_value

    @staticmethod
    def _score(signals: tuple[NormalizedEvidenceSignal, ...], policy: FusionPolicyReference) -> FusionScore:
        contributions: dict[str, float] = {}
        observed = 0.0
        eligible = sum(item.weight for item in policy.scoring_dimensions if item.required)
        rules = {item.rule_id: item for item in policy.normalization_rules}
        for dimension in policy.scoring_dimensions:
            if not dimension.required:
                continue
            value = EvidenceFusionService._normalize_dimension(signals, dimension)
            if value is None:
                continue
            signed = value
            rule = rules.get(dimension.normalization_rule_id)
            if dimension.dimension_id != "inclusion" and rule is not None and not rule.method.startswith("compatibility_strength") and any(item.direction is EvidenceDirection.CONTRADICTS and item.score_dimension_id == dimension.dimension_id for item in signals):
                signed = -abs(value)
            contributions[dimension.dimension_id] = round(dimension.weight * signed, 12)
            if dimension.required:
                observed += dimension.weight
        optional_observed = {item.score_dimension_id for item in signals if item.score_bearing and item.normalized_value is not None}
        for dimension in policy.scoring_dimensions:
            if dimension.required or dimension.dimension_id not in optional_observed or dimension.dimension_id in contributions:
                continue
            value = EvidenceFusionService._normalize_dimension(signals, dimension)
            if value is not None:
                contributions[dimension.dimension_id] = round(dimension.weight * value, 12)
                observed += dimension.weight
                eligible += dimension.weight
        value = round(sum(contributions.values()) / eligible, 12) if contributions and eligible else None
        return FusionScore(value=value, score_semantics=policy.score_semantics, eligible_weight=eligible, observed_weight=observed, evidence_coverage=round(observed / eligible, 12) if eligible else 0.0, sufficient=bool(contributions), contributions=contributions)

    @staticmethod
    def _band(score: FusionScore, conflicts: list[Conflict], policy: FusionPolicyReference) -> Any:
        from dirty_data_to_olap.domain.contracts.evidence_fusion import ConfidenceBand
        if conflicts:
            return ConfidenceBand.CONFLICTED
        if not score.sufficient:
            return ConfidenceBand.INSUFFICIENT
        bands = sorted(policy.band_policy, key=lambda item: item.minimum if item.minimum is not None else -2, reverse=True)
        for band in bands:
            if band.minimum is not None and (score.value or 0) >= band.minimum:
                return band.band
            if band.minimum is None and band.maximum is not None and (score.value or 0) < band.maximum:
                return band.band
        return ConfidenceBand.LOW

    @staticmethod
    def _subject_missing(subject: str, signals: tuple[NormalizedEvidenceSignal, ...], statuses: list[ProducerEvidenceStatus], policy: FusionPolicyReference) -> tuple[tuple[str, ...], tuple[str, ...]]:
        missing = []
        for dimension in policy.scoring_dimensions:
            if dimension.required and not any(item.score_dimension_id == dimension.dimension_id and item.presence is EvidencePresenceState.OBSERVED and item.score_bearing for item in signals):
                missing.append("subject:dimension:" + dimension.dimension_id)
        if policy.subject_kind is FusionSubjectKind.MAPPING and not any(item.family is EvidenceFamily.SCHEMA_MATCHING and item.raw_metric_name.startswith("matcher_rank:") and item.presence is EvidencePresenceState.OBSERVED and item.score_bearing for item in signals):
            missing.append("subject:matcher_evidence")
        required = set(policy.required_producer_families or (EvidenceFamily.PROFILE, EvidenceFamily.DEPENDENCY, EvidenceFamily.QUALITY))
        for family in sorted(required, key=lambda item: item.value):
            matching = [item for item in statuses if item.family is family]
            if not matching:
                missing.append("producer:" + family.value.lower())
            else:
                missing.extend("producer:" + item.result_id for item in matching if item.state is not ProducerResultState.COMPLETE)
        unavailable = tuple("producer:" + item.result_id for item in statuses if item.state in {ProducerResultState.UNAVAILABLE, ProducerResultState.SKIPPED, ProducerResultState.NOT_CONFIGURED, ProducerResultState.PRIVACY_BLOCKED})
        unavailable += tuple(item.evidence_id for item in signals if item.presence is not EvidencePresenceState.OBSERVED)
        return tuple(sorted(set(missing))), tuple(sorted(set(unavailable)))

    def _relationship_decision(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], request: EvidenceFusionRequest, statuses: list[ProducerEvidenceStatus], competitors: list[Mapping[str, Any]], missing: tuple[str, ...], policy_ok: bool) -> RelationshipDecision:
        score = self._score(signals, request.policy)
        state = DecisionState.INCOMPLETE_REQUIRED_EVIDENCE if missing or not policy_ok else DecisionState.REVIEW_REQUIRED
        context = {"candidate": candidate, "subject_kind": "RELATIONSHIP", "competitors": [item.get("candidate_id") for item in competitors], "statuses": [item.model_dump(mode="json") for item in statuses], "missing": missing, "contract": request.fusion_contract_version}
        fingerprint = fusion_input_fingerprint(subject, signals, request.policy, context)
        return RelationshipDecision(decision_id=fusion_decision_id(subject, fingerprint, request.policy), candidate_id=str(candidate["candidate_id"]), subject_id=subject, from_table=str(candidate["from_table"]), from_columns=tuple(candidate["from_columns"]), to_table=str(candidate["to_table"]), to_columns=tuple(candidate["to_columns"]), proposed_cardinality=str(candidate.get("proposed_cardinality", "MANY_TO_ONE")), score=score, confidence_band=self._band(score, conflicts, request.policy), decision_state=state, policy=request.policy, supporting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.SUPPORTS), contradicting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS), missing_evidence_refs=missing, unavailable_evidence_refs=tuple("producer:" + item.result_id for item in statuses if item.state in {ProducerResultState.UNAVAILABLE, ProducerResultState.SKIPPED, ProducerResultState.NOT_CONFIGURED, ProducerResultState.PRIVACY_BLOCKED}), conflict_refs=tuple(item.conflict_id for item in conflicts), explanation=self._explanation(signals, conflicts, missing, score), input_evidence_fingerprint=fingerprint, provenance="application.evidence_fusion:evidence-fusion-v2")

    def _mapping_decision(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], request: EvidenceFusionRequest, statuses: list[ProducerEvidenceStatus], competitors: list[Mapping[str, Any]], missing: tuple[str, ...], policy_ok: bool) -> SemanticMappingDecision:
        score = self._score(signals, request.policy)
        context = {"candidate": candidate, "subject_kind": "MAPPING", "competitors": [item.get("candidate_id") for item in competitors], "statuses": [item.model_dump(mode="json") for item in statuses], "missing": missing, "contract": request.fusion_contract_version}
        fingerprint = fusion_input_fingerprint(subject, signals, request.policy, context)
        return SemanticMappingDecision(decision_id=fusion_decision_id(subject, fingerprint, request.policy), candidate_id=str(candidate["candidate_id"]), subject_id=subject, source_id=str(candidate["source_id"]), source_column_id=str(candidate["source_column_id"]), target_source_id=str(candidate["target_source_id"]), target_column_id=str(candidate["target_column_id"]), score=score, confidence_band=self._band(score, conflicts, request.policy), decision_state=DecisionState.REVIEW_REQUIRED if not missing else DecisionState.INCOMPLETE_REQUIRED_EVIDENCE, policy=request.policy, supporting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.SUPPORTS), contradicting_signal_refs=tuple(item.signal_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS), missing_evidence_refs=missing, unavailable_evidence_refs=tuple("producer:" + item.result_id for item in statuses if item.state in {ProducerResultState.UNAVAILABLE, ProducerResultState.SKIPPED, ProducerResultState.NOT_CONFIGURED, ProducerResultState.PRIVACY_BLOCKED}), conflict_refs=tuple(item.conflict_id for item in conflicts), explanation=self._explanation(signals, conflicts, missing, score), input_evidence_fingerprint=fingerprint, provenance="application.evidence_fusion:evidence-fusion-v2")

    @staticmethod
    def _explanation(signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], missing: tuple[str, ...], score: FusionScore) -> DecisionExplanation:
        supports = tuple(f"{item.evidence_id}: {item.raw_metric_name}={item.raw_metric_value!r}; dimension={item.score_dimension_id}; contribution={score.contributions.get(item.score_dimension_id or '', 0)}" for item in signals if item.direction is EvidenceDirection.SUPPORTS)
        contradicts = tuple(f"{item.evidence_id}: {item.raw_metric_name}={item.raw_metric_value!r}; dimension={item.score_dimension_id}; contribution={score.contributions.get(item.score_dimension_id or '', 0)}" for item in signals if item.direction is EvidenceDirection.CONTRADICTS)
        limitations = tuple(missing) + tuple(f"conflict {item.conflict_id}: {item.explanation}" for item in conflicts) + tuple(f"{item.evidence_id}: visible but non-score-bearing" for item in signals if not item.score_bearing)
        return DecisionExplanation(supports=supports, contradicts=contradicts, limitations=limitations, reconstruction=(f"score={score.value!r} semantics={score.score_semantics} eligible_weight={score.eligible_weight} observed_weight={score.observed_weight} coverage={score.evidence_coverage}", "decision remains REVIEW_REQUIRED until G5"))

    @staticmethod
    def _bundle(subject: str, kind: FusionSubjectKind, hypothesis: str, signals: tuple[NormalizedEvidenceSignal, ...], conflicts: list[Conflict], missing: tuple[str, ...], unavailable: tuple[str, ...]) -> EvidenceBundle:
        lineage = tuple(EvidenceLineageReference(evidence_id=item.evidence_id, producer_id=item.producer_id, family=item.family, source_ids=item.source_ids, snapshot_ids=item.snapshot_ids, snapshot_by_source=item.snapshot_by_source, snapshot_binding=item.snapshot_binding, scope_id=item.scope_id, observation_scope=item.reliability.value, correlation_group=item.correlation_group, derived_from_refs=item.derived_from_refs) for item in signals)
        return EvidenceBundle(bundle_id="fusion_bundle_" + stable_digest((subject, tuple(item.signal_id for item in signals), missing, unavailable))[:32], subject_id=subject, subject_kind=kind, hypothesis=hypothesis, lineage=lineage, signals=signals, supporting_evidence_refs=tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.SUPPORTS), contradicting_evidence_refs=tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS), missing_evidence_refs=missing, unavailable_evidence_refs=unavailable)

    @staticmethod
    def _candidate_scope_failure(candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...]) -> FusionFailure | None:
        expected: dict[str, str] = {}
        if candidate.get("source_id") and (candidate.get("snapshot_id") or candidate.get("source_snapshot_id")):
            expected[str(candidate["source_id"])] = str(candidate.get("snapshot_id") or candidate.get("source_snapshot_id"))
        if candidate.get("target_source_id") and candidate.get("target_snapshot_id"):
            expected[str(candidate["target_source_id"])] = str(candidate["target_snapshot_id"])
        if not expected:
            return None
        mismatched = tuple(item.evidence_id for item in signals if any(source in item.snapshot_by_source and item.snapshot_by_source[source] != snapshot for source, snapshot in expected.items()))
        return FusionFailure(failure_id="fusion-candidate-scope-" + stable_digest(subject)[:24], kind=FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH, detail="candidate snapshot does not match evidence snapshot", subject_id=subject, evidence_refs=mismatched) if mismatched else None

    @staticmethod
    def _scope_failures(by_subject: dict[str, list[NormalizedEvidenceSignal]], failures: list[FusionFailure]) -> None:
        for subject, signals in sorted(by_subject.items()):
            by_source: dict[str, set[str]] = {}
            for signal in signals:
                if signal.snapshot_binding is FusionSnapshotBinding.NOT_APPLICABLE_SCHEMA_METADATA:
                    continue
                for source_id, snapshot_id in signal.snapshot_by_source.items():
                    by_source.setdefault(source_id, set()).add(snapshot_id)
            if any(len(values) > 1 for values in by_source.values()):
                failures.append(FusionFailure(failure_id="fusion-scope-" + stable_digest(subject)[:24], kind=FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH, detail="one subject combines incompatible snapshots for a source", subject_id=subject, evidence_refs=tuple(item.evidence_id for item in signals)))

    def _conflicts_for_relationship(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], all_candidates: list[Mapping[str, Any]], declared: tuple[Any, ...], policy: FusionPolicyReference) -> list[Conflict]:
        conflicts = self._structural_conflicts(subject, signals, policy)
        declared_refs = []
        for item in declared:
            if isinstance(item, DeclaredConstraintInput) and item.from_table == candidate.get("from_table") and item.to_table == candidate.get("to_table") and item.from_columns == tuple(candidate.get("from_columns", ())) and item.to_columns == tuple(candidate.get("to_columns", ())):
                declared_refs.append(item.constraint_id)
            elif isinstance(item, DeclaredConstraint) and item.table_id == candidate.get("from_table") and (item.referenced_table_id or item.referenced_table_name) == candidate.get("to_table"):
                declared_refs.append("declared:" + stable_digest(item.model_dump(mode="json"))[:24])
        contradictions = tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS and self._eligible_conflict_signal(item, ConflictType.DECLARED_DATA_CONFLICT, policy))
        if declared_refs and contradictions:
            conflicts.append(self._conflict(ConflictType.DECLARED_DATA_CONFLICT, subject, tuple(declared_refs), contradictions, "declared FK metadata is retained while observed data contradicts it", "declared_fk_vs_observed_data", policy))
        targets = {(item.get("to_table"), tuple(item.get("to_columns", ()))) for item in all_candidates if item.get("from_table") == candidate.get("from_table") and tuple(item.get("from_columns", ())) == tuple(candidate.get("from_columns", ())) }
        if len(targets) > 1:
            conflicts.append(self._conflict(ConflictType.MULTIPLE_TARGET_AMBIGUITY, subject, (str(candidate.get("candidate_id")),), tuple(str(item.get("candidate_id")) for item in all_candidates), "more than one target is plausible for this source endpoint", "multiple_target_requires_review", policy))
        return conflicts

    def _conflicts_for_mapping(self, candidate: Mapping[str, Any], subject: str, signals: tuple[NormalizedEvidenceSignal, ...], all_candidates: list[Mapping[str, Any]], policy: FusionPolicyReference) -> list[Conflict]:
        conflicts = self._structural_conflicts(subject, signals, policy)
        semantic = tuple(item.evidence_id for item in signals if item.family is EvidenceFamily.SEMANTIC_AI and item.direction is EvidenceDirection.SUPPORTS)
        type_bad = tuple(item.evidence_id for item in signals if item.raw_metric_name == "type_compatibility" and item.direction is EvidenceDirection.CONTRADICTS and self._eligible_conflict_signal(item, ConflictType.TYPE_SEMANTIC_CONFLICT, policy))
        if semantic and type_bad:
            conflicts.append(self._conflict(ConflictType.TYPE_SEMANTIC_CONFLICT, subject, semantic, type_bad, "semantic equivalence conflicts with incompatible type evidence", "semantic_vs_type", policy))
        plausible = [item for item in all_candidates if item.get("source_id") == candidate.get("source_id") and item.get("source_column_id") == candidate.get("source_column_id")]
        if len({(item.get("target_source_id"), item.get("target_column_id")) for item in plausible}) > 1:
            conflicts.append(self._conflict(ConflictType.MULTIPLE_TARGET_AMBIGUITY, subject, (str(candidate.get("candidate_id")),), tuple(str(item.get("candidate_id")) for item in plausible), "one source column has multiple plausible mapping targets", "multiple_mapping_targets_require_review", policy))
        return conflicts

    def _structural_conflicts(self, subject: str, signals: tuple[NormalizedEvidenceSignal, ...], policy: FusionPolicyReference) -> list[Conflict]:
        output = []
        semantic = tuple(item.evidence_id for item in signals if item.family is EvidenceFamily.SEMANTIC_AI and item.direction is EvidenceDirection.SUPPORTS)
        structural = tuple(item.evidence_id for item in signals if item.direction is EvidenceDirection.CONTRADICTS and self._eligible_conflict_signal(item, ConflictType.SEMANTIC_STRUCTURAL_CONFLICT, policy))
        if semantic and structural:
            output.append(self._conflict(ConflictType.SEMANTIC_STRUCTURAL_CONFLICT, subject, semantic, structural, "qualitative semantic support conflicts with structural evidence", "semantic_structural_disagreement", policy))
        type_bad = tuple(item.evidence_id for item in signals if item.raw_metric_name == "type_compatibility" and item.direction is EvidenceDirection.CONTRADICTS and self._eligible_conflict_signal(item, ConflictType.TYPE_SEMANTIC_CONFLICT, policy))
        matcher_support = tuple(item.evidence_id for item in signals if item.raw_metric_name.startswith("matcher_rank:") and item.direction is EvidenceDirection.SUPPORTS)
        if type_bad and (semantic or matcher_support):
            output.append(self._conflict(ConflictType.TYPE_SEMANTIC_CONFLICT, subject, semantic or matcher_support, type_bad, "incompatible type evidence is retained beside semantic or matcher support", "type_semantic_compatibility", policy))
        threshold = next((item.threshold for item in policy.conflict_rules if item.conflict_type is ConflictType.SAMPLE_FULLSCAN_CONFLICT), None)
        if threshold is not None:
            by_metric: dict[str, list[NormalizedEvidenceSignal]] = {}
            for item in signals:
                by_metric.setdefault(item.raw_metric_name, []).append(item)
            for metric, values in by_metric.items():
                sampled = [item for item in values if item.reliability is EvidenceReliabilityState.SAMPLED and item.normalized_value is not None]
                full = [item for item in values if item.reliability is EvidenceReliabilityState.FULL and item.normalized_value is not None]
                if any(set(a.snapshot_by_source) & set(b.snapshot_by_source) and abs(float(a.normalized_value) - float(b.normalized_value)) >= threshold for a in sampled for b in full):
                    output.append(self._conflict(ConflictType.SAMPLE_FULLSCAN_CONFLICT, subject, tuple(item.evidence_id for item in sampled), tuple(item.evidence_id for item in full), f"sampled and full observations disagree for {metric}", "sample_vs_full_observation", policy))
        return output

    @staticmethod
    def _eligible_conflict_signal(signal: NormalizedEvidenceSignal, conflict_type: ConflictType, policy: FusionPolicyReference) -> bool:
        rule = next((item for item in policy.conflict_rules if item.conflict_type is conflict_type), None)
        if rule is None:
            return False
        if rule.eligible_families and signal.family not in rule.eligible_families:
            return False
        if rule.eligible_metric_names and signal.raw_metric_name not in rule.eligible_metric_names:
            return False
        return signal.raw_metric_name != "low_cardinality_risk"

    @staticmethod
    def _conflict(kind: ConflictType, subject: str, supporting: tuple[str, ...], contradicting: tuple[str, ...], explanation: str, rule: str, policy: FusionPolicyReference) -> Conflict:
        refs = tuple(sorted(set(supporting + contradicting)))
        return Conflict(conflict_id=fusion_conflict_id(subject, kind, refs), conflict_type=kind, subject_id=subject, supporting_evidence_refs=tuple(sorted(set(supporting))), contradicting_evidence_refs=tuple(sorted(set(contradicting))), explanation=explanation, policy_rule=rule, scope_id="subject:" + stable_digest(subject)[:20], provenance="application.evidence_fusion:evidence-fusion-v2")

    @staticmethod
    def _unique_conflicts(values: list[Conflict]) -> list[Conflict]:
        unique = {item.conflict_id: item for item in values}
        return [unique[key] for key in sorted(unique)]

    def _publish(self, result: Any) -> Any:
        if self.artifact_root is None:
            return result
        root = self.artifact_root.resolve()
        project_root = Path.cwd().resolve()
        try:
            root.relative_to(project_root)
        except ValueError:
            raise ValueError("fusion artifact root must remain under the project root") from None
        target_dir = root / "evidence_fusion" / "manifests"
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = result.model_dump(mode="json", exclude={"artifacts"})
        serialized = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        target = target_dir / f"{result.request.execution_context_id}.json"
        with tempfile.NamedTemporaryFile("wb", dir=target_dir, prefix=".fusion-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        location = str(target.relative_to(project_root)).replace("\\", "/")
        artifact = FusionArtifactReference(artifact_id="fusion-artifact-" + stable_digest(serialized)[:24], artifact_type="evidence_fusion_result", location=location, content_hash=hashlib.sha256(serialized).hexdigest())
        return result.model_copy(update={"artifacts": (artifact,)})
