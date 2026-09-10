"""Behavioral Step17 integrity validator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    EvidenceDirection, EvidenceFamily, EvidenceFusionInputs, EvidenceFusionRequest,
    EvidencePresenceState, EvidenceReliabilityState, EvidenceRole, FusionEvidenceItem,
    FusionFailureKind, FusionPolicyStatus, ProducerEvidenceStatus, ProducerResultState,
    ExpectedProducerResult,
)


def _statuses(*, dependency=ProducerResultState.COMPLETE):
    return tuple(ProducerEvidenceStatus(producer_id=name, family=family, state=state, result_id=name + "-result") for name, family, state in (
        ("profile", EvidenceFamily.PROFILE, ProducerResultState.COMPLETE),
        ("dependency", EvidenceFamily.DEPENDENCY, dependency),
        ("quality", EvidenceFamily.QUALITY, ProducerResultState.COMPLETE),
    ))


def _relationship_candidate(candidate_id="validator-rel", target="customers"):
    return {"candidate_id": candidate_id, "source_id": "src", "snapshot_id": "snapshot", "from_table": "orders", "from_columns": ("customer_id",), "to_table": target, "to_columns": ("id",), "proposed_cardinality": "MANY_TO_ONE"}


def _mapping_candidate(candidate_id="validator-map"):
    return {"candidate_id": candidate_id, "source_id": "crm", "source_column_id": "customer_code", "target_source_id": "erp", "target_column_id": "client_no"}


def _item(evidence_id, subject, metric_name, value, *, family=EvidenceFamily.DEPENDENCY, direction=EvidenceDirection.SUPPORTS, reliability=EvidenceReliabilityState.FULL, score=True, presence=EvidencePresenceState.OBSERVED, dimension=None):
    return FusionEvidenceItem(evidence_id=evidence_id, subject_id=subject, producer_id="validator", family=family, role=EvidenceRole.DIRECT_OBSERVATION, metric_name=metric_name, metric_value=value, metric_semantics="bounded validator observation", direction=direction, presence=presence, scope_id="src:snapshot", observation_scope=reliability, source_ids=("src",), snapshot_ids=("snapshot",), snapshot_by_source={"src": "snapshot"}, correlation_group=evidence_id, score_dimension_id=dimension, score_bearing=score)


def _relationship_items(subject):
    return (_item("coverage", subject, "inclusion_coverage", .8, dimension="inclusion"), _item("orphan", subject, "orphan_ratio", .2, direction=EvidenceDirection.CONTRADICTS, dimension="inclusion"), _item("unique", subject, "target_uniqueness", .9, dimension="target_uniqueness"), _item("type", subject, "type_compatibility", 1.0, dimension="type_compatibility"))


def _mapping_items(subject, *, reverse=False):
    values = (
        _item("coma", subject, "matcher_rank:coma", 1.0, family=EvidenceFamily.SCHEMA_MATCHING, dimension="matcher:coma:rank"),
        _item("cupid", subject, "matcher_rank:cupid", .7, family=EvidenceFamily.SCHEMA_MATCHING, dimension="matcher:cupid:rank"),
        _item("mapping-type", subject, "type_compatibility", 1.0, family=EvidenceFamily.SCHEMA_MATCHING, dimension="type_compatibility"),
    )
    if reverse:
        values = tuple(reversed(values))
    return tuple(item.model_copy(update={"source_ids": ("crm", "erp"), "snapshot_ids": ("snap-crm", "snap-erp"), "snapshot_by_source": {"crm": "snap-crm", "erp": "snap-erp"}}) for item in values)


def _run_mapping(policy, evidence, *, execution="validator-mapping"):
    candidate = _mapping_candidate()
    subject = "map:crm:customer_code<->erp:client_no"
    statuses = _statuses() + (ProducerEvidenceStatus(producer_id="schema", family=EvidenceFamily.SCHEMA_MATCHING, state=ProducerResultState.COMPLETE, result_id="schema-result"),)
    request = EvidenceFusionRequest(request_id=execution, execution_context_id=execution, cross_source_mapping_scope=True, mapping_candidate_ids=(candidate["candidate_id"],), policy=policy)
    return EvidenceFusionService().fuse(request, EvidenceFusionInputs(producer_statuses=statuses, mapping_candidates=(candidate,), evidence_items=evidence))


def _run_relationship(policy, *, candidate=None, evidence=None, statuses=None, execution="validator"):
    candidate = candidate or _relationship_candidate()
    subject = "rel:orders:customer_id->" + candidate["to_table"] + ":id"
    request = EvidenceFusionRequest(request_id=execution, execution_context_id=execution, relationship_candidate_ids=(candidate["candidate_id"],), policy=policy)
    inputs = EvidenceFusionInputs(producer_statuses=statuses or _statuses(), relationship_candidates=(candidate,), evidence_items=evidence or _relationship_items(subject))
    return EvidenceFusionService().fuse(request, inputs)


def main() -> int:
    checks: list[tuple[str, bool]] = []
    relationship = EvidenceFusionService.load_policy()
    mapping = EvidenceFusionService.load_policy("mapping")
    checks.append(("authoritative policies are typed and uncalibrated", relationship.status is FusionPolicyStatus.UNCALIBRATED and mapping.status is FusionPolicyStatus.UNCALIBRATED and relationship.scoring_dimensions and mapping.scoring_dimensions))
    checks.append(("typed policy runtime is automation-disabled and thresholded", not relationship.automation.enabled and not mapping.automation.enabled and relationship.band_policy and relationship.conflict_rules))
    checks.append(("policy JSON and human YAML identity agree", _yaml_matches_json("relationship") and _yaml_matches_json("mapping")))
    subject = "rel:orders:customer_id->customers:id"
    clean = _run_relationship(relationship, execution="validator-clean")
    score = clean.relationships[0].score
    checks.append(("runtime policy drives exact independent dimensions", set(score.contributions) == {"inclusion", "target_uniqueness", "type_compatibility"} and score.eligible_weight == 2.75 and score.observed_weight == 2.75 and score.contributions["inclusion"] == .8))
    checks.append(("every scoring metric is covered by its declared normalization rule", all(set(dimension.metric_names).issubset(next(rule for rule in relationship.normalization_rules if rule.rule_id == dimension.normalization_rule_id).metric_names) for dimension in relationship.scoring_dimensions) and all(set(dimension.metric_names).issubset(next(rule for rule in mapping.normalization_rules if rule.rule_id == dimension.normalization_rule_id).metric_names) for dimension in mapping.scoring_dimensions)))
    declared_subject = subject
    declared = _item("declared", declared_subject, "declared_foreign_key", 1.0, family=EvidenceFamily.DEPENDENCY, dimension="declared_constraint")
    optional = _run_relationship(relationship, evidence=_relationship_items(subject) + (declared,), execution="validator-optional")
    checks.append(("optional declared-FK evidence expands denominator only when observed", optional.relationships[0].score.eligible_weight == 3.75 and optional.relationships[0].score.observed_weight == 3.75 and optional.relationships[0].score.evidence_coverage == 1.0))
    incompatible = _item("type-bad", subject, "type_compatibility", 0.0, family=EvidenceFamily.SCHEMA_MATCHING, direction=EvidenceDirection.CONTRADICTS, dimension="type_compatibility")
    bad_type = _run_relationship(relationship, evidence=tuple(item for item in _relationship_items(subject) if item.evidence_id != "type") + (incompatible,), execution="validator-type")
    checks.append(("incompatible type is a signed nonzero contribution", bad_type.relationships[0].score.contributions["type_compatibility"] < 0 and bad_type.relationships[0].score.value is not None))
    mapping_subject = "map:crm:customer_code<->erp:client_no"
    mapping_a = _run_mapping(mapping, _mapping_items(mapping_subject), execution="validator-matcher-a")
    mapping_b = _run_mapping(mapping, _mapping_items(mapping_subject, reverse=True), execution="validator-matcher-b")
    checks.append(("matcher families remain separate and order invariant", {item.raw_metric_name for item in mapping_a.signals} >= {"matcher_rank:coma", "matcher_rank:cupid"} and mapping_a.mappings[0].score == mapping_b.mappings[0].score and mapping_a.mappings[0].decision_id == mapping_b.mappings[0].decision_id))
    changed_dimensions = tuple(item.model_copy(update={"weight": 2.0}) if item.dimension_id == "inclusion" else item for item in relationship.scoring_dimensions)
    changed_policy = relationship.model_copy(update={"scoring_dimensions": changed_dimensions, "content_hash": "validator-policy-b"})
    changed = _run_relationship(changed_policy, execution="validator-policy-b")
    irrelevant = _run_relationship(relationship.model_copy(update={"content_hash": "validator-irrelevant"}), execution="validator-policy-c")
    checks.append(("policy replay changes score and identity", changed.relationships[0].score.value != score.value and changed.relationships[0].decision_id != clean.relationships[0].decision_id))
    checks.append(("irrelevant policy identity does not alter score semantics", irrelevant.relationships[0].score.value == score.value))
    mapping_result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="mapping", execution_context_id="mapping", mapping_candidate_ids=("validator-map",), cross_source_mapping_scope=True, policy=mapping), EvidenceFusionInputs(producer_statuses=_statuses(), mapping_candidates=(_mapping_candidate(),), evidence_items=()))
    mixed = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="mixed", execution_context_id="mixed", relationship_candidate_ids=("validator-rel",), policy=relationship), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_relationship_candidate(),), mapping_candidates=(_mapping_candidate(),)))
    checks.append(("relationship and mapping policies are separated", bool(mapping_result.mappings) and any(item.kind is FusionFailureKind.POLICY_INVALID for item in mixed.failures)))
    unknown = _run_relationship(relationship, evidence=_relationship_items(subject) + (_item("unknown", subject, "unknown_metric", .5),), execution="validator-unknown")
    checks.append(("unknown score semantics fail closed", any(item.kind is FusionFailureKind.UNSUPPORTED_SCORE_SEMANTICS for item in unknown.failures)))
    non_observed = _run_relationship(relationship, evidence=(_item("unavailable", subject, "inclusion_coverage", .9, presence=EvidencePresenceState.UNAVAILABLE),), execution="validator-unavailable")
    checks.append(("non-observed evidence remains visible and nonnumeric", any(item.evidence_id == "unavailable" and item.normalized_value is None and not item.score_bearing for item in non_observed.signals) and "unavailable" in non_observed.bundles[0].unavailable_evidence_refs))
    competing = _relationship_candidate("validator-rel-2", "clients")
    two = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="target", execution_context_id="target", relationship_candidate_ids=("validator-rel", "validator-rel-2"), policy=relationship), EvidenceFusionInputs(producer_statuses=_statuses(), relationship_candidates=(_relationship_candidate(), competing), evidence_items=_relationship_items(subject) + _relationship_items("rel:orders:customer_id->clients:id")))
    checks.append(("multiple target changes applicability identity", any(item.conflict_type.value == "MULTIPLE_TARGET_AMBIGUITY" for item in two.conflicts)))
    state_change = _run_relationship(relationship, statuses=_statuses(dependency=ProducerResultState.INCOMPLETE), execution="validator-state")
    checks.append(("required producer state changes completeness and identity", state_change.completeness.value == "INCOMPLETE_REQUIRED_EVIDENCE" and state_change.relationships[0].input_evidence_fingerprint != clean.relationships[0].input_evidence_fingerprint))
    checks.append(("all emitted decisions remain review-only", all(item.decision_state.value in {"REVIEW_REQUIRED", "INCOMPLETE_REQUIRED_EVIDENCE"} for item in (*clean.relationships, *mapping_result.mappings))))
    expected = (ExpectedProducerResult(family=EvidenceFamily.PROFILE, producer_id="profile", result_id="profile-result"), ExpectedProducerResult(family=EvidenceFamily.PROFILE, producer_id="profile", result_id="missing-profile"))
    expected_result = _run_relationship(relationship, execution="validator-expected", statuses=_statuses())
    expected_result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="validator-expected", execution_context_id="validator-expected", relationship_candidate_ids=("validator-rel",), policy=relationship, expected_producer_results=expected), EvidenceFusionInputs(producer_statuses=_statuses() + (ProducerEvidenceStatus(producer_id="profile", family=EvidenceFamily.PROFILE, state=ProducerResultState.COMPLETE, result_id="profile-result", input_fingerprint="different"),), relationship_candidates=(_relationship_candidate(),), evidence_items=_relationship_items(subject)))
    checks.append(("producer identity and stale duplicate evidence are explicit", any(item.kind is FusionFailureKind.STALE_EVIDENCE for item in expected_result.failures) and any("missing-profile" in item.detail for item in expected_result.failures)))
    from dirty_data_to_olap.application.evidence_fusion import _producer_state
    checks.append(("NOT_CONFIGURED remains distinct from unavailable", _producer_state(type("NotConfigured", (), {"status": "NOT_CONFIGURED"})()) is ProducerResultState.NOT_CONFIGURED and _producer_state(type("Unavailable", (), {"status": "UNAVAILABLE"})()) is ProducerResultState.UNAVAILABLE))
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    checks.append(("G4 remains PASS and G5 is pending or review-only validated", state["gates"]["G4_BOUNDED_INTELLIGENCE"] == "PASS" and state["gates"]["G5_INFERENCE_VALIDITY"] in {"PENDING", "REVIEW_ONLY_VALIDATED"}))
    checks.append(("Step18 implementation is absent", not (ROOT / "src/dirty_data_to_olap/application/ml_evaluation.py").exists()))
    runtime = json.loads((ROOT / "benchmarks/evidence_fusion/runtime_input.json").read_text(encoding="utf-8"))
    controls = json.loads((ROOT / "benchmarks/evidence_fusion/expected_control.json").read_text(encoding="utf-8"))
    cases = json.loads((ROOT / "benchmarks/evidence_fusion/relationship_cases.json").read_text(encoding="utf-8"))
    case_ids = {item["case_id"] for item in cases["cases"] + cases["mapping_cases"]}
    checks.append(("benchmark runtime inputs and expected controls are separate", set(runtime["cases"]) == case_ids == set(controls["cases"]) and len(case_ids) == 23 and runtime.get("expected_control") is None))
    failed = [name for name, passed in checks if not passed]
    if failed:
        print("FAIL")
        print("\n".join("- " + name for name in failed))
        return 1
    print(f"PASS: evidence_fusion_checks={len(checks)}")
    return 0


def _yaml_matches_json(kind: str) -> bool:
    stem = "mapping_fusion_v1" if kind == "mapping" else "relationship_fusion_v1"
    data = json.loads((ROOT / "policies/evidence-fusion" / (stem + ".json")).read_text(encoding="utf-8"))
    human = yaml.safe_load((ROOT / "policies/evidence-fusion" / (stem + ".yml")).read_text(encoding="utf-8"))
    def normalize_dimension(item):
        return {key: item.get(key) for key in ("dimension_id", "metric_names", "weight", "normalization_rule_id", "dependency_group", "required")}

    def normalize_rule(item):
        return {key: item.get(key) for key in ("rule_id", "metric_names", "method", "version")}

    def normalize_conflict(item):
        return {key: item.get(key) for key in ("rule_id", "conflict_type", "threshold", "semantics", "eligible_metric_names", "eligible_families")}

    dimensions = sorted((normalize_dimension(item) for item in data["scoring_dimensions"]), key=lambda item: item["dimension_id"])
    yaml_dimensions = sorted((normalize_dimension(item) for item in human.get("scoring_dimensions", [])), key=lambda item: item["dimension_id"])
    rules = sorted((normalize_rule(item) for item in data.get("normalization_rules", [])), key=lambda item: item["rule_id"])
    yaml_rules = sorted((normalize_rule(item) for item in human.get("normalization_rules", [])), key=lambda item: item["rule_id"])
    conflicts = sorted((normalize_conflict(item) for item in data.get("conflict_rules", [])), key=lambda item: item["rule_id"])
    yaml_conflicts = sorted((normalize_conflict(item) for item in human.get("conflict_rules", [])), key=lambda item: item["rule_id"])
    return (data["policy_id"] == human["policy_id"] and str(data["version"]) == str(human["version"])
            and data["status"] == human["status"]
            and dimensions == yaml_dimensions
            and rules == yaml_rules
            and conflicts == yaml_conflicts
            and data.get("required_producer_families") == human.get("critical_evidence", {}).get("required_producers")
            and bool(data.get("cross_source_mapping_requires_schema_matching")) == bool(human.get("critical_evidence", {}).get("cross_source_mapping_requires_schema_matching")))


if __name__ == "__main__":
    raise SystemExit(main())
