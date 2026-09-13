"""Managed CSV import boundary for the local Step29 product path."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import re

from dirty_data_to_olap.application.source_registry import DurableSourceRegistry
from dirty_data_to_olap.domain.contracts.source import (
    SelectionScope,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
    source_id_for,
    normalized_file_locator,
)


class ProductSourceError(ValueError):
    """A user-facing source import or selection error."""


class ProductSourceService:
    """Store bounded CSV imports below the project-owned product workspace."""

    MAX_UPLOAD_BYTES = 5 * 1024 * 1024

    def __init__(self, project_root: Path, registry: DurableSourceRegistry) -> None:
        self.project_root = Path(project_root).resolve()
        self.registry = registry
        self.import_root = self.project_root / "workspace" / "platform" / "product" / "imports"
        self.import_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _filename(value: str) -> str:
        name = Path(value.strip()).name
        if not name or name != value.strip() or "/" in value or "\\" in value or ".." in name:
            raise ProductSourceError("filename must be a simple CSV name without traversal")
        if Path(name).suffix.casefold() != ".csv":
            raise ProductSourceError("only CSV imports are supported by the local V1 product path")
        if len(name) > 160 or "\x00" in name:
            raise ProductSourceError("filename is unsupported")
        return name

    @staticmethod
    def _display_name(filename: str) -> str:
        stem = Path(filename).stem
        value = re.sub(r"[^A-Za-z0-9 _.-]+", "", stem).strip(" .")
        return (value or "Imported CSV")[:120]

    @staticmethod
    def _validate_csv(payload: bytes) -> tuple[str, ...]:
        if not payload:
            raise ProductSourceError("CSV import is empty")
        try:
            text = payload.decode("utf-8-sig")
            rows = csv.reader(text.splitlines(), strict=True)
            header = tuple(next(rows, ()))
            if not header or any(not item.strip() for item in header) or len(set(header)) != len(header):
                raise ProductSourceError("CSV header must contain unique non-empty column names")
            if not any(rows):
                raise ProductSourceError("CSV import must contain at least one data row")
            required = {"order_id", "customer_id", "customer_id_ref", "order_date", "quantity", "unit_price"}
            if not required.issubset({item.strip() for item in header}):
                raise ProductSourceError("V1 CSV imports require order_id, customer_id, customer_id_ref, order_date, quantity and unit_price columns")
            return header
        except ProductSourceError:
            raise
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ProductSourceError("CSV content could not be parsed as UTF-8 CSV") from exc

    def import_csv(self, *, registry_id: str, filename: str, payload: bytes) -> SourceRegistryRecord:
        if len(payload) > self.MAX_UPLOAD_BYTES:
            raise ProductSourceError("CSV import exceeds the bounded 5 MB upload limit")
        clean_name = self._filename(filename)
        self._validate_csv(payload)
        destination = (self.import_root / f"{registry_id}.csv").resolve()
        try:
            destination.relative_to(self.import_root.resolve())
        except ValueError as exc:
            raise ProductSourceError("managed import path escaped the project-owned import area") from exc
        temporary = destination.with_suffix(".partial")
        try:
            temporary.write_bytes(payload)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ProductSourceError("managed CSV import could not be persisted") from exc
        locator = normalized_file_locator(destination)
        record = SourceRegistryRecord(
            registry_id=registry_id,
            source_id=source_id_for(SourceType.CSV, locator),
            display_name=self._display_name(clean_name),
            source_type=SourceType.CSV,
            file_locator=locator,
            scope=SelectionScope(),
            adapter_name="file_source",
            adapter_version="1.0.0",
            adapter_config={"managed_import": "true", "original_filename": clean_name},
            read_only=True,
        )
        try:
            return self.registry.register(record)
        except ValueError:
            destination.unlink(missing_ok=True)
            raise

    def get(self, registry_id: str) -> SourceRegistryRecord:
        try:
            return self.registry.get(registry_id)
        except KeyError as exc:
            raise ProductSourceError("source was not found") from exc

    def list(self) -> tuple[SourceRegistryRecord, ...]:
        return self.registry.list()

    def selection(self, *, registry_id: str, scope: SelectionScope, extraction, execution_context_id: str) -> SourceSelection:
        record = self.get(registry_id)
        if scope.excluded_objects and set(scope.excluded_objects).intersection(record.scope.included_objects):
            raise ProductSourceError("selection excludes an object required by the registered source scope")
        return SourceSelection(
            registry_id=record.registry_id,
            scope=scope,
            extraction=extraction,
            execution_context_id=execution_context_id,
        )


__all__ = ["ProductSourceError", "ProductSourceService"]
