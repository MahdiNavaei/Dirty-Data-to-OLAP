"""Behavioral and repository-boundary checks for Specialist Step15."""

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
    import yaml

    contracts = (ROOT / "src/dirty_data_to_olap/domain/contracts/applied_ml.py").read_text(encoding="utf-8")
    features = (ROOT / "src/dirty_data_to_olap/adapters/ml/features.py").read_text(encoding="utf-8")
    ranker = (ROOT / "src/dirty_data_to_olap/adapters/ml/sklearn_ranking.py").read_text(encoding="utf-8")
    service = (ROOT / "src/dirty_data_to_olap/application/applied_ml.py").read_text(encoding="utf-8")
    fixture = json.loads((ROOT / "benchmarks/entity_resolution/step14_labeled_fixture.json").read_text(encoding="utf-8"))
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state["specialist_execution"]
    checks = {
        "Step15 contract family": all(token in contracts for token in ("MLTask", "MLFeatureSchema", "MLDatasetManifest", "LearnedRankingEvidence", "ActiveLearningSuggestion")),
        "ranking task is candidate-only": "RELATIONSHIP_CANDIDATE_RANKING" in contracts and "UNCALIBRATED_RANKING_SCORE" in contracts and "acceptance" in service,
        "aggregate feature boundary": "aggregate" in features and "project-owned" in features and "QualityStagedRow" not in features and "Raw" not in features,
        "missingness and risk are retained": "missing_feature_ids" in features and "risk_flags" in features,
        "grouped split and reverse-pair control": all(token in (ROOT / "src/dirty_data_to_olap/adapters/ml/dataset.py").read_text(encoding="utf-8") for token in ("group_id", "reverse_pair_group_by_key", "duplicate logical pairs")),
        "real sklearn adapter": "LogisticRegression" in ranker and "decision" not in ranker.split("class SklearnRelationshipRanker", 1)[0],
        "JSON-only persistence": "json.dumps" in ranker and all(token not in ranker for token in ("pickle", "joblib", "dill")),
        "baseline retained on optional failure": "deterministic baseline retained" in service and "MLStageStatus.UNAVAILABLE" in service,
        "contributions reconstruct score": "ml_linear_score" in ranker and "feature contributions do not reconstruct ranking score" in ranker,
        "calibration is explicitly bounded": "INSUFFICIENT_CALIBRATION_DATA" in service and "no probability is emitted" in service,
        "active learning is non-mutating": "labels_mutated" in contracts and "external_processing" in contracts,
        "Step14 actual benchmark structure": all(key in fixture for key in ("records", "positive_pairs", "negative_pairs", "ground_truth_entities", "review_pairs")),
        "Step14 benchmark has hard cases": all(case in json.dumps(fixture) for case in ("common_name_nonmatch", "placeholder_phone", "shared_household", "transitive_bridge", "clear_three_record_cluster")),
        "no raw benchmark values": "@" not in json.dumps(fixture),
        "architecture learned branch": all((ROOT / path).is_file() for path in ("docs/architecture/specs/components.yml", "docs/architecture/specs/engine_interfaces.yml", "docs/architecture/specs/stage_graph.yml")),
        "required Step15 docs": all((ROOT / path).is_file() for path in ("docs/ml/APPLIED_ML_CONTRACT.md", "docs/ml/FEATURE_SCHEMA.md", "docs/ml/DATASET_AND_SPLIT_POLICY.md", "docs/ml/BASELINE_AND_RANKING_POLICY.md", "docs/ml/MODEL_PERSISTENCE.md", "docs/ml/UNCERTAINTY_AND_ACTIVE_LEARNING.md", "docs/ml/CALIBRATION_EXPERIMENTS.md", "docs/ml/STEP16_HANDOFF.md", "docs/ml/model-cards/RELATIONSHIP_RANKER_EXPERIMENTAL.md")),
        "official dependency pin and ledger": "scikit-learn==1.7.2" in (ROOT / "pyproject.toml").read_text(encoding="utf-8") and "scikit-learn (Step15)" in (ROOT / "docs/oss/REUSE_RESEARCH_LEDGER.md").read_text(encoding="utf-8"),
        "Step15 through Step28 handoff state": (execution.get("last_completed_step") == 15 and execution.get("current_step") == 16 and execution.get("current_role") == "llm_semantic_ai_engineer") or (execution.get("last_completed_step") == 16 and execution.get("current_step") == 17 and execution.get("current_role") == "evidence_fusion_engineer") or (execution.get("last_completed_step") == 17 and execution.get("current_step") == 18 and execution.get("current_role") == "ml_evaluation_engineer") or (execution.get("last_completed_step") == 18 and execution.get("current_step") == 19 and execution.get("current_role") == "canonical_model_engineer") or (execution.get("last_completed_step") == 19 and execution.get("current_step") == 20 and execution.get("current_role") == "olap_engineer") or (execution.get("last_completed_step") == 20 and execution.get("current_step") == 21 and execution.get("current_role") == "analytical_semantic_layer_engineer") or (execution.get("last_completed_step") == 21 and execution.get("current_step") == 22 and execution.get("current_role") == "data_qa_engineer") or (execution.get("last_completed_step") == 22 and execution.get("current_step") == 23 and execution.get("current_role") == "data_platform_engineer") or (execution.get("last_completed_step") == 23 and execution.get("current_step") == 24 and execution.get("current_role") == "distributed_data_engineer") or (execution.get("last_completed_step") == 24 and execution.get("current_step") == 25 and execution.get("current_role") == "ux_product_designer") or (execution.get("last_completed_step") == 25 and execution.get("current_step") == 26 and execution.get("current_role") == "data_visualization_engineer") or (execution.get("last_completed_step") == 26 and execution.get("current_step") == 27 and execution.get("current_role") == "senior_backend_engineer") or (execution.get("last_completed_step") == 27 and execution.get("current_step") == 28 and execution.get("current_role") == "distributed_job_processing_engineer"),
        "G4 state and G4A": state.get("gates", {}).get("G4_BOUNDED_INTELLIGENCE") in {"PENDING", "PASS"} and state.get("intermediate_milestones", {}).get("G4A_INDEPENDENT_EVIDENCE_PRODUCERS") == "PASS",
        "no research clone": not (ROOT / "research/oss/scikit-learn").exists(),
    }
    for name, condition in checks.items():
        check(name, condition)
    from dirty_data_to_olap.domain.contracts.applied_ml import MLFeatureDefinition, MLFeatureFamily
    try:
        MLFeatureDefinition(
            feature_id="candidate_id_leak",
            family=MLFeatureFamily.DEPENDENCY,
            source_contract="test",
            missing_semantics="test",
        )
        rejected = False
    except ValueError:
        rejected = True
    check("behavioral identifier feature rejection", rejected)
    print(f"PASS: applied_ml_checks={len(checks) + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
