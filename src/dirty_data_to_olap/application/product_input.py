"""Typed analytical input translation from the immutable staged snapshot."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalCell,
    AnalyticalColumnBinding,
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalInputTable,
    AnalyticalRowBatch,
)
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, stable_id


def _typed_value(value: Any, logical_type: str) -> Any:
    if value is None:
        return None
    kind = str(logical_type).strip().upper()
    if kind == "DATE":
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if kind == "DECIMAL":
        return value if isinstance(value, Decimal) else Decimal(str(value))
    return str(value) if kind == "STRING" else value


def build_order_dataset(
    *,
    run_id: str,
    catalog: SourceCatalog,
    snapshot: SourceSnapshotResult,
    canonical: CanonicalModel,
    project_root: Path,
    table_name: str,
    column_types: Mapping[str, str],
) -> tuple[AnalyticalInputDataset, AnalyticalInputBinding]:
    table = next(item for item in catalog.tables if item.physical_name == table_name)
    source_columns = tuple(item for item in catalog.columns if item.table_id == table.table_id)
    map_by_record = {item.record_ref: item for item in canonical.source_record_maps}
    physical_names = tuple(item.physical_name for item in source_columns)
    reader = ParquetQualityStagedReader()
    rows: list[AnalyticalInputRow] = []
    for staged in reader.iter_table(snapshot, catalog, table, physical_names, project_root=project_root):
        mapping = map_by_record.get(staged.record_ref)
        if mapping is None:
            raise ValueError("analytical input row is not covered by finalized canonical membership")
        values = [
            AnalyticalCell(column_name=name, value=_typed_value(staged.values.get(name), column_types.get(name, "STRING")))
            for name in physical_names
        ]
        values.append(AnalyticalCell(column_name="canonical_entity_id", value=mapping.canonical_entity_id))
        rows.append(AnalyticalInputRow(
            row_ref=stable_id("ainput-row", {"run": run_id, "record_ref": staged.record_ref}),
            canonical_reference=mapping.canonical_entity_id,
            values=tuple(values),
            source_record_refs=(staged.record_ref,),
            lineage_refs=(staged.record_ref, snapshot.snapshot.snapshot_id),
        ))
    columns = tuple(
        AnalyticalColumnBinding(
            column_id=column.column_id if column.physical_name != "canonical_entity_id" else stable_id("derived-column", {"table": table.table_id, "name": "canonical_entity_id"}),
            column_name=column.physical_name,
            logical_type=column_types.get(column.physical_name, "STRING"),
            nullable=column.schema_nullable is not False,
            canonical_attribute_refs=(),
            lineage_refs=(column.column_id, snapshot.snapshot.snapshot_id),
        )
        for column in source_columns
    ) + (
        AnalyticalColumnBinding(
            column_id=stable_id("derived-column", {"table": table.table_id, "name": "canonical_entity_id"}),
            column_name="canonical_entity_id",
            logical_type="STRING",
            nullable=False,
            canonical_attribute_refs=(),
            lineage_refs=(canonical.model_id, snapshot.snapshot.snapshot_id),
        ),
    )
    batch = AnalyticalRowBatch(
        batch_id=stable_id("analytical-batch", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "table": table.table_id}),
        table_id=table.table_id,
        rows=tuple(rows),
        source_batch_refs=tuple(item.batch_id for item in snapshot.batches if item.table_id == table.table_id),
        source_snapshot_fingerprints={snapshot.snapshot.source_id: snapshot.snapshot.source_fingerprint or snapshot.snapshot.schema_fingerprint},
        lineage_refs=(snapshot.snapshot.snapshot_id, table.table_id),
    )
    dataset = AnalyticalInputDataset(
        dataset_id=stable_id("analytical-dataset", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "table": table.table_id, "rows": [item.row_ref for item in rows]}),
        canonical_model_id=canonical.model_id,
        canonical_model_content_hash=canonical.content_hash,
        tables=(AnalyticalInputTable(
            table_id=table.table_id,
            canonical_concept_ref=canonical.entity_types[0].semantic_id,
            columns=columns,
            batches=(batch,),
            source_table_refs=(table.table_id,),
            lineage_refs=(snapshot.snapshot.snapshot_id, table.table_id),
        ),),
        source_schema_fingerprints={catalog.source.source_id: catalog.source.schema_fingerprint},
        source_snapshot_fingerprints={catalog.source.source_id: snapshot.snapshot.source_fingerprint or snapshot.snapshot.schema_fingerprint},
        allow_literal_sql=False,
        provenance_refs=("application.product_input", snapshot.snapshot.snapshot_id, catalog.source.schema_fingerprint, canonical.model_id),
    )
    binding = AnalyticalInputBinding(
        binding_id=stable_id("input-binding", {"run": run_id, "dataset": dataset.dataset_id, "content": dataset.content_hash}),
        canonical_model_id=canonical.model_id,
        canonical_model_content_hash=canonical.content_hash,
        dataset_id=dataset.dataset_id,
        dataset_content_hash=dataset.content_hash,
        source_schema_fingerprints=dict(dataset.source_schema_fingerprints),
        source_snapshot_fingerprints=dict(dataset.source_snapshot_fingerprints),
        row_counts=dataset.row_counts,
        provenance_refs=(dataset.dataset_id, snapshot.snapshot.snapshot_id, canonical.model_id),
    )
    return dataset, binding
