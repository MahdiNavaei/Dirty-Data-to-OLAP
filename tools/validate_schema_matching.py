"""Behavioral and boundary checks for the Step13 closure."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'failed'}")
    print(f"PASS {name}")


def main() -> int:
    from dirty_data_to_olap.adapters.dependencies.staged import StagedDependencyRow
    from dirty_data_to_olap.adapters.matching.valentine import ValentineSchemaMatchingAdapter
    from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchMode, SchemaMatchRequest, SchemaMatcherReference, schema_match_config_hash
    from tools.evaluate_schema_matching import load_labeled_fixture
    import pandas as pd

    contracts = (ROOT / "src/dirty_data_to_olap/domain/contracts/schema_matching.py").read_text(encoding="utf-8")
    adapter = (ROOT / "src/dirty_data_to_olap/adapters/matching/valentine.py").read_text(encoding="utf-8")
    service = (ROOT / "src/dirty_data_to_olap/application/schema_matching.py").read_text(encoding="utf-8")
    state = (ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8")
    test = (ROOT / "tests/integration/matching/test_step13_real_valentine.py").read_text(encoding="utf-8")
    checks = {
        "project contracts": all(token in contracts for token in ("SchemaMatchRequest", "SchemaMatchCandidate", "SchemaMatchSignal", "MatchingAuthorization")),
        "multi-source request": "source_ids: tuple[str, ...] = Field(min_length=2)" in contracts,
        "symmetric IDs": "tuple(sorted((left, right)))" in contracts,
        "native Valentine boundary": "import valentine" in adapter and "SchemaMatchScore" in adapter,
        "scores are not probabilities": "raw_native_score" in contracts and 'probability: ' not in contracts and 'confidence: ' not in contracts,
        "candidate-only state": 'state: str = "CANDIDATE"' in contracts and "final_acceptance_allowed" in contracts,
        "privacy and staged input": "verify_schema_matching_authorization" in adapter and "DependencyStagedReader" in adapter,
        "hash and deterministic sampling": "schema_match_config_hash" in contracts and "_sample_stream" in adapter and "sample_identity" in contracts and "sample_seed" in contracts,
        "bounded search and coverage": all(token in contracts for token in ("max_column_pairs", "top_k_per_left_column", "evaluated_by_matcher", "returned_by_matcher")),
        "artifact boundary": "SchemaMatchingArtifactStore" in adapter and '"schema_matching"' in adapter,
        "official runtime pin": '"valentine==1.0.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        "labeled evaluation": (ROOT / "benchmarks/schema_matching/step13_labeled_fixture.json").is_file() and (ROOT / "tools/evaluate_schema_matching.py").is_file(),
        "step12 immutable digest": "self.image_digest" in (ROOT / "src/dirty_data_to_olap/adapters/dependencies/desbordante.py").read_text(encoding="utf-8") and "provider_rows_by_table" in (ROOT / "src/dirty_data_to_olap/domain/contracts/dependency.py").read_text(encoding="utf-8"),
        "step12 project scratch test": "workspace" in test and "test-temp" in test and "tmp_path" not in test,
        "no research clone": not (ROOT / "research/oss/valentine").exists(),
        "step14 implementation is present and later handoff is explicit": (ROOT / "src/dirty_data_to_olap/adapters/entity_resolution").exists() and (("current_step: 15" in state and "last_completed_step: 14" in state) or ("current_step: 16" in state and "last_completed_step: 15" in state) or ("current_step: 17" in state and "last_completed_step: 16" in state) or ("current_step: 18" in state and "last_completed_step: 17" in state) or ("current_step: 19" in state and "last_completed_step: 18" in state) or ("current_step: 20" in state and "last_completed_step: 19" in state) or ("current_step: 21" in state and "last_completed_step: 20" in state) or ("current_step: 22" in state and "last_completed_step: 21" in state)) and ("G4_BOUNDED_INTELLIGENCE: \"PENDING\"" in state or "G4_BOUNDED_INTELLIGENCE: \"PASS\"" in state),
        "service owns authorization": "authorize_schema_matching_analysis" in service and "self.adapter.discover" in service,
        "schema-only zero-row boundary": "request.mode is SchemaMatchMode.INSTANCE_AWARE" in adapter and "pd.DataFrame([item.values for item in selected]" in adapter and "instance_rows_read=instance_rows_read" in adapter,
        "unsupported instance matcher boundary": "_UnsupportedMatcherMode" in adapter and "SchemaMatchFailureKind.UNSUPPORTED_MODE" in adapter,
        "provider pair boundary": "valentine.valentine_match(\n                        [left_frame, right_frame]" in adapter and "_bounded_pair_inputs" in adapter,
        "provider-visible column accounting": all(token in contracts for token in ("provider_visible_column_pairs_by_matcher", "eligible_column_pairs_by_matcher", "top_k_retained_by_matcher", "column_pairs_project_ineligible", "column_pairs_post_provider_type_rejected", "column_pairs_workload_avoided")),
        "hard-negative evaluation contract": all(token in contracts for token in ("hard_negative_count", "hard_negative_exposed_at_k", "false_positive_at_k")) and "load_labeled_fixture" in (ROOT / "tools/evaluate_schema_matching.py").read_text(encoding="utf-8"),
        "schema-only fixture sentinel": "SCHEMA_ONLY must not invoke the staged reader" in test and "actual_labeled_fixture_is_exercised" in test,
        "no global score fusion": "max((score.raw_native_score" not in adapter and "matcher_order" in adapter,
        "conditional authorization": "MatchingAuthorization when INSTANCE_AWARE" in (ROOT / "docs/architecture/specs/stage_graph.yml").read_text(encoding="utf-8") and "optional_input_contracts" in (ROOT / "docs/architecture/specs/components.yml").read_text(encoding="utf-8"),
        "G4 is pending or evidenced pass and G4A is intermediate": ('G4_BOUNDED_INTELLIGENCE: "PENDING"' in state or 'G4_BOUNDED_INTELLIGENCE: "PASS"' in state) and 'G4A_INDEPENDENT_EVIDENCE_PRODUCERS: "PASS"' in state and 'G4A_INDEPENDENT_EVIDENCE_PRODUCERS: "PENDING"' not in state,
    }
    for name, condition in checks.items():
        check(name, condition)
    adapter_instance = ValentineSchemaMatchingAdapter(project_root=ROOT)
    class _Matcher:
        def __init__(self, **config):
            self.config = config
    schema_matcher = SchemaMatcherReference(matcher_id="schema", name="Coma", version="1", configuration={"use_instances": False})
    try:
        adapter_instance._build_matcher(SchemaMatcherReference(matcher_id="bad", name="Coma", version="1", configuration={"use_instances": True}), SchemaMatchMode.SCHEMA_ONLY, _Matcher, _Matcher, _Matcher)
        schema_mode_rejected = False
    except ValueError:
        schema_mode_rejected = True
    check("behavioral schema-only matcher rejection", schema_mode_rejected)
    sampled, read_count = adapter_instance._sample_stream((StagedDependencyRow(f"r{i}", i, {"x": i}) for i in range(4)), 2, 7)
    check("behavioral bounded sampling", read_count == 4 and len(sampled) == 2)
    base_request = SchemaMatchRequest(request_id="validator", source_ids=("a", "b"), snapshot_ids={"a": "sa", "b": "sb"}, selected_table_ids_by_source={"a": ("ta",), "b": ("tb",)}, matcher_references=(schema_matcher,))
    check("behavioral sample fingerprint semantics", schema_match_config_hash(base_request.model_copy(update={"sample_seed": 1})) == schema_match_config_hash(base_request.model_copy(update={"sample_seed": 2})) and schema_match_config_hash(base_request.model_copy(update={"mode": SchemaMatchMode.INSTANCE_AWARE, "sample_seed": 1})) != schema_match_config_hash(base_request.model_copy(update={"mode": SchemaMatchMode.INSTANCE_AWARE, "sample_seed": 2})))
    pair_inputs, pair_stats = adapter_instance._bounded_pair_inputs(
        [("a", "ta", "b", "tb")],
        {"a::ta": pd.DataFrame({"left": [1], "ignored": [2]}), "b::tb": pd.DataFrame({"right": [1], "ignored": [2]})},
        {"a::ta": {"columns": {"left": {"source_id": "a", "table_id": "ta", "column_id": "la", "physical_name": "left", "normalized_type": "numeric"}, "ignored": {"source_id": "a", "table_id": "ta", "column_id": "li", "physical_name": "ignored", "normalized_type": "numeric"}}}, "b::tb": {"columns": {"right": {"source_id": "b", "table_id": "tb", "column_id": "rb", "physical_name": "right", "normalized_type": "numeric"}, "ignored": {"source_id": "b", "table_id": "tb", "column_id": "ri", "physical_name": "ignored", "normalized_type": "numeric"}}}},
        base_request,
    )
    check("behavioral provider projection", pair_stats["provider_visible"] == 4 and len(pair_inputs) == 1 and set(pair_inputs[0][2].columns) == {"left", "ignored"})
    fixture = load_labeled_fixture(ROOT / "benchmarks/schema_matching/step13_labeled_fixture.json")
    check("behavioral labeled fixture load", len(fixture["positives"]) == 2 and len(fixture["hard_negatives"]) == 4 and "crm_orders-status" in {item[0] for item in fixture["hard_negatives"]})
    print(f"PASS: schema_matching_checks={len(checks) + 5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
