"""Minimal in-memory source registry for Step07 services and tests."""

from __future__ import annotations

from dirty_data_to_olap.domain.contracts.source import SourceRegistryRecord


class InMemorySourceRegistry:
    def __init__(self) -> None:
        self._records: dict[str, SourceRegistryRecord] = {}

    def register(self, record: SourceRegistryRecord) -> SourceRegistryRecord:
        existing = self._records.get(record.registry_id)
        if existing is not None and existing != record:
            raise ValueError(f"source registry id already registered: {record.registry_id}")
        self._records[record.registry_id] = record
        return record

    def get(self, registry_id: str) -> SourceRegistryRecord:
        try:
            return self._records[registry_id]
        except KeyError:
            raise KeyError(f"source registry record not found: {registry_id}") from None

    def list(self) -> tuple[SourceRegistryRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

