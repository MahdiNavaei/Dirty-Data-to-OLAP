"""Project-owned source adapter port and registry-facing result types."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from dirty_data_to_olap.domain.contracts.source import (
    SourceCatalog,
    SourceRegistryRecord,
    SourceSelection,
    SourceSnapshotResult,
)


@runtime_checkable
class SourceAdapter(Protocol):
    """Read-only source boundary; vendor-native objects must not escape it."""

    name: str
    version: str

    def discover_source(
        self, selection: SourceSelection, registry_record: SourceRegistryRecord
    ) -> SourceCatalog:
        ...

    def create_bounded_snapshot(
        self,
        catalog: SourceCatalog,
        selection: SourceSelection,
        *,
        execution_context_id: str,
        staging_root: Path,
    ) -> SourceSnapshotResult:
        ...

