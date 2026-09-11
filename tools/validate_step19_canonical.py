"""Behavioral validator for the Step19 canonical model boundary."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "workspace" / "runs" / "step19-reference-run" / "canonical"
SRC = ROOT / "src" / "dirty_data_to_olap"
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalizationError
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecision, ReviewDecisionStatus


def load(name):
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def check(name, condition, detail):
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    return name


def main() -> int:
    required = ("canonical_model_hypothesis.json", "canonical_model.json", "review_evidence_decisions.json", "review_canonical_identity.json", "source_record_canonical_map.json", "survivorship_decisions.json", "conflicts.json", "record_accounting.json", "entity_resolution_binding.json", "run_manifest.json")
    for name in required:
        check("artifact:" + name, (RUN / name).is_file(), "missing")
    hypothesis = load("canonical_model_hypothesis.json")
    model = load("canonical_model.json")
    binding = load("entity_resolution_binding.json")
    maps = load("source_record_canonical_map.json")
    survivorship = load("survivorship_decisions.json")
    conflicts = load("conflicts.json")
    accounting = load("record_accounting.json")
    manifest = load("run_manifest.json")
    passed = []
    passed.append(check("exact checkpoints", manifest["flow"] == ["REVIEW_EVIDENCE_DECISIONS", "CANONICAL_HYPOTHESES", "ENTITY_RESOLUTION", "REVIEW_CANONICAL_IDENTITY", "CANONICAL_FINALIZATION"], "unexpected stage order"))
    passed.append(check("review accepted", load("review_evidence_decisions.json")[0]["decision"] == "ACCEPTED" and load("review_canonical_identity.json")["decision"] == "ACCEPTED", "accepted reviews required"))
    passed.append(check("er requirement", hypothesis["entity_resolution_requirements"]["person"] == "ER_REQUIRED" and binding["status"] == "COMPLETE", "required family is not bound to a complete result"))
    passed.append(check("non-er event", hypothesis["entity_resolution_requirements"]["order"] == "ER_NOT_REQUIRED" and any(item["semantic_id"] == "order" and item["kind"] == "EVENT" for item in hypothesis["entity_types"]), "event boundary missing"))
    passed.append(check("canonical namespace", model["model_id"].startswith("cmodel_") and all(item["canonical_entity_id"].startswith("cent_") for item in model["instances"]), "canonical ID namespace invalid"))
    passed.append(check("cluster separation", not any("cluster" in item["canonical_entity_id"].lower() for item in model["instances"]), "cluster ID leaked into canonical ID"))
    passed.append(check("record preservation", accounting["source_records_preserved"] is True and accounting["destructive_deduplication"] is False and len(maps) == accounting["source_records_mapped"] == 2, "source mapping accounting mismatch"))
    passed.append(check("terminal dispositions", all(item["terminal_disposition"] in {"CONSOLIDATED", "EMITTED_DIRECT", "QUARANTINED", "UNRESOLVED", "FILTERED_EXPLICIT", "AGGREGATED"} for item in maps), "non-terminal record state published"))
    passed.append(check("losers retained", len(survivorship) == 1 and set(survivorship[0]["eligible_value_refs"]) == {"value-customer-name-crm", "value-customer-name-erp"} and set(survivorship[0]["losing_value_refs"]) == {"value-customer-name-erp"} and len(conflicts) == 1, "survivorship alternatives were lost"))
    passed.append(check("conflict inspectable", conflicts[0]["resolution_state"] == "RESOLVED_BY_POLICY" and set(conflicts[0]["alternative_value_refs"]) == {"value-customer-name-crm", "value-customer-name-erp"}, "conflict is not retained"))
    passed.append(check("no evaluation truth", manifest["step18_evaluation_truth_used"] is False and manifest["runtime_truth_inputs"] == [], "evaluation truth was used as runtime input"))
    passed.append(check("no step20 module", not (SRC / "application" / "canonical_model.py").exists(), "Step20-named canonical module exists"))
    for path in (SRC / "application" / "canonical.py", SRC / "domain" / "contracts" / "canonical.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        check("runtime import boundary:" + path.name, not any("evaluation" in module or "splink" in module or "valentine" in module for module in imported), "forbidden runtime import")

    identity = ReviewDecision.model_validate(load("review_canonical_identity.json"))
    h = __import__("dirty_data_to_olap.domain.contracts.canonical", fromlist=["CanonicalModelHypothesis"]).CanonicalModelHypothesis.model_validate(hypothesis)
    finalizer = CanonicalFinalizationService(ReviewPolicyService())
    stale = identity.model_copy(update={"subject_content_hash": "stale-content"})
    try:
        finalizer.finalize(hypothesis=h, identity_review=stale, er_results={}, memberships={"customer": ("crm-r1",)}, source_record_metadata={"crm-r1": {"source_id": "crm", "snapshot_id": "snapshot-crm", "table_id": "customers"}})
    except CanonicalizationError:
        passed.append("stale review fails closed")
    else:
        raise AssertionError("stale review was accepted")
    rerun = subprocess.run([sys.executable, str(ROOT / "tools" / "run_step19_reference.py")], cwd=ROOT, capture_output=True, text=True)
    check("deterministic rerun", rerun.returncode == 0, rerun.stderr[-1000:])
    rerun_manifest = load("run_manifest.json")
    passed.append(check("stable semantic artifacts", rerun_manifest["hypothesis_content_hash"] == manifest["hypothesis_content_hash"] and rerun_manifest["canonical_model_content_hash"] == manifest["canonical_model_content_hash"], "rerun changed semantic identity"))
    print(json.dumps({"status": "PASS", "checks": len(passed), "run": str(RUN)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
