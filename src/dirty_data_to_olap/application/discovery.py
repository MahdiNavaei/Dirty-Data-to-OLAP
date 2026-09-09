"""Source discovery service with no dependency on snapshots or vendor engines."""

from __future__ import annotations

from .source_adapter import SourceAdapter
from .source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSelection


class SourceDiscoveryService:
    def __init__(self, registry: InMemorySourceRegistry, adapters: dict[str, SourceAdapter]) -> None:
        self._registry = registry
        self._adapters = dict(adapters)

    def discover(self, selection: SourceSelection) -> SourceCatalog:
        record = self._registry.get(selection.registry_id)
        try:
            adapter = self._adapters[record.adapter_name]
        except KeyError:
            raise ValueError(f"source adapter not registered: {record.adapter_name}") from None
        return adapter.discover_source(selection, record)

