"""Shared Step18 v3 staged-fixture construction for provider runners."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference, BatchReference, ColumnDescriptor, ExtractionMetrics, ExtractionPolicy,
    ObservationMode, ObservationScope, PublicationState, RecordLocatorKind, RowAccounting,
    SelectionScope, SnapshotConsistency, SourceCatalog, SourceDescriptor, SourceRecordReference,
    SourceSnapshot, SourceSnapshotResult, SourceTableKind, SourceType, StabilityScope,
    TableDescriptor, TableObservationStatus, TableSnapshotObservation,
)
from dirty_data_to_olap.evaluation.step18_scenarios import population_fingerprint, relationship_tables, scenario_content_fingerprint


def build_fixture(scenario: dict, run_root: Path) -> tuple[SourceCatalog, SourceSnapshotResult, str]:
    group = str(scenario["scenario_group_id"])
    source_id, snapshot_id = f"source-{group}", f"snapshot-{group}"
    table_rows = relationship_tables(scenario)
    adapter_ref = AdapterReference(name="step18-v3-fixture", version="3", config_fingerprint=f"scenario-{group}")
    tables = tuple(TableDescriptor(table_id=f"{name}-{group}", source_id=source_id, physical_name=f"{name}-{group}", table_kind=SourceTableKind.FILE, row_count=len(rows)) for name, rows in table_rows.items())
    columns = tuple(
        ColumnDescriptor(
            column_id=f"{table.table_id}-{name}",
            table_id=table.table_id,
            physical_name=name,
            ordinal=index,
            native_physical_type="INTEGER" if name == "customer_id_numeric" and table.table_id.startswith("orders-") else "TEXT",
            normalized_physical_type="integer" if name == "customer_id_numeric" and table.table_id.startswith("orders-") else "text",
        )
        for table, rows in zip(tables, table_rows.values())
        for index, name in enumerate(sorted({str(key) for row in rows for key in row}))
    )
    catalog = SourceCatalog(
        source=SourceDescriptor(source_id=source_id, display_name=group, source_type=SourceType.CSV, file_locator=f"{group}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{group}", adapter_reference=adapter_ref),
        tables=tables,
        columns=columns,
        declared_constraints=(),
    )
    snapshot = SourceSnapshot(
        source_id=source_id,
        snapshot_id=snapshot_id,
        execution_context_id=f"step18-v3-{group}",
        schema_fingerprint=f"schema-{group}",
        source_fingerprint=f"scenario-{scenario_content_fingerprint(scenario)}",
        observed_at="2026-09-11T00:00:00Z",
        selection_scope=SelectionScope(),
        observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=200, input_records_observed=sum(len(rows) for rows in table_rows.values())),
        extraction_policy=ExtractionPolicy(chunk_size=200),
        consistency=SnapshotConsistency.FILE_IMMUTABLE,
        adapter_reference=adapter_ref,
    )
    batches: list[BatchReference] = []
    references: list[SourceRecordReference] = []
    for table, rows in zip(tables, table_rows.values()):
        path = run_root / "staging" / group / f"{table.table_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(list(rows)), path)
        batch_id = f"batch-{group}-{table.table_id}"
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        batches.append(BatchReference(batch_id=batch_id, source_id=source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_index=0, row_count=len(rows), artifact_location=path.relative_to(run_root).as_posix(), content_hash=content_hash, schema_fingerprint=f"schema-{group}", first_extraction_ordinal=0, last_extraction_ordinal=len(rows) - 1, adapter_reference=adapter_ref, publication_state=PublicationState.COMPLETE))
        references.extend(SourceRecordReference(record_ref=f"{table.table_id}-record-{index}", source_id=source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_id=batch_id, extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for index in range(len(rows)))
    result = SourceSnapshotResult(
        snapshot=snapshot,
        batches=tuple(batches),
        record_references=tuple(references),
        accounting=RowAccounting(input_records_observed=len(references), successfully_staged_records=len(references), explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True),
        metrics=ExtractionMetrics(input_records_observed=len(references), staged_records=len(references), batch_count=len(batches), configured_chunk_size=200, bytes_staged=sum((run_root / batch.artifact_location).stat().st_size for batch in batches)),
        table_observations=tuple(TableSnapshotObservation(table_id=table.table_id, rows_observed=table.row_count or 0, status=TableObservationStatus.FULLY_OBSERVED) for table in tables),
    )
    population = {"scenario_group_id": group, "source_id": source_id, "snapshot_id": snapshot_id, "table_ids": sorted(table.table_id for table in tables), "scenario_content_fingerprint": scenario_content_fingerprint(scenario)}
    return catalog, result, hashlib.sha256(str(population).encode()).hexdigest()
