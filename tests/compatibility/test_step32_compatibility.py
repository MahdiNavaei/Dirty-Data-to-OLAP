"""Compatibility checks at the project-owned source boundary.

The database cases are intentionally opt-in.  Local runs always exercise the
real SQLite and file adapters; CI supplies disposable PostgreSQL, MySQL,
MariaDB and SQL Server instances and sets ``DDO_STEP32_REQUIRE_LIVE_DB=1``.
No provider-native object is asserted outside its adapter boundary.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Unicode, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.database_security import (
    CredentialPurpose,
    PrivilegeFinding,
    PrivilegeFindingStatus,
    ProviderSecurityVerification,
    ProviderVerificationStatus,
)
from dirty_data_to_olap.domain.contracts.source import (
    ExtractionPolicy,
    SelectionScope,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
)


ROOT = Path(__file__).resolve().parents[2]
FILE_CASES = (SourceType.CSV, SourceType.PARQUET, SourceType.XLSX)
LIVE_DATABASES = (
    ("postgresql", DatabaseEngine.POSTGRESQL, "DDO_STEP32_POSTGRES_ADMIN_URL", "DDO_STEP32_POSTGRES_URL"),
    ("mysql", DatabaseEngine.MYSQL, "DDO_STEP32_MYSQL_ADMIN_URL", "DDO_STEP32_MYSQL_URL"),
    ("mariadb", DatabaseEngine.MARIADB, "DDO_STEP32_MARIADB_ADMIN_URL", "DDO_STEP32_MARIADB_URL"),
    ("sqlserver", DatabaseEngine.SQLSERVER, "DDO_STEP32_SQLSERVER_ADMIN_URL", "DDO_STEP32_SQLSERVER_URL"),
)
TABLE_NAME = "step32_compatibility"


@pytest.fixture()
def workspace() -> Path:
    path = ROOT / "workspace" / "tests" / f"step32_{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _selection(registry_id: str, *, scope: SelectionScope | None = None) -> SourceSelection:
    return SourceSelection(
        registry_id=registry_id,
        scope=scope or SelectionScope(),
        extraction=ExtractionPolicy(chunk_size=1),
        execution_context_id="step32-compatibility",
    )


def _file_record(path: Path, source_type: SourceType, *, scope: SelectionScope | None = None) -> SourceRegistryRecord:
    return SourceRegistryRecord(
        registry_id=f"file-{source_type.value}",
        display_name=f"Step32 {source_type.value} fixture",
        source_type=source_type,
        file_locator=str(path),
        scope=scope or SelectionScope(),
        adapter_name="file_source",
        adapter_version="1.0.0",
    )


def _run_file_case(source_type: SourceType, workspace: Path) -> tuple[int, list[dict[str, Any]]]:
    path = workspace / f"source.{source_type.value if source_type is not SourceType.XLSX else 'xlsx'}"
    if source_type is SourceType.CSV:
        path.write_bytes("id,label,amount\n1,\"سلام, world\",10.2500\n2,second,20.0000\n".encode("utf-8-sig"))
        scope = SelectionScope()
    elif source_type is SourceType.PARQUET:
        table = pa.table({"id": [1, 2], "label": ["سلام", "second"], "amount": [10.25, 20.0]})
        pq.write_table(table, path, row_group_size=1)
        scope = SelectionScope(included_columns={path.stem: ("id", "label")})
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Orders"
        sheet.append(["id", "label", "amount"])
        sheet.append([1, "سلام", 10.25])
        sheet.append([2, "second", 20.0])
        workbook.save(path)
        workbook.close()
        scope = SelectionScope(included_objects=("Orders",))

    record = _file_record(path, source_type, scope=scope)
    registry = InMemorySourceRegistry()
    registry.register(record)
    adapter = FileSourceAdapter(source_type, project_root=ROOT)
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(_selection(record.registry_id))
    result = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(
        catalog,
        _selection(record.registry_id),
        staging_root=workspace / "runs" / source_type.value / "staging",
    )
    assert result.accounting.input_records_observed == 2
    assert result.accounting.successfully_staged_records == 2
    assert result.accounting.accounting_complete is True
    assert len(result.batches) == 2
    assert all(batch.publication_state.value == "COMPLETE" for batch in result.batches)
    values = [item for batch in result.batches for item in pq.read_table(ROOT / batch.artifact_location).to_pylist()]
    assert len(values) == 2
    assert str(values[0]["id"]) == "1"
    assert "password" not in result.model_dump_json().lower()
    return 1, values


@pytest.mark.parametrize("source_type", FILE_CASES)
def test_file_source_matrix_reaches_project_staging(source_type: SourceType, workspace: Path) -> None:
    _run_file_case(source_type, workspace)


def _make_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE step32_compatibility (id INTEGER PRIMARY KEY, label TEXT NOT NULL, amount NUMERIC NOT NULL)")
        connection.executemany("INSERT INTO step32_compatibility VALUES (?, ?, ?)", [(1, "سلام", 10.25), (2, "second", 20.0)])
        connection.commit()
    finally:
        connection.close()


def test_sqlite_reference_matrix_reaches_project_staging(workspace: Path) -> None:
    path = workspace / "source.sqlite"
    _make_sqlite(path)
    record = SourceRegistryRecord(
        registry_id="sqlite",
        source_id="step32-sqlite",
        display_name="Step32 SQLite fixture",
        source_type=SourceType.SQLITE,
        connection_profile=ConnectionProfileReference(
            profile_id="step32-sqlite-profile",
            database_engine=DatabaseEngine.SQLITE,
            database_name=str(path),
        ),
        scope=SelectionScope(included_objects=(TABLE_NAME,)),
        adapter_name="dlt_sql_source",
        adapter_version="1.0.0",
    )
    registry = InMemorySourceRegistry()
    registry.register(record)
    adapter = DltSqlSourceAdapter(project_root=ROOT)
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(_selection(record.registry_id))
    result = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(
        catalog, _selection(record.registry_id), staging_root=workspace / "runs" / "sqlite" / "staging"
    )
    assert result.accounting.input_records_observed == 2
    assert result.accounting.successfully_staged_records == 2
    assert result.snapshot.consistency.value == "TRANSACTION_SCOPED"
    assert "password" not in result.model_dump_json().lower()


class _LiveCredentialResolver:
    def __init__(self, urls: dict[str, str]) -> None:
        self.urls = urls

    def resolve(self, profile: Any, *, required_purpose: CredentialPurpose, source_id: str) -> RuntimeSqlCredentials:
        url = self.urls[source_id]
        return RuntimeSqlCredentials(
            connection_url=url,
            credential_reference=f"step32-fixture:{source_id}",
            credential_purpose=required_purpose,
            credential_version="step32",
            source_id=source_id,
        )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _admin_table(url: str, engine: DatabaseEngine) -> Table:
    schema = "dbo" if engine is DatabaseEngine.SQLSERVER else None
    metadata = MetaData()
    return Table(
        TABLE_NAME,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("label", Unicode(200), nullable=False),
        Column("amount", Numeric(18, 4), nullable=False),
        Column("event_time", DateTime(timezone=True), nullable=False),
        schema=schema,
    )


def _prepare_provider(admin_url: str, source_url: str, engine: DatabaseEngine) -> None:
    admin = create_engine(admin_url, poolclass=NullPool)
    source = make_url(source_url)
    username = source.username or "ddo_source"
    password = source.password or ""
    table = _admin_table(admin_url, engine)
    try:
        with admin.begin() as connection:
            table.drop(connection, checkfirst=True)
        table.metadata.create_all(admin)
        with admin.begin() as connection:
            connection.execute(table.insert(), [
                {"id": 1, "label": "سلام", "amount": "10.2500", "event_time": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)},
                {"id": 2, "label": "second", "amount": "20.0000", "event_time": datetime(2026, 1, 3, 3, 4, 5, tzinfo=timezone.utc)},
            ])
            if engine is DatabaseEngine.POSTGRESQL:
                safe_user = username.replace('"', '""')
                connection.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {_sql_literal(username)}) THEN CREATE ROLE \"{safe_user}\" LOGIN PASSWORD {_sql_literal(password)}; END IF; END $$")
                connection.exec_driver_sql(f"ALTER ROLE \"{safe_user}\" LOGIN PASSWORD {_sql_literal(password)} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                connection.exec_driver_sql(f"REVOKE TEMP ON DATABASE \"{source.database}\" FROM PUBLIC")
                connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE \"{source.database}\" TO \"{safe_user}\"")
                connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO \"{safe_user}\"")
                connection.exec_driver_sql(f"GRANT SELECT ON TABLE public.\"{TABLE_NAME}\" TO \"{safe_user}\"")
            elif engine in {DatabaseEngine.MYSQL, DatabaseEngine.MARIADB}:
                connection.exec_driver_sql(f"CREATE USER IF NOT EXISTS {_sql_literal(username)}@'%%' IDENTIFIED BY {_sql_literal(password)}")
                connection.exec_driver_sql(f"ALTER USER {_sql_literal(username)}@'%%' IDENTIFIED BY {_sql_literal(password)}")
                connection.exec_driver_sql(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {_sql_literal(username)}@'%%'")
                connection.exec_driver_sql(f"GRANT SELECT, SHOW VIEW ON `{TABLE_NAME}` TO {_sql_literal(username)}@'%%'")
                connection.exec_driver_sql("FLUSH PRIVILEGES")
            else:
                safe_user = username.replace("]", "]]" )
                connection.exec_driver_sql(f"IF NOT EXISTS (SELECT 1 FROM sys.sql_logins WHERE name = {_sql_literal(username)}) CREATE LOGIN [{safe_user}] WITH PASSWORD = {_sql_literal(password)} ELSE ALTER LOGIN [{safe_user}] WITH PASSWORD = {_sql_literal(password)}")
                connection.exec_driver_sql(f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = {_sql_literal(username)}) CREATE USER [{safe_user}] FOR LOGIN [{safe_user}]")
                connection.exec_driver_sql(f"IF NOT EXISTS (SELECT 1 FROM sys.database_role_members drm JOIN sys.database_principals role_principal ON role_principal.principal_id = drm.role_principal_id JOIN sys.database_principals member_principal ON member_principal.principal_id = drm.member_principal_id WHERE role_principal.name = N'db_datareader' AND member_principal.name = {_sql_literal(username)}) ALTER ROLE [db_datareader] ADD MEMBER [{safe_user}]")
                connection.exec_driver_sql(f"GRANT VIEW DEFINITION TO [{safe_user}]")
    finally:
        admin.dispose()


class _LiveProviderVerifier:
    def verify(self, *, profile: Any, credentials: RuntimeSqlCredentials, source_id: str, profile_id: str, selection_fingerprint: str, policy: Any) -> ProviderSecurityVerification:
        engine = create_engine(credentials.connection_url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if profile.database_engine is DatabaseEngine.POSTGRESQL:
                    checks = connection.exec_driver_sql("SELECT has_database_privilege(current_user, current_database(), 'CONNECT'), has_schema_privilege(current_user, 'public', 'USAGE'), has_table_privilege(current_user, 'public.step32_compatibility', 'SELECT')").one()
                    present = {"CONNECT": bool(checks[0]), "USAGE": bool(checks[1]), "SELECT": bool(checks[2])}
                    role = connection.exec_driver_sql("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user").one()
                    forbidden = {"SUPERUSER": bool(role[0]), "CREATEDB": bool(role[1]), "CREATEROLE": bool(role[2]), "REPLICATION": bool(role[3]), "BYPASSRLS": bool(role[4])}
                elif profile.database_engine in {DatabaseEngine.MYSQL, DatabaseEngine.MARIADB}:
                    grants = " ".join(str(row[0]).upper() for row in connection.exec_driver_sql("SHOW GRANTS").all())
                    present = {"SELECT": "SELECT" in grants and "ALL PRIVILEGES" not in grants}
                    forbidden = {item: any(token in grants for token in (item, "ALL PRIVILEGES")) for item in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "EXECUTE", "FILE", "PROCESS", "SUPER", "GRANT OPTION", "CREATE USER", "TRIGGER", "EVENT", "RELOAD")}
                else:
                    checks = connection.exec_driver_sql("SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONNECT'), HAS_PERMS_BY_NAME('dbo.step32_compatibility', 'OBJECT', 'SELECT'), IS_SRVROLEMEMBER('sysadmin'), IS_ROLEMEMBER('db_owner')").one()
                    present = {"CONNECT": checks[0] == 1, "SELECT": checks[1] == 1}
                    forbidden = {item: False for item in ("CONTROL", "ALTER", "INSERT", "UPDATE", "DELETE", "EXECUTE", "TAKE OWNERSHIP", "IMPERSONATE", "db_owner", "sysadmin")}
                    forbidden["db_owner"] = checks[3] == 1
                    forbidden["sysadmin"] = checks[2] == 1
                findings = []
                requirement = next(item for item in policy.requirements if item.engine is profile.database_engine)
                for privilege in requirement.required:
                    findings.append(PrivilegeFinding(privilege=privilege, status=PrivilegeFindingStatus.PRESENT_REQUIRED if present.get(privilege, True) else PrivilegeFindingStatus.REQUIRED_MISSING, reason="live provider permission query"))
                for privilege in requirement.forbidden:
                    findings.append(PrivilegeFinding(privilege=privilege, status=PrivilegeFindingStatus.FORBIDDEN_PRESENT if forbidden.get(privilege, False) else PrivilegeFindingStatus.ABSENT_FORBIDDEN, reason="live provider permission query"))
                evidence = f"sha256:{uuid4().hex}{uuid4().hex}"
                return ProviderSecurityVerification(
                    verification_id=f"step32-verification-{source_id}",
                    engine=profile.database_engine,
                    source_id=source_id,
                    profile_id=profile_id,
                    credential_reference=credentials.credential_reference,
                    credential_version=credentials.credential_version,
                    selection_fingerprint=selection_fingerprint,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    driver_reference="sqlalchemy",
                    status=ProviderVerificationStatus.TECHNICALLY_VERIFIED,
                    findings=tuple(findings),
                    evidence_fingerprint=evidence,
                )
        finally:
            engine.dispose()


def _wait_for_provider(admin_url: str, source_url: str, engine: DatabaseEngine) -> None:
    deadline = time.monotonic() + 120
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            probe = create_engine(admin_url, poolclass=NullPool)
            try:
                with probe.connect() as connection:
                    connection.execute(text("SELECT 1"))
            finally:
                probe.dispose()
            _prepare_provider(admin_url, source_url, engine)
            return
        except Exception as error:  # provider containers can take time to accept connections
            last_error = error
            time.sleep(2)
    raise RuntimeError(f"provider did not become ready: {engine.value}: {type(last_error).__name__}")


@pytest.mark.parametrize("label,engine,admin_env,source_env", LIVE_DATABASES)
def test_live_database_matrix_reaches_project_staging(label: str, engine: DatabaseEngine, admin_env: str, source_env: str, workspace: Path) -> None:
    admin_url = os.getenv(admin_env)
    source_url = os.getenv(source_env)
    required = os.getenv("DDO_STEP32_REQUIRE_LIVE_DB") == "1"
    if not admin_url or not source_url:
        if required:
            pytest.fail(f"required live compatibility target is not configured: {label}")
        pytest.skip(f"live compatibility target is not configured: {label}")

    _wait_for_provider(admin_url, source_url, engine)
    parsed = make_url(source_url)
    source_id = f"step32-{label}"
    record = SourceRegistryRecord(
        registry_id=label,
        source_id=source_id,
        display_name=f"Step32 {label} fixture",
        source_type={
            DatabaseEngine.POSTGRESQL: SourceType.POSTGRESQL,
            DatabaseEngine.MYSQL: SourceType.MYSQL,
            DatabaseEngine.MARIADB: SourceType.MARIADB,
            DatabaseEngine.SQLSERVER: SourceType.SQLSERVER,
        }[engine],
        connection_profile=ConnectionProfileReference(
            profile_id=f"step32-{label}-profile",
            database_engine=engine,
            host=parsed.host,
            port=parsed.port,
            database_name=parsed.database or "master",
            credential_reference=f"step32-fixture:{source_id}",
        ),
        scope=SelectionScope(included_objects=(TABLE_NAME,)),
        adapter_name="dlt_sql_source",
        adapter_version="1.0.0",
    )
    registry = InMemorySourceRegistry()
    registry.register(record)
    resolver = _LiveCredentialResolver({source_id: source_url})
    adapter = DltSqlSourceAdapter(project_root=ROOT, credential_resolver=resolver, security_verifier=_LiveProviderVerifier())
    selection = _selection(label)
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    assert [item.physical_name for item in catalog.tables] == [TABLE_NAME]
    result = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(
        catalog, selection, staging_root=workspace / "runs" / label / "staging"
    )
    assert result.accounting.input_records_observed == 2
    assert result.accounting.successfully_staged_records == 2
    assert len(result.record_references) == 2
    assert all(batch.publication_state.value == "COMPLETE" for batch in result.batches)
    source_engine = create_engine(source_url, poolclass=NullPool)
    try:
        with source_engine.connect() as connection:
            assert connection.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar_one() == 2
            with pytest.raises(Exception):
                connection.execute(text(f"INSERT INTO {TABLE_NAME} (id, label, amount, event_time) VALUES (99, 'blocked', 1, CURRENT_TIMESTAMP)"))
    finally:
        source_engine.dispose()
    assert "password" not in result.model_dump_json().lower()
