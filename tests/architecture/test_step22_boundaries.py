from pathlib import Path

import yaml


def test_validation_component_is_implemented_and_owned_by_step22():
    root = Path(__file__).parents[2]
    components = yaml.safe_load((root / "docs/architecture/specs/components.yml").read_text(encoding="utf-8"))
    validation = next(item for item in components["components"] if item["component_id"] == "application.validation")
    assert validation["implementation_status"] == "IMPLEMENTED"
    assert validation["implementation_step"] == 22
    assert "ValidationReport" in validation["output_contracts"]
    assert "SourceTruthManifest" in validation["input_contracts"]


def test_validation_application_stays_provider_agnostic():
    root = Path(__file__).parents[2]
    source = (root / "src/dirty_data_to_olap/application/validation.py").read_text(encoding="utf-8").lower()
    assert "import duckdb" not in source
    assert "step23" not in source
    assert "run_step20_reference" not in source


def test_runtime_transformation_and_domain_modules_do_not_consume_qa_truth():
    root = Path(__file__).parents[2]
    transformation = (root / "tools/step22_transformation_support.py").read_text(encoding="utf-8").lower()
    assert "benchmarks/validation" not in transformation
    assert "load_truth" not in transformation
    for relative in (
        "src/dirty_data_to_olap/application/canonical.py",
        "src/dirty_data_to_olap/application/analytical_planner.py",
        "src/dirty_data_to_olap/application/materializer.py",
        "src/dirty_data_to_olap/application/semantic_layer.py",
    ):
        source = (root / relative).read_text(encoding="utf-8").lower()
        assert "benchmarks/validation" not in source
        assert "step22_retail_source_truth" not in source
    qa_support = (root / "tools/step22_reference_support.py").read_text(encoding="utf-8").lower()
    assert "load_truth" in qa_support
