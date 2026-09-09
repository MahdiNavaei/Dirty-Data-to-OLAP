from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.source import (
    PublicationState,
    SelectionScope,
    SourceFailureKind,
    SourceIngestionError,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
    ExtractionPolicy,
    file_content_fingerprint,
    RowCountSemantics,
)


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def step07_workspace() -> Path:
    path = ROOT / "workspace" / "tests" / f"step07_{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _selection(*, chunk_size: int = 2, max_rows: int | None = None, scope: SelectionScope | None = None) -> SourceSelection:
    return SourceSelection(
        registry_id="source-1",
        scope=scope or SelectionScope(),
        extraction=ExtractionPolicy(chunk_size=chunk_size, max_rows=max_rows),
        execution_context_id="step07-test-run",
    )


def _sqlite_record(path: Path) -> SourceRegistryRecord:
    return SourceRegistryRecord(
        registry_id="source-1",
        display_name="Step07 SQLite fixture",
        source_type=SourceType.SQLITE,
        connection_profile=ConnectionProfileReference(
            profile_id="step07-sqlite",
            database_engine=DatabaseEngine.SQLITE,
            database_name=str(path),
        ),
        scope=SelectionScope(included_objects=("parent", "child", "duplicate_rows", "child_view", "odd table"), include_views=True),
        adapter_name="dlt_sql_source",
        adapter_version="1.0.0",
    )


