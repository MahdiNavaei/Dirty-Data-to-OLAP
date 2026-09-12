from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader, ValidationTargetError


def test_validation_application_has_no_database_driver_or_caller_sql_boundary():
    root = Path(__file__).parents[2]
    source = (root / "src/dirty_data_to_olap/application/validation.py").read_text(encoding="utf-8").lower()
    assert "import duckdb" not in source
    assert "connection" not in source
    assert "execute(" not in source


def test_validation_reader_rejects_path_traversal_and_unsafe_table_names(tmp_path):
    reader = DuckDBValidationTargetReader(tmp_path)
    with pytest.raises(ValidationTargetError, match="escapes repository"):
        reader.snapshot("../outside.duckdb", expected_sha256="0" * 64, allowed_table_names=("fact",))


def test_validation_reader_requires_exact_target_hash():
    root = Path(__file__).parents[2]
    reader = DuckDBValidationTargetReader(root)
    with pytest.raises(ValidationTargetError, match="STALE_TARGET"):
        reader.snapshot(
            "workspace/runs/step20-reference-run/olap/target.duckdb",
            expected_sha256="0" * 64,
            allowed_table_names=("fact_order_line",),
        )
