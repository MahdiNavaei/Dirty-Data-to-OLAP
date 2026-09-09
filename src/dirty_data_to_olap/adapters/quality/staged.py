"""PyArrow reader for integrity-checked Step07 staged Parquet batches."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from dirty_data_to_olap.application.quality_reader import QualityInputIntegrityError, QualityStagedReader
from dirty_data_to_olap.domain.contracts.quality import QualityStagedRow
from dirty_data_to_olap.domain.contracts.source import (
    BatchReference,
    PublicationState,
    SourceSnapshotResult,
    SourceCatalog,
    TableDescriptor,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ParquetQualityStagedReader(QualityStagedReader):
    """Reads only COMPLETE batches pinned to one source snapshot."""

    def scan_table(self, snapshot_result: SourceSnapshotResult, catalog: SourceCatalog, table: TableDescriptor, physical_columns: Sequence[str], *, project_root: Path) -> tuple[QualityStagedRow, ...]:
        import pyarrow.parquet as pq

        project_root = project_root.resolve()
        refs = {(ref.batch_id, ref.extraction_ordinal): ref for ref in snapshot_result.record_references}
        rows: list[QualityStagedRow] = []
        batches = tuple(batch for batch in snapshot_result.batches if batch.table_id == table.table_id)
        observation = next((item for item in snapshot_result.table_observations if item.table_id == table.table_id), None)
        if not batches and observation is not None and observation.rows_observed:
            raise QualityInputIntegrityError("observed table has no COMPLETE staged batches")
        for batch in batches:
            self._verify_batch(batch, snapshot_result, catalog, table, project_root)
            path = (project_root / batch.artifact_location).resolve()
            try:
                parquet = pq.ParquetFile(path)
                if any(name not in parquet.schema_arrow.names for name in physical_columns):
                    raise QualityInputIntegrityError("requested quality projection is absent from staged schema")
                yielded = 0
                ordinal = batch.first_extraction_ordinal or 0
                for arrow_batch in parquet.iter_batches(batch_size=min(max(batch.row_count, 1), 10_000), columns=list(physical_columns)):
                    for values in arrow_batch.to_pylist():
                        ref = refs.get((batch.batch_id, ordinal))
                        if ref is None or ref.source_id != snapshot_result.snapshot.source_id or ref.snapshot_id != snapshot_result.snapshot.snapshot_id or ref.table_id != table.table_id:
                            raise QualityInputIntegrityError("staged row has no matching source record reference")
                        rows.append(QualityStagedRow(record_ref=ref.record_ref, extraction_ordinal=ordinal, values=values))
                        ordinal += 1
                        yielded += 1
                if yielded != batch.row_count:
                    raise QualityInputIntegrityError("staged row count differs from COMPLETE batch reference")
            except QualityInputIntegrityError:
                raise
            except Exception as error:
                raise QualityInputIntegrityError(f"staged Parquet batch could not be read: {error.__class__.__name__}") from None
        return tuple(rows)

    @staticmethod
    def _verify_batch(batch: BatchReference, snapshot_result: SourceSnapshotResult, catalog: SourceCatalog, table: TableDescriptor, project_root: Path) -> None:
        if batch.publication_state is not PublicationState.COMPLETE:
            raise QualityInputIntegrityError("quality analysis accepts COMPLETE batches only")
        if batch.source_id != snapshot_result.snapshot.source_id or batch.snapshot_id != snapshot_result.snapshot.snapshot_id:
            raise QualityInputIntegrityError("batch is bound to another source snapshot")
        if batch.table_id != table.table_id or batch.schema_fingerprint != catalog.source.schema_fingerprint:
            raise QualityInputIntegrityError("staged batch schema or table binding mismatch")
        path = (project_root / batch.artifact_location).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            raise QualityInputIntegrityError("staged batch path escapes project root") from None
        if not path.is_file() or _sha256(path) != batch.content_hash:
            raise QualityInputIntegrityError("staged batch content hash mismatch")
