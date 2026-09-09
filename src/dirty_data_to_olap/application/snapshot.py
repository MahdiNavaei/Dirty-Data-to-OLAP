"""Source snapshot service; orchestration remains narrow and local to Step07."""

from __future__ import annotations

from pathlib import Path

from .source_adapter import SourceAdapter
from .source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSelection, SourceSnapshotResult


class SourceSnapshotService:
    def __init__(self, registry: InMemorySourceRegistry, adapters: dict[str, SourceAdapter]) -> None:
        self._registry = registry
        self._adapters = dict(adapters)

    def extract(
        self,
        catalog: SourceCatalog,
        selection: SourceSelection,
        *,
        staging_root: Path,
    ) -> SourceSnapshotResult:
        record = self._registry.get(selection.registry_id)
        adapter = self._adapters[record.adapter_name]
        return adapter.create_bounded_snapshot(
            catalog,
            selection,
            execution_context_id=selection.execution_context_id,
            staging_root=staging_root,
        )

