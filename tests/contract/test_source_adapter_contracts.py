from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.source import (
    ExtractionPolicy,
    SelectionScope,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
)


ROOT = Path(__file__).resolve().parents[2]


def _assert_project_contracts(catalog: object, result: object) -> None:
    catalog_json = catalog.model_dump_json()
    result_json = result.model_dump_json()
    assert catalog_json == catalog.model_dump_json()
    assert result_json == result.model_dump_json()
    assert all(getattr(item, "schema_version") == "1.0" for item in [catalog, *result.batches, *result.record_references])
    assert all(batch.publication_state.value == "COMPLETE" for batch in result.batches)
    assert result.accounting.input_records_observed == result.accounting.successfully_staged_records
    assert not any(token in catalog_json + result_json for token in ("DltResource", "MetaData", "Engine", "pyarrow.Table", "Workbook"))


def test_reusable_contract_suite_csv() -> None:
    root = ROOT / "workspace" / "tests" / f"contract_csv_{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        path = root / "source.csv"
        path.write_text("id,label\n1,one\n2,two\n", encoding="utf-8")
        record = SourceRegistryRecord(registry_id="csv", display_name="CSV", source_type=SourceType.CSV, file_locator=str(path), adapter_name="file_source", adapter_version="1.0.0")
        registry = InMemorySourceRegistry()
        registry.register(record)
        adapter = FileSourceAdapter(SourceType.CSV, project_root=ROOT)
        selection = SourceSelection(registry_id="csv", extraction=ExtractionPolicy(chunk_size=1), execution_context_id="contract")
        catalog = SourceDiscoveryService(registry, {"file_source": adapter}).discover(selection)
        result = SourceSnapshotService(registry, {"file_source": adapter}).extract(catalog, selection, staging_root=root / "run" / "staging")
        _assert_project_contracts(catalog, result)
        assert {item.table_id for item in result.record_references} == {catalog.tables[0].table_id}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reusable_contract_suite_parquet() -> None:
    root = ROOT / "workspace" / "tests" / f"contract_parquet_{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        path = root / "source.parquet"
        pq.write_table(pa.table({"id": [1, 2], "label": ["one", "two"]}), path, row_group_size=1)
        record = SourceRegistryRecord(registry_id="parquet", display_name="Parquet", source_type=SourceType.PARQUET, file_locator=str(path), adapter_name="file_source", adapter_version="1.0.0")
        registry = InMemorySourceRegistry()
        registry.register(record)
        adapter = FileSourceAdapter(SourceType.PARQUET, project_root=ROOT)
        selection = SourceSelection(registry_id="parquet", extraction=ExtractionPolicy(chunk_size=1), execution_context_id="contract")
        catalog = SourceDiscoveryService(registry, {"file_source": adapter}).discover(selection)
        result = SourceSnapshotService(registry, {"file_source": adapter}).extract(catalog, selection, staging_root=root / "run" / "staging")
        _assert_project_contracts(catalog, result)
        assert len(result.batches) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reusable_contract_suite_dlt_sqlite(sqlite_fixture_path: Path) -> None:
    record = SourceRegistryRecord(
        registry_id="sqlite",
        display_name="SQLite",
        source_type=SourceType.SQLITE,
        connection_profile=ConnectionProfileReference(profile_id="contract-sqlite", database_engine=DatabaseEngine.SQLITE, database_name=str(sqlite_fixture_path)),
        adapter_name="dlt_sql_source",
        adapter_version="1.0.0",
    )
    registry = InMemorySourceRegistry()
    registry.register(record)
    adapter = DltSqlSourceAdapter(project_root=ROOT)
    selection = SourceSelection(registry_id="sqlite", extraction=ExtractionPolicy(chunk_size=2), execution_context_id="contract-sqlite")
    catalog = SourceDiscoveryService(registry, {"dlt_sql_source": adapter}).discover(selection)
    root = ROOT / "workspace" / "tests" / f"contract_sql_{uuid4().hex}"
    try:
        result = SourceSnapshotService(registry, {"dlt_sql_source": adapter}).extract(catalog, selection, staging_root=root / "run" / "staging")
        _assert_project_contracts(catalog, result)
    finally:
        shutil.rmtree(root, ignore_errors=True)

