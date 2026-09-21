"""Project-owned contracts for source selection, discovery and staging.

The models in this module are deliberately independent from dlt, SQLAlchemy,
PyArrow and openpyxl.  Concrete adapters normalize vendor objects into these
contracts before returning from the source boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .database import ConnectionProfileReference, DatabaseEngine


SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|raw[_-]?dsn|connection[_-]?string)", re.I)
_SECRET_VALUE = re.compile(r"(?P<key>password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", re.I)


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    schema_version: str = SCHEMA_VERSION


class SourceType(str, Enum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    SQLSERVER = "sqlserver"
    CSV = "csv"
    PARQUET = "parquet"
    XLSX = "xlsx"


class SourceTableKind(str, Enum):
    TABLE = "table"
    VIEW = "view"
    FILE = "file"
    WORKSHEET = "worksheet"


class RowCountSemantics(str, Enum):
    EXACT = "exact"
    ESTIMATE = "estimate"
    OBSERVED = "observed"
    UNKNOWN = "unknown"


class SnapshotConsistency(str, Enum):
    TRANSACTION_SCOPED = "TRANSACTION_SCOPED"
    FILE_IMMUTABLE = "FILE_IMMUTABLE"
    BEST_EFFORT = "BEST_EFFORT"


class ObservationMode(str, Enum):
    FULL = "full"
    BOUNDED = "bounded"


class MaxRowsScope(str, Enum):
    SOURCE_WIDE = "SOURCE_WIDE"
    PER_TABLE = "PER_TABLE"


class TableObservationStatus(str, Enum):
    NOT_OBSERVED = "NOT_OBSERVED"
    PARTIALLY_OBSERVED = "PARTIALLY_OBSERVED"
    FULLY_OBSERVED = "FULLY_OBSERVED"


class RecordLocatorKind(str, Enum):
    PRIMARY_KEY = "PRIMARY_KEY"
    SNAPSHOT_ORDINAL = "SNAPSHOT_ORDINAL"


class StabilityScope(str, Enum):
    SOURCE_KEY_SCOPE = "SOURCE_KEY_SCOPE"
    SNAPSHOT_ONLY = "SNAPSHOT_ONLY"


class PublicationState(str, Enum):
    WRITING = "WRITING"
    COMPLETE = "COMPLETE"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class SourceFailureKind(str, Enum):
    INVALID_SELECTION = "INVALID_SELECTION"
    UNSUPPORTED = "UNSUPPORTED"
    ACCESS_FAILED = "ACCESS_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    STAGING_FAILED = "STAGING_FAILED"
    SNAPSHOT_INVALID = "SNAPSHOT_INVALID"
    TIMEOUT = "TIMEOUT"


class AdapterReference(_SourceModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    config_fingerprint: str = Field(min_length=1)


class SelectionScope(_SourceModel):
    included_objects: tuple[str, ...] = ()
    excluded_objects: tuple[str, ...] = ()
    include_views: bool = False
    included_columns: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)

    @field_validator("included_objects", "excluded_objects")
    @classmethod
    def validate_objects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("source object names must be non-empty and NUL-free")
        if len(set(value)) != len(value):
            raise ValueError("source object selections must be unique")
        return value

    @field_validator("included_columns")
    @classmethod
    def validate_columns(cls, value: Mapping[str, tuple[str, ...]]) -> Mapping[str, tuple[str, ...]]:
        normalized: dict[str, tuple[str, ...]] = {}
        for object_name, columns in value.items():
            if not object_name or any(not column or "\x00" in column for column in columns):
                raise ValueError("source column selections must be valid identifiers")
            if len(set(columns)) != len(columns):
                raise ValueError("source column selections must be unique")
            normalized[object_name] = tuple(columns)
        return normalized


class ExtractionPolicy(_SourceModel):
    chunk_size: int = Field(gt=0, le=1_000_000)
    max_rows: int | None = Field(default=None, gt=0, le=100_000_000)
    max_rows_scope: MaxRowsScope = MaxRowsScope.SOURCE_WIDE
    null_markers: tuple[str, ...] = ()
    preserve_raw_values: bool = True

    @model_validator(mode="after")
    def require_raw_values(self) -> "ExtractionPolicy":
        if not self.preserve_raw_values:
            raise ValueError("Step07 staging must preserve raw source values")
        return self


class SourceSelection(_SourceModel):
    registry_id: str = Field(min_length=1)
    scope: SelectionScope = Field(default_factory=SelectionScope)
    extraction: ExtractionPolicy = Field(default_factory=lambda: ExtractionPolicy(chunk_size=1000))
    execution_context_id: str = Field(default="step07-local", min_length=1)

    @field_validator("registry_id", "execution_context_id")
    @classmethod
    def validate_context(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("source selection context must be NUL-free")
        return value


class SourceSetSelection(_SourceModel):
    """One immutable, run-scoped selection of two or more source inputs.

    The existing ``SourceSelection`` remains the Step29 single-source
    contract.  This additive contract binds the complete source set before
    discovery so later stages cannot silently add, remove, or reorder sources.
    """

    selections: tuple[SourceSelection, ...] = Field(min_length=2)
    source_set_fingerprint: str = Field(min_length=1)
    finalized: bool = True

    @model_validator(mode="after")
    def validate_source_set(self) -> "SourceSetSelection":
        registry_ids = tuple(item.registry_id for item in self.selections)
        if len(set(registry_ids)) != len(registry_ids):
            raise ValueError("source-set selections must have unique registry IDs")
        contexts = {item.execution_context_id for item in self.selections}
        if len(contexts) != 1:
            raise ValueError("all source selections must share one execution context")
        canonical = tuple(sorted(self.selections, key=lambda item: item.registry_id))
        expected = stable_digest({"selections": [item.model_dump(mode="json") for item in canonical]})
        if self.source_set_fingerprint != expected:
            raise ValueError("source-set fingerprint does not match its immutable selections")
        if not self.finalized:
            raise ValueError("source sets must be finalized before execution")
        return self

    @property
    def ordered_selections(self) -> tuple[SourceSelection, ...]:
        return tuple(sorted(self.selections, key=lambda item: item.registry_id))


def source_set_fingerprint(selections: tuple[SourceSelection, ...] | list[SourceSelection]) -> str:
    """Return the stable fingerprint used by the source-set binding."""

    ordered = tuple(sorted(selections, key=lambda item: item.registry_id))
    return stable_digest({"selections": [item.model_dump(mode="json") for item in ordered]})


class SourceRegistryRecord(_SourceModel):
    registry_id: str = Field(min_length=1)
    source_id: str | None = None
    display_name: str = Field(min_length=1)
    source_type: SourceType
    connection_profile: ConnectionProfileReference | None = None
    file_locator: str | None = None
    scope: SelectionScope = Field(default_factory=SelectionScope)
    adapter_name: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    adapter_config: Mapping[str, str] = Field(default_factory=dict)
    read_only: bool = True
    owner_subject: str | None = None

    @field_validator("registry_id", "source_id", "display_name", "adapter_name", "adapter_version")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("registry values must be NUL-free")
        return value

    @field_validator("owner_subject")
    @classmethod
    def validate_owner_subject(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise ValueError("owner subject is invalid")
        return value

    @model_validator(mode="after")
    def validate_locator(self) -> "SourceRegistryRecord":
        if not self.read_only:
            raise ValueError("source registry records are read-only")
        sql_types = {
            SourceType.SQLITE,
            SourceType.POSTGRESQL,
            SourceType.MYSQL,
            SourceType.MARIADB,
            SourceType.SQLSERVER,
        }
        if self.source_type in sql_types and self.connection_profile is None:
            raise ValueError("SQL sources require a safe connection profile reference")
        if self.source_type in {SourceType.CSV, SourceType.PARQUET, SourceType.XLSX} and not self.file_locator:
            raise ValueError("file sources require a file locator")
        if self.connection_profile is not None and self.file_locator is not None:
            raise ValueError("a source must use either a connection profile or a file locator")
        for key, value in self.adapter_config.items():
            if _SECRET_KEY.search(str(key)) or _SECRET_VALUE.search(str(value)):
                raise ValueError("adapter_config must not contain secret material")
        return self


class ColumnDescriptor(_SourceModel):
    column_id: str
    table_id: str
    physical_name: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    native_physical_type: str = ""
    normalized_physical_type: str = "unknown"
    schema_nullable: bool | None = None
    raw_declared_notnull: bool | None = None
    declared_default: str | None = None
    primary_key_position: int | None = Field(default=None, ge=1)


class DeclaredConstraint(_SourceModel):
    constraint_type: str = Field(min_length=1)
    source_id: str
    table_id: str
    columns: tuple[str, ...] = Field(min_length=1)
    referenced_table_id: str | None = None
    referenced_table_name: str | None = None
    referenced_columns: tuple[str, ...] = ()
    declared: bool = True
    provenance: AdapterReference

    @model_validator(mode="after")
    def validate_constraint(self) -> "DeclaredConstraint":
        if not self.declared:
            raise ValueError("Step07 only publishes declared physical constraints")
        if self.constraint_type == "FOREIGN_KEY":
            if not self.referenced_table_name or len(self.columns) != len(self.referenced_columns):
                raise ValueError("declared foreign keys require matching referenced columns")
        return self


class TableDescriptor(_SourceModel):
    table_id: str
    source_id: str
    namespace: str | None = None
    physical_name: str = Field(min_length=1)
    table_kind: SourceTableKind
    row_count: int | None = Field(default=None, ge=0)
    row_count_semantics: RowCountSemantics = RowCountSemantics.UNKNOWN
    fingerprint: str | None = None


class SourceDescriptor(_SourceModel):
    source_id: str
    display_name: str = Field(min_length=1)
    source_type: SourceType
    connection_profile: ConnectionProfileReference | None = None
    file_locator: str | None = None
    selection_scope: SelectionScope
    source_fingerprint: str | None = None
    schema_fingerprint: str
    adapter_reference: AdapterReference

    @model_validator(mode="after")
    def validate_locator(self) -> "SourceDescriptor":
        if (self.connection_profile is None) == (self.file_locator is None):
            raise ValueError("source descriptors require exactly one safe locator")
        return self


class SourceCatalog(_SourceModel):
    source: SourceDescriptor
    tables: tuple[TableDescriptor, ...]
    columns: tuple[ColumnDescriptor, ...]
    declared_constraints: tuple[DeclaredConstraint, ...]

    @property
    def source_id(self) -> str:
        return self.source.source_id


class ObservationScope(_SourceModel):
    mode: ObservationMode
    chunk_size: int = Field(gt=0)
    max_rows: int | None = Field(default=None, ge=1)
    max_rows_scope: MaxRowsScope = MaxRowsScope.SOURCE_WIDE
    input_records_observed: int = Field(ge=0)


class TableSnapshotObservation(_SourceModel):
    table_id: str
    rows_observed: int = Field(ge=0)
    status: TableObservationStatus


class SourceSnapshot(_SourceModel):
    source_id: str
    snapshot_id: str
    execution_context_id: str
    schema_fingerprint: str
    source_fingerprint: str | None = None
    observed_at: datetime
    selection_scope: SelectionScope
    observation_scope: ObservationScope
    extraction_policy: ExtractionPolicy
    consistency: SnapshotConsistency
    adapter_reference: AdapterReference


class BatchReference(_SourceModel):
    batch_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    batch_index: int = Field(ge=0)
    row_count: int = Field(ge=0)
    artifact_location: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    schema_fingerprint: str
    first_extraction_ordinal: int | None = Field(default=None, ge=0)
    last_extraction_ordinal: int | None = Field(default=None, ge=0)
    adapter_reference: AdapterReference
    publication_state: PublicationState

    @field_validator("artifact_location")
    @classmethod
    def validate_artifact_location(cls, value: str) -> str:
        if "\x00" in value or _SECRET_KEY.search(value):
            raise ValueError("artifact locations must be safe project-relative references")
        return value

    @model_validator(mode="after")
    def validate_ordinals(self) -> "BatchReference":
        if self.row_count == 0 and self.first_extraction_ordinal is not None:
            raise ValueError("empty batches cannot claim extraction ordinals")
        if self.row_count and self.first_extraction_ordinal is None:
            raise ValueError("non-empty batches require extraction ordinals")
        if self.last_extraction_ordinal is not None and self.first_extraction_ordinal is not None:
            if self.last_extraction_ordinal < self.first_extraction_ordinal:
                raise ValueError("batch extraction ordinals must be ordered")
        if self.publication_state is not PublicationState.COMPLETE:
            raise ValueError("returned batch references must be COMPLETE")
        return self


class SourceRecordReference(_SourceModel):
    record_ref: str
    source_id: str
    snapshot_id: str
    table_id: str
    batch_id: str
    extraction_ordinal: int = Field(ge=0)
    locator_kind: RecordLocatorKind
    key_columns: tuple[str, ...] = ()
    key_values: tuple[Any, ...] = ()
    stability_scope: StabilityScope

    @model_validator(mode="after")
    def validate_locator(self) -> "SourceRecordReference":
        if self.locator_kind is RecordLocatorKind.PRIMARY_KEY:
            if not self.key_columns or len(self.key_columns) != len(self.key_values):
                raise ValueError("primary-key references require ordered key columns and values")
            if self.stability_scope is not StabilityScope.SOURCE_KEY_SCOPE:
                raise ValueError("primary-key references require source-key stability scope")
        elif self.stability_scope is not StabilityScope.SNAPSHOT_ONLY:
            raise ValueError("ordinal references are snapshot-bound")
        return self


class RowAccounting(_SourceModel):
    input_records_observed: int = Field(ge=0)
    successfully_staged_records: int = Field(ge=0)
    explicitly_quarantined_records: int = Field(ge=0)
    unresolved_records: int = Field(ge=0)
    accounting_complete: bool

    @model_validator(mode="after")
    def validate_counts(self) -> "RowAccounting":
        accounted = self.successfully_staged_records + self.explicitly_quarantined_records + self.unresolved_records
        if self.accounting_complete and accounted != self.input_records_observed:
            raise ValueError("completed row accounting must reconcile every observed record")
        return self


class ExtractionMetrics(_SourceModel):
    input_records_observed: int = Field(ge=0)
    staged_records: int = Field(ge=0)
    batch_count: int = Field(ge=0)
    configured_chunk_size: int = Field(gt=0)
    bytes_staged: int = Field(ge=0)


class SourceSnapshotResult(_SourceModel):
    snapshot: SourceSnapshot
    batches: tuple[BatchReference, ...]
    record_references: tuple[SourceRecordReference, ...]
    accounting: RowAccounting
    metrics: ExtractionMetrics
    table_observations: tuple[TableSnapshotObservation, ...] = ()


class SourceFailure(_SourceModel):
    kind: SourceFailureKind
    operation: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    retryable: bool
    context_id: str | None = None
    records_observed_before_failure: int = Field(default=0, ge=0)

    @field_validator("detail")
    @classmethod
    def redact_detail(cls, value: str) -> str:
        value = _SECRET_VALUE.sub(r"\g<key>=<redacted>", value)
        value = re.sub(r"(?i)(?:[a-z][a-z0-9+.-]*)://[^\s]+", "<redacted-connection>", value)
        return value

    @field_validator("operation", "context_id")
    @classmethod
    def reject_secret_context(cls, value: str | None) -> str | None:
        if value is not None and re.search(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]", value):
            raise ValueError("failure context must not contain secret material")
        return value


class SourceIngestionError(RuntimeError):
    def __init__(self, failure: SourceFailure):
        self.failure = failure
        super().__init__(f"{failure.kind.value} during {failure.operation}: {failure.detail}")


def _canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{stable_digest(value)[:32]}"


def normalized_file_locator(locator: str | Path) -> str:
    return str(Path(locator).expanduser().resolve()).replace("\\", "/")


def file_content_fingerprint(locator: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(locator).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def source_id_for(source_type: SourceType, locator: str) -> str:
    return stable_id("src", {"source_type": source_type.value, "locator": locator})


def table_id_for(source_id: str, namespace: str | None, physical_name: str, kind: SourceTableKind) -> str:
    return stable_id("tbl", {"source_id": source_id, "namespace": namespace, "physical_name": physical_name, "kind": kind.value})


def column_id_for(table_id: str, physical_name: str, ordinal: int) -> str:
    return stable_id("col", {"table_id": table_id, "physical_name": physical_name, "ordinal": ordinal})


def schema_fingerprint(catalog_parts: Mapping[str, Any]) -> str:
    return f"schema_{stable_digest(catalog_parts)}"


def snapshot_id_for(source_id: str, schema_fp: str, selection: SelectionScope, extraction: ExtractionPolicy, execution_context_id: str) -> str:
    return stable_id("snap", {
        "source_id": source_id,
        "schema_fingerprint": schema_fp,
        "selection": selection,
        "extraction": extraction,
        "execution_context_id": execution_context_id,
    })


def batch_id_for(snapshot_id: str, table_id: str, batch_index: int, content_hash: str) -> str:
    return stable_id("batch", {"snapshot_id": snapshot_id, "table_id": table_id, "batch_index": batch_index, "content_hash": content_hash})


def record_ref_for_pk(source_id: str, snapshot_id: str, table_id: str, key_columns: Sequence[str], key_values: Sequence[Any]) -> str:
    return stable_id("rec", {"source_id": source_id, "snapshot_id": snapshot_id, "table_id": table_id, "key_columns": list(key_columns), "key_values": list(key_values)})


def record_ref_for_ordinal(source_id: str, snapshot_id: str, table_id: str, extraction_ordinal: int) -> str:
    return stable_id("rec", {"source_id": source_id, "snapshot_id": snapshot_id, "table_id": table_id, "extraction_ordinal": extraction_ordinal})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
