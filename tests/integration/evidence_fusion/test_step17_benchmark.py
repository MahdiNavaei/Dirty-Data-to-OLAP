from __future__ import annotations

import json
from pathlib import Path

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import *


ROOT = Path(__file__).resolve().parents[3]


def _item(raw: dict, subject: str, *, source_ids=("src",), snapshot_map=None) -> FusionEvidenceItem:
    snapshot_map = snapshot_map or {source_ids[0]: "snapshot"}
    return FusionEvidenceItem(evidence_id=raw["evidence_id"], subject_id=subject, producer_id="benchmark", family=raw.get("family", "DEPENDENCY"), metric_name=raw["metric_name"], metric_value=raw.get("metric_value"), metric_semantics="safe benchmark aggregate observation", direction=raw.get("direction", "SUPPORTS"), scope_id="benchmark", observation_scope=raw.get("reliability", "FULL"), source_ids=source_ids, snapshot_ids=tuple(snapshot_map.values()), snapshot_by_source=snapshot_map, correlation_group=raw["evidence_id"], score_dimension_id=raw.get("score_dimension_id"), score_bearing=raw.get("score_bearing", True), qualitative_text=raw.get("qualitative_text"))


def _status(raw: dict) -> ProducerEvidenceStatus:
    return ProducerEvidenceStatus(producer_id=raw["producer_id"], family=raw["family"], state=raw["state"], result_id=raw["result_id"])


def _run(case_id: str, runtime: dict):
    defaults = runtime["defaults"]
    override = runtime["cases"].get(case_id, {})
    kind = override.get("kind", "relationship")
    if kind == "mapping":
        candidate = {"candidate_id": case_id + "-candidate", "source_id": "crm", "source_column_id": "customer_code", "target_source_id": "erp", "target_column_id": "client_no"}
        subject = "map:crm:customer_code<->erp:client_no"
        policy = EvidenceFusionService.load_policy("mapping")
        candidate_ids = (candidate["candidate_id"],)
        evidence = [{"evidence_id": "matcher", "metric_name": "matcher_rank", "metric_value": 1.0, "direction": "SUPPORTS", "family": "SCHEMA_MATCHING", "score_dimension_id": "matcher_rank"}, {"evidence_id": "type", "metric_name": "type_compatibility", "metric_value": 1.0, "direction": "SUPPORTS", "family": "SCHEMA_MATCHING", "score_dimension_id": "type_compatibility"}]
        snapshot_map = {"crm": "snap-crm", "erp": "snap-erp"}
    else:
        candidate = dict(defaults["relationship_candidate"], candidate_id=case_id + "-candidate")
        subject = "rel:orders:customer_id->customers:id"
        policy = EvidenceFusionService.load_policy()
        candidate_ids = (candidate["candidate_id"],)
        evidence = list(override.get("relationship_evidence", defaults["relationship_evidence"]))
        snapshot_map = {"src": "wrong-snapshot" if override.get("wrong_snapshot") else "snapshot"}
    declared = ()
    if override.get("declared_fk"):
        declared = (DeclaredConstraintInput(constraint_id="declared-fk", constraint_type="FOREIGN_KEY", source_id="src", from_table="orders", from_columns=("customer_id",), to_table="customers", to_columns=("id",), scope_id="catalog:src"),)
    if override.get("semantic_support"):
        evidence.append({"evidence_id": "semantic", "metric_name": "semantic_hypothesis", "metric_value": None, "direction": "SUPPORTS", "family": "SEMANTIC_AI", "score_bearing": False, "qualitative_text": "qualitative candidate hypothesis"})
    if override.get("domain_assertion"):
        evidence.append({"evidence_id": "domain", "metric_name": "domain_assertion", "metric_value": None, "direction": "CONTEXT", "family": "DOMAIN_ASSERTION", "score_bearing": False, "qualitative_text": "domain context"})
    if override.get("low_cardinality"):
        evidence.append({"evidence_id": "low-cardinality", "metric_name": "low_cardinality_risk", "metric_value": None, "direction": "CONTRADICTS", "family": "DEPENDENCY", "score_bearing": False})
    if override.get("type_conflict"):
        evidence = [item for item in evidence if item["metric_name"] != "type_compatibility"]
        evidence.append({"evidence_id": "type", "metric_name": "type_compatibility", "metric_value": 0.0, "direction": "CONTRADICTS", "family": "SCHEMA_MATCHING", "score_dimension_id": "type_compatibility"})
    if override.get("sample_full"):
        evidence.append({"evidence_id": "coverage-sample", "metric_name": "inclusion_coverage", "metric_value": .1, "direction": "SUPPORTS", "family": "DEPENDENCY", "score_dimension_id": "inclusion", "reliability": "SAMPLED"})
    if override.get("collision"):
        evidence.append({"evidence_id": "coverage", "metric_name": "inclusion_coverage", "metric_value": .1, "direction": "SUPPORTS", "family": "DEPENDENCY", "score_dimension_id": "inclusion"})
    items = tuple(_item(raw, subject, source_ids=tuple(snapshot_map), snapshot_map=snapshot_map) for raw in evidence)
    statuses = list(_status(raw) for raw in defaults["producer_statuses"])
    if override.get("dependency_state"):
        statuses = [item.model_copy(update={"state": ProducerResultState(override["dependency_state"])}) if item.family is EvidenceFamily.DEPENDENCY else item for item in statuses]
    if override.get("semantic_unavailable"):
        statuses.append(ProducerEvidenceStatus(producer_id="semantic-ai", family=EvidenceFamily.SEMANTIC_AI, state=ProducerResultState.UNAVAILABLE, result_id="semantic-unavailable"))
    if override.get("ml_unavailable"):
        statuses.append(ProducerEvidenceStatus(producer_id="applied-ml", family=EvidenceFamily.APPLIED_ML, state=ProducerResultState.UNAVAILABLE, result_id="ml-unavailable"))
    candidates = [candidate]
    if override.get("competing_target"):
        if kind == "mapping":
            candidates.append(dict(candidate, candidate_id=case_id + "-competing", target_source_id="erp", target_column_id="alternate_client_no"))
            candidate_ids = tuple(item["candidate_id"] for item in candidates)
        else:
            candidates.append(dict(candidate, candidate_id=case_id + "-competing", to_table="clients", to_columns=("id",)))
            candidate_ids = tuple(item["candidate_id"] for item in candidates)
        if kind != "mapping":
            items += tuple(_item(raw, "rel:orders:customer_id->clients:id", source_ids=("src",), snapshot_map={"src": "snapshot"}) for raw in defaults["relationship_evidence"])
    if kind == "mapping":
        request = EvidenceFusionRequest(request_id="benchmark-" + case_id, execution_context_id="benchmark-" + case_id, cross_source_mapping_scope=True, mapping_candidate_ids=candidate_ids, policy=policy)
        candidate_input = EvidenceFusionInputs(producer_statuses=tuple(statuses), mapping_candidates=tuple(candidates), evidence_items=items, declared_constraints=declared)
    else:
        request = EvidenceFusionRequest(request_id="benchmark-" + case_id, execution_context_id="benchmark-" + case_id, relationship_candidate_ids=candidate_ids, policy=policy)
        candidate_input = EvidenceFusionInputs(producer_statuses=tuple(statuses), relationship_candidates=tuple(candidates), evidence_items=items, declared_constraints=declared)
    return EvidenceFusionService().fuse(request, candidate_input)


