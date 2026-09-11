from __future__ import annotations

from pathlib import Path
import pytest

from dirty_data_to_olap.adapters.entity_resolution.splink import SplinkEntityResolutionAdapter
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityMatchEdge, EntityMatchPredictionBand
from dirty_data_to_olap.evaluation.metrics import entity_resolution_metrics
from tools.step18_provider_fixtures import entity_spec


def _edge(band: EntityMatchPredictionBand) -> EntityMatchEdge:
    return EntityMatchEdge(
        edge_id="edge-a-b", left_record_ref="a", right_record_ref="b",
        left_source_id="crm", right_source_id="erp", left_snapshot_id="s",
        right_snapshot_id="s", match_weight=0.1, match_probability=0.5,
        model_prediction_band=band, blocking_rule_ids=("block-email",),
        model_evidence_ref="model", independent_evidence_refs=("email",),
    )


def _clusters(include_review: bool, band: EntityMatchPredictionBand):
    adapter = SplinkEntityResolutionAdapter(project_root=Path("."), privacy_policy=None)
    spec = entity_spec("v4")
    spec = spec.model_copy(update={"clustering_policy": spec.clustering_policy.model_copy(update={"include_review_edges": include_review})})
    records = {"crm::a": {"_record_ref": "a", "_source_id": "crm"}, "erp::b": {"_record_ref": "b", "_source_id": "erp"}}
    return adapter._clusters(records, [_edge(band)], spec, "model", "config")[0]


def test_review_edge_is_excluded_from_project_clusters_by_default():
    assert _clusters(False, EntityMatchPredictionBand.REVIEW_LINK_EVIDENCE) == []


def test_review_edge_is_included_only_when_project_policy_allows_it():
    assert len(_clusters(True, EntityMatchPredictionBand.REVIEW_LINK_EVIDENCE)) == 1


def test_below_threshold_edge_never_connects():
    assert _clusters(True, EntityMatchPredictionBand.BELOW_EVIDENCE_THRESHOLD) == []


def test_er_false_split_and_completeness_use_complete_partition():
    result = entity_resolution_metrics(
        set(), set(), {"truth": {"a", "b"}}, [],
        evaluated_universe={frozenset(("a", "b")), frozenset(("a", "c")), frozenset(("b", "c"))},
        evaluated_record_refs={"a", "b", "c"},
    )
    assert result.truth_clusters_split == 1
    assert result.false_split_rate.value == 1.0
    assert result.mean_truth_cluster_completeness.value == 0.5
    assert result.mean_predicted_cluster_purity.value is None
    assert result.mean_predicted_cluster_purity.undefined_reason == "NO_PREDICTED_CLUSTERS"
    assert result.truth_negative_pair_count == 3
    assert result.pairwise.true_negative == 3


def test_er_partial_and_perfect_partition_semantics():
    universe = {frozenset(pair) for pair in (("a", "b"), ("a", "c"), ("b", "c"))}
    partial = entity_resolution_metrics({frozenset(("a", "b"))}, {frozenset(("a", "b")), frozenset(("a", "c")), frozenset(("b", "c"))}, {"truth": {"a", "b", "c"}}, [{"a", "b"}], evaluated_universe=universe, evaluated_record_refs={"a", "b", "c"})
    perfect = entity_resolution_metrics({frozenset(pair) for pair in (("a", "b"), ("a", "c"), ("b", "c"))}, {frozenset(pair) for pair in (("a", "b"), ("a", "c"), ("b", "c"))}, {"truth": {"a", "b", "c"}}, [{"a", "b", "c"}], evaluated_universe=universe, evaluated_record_refs={"a", "b", "c"})
    assert partial.truth_clusters_split == 1 and partial.mean_truth_cluster_completeness.value == 2 / 3
    assert perfect.truth_clusters_split == 0 and perfect.mean_truth_cluster_completeness.value == 1.0


def test_er_rejects_overlapping_or_out_of_universe_clusters():
    universe = {frozenset(("a", "b")), frozenset(("a", "c")), frozenset(("b", "c"))}
    with pytest.raises(ValueError, match="overlap"):
        entity_resolution_metrics(set(), set(), {}, [{"a", "b"}, {"b", "c"}], evaluated_universe=universe, evaluated_record_refs={"a", "b", "c"})
    with pytest.raises(ValueError, match="out-of-universe"):
        entity_resolution_metrics(set(), set(), {}, [{"a", "z"}], evaluated_universe=universe, evaluated_record_refs={"a", "b", "c"})


def test_ten_record_pair_universe_has_eight_positive_and_thirty_seven_negative():
    refs = {f"r{index}" for index in range(10)}
    universe = {frozenset(pair) for pair in __import__("itertools").combinations(sorted(refs), 2)}
    truth = {frozenset((f"r{index}", f"r{index + 1}")) for index in range(8)}
    result = entity_resolution_metrics(set(), truth, {f"e{index}": {f"r{index}", f"r{index + 1}"} for index in range(8)}, [], evaluated_universe=universe, evaluated_record_refs=refs)
    assert len(universe) == 45
    assert result.truth_positive_pair_count == 8
    assert result.truth_negative_pair_count == 37
    assert result.pairwise.true_positive + result.pairwise.false_positive + result.pairwise.false_negative + result.pairwise.true_negative == 45
