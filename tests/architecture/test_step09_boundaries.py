from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "dirty_data_to_olap"


def test_quality_core_has_no_provider_imports_or_source_write_boundary():
    for root in (SRC / "domain", SRC / "application"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
                    assert not any(name in {"pandas", "numpy", "pyarrow", "dataprofiler"} or name.startswith(("pandas.", "numpy.", "pyarrow.", "dataprofiler.")) for name in names + [module]), path
    quality_text = "\n".join(path.read_text(encoding="utf-8") for path in (SRC / "application").glob("quality*.py"))
    assert "source writes" in quality_text.lower() or "source write" not in quality_text.lower()
    assert "overall score" not in quality_text.lower()
    assert "canonical" not in quality_text.lower()


def test_quality_governance_artifacts_and_validator_exist():
    assert (ROOT / "tools" / "validate_data_quality.py").is_file()
    assert sorted(path.name for path in (ROOT / "docs" / "quality").glob("*.md")) == [
        "MEASUREMENT_AND_SEVERITY.md",
        "QUALITY_CONTRACT.md",
        "QUALITY_VECTOR.md",
        "REPAIR_PROPOSALS.md",
        "RULE_CATALOG.md",
    ]
    assert list((ROOT / "rules" / "quality").glob("*.yml"))
