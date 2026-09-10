from __future__ import annotations

from dirty_data_to_olap.evaluation.contracts import EvaluationTask
from dirty_data_to_olap.evaluation.metrics import average_precision, entity_resolution_metrics, group_bootstrap, ranking_metrics
from dirty_data_to_olap.evaluation.splitting import build_split_manifest
from dirty_data_to_olap.evaluation.contracts import InferenceEvaluationExample


def test_metrics_keep_explicit_denominators_and_undefined_reasons():
    metric = average_precision((("negative", 0, 0.9), ("positive", 1, 0.2)))
    assert metric.value == 0.5
    assert metric.denominator == 1
    undefined = ranking_metrics({"q": (("only", 0.1),)}, {"q": set()})
    assert undefined.no_positive_query_count == 1
    assert undefined.mean_reciprocal_rank.value is None
    assert undefined.mean_reciprocal_rank.undefined_reason == "NO_ELIGIBLE_QUERIES"


def test_entity_metrics_expose_false_merge_and_false_split():
    pairs = {frozenset(("a", "b")), frozenset(("a", "c"))}
    truth = {frozenset(("a", "b"))}
    result = entity_resolution_metrics(
        pairs,
        truth,
        {"entity": {"a", "b", "c"}},
        ({"a", "b", "c"},),
        evaluated_universe={frozenset(("a", "b")), frozenset(("a", "c")), frozenset(("b", "c"))},
    )
    assert result.false_merge_pair_count == 1
    assert result.contaminated_cluster_count == 1
    assert result.truth_clusters_split == 0


def test_group_split_is_disjoint_and_bootstrap_is_seeded():
    rows = tuple(
        InferenceEvaluationExample(
            example_id=f"row-{index}",
            scenario_group_id=group,
            task=EvaluationTask.RELATIONSHIP_FUSION,
            query_id=f"q-{index}",
            candidate_id=f"c-{index}",
            score=float(index),
            label=index % 2,
        )
        for index, group in enumerate(("dev", "cal", "test"))
    )
    split = build_split_manifest(examples=rows, truth_fingerprint="truth", seed=7, explicit_roles={"dev": "DEVELOPMENT", "cal": "CALIBRATION", "test": "TEST"})
    assert not set(split.group_ids_by_split["DEVELOPMENT"]).intersection(split.group_ids_by_split["TEST"])
    first = group_bootstrap({"g1": 0.0, "g2": 1.0}, seed=7, metric_id="metric")
    second = group_bootstrap({"g1": 0.0, "g2": 1.0}, seed=7, metric_id="metric")
    assert first.model_dump() == second.model_dump()
