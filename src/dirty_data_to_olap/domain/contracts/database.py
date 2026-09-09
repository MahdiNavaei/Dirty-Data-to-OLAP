"""Project-owned, driver-neutral database contracts for Specialist Step06.

This module deliberately contains no database-driver imports.  Concrete source
implementations normalize into these contracts at the adapter boundary.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.0"
_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|raw[_-]?dsn|connection[_-]?string)", re.I)
_SECRET_VALUE = re.compile(r"(?P<key>password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", re.I)


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: str = SCHEMA_VERSION


class DatabaseEngine(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    SQLSERVER = "sqlserver"
    SQLITE = "sqlite"
    ORACLE = "oracle"


class CapabilityStatus(str, Enum):
    PLANNED_REQUIRED = "PLANNED_REQUIRED"
    CONTRACT_DEFINED = "CONTRACT_DEFINED"
    REFERENCE_TESTED = "REFERENCE_TESTED"
    LIVE_VERIFIED = "LIVE_VERIFIED"
    DEFERRED = "DEFERRED"


class DatabaseFailureKind(str, Enum):
    CONNECTION_FAILED = "CONNECTION_FAILED"
    ACCESS_DENIED = "ACCESS_DENIED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    TIMEOUT = "TIMEOUT"
    READ_ONLY_VIOLATION = "READ_ONLY_VIOLATION"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    QUERY_FAILED = "QUERY_FAILED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"


class SamplingMethod(str, Enum):
    HEAD = "head"


class PoolOverflowPolicy(str, Enum):
    WAIT = "wait"
    REJECT = "reject"


class ConstraintType(str, Enum):
    PRIMARY_KEY = "PRIMARY_KEY"
    FOREIGN_KEY = "FOREIGN_KEY"
    UNIQUE = "UNIQUE"
    NOT_NULL = "NOT_NULL"


class TableKind(str, Enum):
    TABLE = "table"
    VIEW = "view"


def _reject_secret_material(value: Any, field_name: str) -> Any:
    if value is None:
        return value
    text = str(value)
    if _SECRET_VALUE.search(text):
        raise ValueError(f"{field_name} must not contain secret material")
    return value


class ConnectionProfileReference(_ContractModel):
    """A non-secret pointer to externally managed connection credentials."""

    profile_id: str = Field(min_length=1)
    database_engine: DatabaseEngine
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str = Field(min_length=1)
    username_reference: str | None = None
    credential_reference: str | None = None
    driver_options: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("profile_id", "host", "database_name", "username_reference", "credential_reference")
    @classmethod
    def reject_secret_bearing_values(cls, value: Any, info: Any) -> Any:
        return _reject_secret_material(value, info.field_name)

    @field_validator("driver_options")
    @classmethod
    def reject_secret_options(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        for key, option_value in value.items():
            if _SECRET_KEY.search(str(key)) or _SECRET_VALUE.search(str(option_value)):
                raise ValueError("driver_options must not contain secret material")
        return dict(value)

    @model_validator(mode="after")
    def reject_secret_like_profile(self) -> "ConnectionProfileReference":
        if _SECRET_VALUE.search(self.database_name) or "://" in self.database_name:
            raise ValueError("database_name must be a non-secret database name, not a DSN or URI")
        return self


class DatabaseIdentifier(_ContractModel):
    """A structured database object name; schema and object are never SQL text."""

    name: str = Field(min_length=1)
    schema_name: str | None = None

    @field_validator("name", "schema_name")
    @classmethod
    def reject_malformed_identifier(cls, value: str | None) -> str | None:
        if value is not None and (not value or "\x00" in value):
            raise ValueError("database identifiers cannot be empty or contain NUL")
        return value


class SamplingPolicy(_ContractModel):
    method: SamplingMethod = SamplingMethod.HEAD
    max_rows: int = Field(gt=0, le=100_000)
    seed: int | None = None


class BoundedSampleRequest(_ContractModel):
    table: DatabaseIdentifier
    columns: tuple[str, ...] = Field(min_length=1)
    sampling_policy: SamplingPolicy

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not column or column == "*" or "\x00" in column for column in value):
            raise ValueError("bounded sampling requires explicit, valid columns")
        if len(set(value)) != len(value):
            raise ValueError("bounded sampling columns must be unique")
        return value


class TimeoutPolicy(_ContractModel):
    connection_timeout_seconds: float = Field(gt=0, le=300)
    busy_timeout_seconds: float = Field(gt=0, le=300)
    statement_timeout_seconds: float = Field(gt=0, le=300)


class DatabaseAccessPolicy(_ContractModel):
    read_only: bool = True
    transaction_intent: str = "read_only"
    isolation_intent: str = "deferred_read"
    allow_dml: bool = False
    allow_ddl: bool = False

    @model_validator(mode="after")
    def enforce_protection(self) -> "DatabaseAccessPolicy":
        if not self.read_only or self.allow_dml or self.allow_ddl:
            raise ValueError("Step06 database access policy is read-only and forbids DDL/DML")
        return self


class PoolPolicy(_ContractModel):
    max_pool_size: int = Field(ge=1, le=100)
    acquire_timeout_seconds: float = Field(gt=0, le=300)
    idle_timeout_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_lifetime_seconds: float | None = Field(default=None, gt=0, le=86_400)
    overflow_policy: PoolOverflowPolicy = PoolOverflowPolicy.REJECT


class DatabaseCapabilities(_ContractModel):
    database_engine: DatabaseEngine
    status: CapabilityStatus
    read_only_sessions: bool
    metadata_introspection: bool
    bounded_sampling: bool
    explain_bounded_reads: bool
    declared_constraints: tuple[str, ...] = ()
    live_verified: bool = False
    limitation: str | None = None

    @model_validator(mode="after")
    def enforce_status_claim(self) -> "DatabaseCapabilities":
        if self.live_verified and self.status is not CapabilityStatus.LIVE_VERIFIED:
            raise ValueError("live_verified requires LIVE_VERIFIED status")
        if self.status is CapabilityStatus.LIVE_VERIFIED and not self.live_verified:
            raise ValueError("LIVE_VERIFIED requires live_verified=true")
        return self


class DatabaseColumnMetadata(_ContractModel):
    name: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    declared_type: str
    normalized_type: str
    nullable: bool
    default_sql: str | None = None
    primary_key_position: int | None = Field(default=None, ge=1)


class DatabaseDeclaredConstraint(_ContractModel):
    constraint_type: ConstraintType
    columns: tuple[str, ...] = Field(min_length=1)
    name: str | None = None
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()
    declared: bool = True

    @model_validator(mode="after")
    def validate_foreign_key_shape(self) -> "DatabaseDeclaredConstraint":
        if self.constraint_type is ConstraintType.FOREIGN_KEY:
            if not self.referenced_table or len(self.columns) != len(self.referenced_columns):
                raise ValueError("declared foreign keys require matching referenced columns")
        return self


class DatabaseTableMetadata(_ContractModel):
    schema_name: str | None
    name: str
    kind: TableKind
    columns: tuple[DatabaseColumnMetadata, ...]
    constraints: tuple[DatabaseDeclaredConstraint, ...]


class DatabaseMetadata(_ContractModel):
    profile_id: str
    database_engine: DatabaseEngine
    tables: tuple[DatabaseTableMetadata, ...]


class BoundedSampleObservation(_ContractModel):
    table: DatabaseIdentifier
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    rows_observed: int = Field(ge=0)
    observation_scope: str = "sample"
    sampling_method: SamplingMethod
    requested_max_rows: int = Field(gt=0)
    bounded: bool = True
    representative: bool = False

    @model_validator(mode="after")
    def validate_observation(self) -> "BoundedSampleObservation":
        if self.rows_observed != len(self.rows) or self.rows_observed > self.requested_max_rows:
            raise ValueError("sample observation violates its explicit bound")
        return self


class ExplainPlanStep(_ContractModel):
    select_id: int | None
    order: int | None
    from_id: int | None
    detail: str


class DatabaseFailure(_ContractModel):
    kind: DatabaseFailureKind
    database_engine: DatabaseEngine
    operation: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    retryable: bool
    cause_category: str
    context_id: str | None = None

    @field_validator("detail")
    @classmethod
    def redact_detail(cls, value: str) -> str:
        value = _SECRET_VALUE.sub(lambda match: f"{match.group('key')}=<redacted>", value)
        value = re.sub(r"(?i)(sqlite|postgresql|mysql|mariadb|sqlserver)://[^\s]+", "<redacted-connection>", value)
        return value

    @field_validator("operation", "cause_category", "context_id")
    @classmethod
    def reject_secret_failure_context(cls, value: str | None, info: Any) -> str | None:
        return _reject_secret_material(value, info.field_name)

    def __str__(self) -> str:
        context = f" context={self.context_id}" if self.context_id else ""
        return f"{self.kind.value} during {self.operation}: {self.detail}{context}"

    def __repr__(self) -> str:
        return f"DatabaseFailure(kind={self.kind.value!r}, operation={self.operation!r}, detail={self.detail!r})"


class DatabaseAccessError(RuntimeError):
    """Cross-module failure carrying only the normalized project contract."""

    def __init__(self, failure: DatabaseFailure):
        self.failure = failure
        super().__init__(str(failure))


def capabilities_for(engine: DatabaseEngine) -> DatabaseCapabilities:
    if engine is DatabaseEngine.SQLITE:
        return DatabaseCapabilities(
            database_engine=engine,
            status=CapabilityStatus.REFERENCE_TESTED,
            read_only_sessions=True,
            metadata_introspection=True,
            bounded_sampling=True,
            explain_bounded_reads=True,
            declared_constraints=("PRIMARY_KEY", "FOREIGN_KEY", "UNIQUE", "NOT_NULL"),
            limitation="SQLite reference behavior is tested locally; this is not a cross-engine claim.",
        )
    if engine is DatabaseEngine.ORACLE:
        return DatabaseCapabilities(
            database_engine=engine,
            status=CapabilityStatus.DEFERRED,
            read_only_sessions=False,
            metadata_introspection=False,
            bounded_sampling=False,
            explain_bounded_reads=False,
            limitation="Oracle is deferred by the V1 scope.",
        )
    return DatabaseCapabilities(
        database_engine=engine,
        status=CapabilityStatus.PLANNED_REQUIRED,
        read_only_sessions=True,
        metadata_introspection=True,
        bounded_sampling=True,
        explain_bounded_reads=False,
        declared_constraints=("PRIMARY_KEY", "FOREIGN_KEY", "UNIQUE", "NOT_NULL"),
        limitation="Contract policy only; no live provider was exercised in Step06.",
    )


def quote_identifier(value: str, engine: DatabaseEngine) -> str:
    if not value or "\x00" in value:
        raise ValueError("database identifiers cannot be empty or contain NUL")
    if engine in (DatabaseEngine.MYSQL, DatabaseEngine.MARIADB):
        return "`" + value.replace("`", "``") + "`"
    if engine is DatabaseEngine.SQLSERVER:
        return "[" + value.replace("]", "]]" ) + "]"
    return '"' + value.replace('"', '""') + '"'


def quote_qualified_identifier(identifier: DatabaseIdentifier, engine: DatabaseEngine) -> str:
    parts = []
    if identifier.schema_name:
        parts.append(quote_identifier(identifier.schema_name, engine))
    parts.append(quote_identifier(identifier.name, engine))
    return ".".join(parts)
