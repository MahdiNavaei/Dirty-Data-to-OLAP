from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySession


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "dirty_data_to_olap"


def test_domain_contracts_do_not_import_driver_or_adapter_modules() -> None:
    domain_root = SRC / "domain"
    forbidden = {"sqlite3", "dirty_data_to_olap.adapters", "sqlalchemy", "dlt"}
    for path in domain_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(any(name == item or name.startswith(item + ".") for item in forbidden) for name in imports), path


def test_sql_session_has_no_public_arbitrary_query_surface() -> None:
    public_names = {name for name in dir(SQLiteReadOnlySession) if not name.startswith("_")}
    assert "execute" not in public_names
    assert "run_any_query" not in public_names
    assert {"inspect_database_metadata", "inspect_table_metadata", "sample_rows_bounded", "explain_bounded_read"} <= public_names


def test_driver_objects_and_research_clones_do_not_cross_project_boundary() -> None:
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))
    assert "research/oss" not in source_text.replace("\\", "/")
    assert "SourceSnapshot" not in source_text
    assert "SourceCatalog" not in source_text


def test_phase_aware_governance_validators_accept_authorized_step06() -> None:
    for validator in ("validate_domain_docs.py", "validate_data_architecture.py", "validate_solution_architecture.py"):
        result = subprocess.run(
            ["python", str(ROOT / "tools" / validator)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{validator}: {result.stdout}\n{result.stderr}"
