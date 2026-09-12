from dataclasses import replace
from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader, ValidationTargetError
from dirty_data_to_olap.application.validation import ValidationService
from dirty_data_to_olap.domain.contracts.validation import ValidationStatus
from tools.step22_reference_support import build_reference_context


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


def test_validation_rejects_stale_analytical_input_binding():
    root = Path(__file__).parents[2]
    context = build_reference_context("generic")
    inputs = replace(
        context.inputs,
        analytical_input_binding=context.inputs.analytical_input_binding.model_copy(
            update={"binding_id": "stale-input-binding"}
        ),
    )
    outcome = ValidationService().validate(inputs, DuckDBValidationTargetReader(root))
    assert next(item for item in outcome.report.checks if item.check_id == "artifact_binding").status is ValidationStatus.FAIL
