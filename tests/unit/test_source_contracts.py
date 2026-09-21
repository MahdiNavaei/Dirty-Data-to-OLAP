from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    ExtractionPolicy,
    RecordLocatorKind,
    SelectionScope,
    SourceFailure,
    SourceFailureKind,
    SourceRecordReference,
    SourceSelection,
    StabilityScope,
    column_id_for,
    file_content_fingerprint,
    record_ref_for_ordinal,
    record_ref_for_pk,
    schema_fingerprint,
    table_id_for,
    SourceTableKind,
    SourceRegistryRecord,
    SourceType,
)
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.adapters.sources.files import detect_file_source_type


def test_physical_ids_and_record_refs_are_stable_and_scoped() -> None:
    table_id = table_id_for("src_a", None, "orders", SourceTableKind.TABLE)
    assert table_id == table_id_for("src_a", None, "orders", SourceTableKind.TABLE)
    assert table_id != table_id_for("src_b", None, "orders", SourceTableKind.TABLE)
    assert column_id_for(table_id, "id", 0) == column_id_for(table_id, "id", 0)
    assert record_ref_for_pk("src_a", "snap_a", table_id, ("first", "second"), (1, 2)) != record_ref_for_pk("src_a", "snap_a", table_id, ("second", "first"), (2, 1))
    assert record_ref_for_ordinal("src_a", "snap_a", table_id, 0) != record_ref_for_ordinal("src_a", "snap_a", table_id, 1)


def test_schema_fingerprint_changes_for_physical_structure() -> None:
    base = {"tables": [{"name": "orders", "columns": [{"name": "id", "type": "integer"}]}]}
    assert schema_fingerprint(base) == schema_fingerprint(base)
    assert schema_fingerprint(base) != schema_fingerprint({"tables": [{"name": "orders", "columns": [{"name": "id", "type": "text"}]}]})
    assert schema_fingerprint(base) != schema_fingerprint({"tables": [{"name": "orders", "columns": [{"name": "id", "type": "integer"}, {"name": "new", "type": "text"}]}]})


def test_selection_and_raw_value_policy_are_explicit() -> None:
    selection = SourceSelection(registry_id="registry", scope=SelectionScope(included_objects=("odd table",)), extraction=ExtractionPolicy(chunk_size=3, null_markers=("NULL",)))
    assert selection.extraction.preserve_raw_values is True
    with pytest.raises(ValueError):
        ExtractionPolicy(chunk_size=0)
    with pytest.raises(ValueError):
        ExtractionPolicy(chunk_size=3, preserve_raw_values=False)


def test_file_fingerprint_uses_content_not_mtime() -> None:
    path = Path(__file__).resolve().parents[2] / "workspace" / "tests" / f"fingerprint_{uuid4().hex}.csv"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("id\n1\n", encoding="utf-8")
        first = file_content_fingerprint(path)
        path.write_text("id\n2\n", encoding="utf-8")
        assert first != file_content_fingerprint(path)
    finally:
        path.unlink(missing_ok=True)


def test_record_reference_contract_preserves_key_order_and_scope() -> None:
    reference = SourceRecordReference(
        record_ref="rec_1",
        source_id="src_1",
        snapshot_id="snap_1",
        table_id="tbl_1",
        batch_id="batch_1",
        extraction_ordinal=0,
        locator_kind=RecordLocatorKind.PRIMARY_KEY,
        key_columns=("part_a", "part_b"),
        key_values=("A", "B"),
        stability_scope=StabilityScope.SOURCE_KEY_SCOPE,
    )
    assert reference.key_columns == ("part_a", "part_b")
    assert reference.model_dump_json() == reference.model_dump_json()


def test_source_failure_redacts_runtime_secret_material() -> None:
    failure = SourceFailure(kind=SourceFailureKind.ACCESS_FAILED, operation="connect", detail="postgresql://user:fake-token@host/db password=super-secret", retryable=False)
    rendered = failure.model_dump_json() + str(failure)
    assert "fake-token" not in rendered
    assert "super-secret" not in rendered


def test_registry_rejects_secret_bearing_adapter_configuration() -> None:
    with pytest.raises(ValueError):
        SourceRegistryRecord(
            registry_id="r1",
            display_name="unsafe",
            source_type=SourceType.SQLITE,
            connection_profile=ConnectionProfileReference(profile_id="p1", database_engine=DatabaseEngine.SQLITE, database_name="source.sqlite"),
            adapter_name="dlt_sql_source",
            adapter_version="1",
            adapter_config={"password": "secret-value"},
        )


def test_registry_owner_is_separate_from_adapter_configuration() -> None:
    record = SourceRegistryRecord(
        registry_id="owned-source",
        display_name="owned source",
        source_type=SourceType.CSV,
        file_locator="workspace/tests/owned.csv",
        adapter_name="file_source",
        adapter_version="1.0.0",
        adapter_config={"managed_import": "true"},
        owner_subject="prompt02-owner",
    )
    assert record.owner_subject == "prompt02-owner"
    assert "_owner_subject" not in record.adapter_config


def test_file_source_type_detection_is_explicit() -> None:
    assert detect_file_source_type("orders.csv").value == "csv"
    assert detect_file_source_type("orders.parquet").value == "parquet"
    with pytest.raises(Exception):
        detect_file_source_type("orders.json")
