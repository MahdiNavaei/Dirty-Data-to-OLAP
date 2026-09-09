from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "dirty_data_to_olap"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def test_profiling_vendor_boundary_and_no_native_persistence():
    forbidden_core = ("dataprofiler", "pandas", "numpy", "pyarrow", "pickle")
    for root in (SRC / "domain", SRC / "application"):
        for path in root.rglob("*.py"):
            imports = _imports(path)
            assert not any(name == token or name.startswith(token + ".") for name in imports for token in forbidden_core), path
    adapter_imports = set().union(*(_imports(path) for path in (SRC / "adapters" / "profiling").rglob("*.py")))
    assert any(name == "dataprofiler" or name.startswith("dataprofiler.") for name in adapter_imports)
    source = "\n".join(path.read_text(encoding="utf-8") for path in (SRC / "adapters" / "profiling").rglob("*.py"))
    assert "pickle" not in source.lower()
    assert "quality_issue" not in source.lower()
