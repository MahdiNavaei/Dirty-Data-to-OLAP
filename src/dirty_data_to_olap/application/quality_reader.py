"""Port for reading the immutable Step07 staged snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from dirty_data_to_olap.domain.contracts.quality import QualityStagedRow
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, TableDescriptor


class QualityInputIntegrityError(ValueError):
    """A staged batch cannot be trusted for quality measurement."""


class QualityStagedReader(Protocol):
    def scan_table(
        self,
        snapshot_result: SourceSnapshotResult,
        catalog: SourceCatalog,
        table: TableDescriptor,
        physical_columns: Sequence[str],
        *,
        project_root: Path,
    ) -> Sequence[QualityStagedRow]:
        """Yield bounded staged rows with source record references."""
