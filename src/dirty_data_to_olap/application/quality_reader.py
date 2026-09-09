"""Port for reading the immutable Step07 staged snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol, Sequence

from dirty_data_to_olap.domain.contracts.quality import QualityStagedRow
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, TableDescriptor


class QualityInputIntegrityError(ValueError):
    """A staged batch cannot be trusted for quality measurement."""


class QualityStagedReader(Protocol):
    def iter_table(
        self,
        snapshot_result: SourceSnapshotResult,
        catalog: SourceCatalog,
        table: TableDescriptor,
        physical_columns: Sequence[str],
        *,
        project_root: Path,
    ) -> Iterator[QualityStagedRow]:
        """Stream staged rows with source record references; never return all rows."""