def _make_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE parent (parent_id INTEGER PRIMARY KEY, parent_code TEXT NOT NULL UNIQUE);
            CREATE TABLE child (
                parent_id INTEGER NOT NULL,
                child_seq INTEGER NOT NULL,
                label TEXT,
                PRIMARY KEY (parent_id, child_seq),
                FOREIGN KEY (parent_id) REFERENCES parent(parent_id)
            );
            CREATE TABLE duplicate_rows (value TEXT, note TEXT);
            CREATE TABLE "odd table" ("odd column" TEXT, value INTEGER);
            CREATE VIEW child_view AS SELECT parent_id, child_seq, label FROM child;
            INSERT INTO parent VALUES (1, 'P-001'), (2, 'P-002');
            INSERT INTO child VALUES (1, 1, 'first'), (1, 2, 'second'), (2, 1, 'third');
            INSERT INTO duplicate_rows VALUES ('same', 'row'), ('same', 'row');
            INSERT INTO "odd table" VALUES ('quoted', 7);
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_sqlite_dlt_discovery_extraction_and_staging(step07_workspace: Path) -> None:
    database_path = step07_workspace / "source.sqlite"
    _make_sqlite(database_path)
    registry = InMemorySourceRegistry()
    record = registry.register(_sqlite_record(database_path))
    adapter = DltSqlSourceAdapter(project_root=ROOT)
    discovery = SourceDiscoveryService(registry, {record.adapter_name: adapter})
    selection = _selection()
    catalog = discovery.discover(selection)
    assert catalog.source.source_type is SourceType.SQLITE
    assert {table.physical_name for table in catalog.tables} == {"parent", "child", "duplicate_rows", "child_view", "odd table"}
    child_fk = next(item for item in catalog.declared_constraints if item.constraint_type == "FOREIGN_KEY")
    assert child_fk.declared is True
    assert child_fk.referenced_table_name == "parent"
    child_columns = [item for item in catalog.columns if item.table_id == child_fk.table_id]
    assert next(item for item in child_columns if item.physical_name == "parent_id").schema_nullable is False
    # SQLite's ordinary INTEGER PRIMARY KEY reports raw notnull=false; PK status stays separate.
    parent_id = next(item for item in catalog.columns if item.physical_name == "parent_id" and item.table_id != child_fk.table_id)
    assert parent_id.primary_key_position == 1
    assert parent_id.raw_declared_notnull is False
    assert parent_id.schema_nullable is True

    result = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(
        catalog, selection, staging_root=step07_workspace / "workspace" / "runs" / "step07-test-run" / "staging"
    )
    assert result.accounting.input_records_observed == 11
    assert result.accounting.successfully_staged_records == 11
    assert result.snapshot.consistency.value == "TRANSACTION_SCOPED"
    assert len(result.batches) >= 5
    assert all(batch.publication_state is PublicationState.COMPLETE for batch in result.batches)
    assert all((ROOT / batch.artifact_location).is_file() for batch in result.batches)
    assert any(item.locator_kind.value == "PRIMARY_KEY" for item in result.record_references)
    no_key_refs = [item for item in result.record_references if item.table_id == next(t.table_id for t in catalog.tables if t.physical_name == "duplicate_rows")]
    assert len(no_key_refs) == 2 and no_key_refs[0].record_ref != no_key_refs[1].record_ref
    assert (step07_workspace / "workspace" / "runs" / "step07-test-run" / "catalog" / "sources.json").is_file()
    assert (step07_workspace / "workspace" / "runs" / "step07-test-run" / "source_manifests" / "extraction.json").is_file()
    engine = adapter._engine(record.connection_profile)
    try:
        with engine.connect() as connection:
            with pytest.raises(Exception):
                connection.exec_driver_sql("INSERT INTO parent VALUES (99, 'blocked')")
    finally:
        engine.dispose()
    check_connection = sqlite3.connect(database_path)
    try:
        assert check_connection.execute("SELECT COUNT(*) FROM parent").fetchone()[0] == 2
    finally:
        check_connection.close()
    run_root = step07_workspace / "workspace" / "runs" / "step07-test-run"
    catalog_json = json.loads((run_root / "catalog" / "sources.json").read_text(encoding="utf-8"))
    manifest_json = json.loads((run_root / "source_manifests" / "extraction.json").read_text(encoding="utf-8"))
    print("INSPECT_CATALOG_SOURCE", catalog_json[0])
    print("INSPECT_TABLE", json.loads((run_root / "catalog" / "tables.json").read_text(encoding="utf-8"))[0])
    print("INSPECT_COLUMN", json.loads((run_root / "catalog" / "columns.json").read_text(encoding="utf-8"))[0])
    print("INSPECT_CONSTRAINT", json.loads((run_root / "catalog" / "declared_constraints.json").read_text(encoding="utf-8"))[0])
    print("INSPECT_SNAPSHOT", manifest_json["snapshot"])
    print("INSPECT_BATCH", manifest_json["batches"][0])
    print("INSPECT_RECORD_REF", manifest_json["record_references"][0])
    print("INSPECT_ACCOUNTING", manifest_json["accounting"])
    first_batch = ROOT / result.batches[0].artifact_location
    print("INSPECT_PARQUET_SCHEMA", str(pq.read_schema(first_batch)))
    print("INSPECT_PARQUET_VALUES", pq.read_table(first_batch).to_pylist())
    serialized = json.dumps(manifest_json, ensure_ascii=False)
    print("INSPECT_SECRET_SCAN", "password" not in serialized.lower() and "token" not in serialized.lower())
    print("INSPECT_PARTIAL_FILES", [str(path) for path in run_root.rglob("*.partial")])


def _file_record(path: Path, source_type: SourceType, *, scope: SelectionScope | None = None) -> SourceRegistryRecord:
    return SourceRegistryRecord(
        registry_id="source-1",
        display_name=f"Step07 {source_type.value} fixture",
        source_type=source_type,
        file_locator=str(path),
        scope=scope or SelectionScope(),
        adapter_name="file_source",
        adapter_version="1.0.0",
    )


