from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.ml.dataset import (
    build_dataset,
    make_grouped_split,
)
from dirty_data_to_olap.adapters.ml.features import (
    build_feature_vector,
    default_feature_schema,
)
from dirty_data_to_olap.adapters.ml.sklearn_ranking import (
    SklearnRelationshipRanker,
)
from dirty_data_to_olap.application.applied_ml import AppliedMLService
from dirty_data_to_olap.domain.contracts.applied_ml import (
    MLFeatureVector,
    MLTrainingLabel,
)
from dirty_data_to_olap.domain.contracts.dependency import RelationshipCandidate


def _candidate(index: int) -> RelationshipCandidate:
    return RelationshipCandidate(
        candidate_id=f"candidate_{index}",
        source_id="source_a",
        snapshot_id="snapshot_a",
        from_table=f"left_{index}",
        from_columns=("key",),
        to_table=f"right_{index}",
        to_columns=("key",),
        source_orphan_ratio=0.1 if index % 2 else 0.8,
        target_uniqueness_ratio=0.95 if index % 2 else 0.2,
        type_compatible=index % 2 == 1,
        low_cardinality_risk=index % 2 == 0,
        evidence_refs=(f"evidence_{index}",),
    )


def _rows():
    schema = default_feature_schema()
    vectors = tuple(
        build_feature_vector(candidate)
        for candidate in (_candidate(index) for index in range(6))
    )
    vectors = tuple(vector.model_copy(update={"logical_pair_key": "reverse:table_a.table_b"} if index in {0, 1} else {}) for index, vector in enumerate(vectors))
    labels = tuple(
        MLTrainingLabel(
            label_id=f"label_{index}",
            candidate_id=f"candidate_{index}",
            logical_pair_key=vectors[index].logical_pair_key,
            label=index % 2,
            group_id=f"group_{index // 2}",
            base_scenario_id=f"scenario_{index // 2}",
            source="synthetic_step15_benchmark",
            provenance_refs=(f"fixture_case_{index}",),
        )
        for index in range(6)
    )
    return schema, vectors, labels


def test_feature_builder_preserves_missingness_and_excludes_identifiers():
    vector = build_feature_vector(_candidate(0))
    assert "inclusion_coverage" in vector.missing_feature_ids
    assert vector.values["inclusion_coverage"] == 0.0
    assert vector.values["inclusion_coverage_missing"] == 1.0
    assert all(
        token not in feature_id
        for feature_id in default_feature_schema().feature_order
        for token in ("candidate_id", "source_id", "table_id", "column_id", "label")
    )


def test_grouped_split_keeps_reverse_pair_groups_together():
    schema, vectors, labels = _rows()
    rows, _ = build_dataset(vectors, labels, schema)
    split = make_grouped_split(rows)
    assert set(split.assignments.values()) == {"train", "validation", "test"}
    assert not (
        set(split.row_group_by_id[row_id] for row_id, value in split.assignments.items() if value == "train")
        & set(split.row_group_by_id[row_id] for row_id, value in split.assignments.items() if value == "test")
    )
    reverse_rows = [row_id for row_id, group in split.row_group_by_id.items() if group == "group_0"]
    assert len(reverse_rows) == 2 and len({split.assignments[row_id] for row_id in reverse_rows}) == 1


def test_step15_fixture_matches_current_feature_schema():
    fixture = json.loads(Path("benchmarks/applied_ml/step15_relationship_ranker_fixture.json").read_text(encoding="utf-8"))
    assert fixture["feature_schema_id"] == default_feature_schema().schema_id
    assert "matcher_max_score" not in json.dumps(fixture)


def test_label_shuffle_and_source_id_permutation_are_leakage_negative_controls():
    schema, vectors, labels = _rows()
    rows, manifest = build_dataset(vectors, labels, schema)
    shuffled_labels = tuple(
        label.model_copy(update={"label": 1 - label.label}) for label in labels
    )
    shuffled_rows, shuffled_manifest = build_dataset(vectors, shuffled_labels, schema)
    assert tuple(row.feature_vector.values for row in rows) == tuple(
        row.feature_vector.values for row in shuffled_rows
    )
    assert manifest.label_fingerprint != shuffled_manifest.label_fingerprint
    permuted_candidate = _candidate(0).model_copy(
        update={"source_id": "permuted_source"}
    )
    permuted_vector = build_feature_vector(permuted_candidate)
    assert vectors[0].values == permuted_vector.values


def test_real_sklearn_adapter_persists_json_and_reconstructs_contributions():
    if not SklearnRelationshipRanker.capability()[0]:
        pytest.skip("optional sklearn runtime is unavailable")
    schema, vectors, labels = _rows()
    rows, manifest = build_dataset(vectors, labels, schema)
    split = make_grouped_split(rows)
    ranker = SklearnRelationshipRanker(schema, random_seed=7)
    root = Path("workspace/test-temp/applied-ml").resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifact = root / "model.json"
    try:
        evidence = ranker.train(rows, split_manifest=split, dataset_manifest=manifest, model_id="step15-test-model", artifact_path=artifact)
        assert evidence.artifact_location is not None
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert "candidate_id" not in json.dumps(payload)
        assert not any(path.suffix in {".pkl", ".pickle", ".joblib"} for path in root.iterdir())
        score = ranker.score(vectors[1])
        contributions = ranker.contributions(vectors[1])
        assert score == pytest.approx(evidence.intercept + sum(item.contribution for item in contributions))
        loaded = SklearnRelationshipRanker.from_json_artifact(schema, artifact)
        assert loaded.score(vectors[1]) == pytest.approx(score)
        tampered = json.loads(artifact.read_text(encoding="utf-8"))
        tampered["coefficients"][0] = float(tampered["coefficients"][0]) + 1.0
        artifact.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="")
        with pytest.raises(ValueError, match="content hash"):
            SklearnRelationshipRanker.from_json_artifact(schema, artifact)
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_service_runs_project_contract_to_feature_builder_to_sklearn():
    if not SklearnRelationshipRanker.capability()[0]:
        pytest.skip("optional sklearn runtime is unavailable")
    candidates = tuple(_candidate(index) for index in range(6))
    vectors = tuple(build_feature_vector(candidate) for candidate in candidates)
    labels = tuple(
        MLTrainingLabel(
            label_id=f"label_{index}",
            candidate_id=candidate.candidate_id,
            logical_pair_key=vectors[index].logical_pair_key,
            label=index % 2,
            group_id=f"group_{index // 2}",
            base_scenario_id=f"scenario_{index // 2}",
            source="synthetic_step15_benchmark",
            provenance_refs=(f"fixture_case_{index}",),
        )
        for index, candidate in enumerate(candidates)
    )
    result = AppliedMLService().rank(candidates, labels=labels)
    assert result.status.value == "EXECUTED_EXPERIMENTAL"
    assert result.model is not None
    assert result.learned_evidence
    assert result.contributions
    assert all(
        evidence.score_kind == "UNCALIBRATED_RANKING_SCORE"
        for evidence in result.learned_evidence
    )
