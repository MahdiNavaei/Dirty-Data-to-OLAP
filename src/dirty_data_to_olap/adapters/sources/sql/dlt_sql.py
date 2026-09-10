"""dlt-backed SQL SourceAdapter implementation.

dlt and SQLAlchemy are intentionally imported only inside this concrete
adapter.  Discovery and extraction return project-owned contracts; no dlt
resource, SQLAlchemy engine/table, cursor or row object escapes this module.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, Protocol

from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource, configure_sqlite_connection
from dirty_data_to_olap.application.database_security import DatabaseSecurityOperationError, DatabaseSecurityService
from dirty_data_to_olap.domain.contracts.database_security import CredentialPurpose, PrivilegeFinding, ReadOnlyEnforcementMethod
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
    MaxRowsScope,
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
    TableObservationStatus,
    TableSnapshotObservation,
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
    def resolve(self, profile: Any, *, required_purpose: CredentialPurpose, source_id: str) -> "RuntimeSqlCredentials":
        ...


@dataclass(frozen=True, repr=False)
class RuntimeSqlCredentials:
    """Runtime-only credentials.  The URL is never placed in project models or logs."""

    connection_url: str
    credential_reference: str = "runtime-only"
    credential_purpose: CredentialPurpose = CredentialPurpose.SOURCE_READ_ONLY
    credential_version: str = "1"
    source_id: str | None = None
    provider_security_verified: bool = False
    privilege_findings: tuple[PrivilegeFinding, ...] = ()

    def __repr__(self) -> str:
        return "RuntimeSqlCredentials(connection_url=<redacted>)"

    def __str__(self) -> str:
        return "RuntimeSqlCredentials(<redacted>)"


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
        self.security = DatabaseSecurityService()

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

    def _engine(self, profile: Any, *, source_id: str | None = None) -> Any:
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
                    configure_sqlite_connection(connection, profile_id=profile.profile_id)
                    return connection

                engine = create_engine("sqlite+pysqlite://", creator=creator, poolclass=StaticPool)
                from sqlalchemy import event
                event.listen(engine, "connect", lambda dbapi_connection, _record: configure_sqlite_connection(dbapi_connection, profile_id=profile.profile_id), insert=True)
                return engine
            if self.credential_resolver is None:
                raise ValueError("non-SQLite extraction requires an injected runtime credential resolver")
            scoped_source_id = source_id or ("profile_" + stable_digest(profile.profile_id)[:24])
            runtime_credentials = self.credential_resolver.resolve(profile, required_purpose=CredentialPurpose.SOURCE_READ_ONLY, source_id=scoped_source_id)
            if runtime_credentials.credential_purpose is not CredentialPurpose.SOURCE_READ_ONLY or runtime_credentials.source_id not in {None, scoped_source_id} or not runtime_credentials.provider_security_verified:
                raise ValueError("runtime source credential has no valid read-only security assurance")
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
                table_adapter_callback=None,
                query_adapter_callback=None,
                engine_adapter_callback=None,
            )
        except Exception as error:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "initialize_dlt_sql_source", f"dlt SQL source initialization failed ({error.__class__.__name__})") from None

    def discover_source(self, selection: SourceSelection, registry_record: SourceRegistryRecord) -> SourceCatalog:
        self._validate_adapter_config(registry_record)
        profile = self._profile(registry_record)
        reference = _adapter_ref(registry_record, selection)
        if profile.database_engine is DatabaseEngine.SQLITE:
            self._require_security(registry_record, selection, reference)
            metadata = self._sqlite_metadata(profile)
            return self._build_catalog_from_metadata(registry_record, selection, metadata, adapter_reference=reference)
        self._require_security(registry_record, selection, reference)
        engine = self._engine(profile, source_id=registry_record.source_id or self._source_id(registry_record))
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
        self._require_security_from_catalog(catalog, selection)
        engine = self._engine(profile, source_id=catalog.source_id)
        snapshot_id = snapshot_id_for(catalog.source_id, catalog.source.schema_fingerprint, catalog.source.selection_scope, selection.extraction, execution_context_id)
        # dlt receives the engine, not the specific connection used by a
        # separate transaction.  The extraction is therefore best-effort.
        consistency = SnapshotConsistency.BEST_EFFORT
        snapshot = SourceSnapshot(source_id=catalog.source_id, snapshot_id=snapshot_id, execution_context_id=execution_context_id, schema_fingerprint=catalog.source.schema_fingerprint, source_fingerprint=catalog.source.source_fingerprint, observed_at=utc_now(), selection_scope=catalog.source.selection_scope, observation_scope=ObservationScope(mode=ObservationMode.BOUNDED if selection.extraction.max_rows is not None else ObservationMode.FULL, chunk_size=selection.extraction.chunk_size, max_rows=selection.extraction.max_rows, max_rows_scope=selection.extraction.max_rows_scope, input_records_observed=0), extraction_policy=selection.extraction, consistency=consistency, adapter_reference=catalog.source.adapter_reference)
        stager = SourceFaithfulParquetStager(self.project_root, adapter_reference=catalog.source.adapter_reference)
        batches = []
        references = []
        table_observations = []
        observed = 0
        staged = 0
        try:
            names = [table.physical_name for table in catalog.tables]
            source = self._dlt_database(engine, names, catalog.source.selection_scope, selection.extraction.chunk_size)
            resource_by_name = {str(resource.name): resource for resource in source.resources.values()}
            for table in catalog.tables:
                if (
                    selection.extraction.max_rows is not None
                    and selection.extraction.max_rows_scope is MaxRowsScope.SOURCE_WIDE
                    and observed >= selection.extraction.max_rows
                ):
                    table_observations.append(TableSnapshotObservation(table_id=table.table_id, rows_observed=0, status=TableObservationStatus.NOT_OBSERVED))
                    continue
                resource = resource_by_name.get(table.physical_name)
                if resource is None:
                    raise _failure(SourceFailureKind.EXTRACTION_FAILED, "extract_sql_source", "dlt did not return a selected table")
                table_columns = tuple(column for column in catalog.columns if column.table_id == table.table_id)
                pending: list[Mapping[str, Any]] = []
                batch_index = 0
                table_ordinal = 0
                table_exhausted = True
                for native_batch in resource:
                    if isinstance(native_batch, dict):
                        rows = [native_batch]
                    elif isinstance(native_batch, list):
                        rows = native_batch
                    else:
                        rows = list(native_batch)
                    for native_row in rows:
                        at_source_limit = selection.extraction.max_rows_scope is MaxRowsScope.SOURCE_WIDE and selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows
                        at_table_limit = selection.extraction.max_rows_scope is MaxRowsScope.PER_TABLE and selection.extraction.max_rows is not None and table_ordinal >= selection.extraction.max_rows
                        if at_source_limit or at_table_limit:
                            table_exhausted = False
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
                    if selection.extraction.max_rows_scope is MaxRowsScope.SOURCE_WIDE and selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows:
                        table_exhausted = False
                        break
                if pending:
                    first = table_ordinal - len(pending)
                    batch = stager.stage_rows(source_id=catalog.source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_index=batch_index, first_ordinal=first, rows=pending, schema_fingerprint=catalog.source.schema_fingerprint, staging_root=staging_root)
                    batches.append(batch)
                    references.extend(self._references(catalog, snapshot_id, table, batch, pending, first, table_columns))
                    staged += len(pending)
                if selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows:
                    if selection.extraction.max_rows_scope is MaxRowsScope.SOURCE_WIDE:
                        table_exhausted = False
                table_status = TableObservationStatus.FULLY_OBSERVED if table_exhausted else (TableObservationStatus.PARTIALLY_OBSERVED if table_ordinal else TableObservationStatus.NOT_OBSERVED)
                table_observations.append(TableSnapshotObservation(table_id=table.table_id, rows_observed=table_ordinal, status=table_status))
                if selection.extraction.max_rows_scope is MaxRowsScope.SOURCE_WIDE and selection.extraction.max_rows is not None and observed >= selection.extraction.max_rows:
                    continue
        except SourceIngestionError:
            stager.discard_batches(batches)
            raise
        except Exception:
            stager.discard_batches(batches)
            raise _failure(SourceFailureKind.EXTRACTION_FAILED, "extract_sql_source", "dlt SQL extraction failed") from None
        finally:
            engine.dispose()
        accounting = RowAccounting(input_records_observed=observed, successfully_staged_records=staged, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True)
        result = SourceSnapshotResult(snapshot=snapshot.model_copy(update={"observation_scope": snapshot.observation_scope.model_copy(update={"input_records_observed": observed})}), batches=tuple(batches), record_references=tuple(references), accounting=accounting, metrics=ExtractionMetrics(input_records_observed=observed, staged_records=staged, batch_count=len(batches), configured_chunk_size=selection.extraction.chunk_size, bytes_staged=sum((self.project_root / batch.artifact_location).stat().st_size for batch in batches)), table_observations=tuple(table_observations))
        stager.write_catalog(catalog, run_root=staging_root.parent)
        stager.write_manifest(result, run_root=staging_root.parent)
        return result

    def _require_security(self, record: SourceRegistryRecord, selection: SourceSelection, reference: AdapterReference) -> None:
        profile = self._profile(record)
        source_id = record.source_id or self._source_id(record)
        credential_reference = profile.credential_reference or f"profile:{profile.profile_id}"
        credentials = None
        if profile.database_engine is not DatabaseEngine.SQLITE:
            if self.credential_resolver is None:
                raise _failure(SourceFailureKind.ACCESS_FAILED, "database_security_assurance", "source database security assurance is unavailable")
            try:
                credentials = self.credential_resolver.resolve(profile, required_purpose=CredentialPurpose.SOURCE_READ_ONLY, source_id=source_id)
            except Exception:
                raise _failure(SourceFailureKind.ACCESS_FAILED, "database_security_assurance", "source database security assurance is unavailable") from None
            credential_reference = credentials.credential_reference
            purpose = credentials.credential_purpose
            version = credentials.credential_version
            verified = credentials.provider_security_verified
            findings = credentials.privilege_findings
            methods = (ReadOnlyEnforcementMethod.PROVIDER_ROLE,) if verified else ()
            driver = "sqlalchemy"
        else:
            purpose = CredentialPurpose.SOURCE_READ_ONLY
            version = "sqlite-reference-1"
            verified = True
            findings = ()
            methods = (ReadOnlyEnforcementMethod.SQLITE_URI_MODE_RO, ReadOnlyEnforcementMethod.SQLITE_QUERY_ONLY, ReadOnlyEnforcementMethod.SQLITE_AUTHORIZE_DENY, ReadOnlyEnforcementMethod.SQLALCHEMY_CONNECT_HOOK)
            driver = "sqlite3/sqlalchemy"
        try:
            assessment = self.security.assess_source(source_id=source_id, profile_id=profile.profile_id, engine=profile.database_engine, credential_reference=credential_reference, credential_purpose=purpose, credential_version=version, selection_fingerprint=reference.config_fingerprint, driver_reference=driver, provider_verified=verified, privilege_findings=findings, enforcement_methods=methods)
            self.security.require_pass(assessment)
        except DatabaseSecurityOperationError:
            raise _failure(SourceFailureKind.ACCESS_FAILED, "database_security_assurance", "source database security assurance did not pass") from None

    @staticmethod
    def _validate_adapter_config(record: SourceRegistryRecord) -> None:
        allowed = {"schema", "include_views", "reflection_level", "resolve_foreign_keys"}
        unsafe = [key for key in record.adapter_config if key not in allowed or any(token in key.lower() for token in ("callback", "sql", "engine", "credential", "dsn"))]
        if unsafe:
            raise _failure(SourceFailureKind.INVALID_SELECTION, "validate_sql_adapter_config", "SQL adapter configuration contains an unsupported or unsafe option")

    def _require_security_from_catalog(self, catalog: SourceCatalog, selection: SourceSelection) -> None:
        profile = catalog.source.connection_profile
        if profile is None:
            raise _failure(SourceFailureKind.INVALID_SELECTION, "database_security_assurance", "source database security profile is missing")
        self._require_security(SourceRegistryRecord(registry_id="catalog-security", source_id=catalog.source_id, display_name=catalog.source.display_name, source_type=catalog.source.source_type, connection_profile=profile, scope=catalog.source.selection_scope, adapter_name=self.name, adapter_version=self.version), selection, catalog.source.adapter_reference)

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