def test_csv_bom_dirty_values_duplicates_and_row_accounting(step07_workspace: Path) -> None:
    path = step07_workspace / "dirty.csv"
    path.write_bytes("id,description,empty,null_marker\n1,\"hello,world\",,NULL\n1,\"hello,world\",,NULL\n".encode("utf-8-sig"))
    registry = InMemorySourceRegistry()
    record = registry.register(_file_record(path, SourceType.CSV))
    adapter = FileSourceAdapter(SourceType.CSV, project_root=ROOT)
    selection = _selection(chunk_size=1)
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    assert [column.physical_name for column in catalog.columns] == ["id", "description", "empty", "null_marker"]
    result = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(catalog, selection, staging_root=step07_workspace / "workspace" / "runs" / "csv" / "staging")
    assert result.accounting.input_records_observed == 2
    assert result.accounting.successfully_staged_records == 2
    assert len({item.record_ref for item in result.record_references}) == 2
    staged = pq.read_table(ROOT / result.batches[0].artifact_location).to_pylist()
    assert staged[0] == {"id": "1", "description": "hello,world", "empty": "", "null_marker": "NULL"}
    assert file_content_fingerprint(path).startswith("sha256:")


def test_malformed_csv_fails_explicitly_and_publishes_no_complete_result(step07_workspace: Path) -> None:
    path = step07_workspace / "malformed.csv"
    path.write_text("id,name\n1,ok\n2,\"unterminated\n", encoding="utf-8")
    registry = InMemorySourceRegistry()
    record = registry.register(_file_record(path, SourceType.CSV))
    adapter = FileSourceAdapter(SourceType.CSV, project_root=ROOT)
    selection = _selection()
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    with pytest.raises(SourceIngestionError) as raised:
        SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(catalog, selection, staging_root=step07_workspace / "workspace" / "runs" / "bad" / "staging")
    assert raised.value.failure.kind is SourceFailureKind.PARSE_FAILED
    assert not list((step07_workspace / "workspace" / "runs" / "bad").rglob("*.parquet"))


def test_parquet_row_groups_projection_and_restart(step07_workspace: Path) -> None:
    path = step07_workspace / "source.parquet"
    pq.write_table(pa.table({"id": list(range(5)), "payload": ["a", "b", "c", "d", "e"]}), path, row_group_size=2)
    scope = SelectionScope(included_columns={path.stem: ("id",)})
    registry = InMemorySourceRegistry()
    record = registry.register(_file_record(path, SourceType.PARQUET, scope=scope))
    adapter = FileSourceAdapter(SourceType.PARQUET, project_root=ROOT)
    selection = _selection(chunk_size=2)
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    assert catalog.tables[0].row_count == 5
    assert catalog.tables[0].row_count_semantics is RowCountSemantics.EXACT
    root = step07_workspace / "workspace" / "runs" / "parquet" / "staging"
    service = SourceSnapshotService(registry, {record.adapter_name: adapter})
    first = service.extract(catalog, selection, staging_root=root)
    second = service.extract(catalog, selection, staging_root=root)
    assert len(first.batches) == 3
    assert first.batches == second.batches
    assert all(pq.read_table(ROOT / batch.artifact_location).column_names == ["id"] for batch in first.batches)
    assert not list(root.rglob("*.partial"))


def test_xlsx_optional_read_only_pipeline(step07_workspace: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = step07_workspace / "source.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Orders"
    sheet.append(["id", "amount"])
    sheet.append([1, 10.5])
    sheet.append([2, 20.0])
    workbook.save(path)
    workbook.close()
    registry = InMemorySourceRegistry()
    record = registry.register(_file_record(path, SourceType.XLSX, scope=SelectionScope(included_objects=("Orders",))))
    adapter = FileSourceAdapter(SourceType.XLSX, project_root=ROOT)
    selection = _selection(chunk_size=1)
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    result = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(catalog, selection, staging_root=step07_workspace / "workspace" / "runs" / "xlsx" / "staging")
    assert result.accounting.successfully_staged_records == 2
    assert pq.read_table(ROOT / result.batches[0].artifact_location).to_pylist()[0]["id"] == 1
