"""Integrity-checked, read-only access to complete source-faithful staging."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from dirty_data_to_olap.domain.contracts.source import (
    BatchReference,
    PublicationState,
    SourceCatalog,
    SourceSnapshotResult,
    TableDescriptor,
)


class DependencyInputIntegrityError(ValueError):
    """Raised when staged evidence is not immutable, complete or bound."""


@dataclass(frozen=True)
class StagedDependencyRow:
    record_ref: str
    extraction_ordinal: int
    values: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DependencyStagedReader:
    """Read only COMPLETE Parquet batches from the supplied snapshot result."""

    def iter_table(
        self,
        snapshot_result: SourceSnapshotResult,
        catalog: SourceCatalog,
        table: TableDescriptor,
        physical_columns: Sequence[str],
        *,
        project_root: Path,
    ) -> Iterator[StagedDependencyRow]:
        import pyarrow.parquet as pq

        root = project_root.resolve()
        refs = {(item.batch_id, item.extraction_ordinal): item for item in snapshot_result.record_references}
        batches = tuple(item for item in snapshot_result.batches if item.table_id == table.table_id)
        observation = next((item for item in snapshot_result.table_observations if item.table_id == table.table_id), None)
        if observation is None:
            raise DependencyInputIntegrityError("dependency input is missing table observation provenance")
        if observation.rows_observed and sum(item.row_count for item in batches) != observation.rows_observed:
            raise DependencyInputIntegrityError("complete staged batches do not reconcile table observation")
        if observation.rows_observed and not batches:
            raise DependencyInputIntegrityError("observed table has no complete staged batches")
        expected_columns = {item.physical_name for item in catalog.columns if item.table_id == table.table_id}
        if set(physical_columns) - expected_columns:
            raise DependencyInputIntegrityError("dependency projection contains an unknown physical column")
        for batch in batches:
            self._verify_batch(batch, snapshot_result, catalog, table, root)
            path = (root / batch.artifact_location).resolve()
            parquet = pq.ParquetFile(path)
            if set(physical_columns) - set(parquet.schema_arrow.names):
                raise DependencyInputIntegrityError("dependency projection is absent from staged schema")
            ordinal = batch.first_extraction_ordinal or 0
            yielded = 0
            try:
                for arrow_batch in parquet.iter_batches(batch_size=min(max(batch.row_count, 1), 10_000), columns=list(physical_columns)):
                    for values in arrow_batch.to_pylist():
                        reference = refs.get((batch.batch_id, ordinal))
                        if reference is None or reference.source_id != snapshot_result.snapshot.source_id or reference.snapshot_id != snapshot_result.snapshot.snapshot_id or reference.table_id != table.table_id:
                            raise DependencyInputIntegrityError("staged row has no matching snapshot-bound record reference")
                        yield StagedDependencyRow(reference.record_ref, ordinal, dict(values))
                        ordinal += 1
                        yielded += 1
            except DependencyInputIntegrityError:
                raise
            except Exception as error:
                raise DependencyInputIntegrityError(f"staged dependency batch could not be read: {error.__class__.__name__}") from None
            if yielded != batch.row_count:
                raise DependencyInputIntegrityError("staged row count differs from COMPLETE batch reference")

    @staticmethod
    def _verify_batch(batch: BatchReference, snapshot_result: SourceSnapshotResult, catalog: SourceCatalog, table: TableDescriptor, root: Path) -> None:
        if batch.publication_state is not PublicationState.COMPLETE:
            raise DependencyInputIntegrityError("dependency analysis accepts COMPLETE batches only")
        if batch.source_id != snapshot_result.snapshot.source_id or batch.snapshot_id != snapshot_result.snapshot.snapshot_id:
            raise DependencyInputIntegrityError("batch is bound to another source snapshot")
        if batch.table_id != table.table_id or batch.schema_fingerprint != catalog.source.schema_fingerprint:
            raise DependencyInputIntegrityError("staged dependency batch schema or table binding mismatch")
        path = (root / batch.artifact_location).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise DependencyInputIntegrityError("staged dependency path escapes the project root") from None
        if not path.is_file() or _sha256(path) != batch.content_hash:
            raise DependencyInputIntegrityError("staged dependency batch content hash mismatch")
