"""dlt-backed SQL SourceAdapter implementation.

dlt and SQLAlchemy are intentionally imported only inside this concrete
adapter.  Discovery and extraction return project-owned contracts; no dlt
resource, SQLAlchemy engine/table, cursor or row object escapes this module.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, Protocol

from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource
from dirty_data_to_olap.adapters.sources.staging import SourceFaithfulParquetStager
from dirty_data_to_olap.application.source_adapter import SourceAdapter
from dirty_data_to_olap.domain.contracts.database import (
    DatabaseAccessError,
    DatabaseColumnMetadata,
    DatabaseDeclaredConstraint,
    DatabaseEngine,
    DatabaseMetadata,
    DatabaseTableMetadata,
    ConstraintType,
    DatabaseIdentifier,
)
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    ColumnDescriptor,
    DeclaredConstraint,
    ExtractionMetrics,
    ObservationMode,
    ObservationScope,
    RecordLocatorKind,
    RowAccounting,
    RowCountSemantics,
    SelectionScope,
    SnapshotConsistency,
    SourceCatalog,
    SourceDescriptor,
    SourceFailure,
    SourceFailureKind,
    SourceIngestionError,
    SourceRecordReference,
    SourceRegistryRecord,
    SourceSelection,
    SourceSnapshot,
    SourceSnapshotResult,
    SourceTableKind,
    SourceType,
    StabilityScope,
    TableDescriptor,
    batch_id_for,
    column_id_for,
    record_ref_for_ordinal,
    record_ref_for_pk,
    schema_fingerprint,
    snapshot_id_for,
    stable_digest,
    table_id_for,
    utc_now,
)


class RuntimeCredentialResolver(Protocol):
    def resolve(self, profile: Any) -> "RuntimeSqlCredentials":
        ...


@dataclass(frozen=True, repr=False)
class RuntimeSqlCredentials:
    """Runtime-only credentials.  The URL is never placed in project models."""

    connection_url: str

    def __repr__(self) -> str:
        return "RuntimeSqlCredentials(connection_url=<redacted>)"


def source_type_for_engine(engine: DatabaseEngine) -> SourceType:
    return {
        DatabaseEngine.SQLITE: SourceType.SQLITE,
        DatabaseEngine.POSTGRESQL: SourceType.POSTGRESQL,
        DatabaseEngine.MYSQL: SourceType.MYSQL,
        DatabaseEngine.MARIADB: SourceType.MARIADB,
        DatabaseEngine.SQLSERVER: SourceType.SQLSERVER,
    }.get(engine, SourceType.SQLITE)


def _failure(kind: SourceFailureKind, operation: str, detail: str) -> SourceIngestionError:
    return SourceIngestionError(SourceFailure(
        kind=kind,
        operation=operation,
        detail=detail,
        retryable=kind in {SourceFailureKind.ACCESS_FAILED, SourceFailureKind.TIMEOUT, SourceFailureKind.STAGING_FAILED},
    ))


def _scope(record: SourceRegistryRecord, selection: SourceSelection) -> SelectionScope:
    selected = selection.scope
    configured = record.scope
    return SelectionScope(
        included_objects=selected.included_objects or configured.included_objects,
        excluded_objects=selected.excluded_objects or configured.excluded_objects,
        include_views=selected.include_views or configured.include_views,
        included_columns=selected.included_columns or configured.included_columns,
    )


def _adapter_ref(record: SourceRegistryRecord, selection: SourceSelection) -> AdapterReference:
    return AdapterReference(
        name="dlt_sql_source",
        version="1.0.0",
        config_fingerprint=stable_digest({"adapter_config": dict(record.adapter_config), "scope": _scope(record, selection), "chunk_size": selection.extraction.chunk_size}),
    )


def _source_type_metadata(record: SourceRegistryRecord) -> SourceType:
    return record.source_type


class DltSqlSourceAdapter(SourceAdapter):
    name = "dlt_sql_source"
    version = "1.0.0"

    def __init__(
        self,
        *,
        project_root: Path,
        credential_resolver: RuntimeCredentialResolver | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.credential_resolver = credential_resolver

    def _profile(self, record: SourceRegistryRecord) -> Any:
        if record.connection_profile is None:
            raise _failure(SourceFailureKind.INVALID_SELECTION, "resolve_connection_profile", "SQL source has no connection profile")
        return record.connection_profile

    def _sqlite_metadata(self, profile: Any) -> DatabaseMetadata:
        source = SQLiteReadOnlySource(profile)
        try:
            with source.open_readonly_session() as session:
                return session.inspect_database_metadata()
        except DatabaseAccessError:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "discover_sqlite", "SQLite metadata access failed") from None
        except Exception:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "discover_sqlite", "SQLite metadata access failed") from None

    def _build_catalog_from_metadata(
        self,
        record: SourceRegistryRecord,
        selection: SourceSelection,
        metadata: DatabaseMetadata,
        *,
        adapter_reference: AdapterReference,
    ) -> SourceCatalog:
        scope = _scope(record, selection)
        available = {table.name: table for table in metadata.tables}
        selected_names = scope.included_objects or tuple(sorted(available))
        selected_names = tuple(name for name in selected_names if name not in scope.excluded_objects)
        tables: list[TableDescriptor] = []
        columns: list[ColumnDescriptor] = []
        constraints: list[DeclaredConstraint] = []
        source_id = record.source_id or self._source_id(record)
        selected_physical = [available[name] for name in selected_names if name in available]
        table_id_by_name = {
            physical.name: table_id_for(
                source_id,
                physical.schema_name,
                physical.name,
                SourceTableKind.VIEW if physical.kind.value == "view" else SourceTableKind.TABLE,
            )
            for physical in selected_physical
        }
        for table_name in selected_names:
            physical = available.get(table_name)
            if physical is None:
                raise _failure(SourceFailureKind.INVALID_SELECTION, "discover_sql_source", "selected SQL object was not found")
            if physical.kind.value == "view" and not scope.include_views and table_name not in scope.included_objects:
                continue
            kind = SourceTableKind.VIEW if physical.kind.value == "view" else SourceTableKind.TABLE
            table_id = table_id_by_name[physical.name]
            tables.append(TableDescriptor(
                table_id=table_id,
                source_id=source_id,
                namespace=physical.schema_name,
                physical_name=physical.name,
                table_kind=kind,
                row_count=None,
                row_count_semantics=RowCountSemantics.UNKNOWN,
                fingerprint=stable_digest(physical),
            ))
            table_columns = []
            for column in sorted(physical.columns, key=lambda item: item.ordinal):
                table_columns.append(ColumnDescriptor(
                    column_id=column_id_for(table_id, column.name, column.ordinal),
                    table_id=table_id,
                    physical_name=column.name,
                    ordinal=column.ordinal,
                    native_physical_type=column.declared_type,
                    normalized_physical_type=column.normalized_type,
                    schema_nullable=column.nullable,
                    raw_declared_notnull=not column.nullable,
                    declared_default=column.default_sql,
                    primary_key_position=column.primary_key_position,
                ))
            columns.extend(table_columns)
            for constraint in physical.constraints:
                constraints.append(self._normalize_constraint(source_id, table_id, constraint, table_id_by_name, adapter_reference))
        schema_fp = schema_fingerprint({"selection": scope, "tables": tables, "columns": columns, "constraints": constraints})
        profile = self._profile(record)
        return SourceCatalog(
            source=SourceDescriptor(
                source_id=source_id,
                display_name=record.display_name,
                source_type=_source_type_metadata(record),
                connection_profile=profile,
                selection_scope=scope,
                source_fingerprint=None,
                schema_fingerprint=schema_fp,
                adapter_reference=adapter_reference,
            ),
            tables=tuple(tables),
            columns=tuple(columns),
            declared_constraints=tuple(constraints),
        )

    def _normalize_constraint(
        self,
        source_id: str,
        table_id: str,
        constraint: DatabaseDeclaredConstraint,
        table_ids: Mapping[str, str],
        adapter_reference: AdapterReference,
    ) -> DeclaredConstraint:
        return DeclaredConstraint(
            constraint_type=constraint.constraint_type.value,
            source_id=source_id,
            table_id=table_id,
            columns=constraint.columns,
            referenced_table_id=table_ids.get(constraint.referenced_table or ""),
            referenced_table_name=constraint.referenced_table,
            referenced_columns=constraint.referenced_columns,
            declared=True,
            provenance=adapter_reference,
        )

    def _source_id(self, record: SourceRegistryRecord) -> str:
        profile = self._profile(record)
        return "src_" + stable_digest({"source_type": record.source_type.value, "profile_id": profile.profile_id})[:32]

    def _engine(self, profile: Any) -> Any:
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool, StaticPool
            if profile.database_engine is DatabaseEngine.SQLITE:
                path = Path(profile.database_name).expanduser().resolve()
                if not path.is_file():
                    raise FileNotFoundError("SQLite source file is not accessible")
                uri = f"{path.as_uri()}?mode=ro"

                def creator() -> sqlite3.Connection:
                    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
                    connection.execute("PRAGMA query_only = ON")
                    return connection

                return create_engine("sqlite+pysqlite://", creator=creator, poolclass=StaticPool)
            if self.credential_resolver is None:
                raise ValueError("non-SQLite extraction requires an injected runtime credential resolver")
            runtime_credentials = self.credential_resolver.resolve(profile)
            return create_engine(runtime_credentials.connection_url, poolclass=NullPool)
        except SourceIngestionError:
            raise
        except Exception:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "open_sql_source", "SQL source engine could not be opened") from None

    def _dlt_database(self, engine: Any, table_names: Sequence[str] | None, selection: SelectionScope, chunk_size: int) -> Any:
        try:
            from dlt.sources.sql_database import sql_database
            return sql_database(
                credentials=engine,
                table_names=list(table_names) if table_names else None,
                include_views=selection.include_views,
                reflection_level="full",
                resolve_foreign_keys=True,
                chunk_size=chunk_size,
                backend="sqlalchemy",
            )
        except Exception:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "initialize_dlt_sql_source", "dlt SQL source initialization failed") from None

    def discover_source(self, selection: SourceSelection, registry_record: SourceRegistryRecord) -> SourceCatalog:
        profile = self._profile(registry_record)
        reference = _adapter_ref(registry_record, selection)
        if profile.database_engine is DatabaseEngine.SQLITE:
            metadata = self._sqlite_metadata(profile)
            return self._build_catalog_from_metadata(registry_record, selection, metadata, adapter_reference=reference)
        engine = self._engine(profile)
        try:
            scope = _scope(registry_record, selection)
            source = self._dlt_database(engine, scope.included_objects, scope, selection.extraction.chunk_size)
            tables: list[TableDescriptor] = []
            columns: list[ColumnDescriptor] = []
            constraints: list[DeclaredConstraint] = []
            source_id = registry_record.source_id or self._source_id(registry_record)
            resources = list(source.resources.values())
            for resource in resources:
                schema = resource.compute_table_schema()
                table_name = str(resource.name)
                kind = SourceTableKind.TABLE
                table_id = table_id_for(source_id, None, table_name, kind)
                tables.append(TableDescriptor(table_id=table_id, source_id=source_id, physical_name=table_name, table_kind=kind, row_count=None, row_count_semantics=RowCountSemantics.UNKNOWN, fingerprint=stable_digest(schema)))
                for ordinal, (name, hint) in enumerate(schema.get("columns", {}).items()):
                    columns.append(ColumnDescriptor(column_id=column_id_for(table_id, name, ordinal), table_id=table_id, physical_name=name, ordinal=ordinal, native_physical_type=str(hint.get("data_type", "unknown")), normalized_physical_type=str(hint.get("data_type", "unknown")), schema_nullable=hint.get("nullable"), raw_declared_notnull=None, primary_key_position=(ordinal + 1 if hint.get("primary_key") else None)))
                for reference_info in schema.get("references", []) or []:
                    constraints.append(DeclaredConstraint(constraint_type=ConstraintType.FOREIGN_KEY.value, source_id=source_id, table_id=table_id, columns=tuple(reference_info.get("columns", [])), referenced_table_name=reference_info.get("referenced_table"), referenced_columns=tuple(reference_info.get("referenced_columns", [])), declared=True, provenance=reference))
            schema_fp = schema_fingerprint({"selection": scope, "tables": tables, "columns": columns, "constraints": constraints})
            return SourceCatalog(source=SourceDescriptor(source_id=source_id, display_name=registry_record.display_name, source_type=registry_record.source_type, connection_profile=profile, selection_scope=scope, schema_fingerprint=schema_fp, adapter_reference=reference), tables=tuple(tables), columns=tuple(columns), declared_constraints=tuple(constraints))
        except SourceIngestionError:
            raise
        except Exception:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "discover_sql_source", "SQL source discovery failed") from None
        finally:
            engine.dispose()

    def create_bounded_snapshot(
        self,
        catalog: SourceCatalog,
        selection: SourceSelection,
        *,
        execution_context_id: str,
        staging_root: Path,
    ) -> SourceSnapshotResult:
        profile = catalog.source.connection_profile
        if profile is None:
            raise _failure(SourceFailureKind.INVALID_SELECTION, "extract_sql_source", "SQL connection profile is missing")
        engine = self._engine(profile)
        snapshot_id = snapshot_id_for(catalog.source_id, catalog.source.schema_fingerprint, catalog.source.selection_scope, selection.extraction, execution_context_id)
        transaction_connection = None
        transaction = None
        consistency = SnapshotConsistency.TRANSACTION_SCOPED if profile.database_engine is DatabaseEngine.SQLITE else SnapshotConsistency.BEST_EFFORT
        snapshot = SourceSnapshot(source_id=catalog.source_id, snapshot_id=snapshot_id, execution_context_id=execution_context_id, schema_fingerprint=catalog.source.schema_fingerprint, source_fingerprint=catalog.source.source_fingerprint, observed_at=utc_now(), selection_scope=catalog.source.selection_scope, observation_scope=ObservationScope(mode=ObservationMode.BOUNDED if selection.extraction.max_rows is not None else ObservationMode.FULL, chunk_size=selection.extraction.chunk_size, max_rows=selection.extraction.max_rows, input_records_observed=0), extraction_policy=selection.extraction, consistency=consistency, adapter_reference=catalog.source.adapter_reference)
        stager = SourceFaithfulParquetStager(self.project_root, adapter_reference=catalog.source.adapter_reference)
        batches = []
        references = []
        observed = 0
        staged = 0
        try:
            if profile.database_engine is DatabaseEngine.SQLITE:
                transaction_connection = engine.connect()
                transaction = transaction_connection.begin()
            names = [table.physical_name for table in catalog.tables]
            source = self._dlt_database(engine, names, catalog.source.selection_scope, selection.extraction.chunk_size)
            resource_by_name = {str(resource.name): resource for resource in source.resources.values()}
            for table in catalog.tables:
                resource = resource_by_name.get(table.physical_name)
                if resource is None:
                    raise _failure(SourceFailureKind.EXTRACTION_FAILED, "extract_sql_source", "dlt did not return a selected table")
                table_columns = tuple(column for column in catalog.columns if column.table_id == table.table_id)
                pending: list[Mapping[str, Any]] = []
                batch_index = 0
                table_ordinal = 0
                for native_batch in resource:
                    if isinstance(native_batch, dict):
                        rows = [native_batch]
                    elif isinstance(native_batch, list):
                        rows = native_batch
                    else:
                        rows = list(native_batch)
                    for native_row in rows:
                        if selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows:
                            break
                        row = dict(native_row)
                        pending.append(row)
                        observed += 1
                        table_ordinal += 1
                        if len(pending) >= selection.extraction.chunk_size:
                            batch = stager.stage_rows(source_id=catalog.source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_index=batch_index, first_ordinal=table_ordinal - len(pending), rows=pending, schema_fingerprint=catalog.source.schema_fingerprint, staging_root=staging_root)
                            batches.append(batch)
                            references.extend(self._references(catalog, snapshot_id, table, batch, pending, table_ordinal - len(pending), table_columns))
                            staged += len(pending)
                            pending = []
                            batch_index += 1
                    if selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows:
                        break
                if pending:
                    first = table_ordinal - len(pending)
                    batch = stager.stage_rows(source_id=catalog.source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_index=batch_index, first_ordinal=first, rows=pending, schema_fingerprint=catalog.source.schema_fingerprint, staging_root=staging_root)
                    batches.append(batch)
                    references.extend(self._references(catalog, snapshot_id, table, batch, pending, first, table_columns))
                    staged += len(pending)
                if selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows:
                    break
        except SourceIngestionError:
            raise
        except Exception:
            raise _failure(SourceFailureKind.EXTRACTION_FAILED, "extract_sql_source", "dlt SQL extraction failed") from None
        finally:
            if transaction is not None:
                transaction.rollback()
            if transaction_connection is not None:
                transaction_connection.close()
            engine.dispose()
        accounting = RowAccounting(input_records_observed=observed, successfully_staged_records=staged, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True)
        result = SourceSnapshotResult(snapshot=snapshot.model_copy(update={"observation_scope": snapshot.observation_scope.model_copy(update={"input_records_observed": observed})}), batches=tuple(batches), record_references=tuple(references), accounting=accounting, metrics=ExtractionMetrics(input_records_observed=observed, staged_records=staged, batch_count=len(batches), configured_chunk_size=selection.extraction.chunk_size, bytes_staged=sum((self.project_root / batch.artifact_location).stat().st_size for batch in batches)))
        stager.write_catalog(catalog, run_root=staging_root.parent)
        stager.write_manifest(result, run_root=staging_root.parent)
        return result

    def _references(self, catalog: SourceCatalog, snapshot_id: str, table: TableDescriptor, batch: Any, rows: Sequence[Mapping[str, Any]], first_ordinal: int, columns: Sequence[ColumnDescriptor]) -> tuple[SourceRecordReference, ...]:
        key_columns = tuple(column.physical_name for column in sorted((item for item in columns if item.primary_key_position is not None), key=lambda item: item.primary_key_position or 0))
        refs = []
        for offset, row in enumerate(rows):
            ordinal = first_ordinal + offset
            if key_columns:
                values = tuple(row.get(name) for name in key_columns)
                refs.append(SourceRecordReference(record_ref=record_ref_for_pk(catalog.source_id, snapshot_id, table.table_id, key_columns, values), source_id=catalog.source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_id=batch.batch_id, extraction_ordinal=ordinal, locator_kind=RecordLocatorKind.PRIMARY_KEY, key_columns=key_columns, key_values=values, stability_scope=StabilityScope.SOURCE_KEY_SCOPE))
            else:
                refs.append(SourceRecordReference(record_ref=record_ref_for_ordinal(catalog.source_id, snapshot_id, table.table_id, ordinal), source_id=catalog.source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_id=batch.batch_id, extraction_ordinal=ordinal, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY))
        return tuple(refs)
