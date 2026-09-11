from pathlib import Path


def test_step20_contracts_do_not_import_provider_or_evaluation_runtime():
    root = Path(__file__).parents[2]
    contracts = root / "src" / "dirty_data_to_olap" / "domain" / "contracts"
    analytical = (contracts / "analytical.py").read_text(encoding="utf-8").lower()
    assert "import duckdb" not in analytical
    assert "splink" not in analytical
    assert "evaluation" not in analytical
    planner = (root / "src" / "dirty_data_to_olap" / "application" / "analytical_planner.py").read_text(encoding="utf-8").lower()
    assert "step21" not in planner
    assert "semantic_layer" not in planner


def test_step20_artifact_package_has_no_step21_runtime_module():
    root = Path(__file__).parents[2]
    assert not any(path.name.lower().startswith("step21") for path in (root / "src").rglob("*"))
