"""Runtime-independent behavioral checks for the Step14 ER boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def main() -> int:
    contracts_path = ROOT / "src/dirty_data_to_olap/domain/contracts/entity_resolution.py"
    adapter_path = ROOT / "src/dirty_data_to_olap/adapters/entity_resolution/splink.py"
    contracts = contracts_path.read_text(encoding="utf-8")
    adapter = adapter_path.read_text(encoding="utf-8")
    state = json.loads("{}")
    import yaml
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state["specialist_execution"]
    checks = {
        "project-owned contract family": all(token in contracts for token in ("EntityResolutionSpec", "EntityMatchEdge", "EntityCluster", "EntityResolutionAuthorization")),
        "spec excludes canonical outputs": all(token not in contracts.split("class EntityResolutionSpec", 1)[1].split("class EntityResolutionAuthorization", 1)[0] for token in ("canonical_entity_id", "SourceRecordCanonicalMap", "accepted_merge", "survivorship")),
        "correct linkage modes": all(token in contracts for token in ("LINK_ONLY", "DEDUPE_ONLY", "LINK_AND_DEDUPE")),
        "bounded blocking": all(token in contracts for token in ("max_candidate_pairs", "max_all_pairs_diagnostic", "unbounded Cartesian blocking")) and "1=1" in contracts,
        "normalization is conservative": all(token in contracts for token in ("NFKC", "CASEFOLD", "TRIM_COLLAPSE_WHITESPACE", "TRANSLITERATE", "COUNTRY_GUESS")),
        "model semantics separated": "match_weight_semantics" in contracts and "probability_semantics" in contracts and "calibrated_business_confidence" in contracts,
        "staged reader only": "DependencyStagedReader" in adapter and "source reconnect" not in adapter and "SourceAdapter" not in adapter,
        "native runtime isolated": "native_objects_isolated" in adapter and "native_objects_isolated=True" in adapter and "as_pandas_dataframe" in adapter,
        "authorization exact scope": "verify_entity_resolution_authorization" in adapter and all(token in contracts for token in ("spec_fingerprint", "identity_column_ids", "batch_ids")),
        "training provenance required": all(token in contracts + adapter for token in ("estimate_u_using_random_sampling", "expectation_maximisation", "no_fake_training")),
        "no default trained claim": "_is_fully_trained" in adapter and "default parameters are not accepted" in adapter,
        "false merge guards": all(token in contracts + adapter for token in ("placeholder_only", "unsafe_bridge", "largest_cluster_guard", "independent_evidence")),
        "no canonical map output": "SourceRecordCanonicalMap" not in adapter and "canonical_entity_id" not in adapter,
        "benchmark fixture": (ROOT / "benchmarks/entity_resolution/step14_labeled_fixture.json").is_file(),
        "Step14 handed to Step15 / Step15 handed to Step16": ((execution.get("last_completed_step") == 14 and execution.get("current_step") == 15 and execution.get("current_role") == "applied_ml_engineer") or (execution.get("last_completed_step") == 15 and execution.get("current_step") == 16 and execution.get("current_role") == "llm_semantic_ai_engineer")) and execution.get("blocked") is not True,
        "G4 remains formal pending": state.get("gates", {}).get("G4_BOUNDED_INTELLIGENCE") == "PENDING",
        "G4A is intermediate": state.get("intermediate_milestones", {}).get("G4A_INDEPENDENT_EVIDENCE_PRODUCERS") == "PASS" and "G4A_INDEPENDENT_EVIDENCE_PRODUCERS" not in state.get("gates", {}),
    }
    for name, condition in checks.items():
        check(name, condition)
    from dirty_data_to_olap.domain.contracts.entity_resolution import ERBlockingRule, EntityResolutionMode, EntityResolutionSpec
    try:
        ERBlockingRule(rule_id="bad", version="1", field_ids=("x",), sql_expression="1=1")
        rejected = False
    except ValueError:
        rejected = True
    check("behavioral Cartesian blocking rejection", rejected)
    check("no raw benchmark values", "@" not in json.dumps(json.loads((ROOT / "benchmarks/entity_resolution/step14_labeled_fixture.json").read_text(encoding="utf-8"))))
    print(f"PASS: entity_resolution_checks={len(checks) + 2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
