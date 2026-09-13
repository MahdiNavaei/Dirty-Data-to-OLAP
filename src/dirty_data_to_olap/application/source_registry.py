"""Source registry implementations.

The original in-memory registry remains useful for isolated Step07 tests.  The
local product path uses ``DurableSourceRegistry`` so a browser-created source
does not disappear when the API process is restarted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

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


class DurableSourceRegistry:
    """Small project-owned JSON registry for managed local source records.

    The registry contains only typed source metadata.  Import bytes stay in
    the managed import directory and are never returned by this class.
    Atomic replace plus a process lock is sufficient for the local reference
    runtime; a brokered or multi-node registry is future platform scope.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read(self) -> dict[str, SourceRegistryRecord]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("durable source registry could not be read") from exc
        if not isinstance(payload, list):
            raise ValueError("durable source registry must contain a list")
        records = [SourceRegistryRecord.model_validate(item) for item in payload]
        return {record.registry_id: record for record in records}

    def _write(self, records: dict[str, SourceRegistryRecord]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".partial")
        payload = [records[key].model_dump(mode="json") for key in sorted(records)]
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def register(self, record: SourceRegistryRecord) -> SourceRegistryRecord:
        with self._lock:
            records = self._read()
            existing = records.get(record.registry_id)
            if existing is not None and existing != record:
                raise ValueError(f"source registry id already registered: {record.registry_id}")
            records[record.registry_id] = record
            self._write(records)
            return record

    def get(self, registry_id: str) -> SourceRegistryRecord:
        with self._lock:
            try:
                return self._read()[registry_id]
            except KeyError:
                raise KeyError(f"source registry record not found: {registry_id}") from None

    def list(self) -> tuple[SourceRegistryRecord, ...]:
        with self._lock:
            records = self._read()
            return tuple(records[key] for key in sorted(records))
