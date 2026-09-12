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
