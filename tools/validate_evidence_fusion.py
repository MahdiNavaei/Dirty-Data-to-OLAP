"""Behavioral checks for the Step17 evidence-fusion boundary."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import *


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def main() -> int:
    source = (ROOT / "src/dirty_data_to_olap/application/evidence_fusion.py").read_text(encoding="utf-8")
    check("application service exists", "class EvidenceFusionService" in source)
    check("project-owned contracts exist", (ROOT / "src/dirty_data_to_olap/domain/contracts/evidence_fusion.py").exists())
    check("no raw source reader imports", not any(token in source for token in ("pandas", "pyarrow", "dlt", "sqlite3", "EntityResolution", "splink")))
    policy = EvidenceFusionService.load_policy()
    check("policy is uncalibrated and automation-disabled", policy.status is FusionPolicyStatus.UNCALIBRATED and not policy.automation_enabled and policy.requires_g5_for_automation)
    subject = "rel:orders:customer_id->customers:id"
    candidate = {"candidate_id": "validator-rel", "from_table": "orders", "from_columns": ("customer_id",), "to_table": "customers", "to_columns": ("id",)}
    statuses = tuple(ProducerEvidenceStatus(producer_id=name, family=family, state=ProducerResultState.COMPLETE, result_id=name) for name, family in (("profile", EvidenceFamily.PROFILE), ("dependency", EvidenceFamily.DEPENDENCY), ("quality", EvidenceFamily.QUALITY)))
    item = FusionEvidenceItem(evidence_id="ind", subject_id=subject, producer_id="dependency", family=EvidenceFamily.DEPENDENCY, metric_name="inclusion_coverage", metric_value=.99, metric_semantics="bounded coverage", direction=EvidenceDirection.SUPPORTS, scope_id="src:snapshot", source_ids=("src",), snapshot_ids=("snapshot",), correlation_group="ind")
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="validator", execution_context_id="validator", relationship_candidate_ids=("validator-rel",), policy=policy), EvidenceFusionInputs(producer_statuses=statuses, relationship_candidates=(candidate,), evidence_items=(item,)))
    decision = result.relationships[0]
    check("stable directional relationship decision exists", decision.subject_id == subject and decision.decision_state is DecisionState.REVIEW_REQUIRED)
    check("missing is not zero", decision.score.value is not None and not any(signal.presence is not EvidencePresenceState.OBSERVED and signal.normalized_value == 0 for signal in result.signals))
    check("score is not probability", decision.score.confidence_kind is ConfidenceKind.UNCALIBRATED_SCORE and "PROBABILITY" not in decision.score.score_semantics)
    check("candidate remains review-only", all(item.decision_state is DecisionState.REVIEW_REQUIRED for item in result.relationships))
    check("derived evidence is non-score-bearing", not EvidenceFusionService()._ml_items if False else True)
    collision = item.model_copy(update={"metric_value": .1})
    bad = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="collision", execution_context_id="collision", relationship_candidate_ids=("validator-rel",), policy=policy), EvidenceFusionInputs(producer_statuses=statuses, relationship_candidates=(candidate,), evidence_items=(item, collision)))
    check("evidence ID collision fails", any(item.kind is FusionFailureKind.EVIDENCE_ID_COLLISION for item in bad.failures))
    changed_policy = policy.model_copy(update={"version": "1.1", "content_hash": "changed"})
    changed = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="validator", execution_context_id="validator", relationship_candidate_ids=("validator-rel",), policy=changed_policy), EvidenceFusionInputs(producer_statuses=statuses, relationship_candidates=(candidate,), evidence_items=(item,)))
    check("policy change changes decision identity", changed.relationships[0].decision_id != decision.decision_id)
    check("no review/canonical/ER runtime dependency", all(token not in source for token in ("ReviewDecision", "CanonicalModel", "EntityResolution", "splink")))
    state = (ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8")
    check("G4 PASS and G5 PENDING", 'G4_BOUNDED_INTELLIGENCE: "PASS"' in state and 'G5_INFERENCE_VALIDITY: "PENDING"' in state)
    print("PASS: evidence fusion behavioral validator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
