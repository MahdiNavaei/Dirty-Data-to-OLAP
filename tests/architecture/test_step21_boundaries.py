from pathlib import Path

import yaml


def test_step21_contracts_and_service_do_not_import_external_semantic_engines_or_step22_runtime():
    root = Path(__file__).parents[2]
    semantic_contract = (root / "src/dirty_data_to_olap/domain/contracts/semantic.py").read_text(encoding="utf-8").lower()
    service = (root / "src/dirty_data_to_olap/application/semantic_layer.py").read_text(encoding="utf-8").lower()
    assert "import duckdb" not in semantic_contract
    assert not any(name in semantic_contract or name in service for name in ("metricflow", "dbt semantic", "cube", "lookml"))
    assert "step22" not in service
    assert "customer" not in service
    assert "order_line" not in service


def test_machine_architecture_contains_semantic_stage_without_new_review_checkpoint():
    root = Path(__file__).parents[2]
    components = yaml.safe_load((root / "docs/architecture/specs/components.yml").read_text(encoding="utf-8"))
    stages = yaml.safe_load((root / "docs/architecture/specs/stage_graph.yml").read_text(encoding="utf-8"))
    component = next(item for item in components["components"] if item["component_id"] == "application.semantic_layer")
    by_stage = {item["stage_id"]: item for item in stages["stages"]}
    assert component["implementation_step"] == 21
    assert component["implementation_owner"] == "Step21 Analytical Model / Semantic Layer Engineer"
    assert "SemanticModel" in component["output_contracts"]
    assert by_stage["SEMANTIC_MODELING"]["dependencies"] == ["ANALYTICAL_PLANNING", "MATERIALIZATION"]
    assert "SemanticModel" in by_stage["VALIDATION_RECONCILIATION"]["input_artifact_types"]
    assert "REVIEW_SEMANTIC_MODEL" not in (root / "docs/architecture/specs/review_checkpoints.yml").read_text(encoding="utf-8")

