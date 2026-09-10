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
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    checks.append(("G4 remains PASS and G5 remains PENDING", state["gates"]["G4_BOUNDED_INTELLIGENCE"] == "PASS" and state["gates"]["G5_INFERENCE_VALIDITY"] == "PENDING"))
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
    dimensions = {item["dimension_id"]: item["weight"] for item in data["scoring_dimensions"]}
    yaml_dimensions = {item["dimension_id"]: item["weight"] for item in human.get("scoring_dimensions", [])}
    rules = [(item["rule_id"], item["conflict_type"], item.get("threshold")) for item in data.get("conflict_rules", [])]
    yaml_rules = [(item["rule_id"], item["conflict_type"], item.get("threshold")) for item in human.get("conflict_rules", [])]
    return data["policy_id"] == human["policy_id"] and str(data["version"]) == str(human["version"]) and data["status"] == human["status"] and dimensions == yaml_dimensions and rules == yaml_rules


if __name__ == "__main__":
    raise SystemExit(main())
