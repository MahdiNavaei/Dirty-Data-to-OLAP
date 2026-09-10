"""SQLite reference implementation of the Step06 read-only database boundary."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from dirty_data_to_olap.domain.contracts.database import (
    BoundedSampleObservation,
    BoundedSampleRequest,
    CapabilityStatus,
    ConnectionProfileReference,
    ConstraintType,
    DatabaseAccessError,
    DatabaseAccessPolicy,
    DatabaseCapabilities,
    DatabaseColumnMetadata,
    DatabaseDeclaredConstraint,
    DatabaseEngine,
    DatabaseFailure,
    DatabaseFailureKind,
    DatabaseIdentifier,
    DatabaseMetadata,
    DatabaseTableMetadata,
    ExplainPlanStep,
    PoolPolicy,
    SamplingMethod,
    TableKind,
    TimeoutPolicy,
    capabilities_for,
    quote_identifier,
    quote_qualified_identifier,
)
from dirty_data_to_olap.application.database_security import classify_query
from dirty_data_to_olap.domain.contracts.database_security import QueryClass


def normalize_sqlite_failure(
    exception: BaseException,
    *,
    operation: str,
    context_id: str | None = None,
    deadline_expired: bool = False,
) -> DatabaseFailure:
    """Convert a driver exception into a safe project-owned failure."""

    detail = str(exception) or exception.__class__.__name__
    lowered = detail.lower()
    if deadline_expired or "interrupted" in lowered or "timed out" in lowered:
        kind = DatabaseFailureKind.TIMEOUT
        retryable = True
        cause = "statement_deadline"
    elif "readonly" in lowered or "read-only" in lowered:
        kind = DatabaseFailureKind.READ_ONLY_VIOLATION
        retryable = False
        cause = "protected_source_write_attempt"
    elif "not authorized" in lowered or "permission" in lowered:
        kind = DatabaseFailureKind.ACCESS_DENIED
        retryable = False
        cause = "authorization"
    elif "unable to open" in lowered or "no such file" in lowered or "cannot open" in lowered:
        kind = DatabaseFailureKind.CONNECTION_FAILED
        retryable = False
        cause = "connection"
    elif "locked" in lowered or "busy" in lowered:
        kind = DatabaseFailureKind.TIMEOUT
        retryable = True
        cause = "database_lock"
    else:
        kind = DatabaseFailureKind.QUERY_FAILED
        retryable = False
        cause = "driver_error"
    return DatabaseFailure(
        database_engine=DatabaseEngine.SQLITE,
        kind=kind,
        operation=operation,
        detail=detail,
        retryable=retryable,
        cause_category=cause,
        context_id=context_id,
    )


def _sqlite_identifier(identifier: DatabaseIdentifier) -> str:
    if identifier.schema_name not in (None, "main"):
        raise DatabaseAccessError(
            DatabaseFailure(
                database_engine=DatabaseEngine.SQLITE,
                kind=DatabaseFailureKind.UNSUPPORTED_CAPABILITY,
                operation="qualify_identifier",
                detail="Only the SQLite main schema is supported by the reference adapter.",
                retryable=False,
                cause_category="schema_policy",
            )
        )
    return quote_qualified_identifier(identifier, DatabaseEngine.SQLITE)


class SQLiteReadOnlySource:
    """Opens project-owned, read-only SQLite sessions.

    The source exposes only generated metadata, bounded sampling and explain
    operations.  It deliberately has no public arbitrary-SQL method.
    """

    def __init__(
        self,
        profile: ConnectionProfileReference,
        *,
        timeout_policy: TimeoutPolicy | None = None,
        access_policy: DatabaseAccessPolicy | None = None,
        pool_policy: PoolPolicy | None = None,
    ) -> None:
        if profile.database_engine is not DatabaseEngine.SQLITE:
            raise ValueError("SQLiteReadOnlySource requires a sqlite connection profile")
        self.profile = profile
        self.timeout_policy = timeout_policy or TimeoutPolicy(
            connection_timeout_seconds=5.0,
            busy_timeout_seconds=5.0,
            statement_timeout_seconds=10.0,
        )
        self.access_policy = access_policy or DatabaseAccessPolicy()
        self.pool_policy = pool_policy or PoolPolicy(max_pool_size=1, acquire_timeout_seconds=5.0)
        self.capabilities: DatabaseCapabilities = capabilities_for(DatabaseEngine.SQLITE)

    def _readonly_uri(self) -> str:
        database = self.profile.database_name
        if database == ":memory:" or database.startswith("file:"):
            raise ValueError("SQLite reference access requires a filesystem path, not an in-memory or raw URI")
        path = Path(database).expanduser().resolve()
        uri = path.as_uri()
        return f"{uri}&mode=ro" if "?" in uri else f"{uri}?mode=ro"

    @contextmanager
    def open_readonly_session(self) -> Iterator["SQLiteReadOnlySession"]:
        try:
            connection = sqlite3.connect(
                self._readonly_uri(),
                uri=True,
                timeout=self.timeout_policy.busy_timeout_seconds,
                check_same_thread=True,
            )
            connection.row_factory = sqlite3.Row
        except (sqlite3.Error, OSError, ValueError) as exception:
            failure = normalize_sqlite_failure(exception, operation="open_readonly_session", context_id=self.profile.profile_id)
            raise DatabaseAccessError(failure) from None
        session = SQLiteReadOnlySession(
            connection,
            profile_id=self.profile.profile_id,
            timeout_policy=self.timeout_policy,
            access_policy=self.access_policy,
        )
        try:
            session._configure()
            yield session
        finally:
            session.close()


class SQLiteReadOnlySession:
    """Adapter-internal session; raw sqlite objects never leave this class."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        profile_id: str,
        timeout_policy: TimeoutPolicy,
        access_policy: DatabaseAccessPolicy,
    ) -> None:
        self._connection = connection
        self._profile_id = profile_id
        self._timeout_policy = timeout_policy
        self._access_policy = access_policy
        self._closed = False
        self._deadline: float | None = None

    @property
    def read_only(self) -> bool:
        return self._access_policy.read_only

    @property
    def closed(self) -> bool:
        return self._closed

    def _configure(self) -> None:
        try:
            self._connection.isolation_level = None
            _install_sqlite_authorizer(self._connection)
            self._connection.execute("PRAGMA query_only = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {int(self._timeout_policy.busy_timeout_seconds * 1000)}")
            row = self._connection.execute("PRAGMA query_only").fetchone()
            if not row or int(row[0]) != 1:
                raise sqlite3.OperationalError("query_only was not enabled")
        except sqlite3.Error as exception:
            failure = normalize_sqlite_failure(exception, operation="configure_readonly_session", context_id=self._profile_id)
            raise DatabaseAccessError(failure) from None

    def _progress(self) -> int:
        return int(self._deadline is not None and time.monotonic() >= self._deadline)

    def _run(self, sql: str, parameters: Sequence[Any] = (), *, operation: str) -> list[sqlite3.Row]:
        if self._closed:
            raise RuntimeError("database session is closed")
        decision = classify_query(sql)
        if not decision.allowed:
            failure = DatabaseFailure(
                database_engine=DatabaseEngine.SQLITE,
                kind=DatabaseFailureKind.READ_ONLY_VIOLATION if decision.query_class in {QueryClass.WRITE_DML, QueryClass.DDL, QueryClass.FILESYSTEM_EXPORT, QueryClass.PROCEDURE_EXECUTION, QueryClass.MULTI_STATEMENT} else DatabaseFailureKind.QUERY_FAILED,
                operation=operation,
                detail="query guard blocked a non-read source operation",
                retryable=False,
                cause_category="query_guard",
                context_id=self._profile_id,
            )
            raise DatabaseAccessError(failure)
        self._deadline = time.monotonic() + self._timeout_policy.statement_timeout_seconds
        self._connection.set_progress_handler(self._progress, 1_000)
        try:
            cursor = self._connection.execute(sql, tuple(parameters))
            return cursor.fetchall()
        except sqlite3.Error as exception:
            failure = normalize_sqlite_failure(
                exception,
                operation=operation,
                context_id=self._profile_id,
                deadline_expired=self._deadline is not None and time.monotonic() >= self._deadline,
            )
            raise DatabaseAccessError(failure) from None
        finally:
            self._connection.set_progress_handler(None, 0)
            self._deadline = None

    @contextmanager
    def read_only_transaction(self) -> Iterator["SQLiteReadOnlySession"]:
        """Make the read-only transaction intent explicit and always roll it back."""

        self._run("BEGIN", operation="begin_read_only_transaction")
        try:
            yield self
        finally:
            if not self._closed:
                self._run("ROLLBACK", operation="rollback_read_only_transaction")

    def inspect_database_metadata(self) -> DatabaseMetadata:
        rows = self._run(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name",
            operation="inspect_database_metadata",
        )
        tables = tuple(
            self.inspect_table_metadata(DatabaseIdentifier(name=str(row["name"])))
            for row in rows
        )
        return DatabaseMetadata(
            profile_id=self._profile_id,
            database_engine=DatabaseEngine.SQLITE,
            tables=tables,
        )

    def inspect_table_metadata(self, table: DatabaseIdentifier) -> DatabaseTableMetadata:
        table_sql = _sqlite_identifier(table)
        table_rows = self._run(
            "SELECT type FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (table.name,),
            operation="inspect_table_metadata",
        )
        if not table_rows:
            failure = DatabaseFailure(
                database_engine=DatabaseEngine.SQLITE,
                kind=DatabaseFailureKind.QUERY_FAILED,
                operation="inspect_table_metadata",
                detail="requested table or view was not found",
                retryable=False,
                cause_category="missing_object",
                context_id=self._profile_id,
            )
            raise DatabaseAccessError(failure)
        kind = TableKind(str(table_rows[0]["type"]))
        column_rows = self._run(f"PRAGMA table_info({quote_identifier(table.name, DatabaseEngine.SQLITE)})", operation="inspect_columns")
        columns = tuple(
            DatabaseColumnMetadata(
                name=str(row["name"]),
                ordinal=int(row["cid"]),
                declared_type=str(row["type"] or ""),
                normalized_type=_normalize_sqlite_type(str(row["type"] or "")),
                nullable=not bool(row["notnull"]),
                default_sql=None if row["dflt_value"] is None else str(row["dflt_value"]),
                primary_key_position=int(row["pk"]) if row["pk"] else None,
            )
            for row in column_rows
        )
        constraints: list[DatabaseDeclaredConstraint] = []
        pk_columns = tuple(
            column.name
            for column in sorted(
                (column for column in columns if column.primary_key_position is not None),
                key=lambda column: column.primary_key_position or 0,
            )
        )
        if pk_columns:
            constraints.append(DatabaseDeclaredConstraint(constraint_type=ConstraintType.PRIMARY_KEY, columns=pk_columns, name=f"pk_{table.name}"))
        constraints.extend(
            DatabaseDeclaredConstraint(constraint_type=ConstraintType.NOT_NULL, columns=(column.name,), name=f"not_null_{column.name}")
            for column in columns
            if not column.nullable
        )
        constraints.extend(self._foreign_key_constraints(table.name))
        constraints.extend(self._unique_constraints(table.name, pk_columns))
        return DatabaseTableMetadata(
            schema_name=table.schema_name,
            name=table.name,
            kind=kind,
            columns=columns,
            constraints=tuple(constraints),
        )

    def _foreign_key_constraints(self, table_name: str) -> list[DatabaseDeclaredConstraint]:
        rows = self._run(
            f"PRAGMA foreign_key_list({quote_identifier(table_name, DatabaseEngine.SQLITE)})",
            operation="inspect_declared_foreign_keys",
        )
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["id"]), []).append(row)
        constraints = []
        for foreign_key_id, foreign_rows in grouped.items():
            ordered = sorted(foreign_rows, key=lambda row: int(row["seq"]))
            constraints.append(
                DatabaseDeclaredConstraint(
                    constraint_type=ConstraintType.FOREIGN_KEY,
                    columns=tuple(str(row["from"]) for row in ordered),
                    referenced_table=str(ordered[0]["table"]),
                    referenced_columns=tuple(str(row["to"]) for row in ordered),
                    name=f"fk_{table_name}_{foreign_key_id}",
                )
            )
        return constraints

    def _unique_constraints(self, table_name: str, primary_key_columns: tuple[str, ...]) -> list[DatabaseDeclaredConstraint]:
        index_rows = self._run(
            f"PRAGMA index_list({quote_identifier(table_name, DatabaseEngine.SQLITE)})",
            operation="inspect_unique_constraints",
        )
        constraints = []
        for index in index_rows:
            if not bool(index["unique"]) or str(index["origin"] or "") == "pk":
                continue
            index_name = str(index["name"])
            columns = self._run(
                f"PRAGMA index_info({quote_identifier(index_name, DatabaseEngine.SQLITE)})",
                operation="inspect_unique_index_columns",
            )
            names = tuple(str(row["name"]) for row in sorted(columns, key=lambda row: int(row["seqno"])))
            if names and names != primary_key_columns:
                constraints.append(DatabaseDeclaredConstraint(constraint_type=ConstraintType.UNIQUE, columns=names, name=index_name))
        return constraints

    def sample_rows_bounded(self, request: BoundedSampleRequest) -> BoundedSampleObservation:
        table_sql = _sqlite_identifier(request.table)
        column_sql = ", ".join(quote_identifier(column, DatabaseEngine.SQLITE) for column in request.columns)
        rows = self._run(
            f"SELECT {column_sql} FROM {table_sql} LIMIT ?",
            (request.sampling_policy.max_rows,),
            operation="sample_rows_bounded",
        )
        values = tuple(tuple(row[column] for column in request.columns) for row in rows)
        return BoundedSampleObservation(
            table=request.table,
            columns=request.columns,
            rows=values,
            rows_observed=len(values),
            sampling_method=request.sampling_policy.method,
            requested_max_rows=request.sampling_policy.max_rows,
            representative=False,
        )

    def explain_bounded_read(self, request: BoundedSampleRequest) -> tuple[ExplainPlanStep, ...]:
        table_sql = _sqlite_identifier(request.table)
        column_sql = ", ".join(quote_identifier(column, DatabaseEngine.SQLITE) for column in request.columns)
        rows = self._run(
            f"EXPLAIN QUERY PLAN SELECT {column_sql} FROM {table_sql} LIMIT ?",
            (request.sampling_policy.max_rows,),
            operation="explain_bounded_read",
        )
        return tuple(
            ExplainPlanStep(
                select_id=int(row["id"]) if row["id"] is not None else None,
                order=int(row["parent"]) if row["parent"] is not None else None,
                from_id=int(row["notused"]) if row["notused"] is not None else None,
                detail=str(row["detail"]),
            )
            for row in rows
        )

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SQLiteReadOnlySession":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def _normalize_sqlite_type(declared_type: str) -> str:
    upper = declared_type.upper()
    if "INT" in upper:
        return "integer"
    if any(token in upper for token in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if "BLOB" in upper or not upper:
        return "blob"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return "real"
    return "numeric"


def _install_sqlite_authorizer(connection: sqlite3.Connection) -> None:
    """Install a deny-by-default SQLite authorizer for every adapter connection."""
    deny_names = {
        "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VTABLE", "SQLITE_CREATE_VIEW", "SQLITE_DELETE", "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VTABLE",
        "SQLITE_DROP_VIEW", "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_ALTER_TABLE",
        "SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_REINDEX", "SQLITE_ANALYZE",
    }
    deny_codes = {getattr(sqlite3, name) for name in deny_names if hasattr(sqlite3, name)}
    pragma_code = getattr(sqlite3, "SQLITE_PRAGMA", -1)
    function_code = getattr(sqlite3, "SQLITE_FUNCTION", -1)
    safe_pragmas = {
        "query_only", "busy_timeout", "foreign_keys", "table_info", "foreign_key_list",
        "index_list", "index_info", "index_xinfo", "table_xinfo", "database_list",
        "read_uncommitted",
    }

    def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
        if action in deny_codes:
            return sqlite3.SQLITE_DENY
        if action == pragma_code:
            return sqlite3.SQLITE_OK if str(arg1 or "").lower() in safe_pragmas else sqlite3.SQLITE_DENY
        if action == function_code and str(arg2 or arg1 or "").lower() in {"load_extension", "fts3_tokenizer"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)


def configure_sqlite_connection(connection: sqlite3.Connection, *, profile_id: str) -> None:
    """Configure a SQLAlchemy/dlt creator connection with the same controls."""
    try:
        connection.isolation_level = None
        _install_sqlite_authorizer(connection)
        connection.execute("PRAGMA query_only = ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise sqlite3.OperationalError("query_only verification failed")
    except sqlite3.Error as exception:
        failure = normalize_sqlite_failure(exception, operation="configure_sqlalchemy_sqlite_connection", context_id=profile_id)
        raise DatabaseAccessError(failure) from None
