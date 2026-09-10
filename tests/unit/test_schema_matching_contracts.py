from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dirty_data_to_olap.domain.contracts.schema_matching import (
    SchemaMatchCandidate,
    SchemaMatchMode,
    SchemaMatchObservationScope,
    SchemaMatchRequest,
    SchemaMatchScore,
    SchemaMatcherReference,
    SchemaMatchSignalFamily,
    schema_match_candidate_id,
    schema_match_config_hash,
)
from dirty_data_to_olap.domain.contracts.source import AdapterReference, ObservationMode
from tools.evaluate_schema_matching import evaluate_candidates


def _scope():
    return SchemaMatchObservationScope(source_ids=("a", "b"), snapshot_ids={"a": "sa", "b": "sb"}, table_ids_by_source={"a": ("ta",), "b": ("tb",)}, column_ids_by_table={}, source_snapshot_modes={"a": ObservationMode.FULL, "b": ObservationMode.FULL}, complete_by_table={"ta": True, "tb": True}, staged_rows_by_table={"ta": 2, "tb": 2}, sampled_rows_by_table={"ta": 2, "tb": 2}, sample_seed=1, sample_mode="HASHED_DETERMINISTIC_V1", sample_algorithm_version="hash_ordered_sample_v1", sample_identity="sample")


def test_config_hash_excludes_request_and_input_identity():
    first = SchemaMatchRequest(request_id="one", source_ids=("a", "b"), snapshot_ids={"a": "sa", "b": "sb"}, selected_table_ids_by_source={"a": ("ta",), "b": ("tb",)})
    second = first.model_copy(update={"request_id": "two", "snapshot_ids": {"a": "different-a", "b": "different-b"}, "source_ids": ("x", "y")})
    assert schema_match_config_hash(first) == schema_match_config_hash(second)


def test_candidate_id_is_symmetric_and_abbreviation_policy_is_explicit():
    left = ("a", "ta", "ca")
    right = ("b", "tb", "cb")
    assert schema_match_candidate_id(left, right) == schema_match_candidate_id(right, left)
    with pytest.raises(ValueError, match="abbreviation"):
        SchemaMatchRequest(request_id="bad", source_ids=("a", "b"), snapshot_ids={"a": "sa", "b": "sb"}, selected_table_ids_by_source={"a": ("ta",), "b": ("tb",)}, abbreviation_dictionary={"cust": "customer"})


def test_labeled_evaluator_reports_recall_and_mrr_not_confidence():
    scope = _scope()
    reference = SchemaMatcherReference(matcher_id="m", name="fake", version="1", configuration={})
    provenance = AdapterReference(name="test", version="1", config_fingerprint="test")
    score = SchemaMatchScore(score_id="score-1", matcher=reference, source_column_id="ca", target_column_id="cb", raw_native_score=0.9, native_score_name="native_similarity", native_score_semantics="native only; not a probability", rank=1, config_hash="config", mode=SchemaMatchMode.SCHEMA_ONLY, observation_scope=scope, provenance=provenance)
    candidate = SchemaMatchCandidate(candidate_id="candidate-1", source_id="a", source_snapshot_id="sa", source_table_id="ta", source_column_id="ca", source_column_name="customer_code", target_source_id="b", target_snapshot_id="sb", target_table_id="tb", target_column_id="cb", target_column_name="client_no", score_refs=(score.score_id,), signal_refs=(), observation_scope=scope)
    evaluation = evaluate_candidates(candidates=(candidate,), scores=(score,), ground_truth=(("ca", "cb"),), matcher_id="m", fixture_id="fixture", sample_identity="sample")
    assert evaluation.recall_at_k["1"] == 1.0
    assert evaluation.mean_reciprocal_rank == 1.0
    assert "confidence" in " ".join(evaluation.limitations)