def test_every_step17_case_executes_from_runtime_inputs_only():
    runtime = json.loads((ROOT / "benchmarks/evidence_fusion/runtime_input.json").read_text(encoding="utf-8"))
    controls = json.loads((ROOT / "benchmarks/evidence_fusion/expected_control.json").read_text(encoding="utf-8"))["cases"]
    case_ids = [item["case_id"] for item in json.loads((ROOT / "benchmarks/evidence_fusion/relationship_cases.json").read_text(encoding="utf-8"))["cases"]] + [item["case_id"] for item in json.loads((ROOT / "benchmarks/evidence_fusion/relationship_cases.json").read_text(encoding="utf-8"))["mapping_cases"]]
    assert set(case_ids) == set(runtime["cases"]) == set(controls)
    for case_id in case_ids:
        result = _run(case_id, runtime)
        control = controls[case_id]
        decisions = (*result.relationships, *result.mappings)
        assert decisions and all(item.decision_state in {DecisionState.REVIEW_REQUIRED, DecisionState.INCOMPLETE_REQUIRED_EVIDENCE} for item in decisions)
        assert all(item.score.confidence_kind is ConfidenceKind.UNCALIBRATED_SCORE for item in decisions)
        if "conflict" in control:
            assert any(item.conflict_type.value == control["conflict"] for item in result.conflicts), case_id
        if control.get("incomplete"):
            assert result.completeness is EvidenceFusionCompleteness.INCOMPLETE_REQUIRED_EVIDENCE
        if control.get("scope_failure"):
            assert any(item.kind is FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH for item in result.failures)
        if control.get("collision_failure"):
            assert any(item.kind is FusionFailureKind.EVIDENCE_ID_COLLISION for item in result.failures)
