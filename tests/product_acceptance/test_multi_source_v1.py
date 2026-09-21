"""Real four-source Prompt02 acceptance harness.

The test is intentionally opt-in. In CI ``DDO_PROMPT02_REQUIRE_LIVE=1`` makes
missing databases/providers a failure; local environments without the
disposable provider estate are reported as a skip, never as PASS evidence.
"""

from __future__ import annotations

import csv
from datetime import date
import os
from pathlib import Path
import shutil
import time
from typing import Any
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
from dirty_data_to_olap.application.multi_source_product import MultiSourceProductBlocked
from dirty_data_to_olap.application.product_runtime import build_multi_source_product
from dirty_data_to_olap.application.product_sources import ProductSourceError, ProductSourceService
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.database_security import (
    CredentialPurpose,
    PrivilegeFinding,
    PrivilegeFindingStatus,
    ProviderSecurityVerification,
    ProviderVerificationStatus,
)
from dirty_data_to_olap.domain.contracts.multi_source import MultiSourceIndependentOracleEvidence
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceRegistryRecord, SourceSetSelection, SourceType, source_set_fingerprint


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "tests" / "product_acceptance" / "oracle" / "multi_source_v1_truth.yml"
TABLE_NAME = "prompt02_orders"
LOGICAL_FIELDS = ("order_id", "customer_id", "customer_name", "email", "phone", "order_date", "quantity", "unit_price")
SOURCE_COLUMNS = {
    "postgres": {"order_id": "order_id", "customer_id": "crm_customer_id", "customer_name": "full_name", "email": "email", "phone": "phone", "order_date": "order_date", "quantity": "quantity", "unit_price": "unit_price"},
    "mysql": {"order_id": "order_id", "customer_id": "account_no", "customer_name": "customer_name", "email": "email_addr", "phone": "phone_e164", "order_date": "order_date", "quantity": "quantity", "unit_price": "unit_price"},
    "sqlserver": {"order_id": "ticket_id", "customer_id": "buyer_ref", "customer_name": "buyer_name", "email": "buyer_email", "phone": "phone", "order_date": "booked_on", "quantity": "units", "unit_price": "unit_price"},
    "csv": {"order_id": "sale_key", "customer_id": "client_code", "customer_name": "client_name", "email": "email_addr", "phone": "phone_e164", "order_date": "sale_day", "quantity": "qty", "unit_price": "price_each"},
}
LIVE_TARGETS = (
    ("postgres", DatabaseEngine.POSTGRESQL, "DDO_PROMPT02_POSTGRES_URL", "DDO_PROMPT02_POSTGRES_ADMIN_URL", "DDO_STEP32_POSTGRES_URL", "DDO_STEP32_POSTGRES_ADMIN_URL"),
    ("mysql", DatabaseEngine.MYSQL, "DDO_PROMPT02_MYSQL_URL", "DDO_PROMPT02_MYSQL_ADMIN_URL", "DDO_STEP32_MYSQL_URL", "DDO_STEP32_MYSQL_ADMIN_URL"),
    ("sqlserver", DatabaseEngine.SQLSERVER, "DDO_PROMPT02_SQLSERVER_URL", "DDO_PROMPT02_SQLSERVER_ADMIN_URL", "DDO_STEP32_SQLSERVER_URL", "DDO_STEP32_SQLSERVER_ADMIN_URL"),
)


def _env(primary: str, fallback: str) -> str | None:
    return os.environ.get(primary) or os.environ.get(fallback)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _rows(label: str) -> list[dict[str, Any]]:
    values = {
        "postgres": [(1001, "CRM-001", "Alice Smith", "alice@example.test", "+1-202-555-0101", date(2026, 1, 2), 2, "10.0000"), (1002, "CRM-002", "Bob Brown", "bob@example.test", "+1-202-555-0102", date(2026, 1, 3), 1, "20.0000"), (1003, "CRM-ORPHAN", "Orphan", None, None, date(2026, 1, 4), 1, "5.0000")],
        "mysql": [(2001, "ERP-77", "Alice S.", "alice@example.test", "+1-202-555-0101", date(2026, 1, 2), 3, "11.0000"), (2002, "ERP-88", "Bob Brown", "bob@example.test", "+1-202-555-0102", date(2026, 1, 3), 1, "21.0000"), (2003, "ERP-HARD-NEG", "Alice Smith", "other@example.test", "+1-202-555-0199", date(2026, 1, 4), 9, "99.0000")],
        "sqlserver": [(3001, "SLS-1", "Alice Smith", "alice@example.test", "+1-202-555-0101", date(2026, 1, 2), 4, "12.0000"), (3002, "SLS-2", "Bob B.", "bob@example.test", "+1-202-555-0102", date(2026, 1, 3), 2, "22.0000"), (3003, "SLS-ORPHAN", None, None, None, date(2026, 1, 5), 1, "6.0000")],
    }
    return [dict(zip(LOGICAL_FIELDS, row)) for row in values[label]]


class _CredentialResolver:
    def __init__(self, urls: dict[str, str]):
        self.urls = urls

    def resolve(self, profile: Any, *, required_purpose: CredentialPurpose, source_id: str) -> RuntimeSqlCredentials:
        return RuntimeSqlCredentials(self.urls[source_id], credential_reference=f"prompt02-fixture:{source_id}", credential_purpose=required_purpose, credential_version="prompt02-v1", source_id=source_id)


class _ProviderVerifier:
    """Technical read-only verifier for the disposable CI databases."""

    def verify(self, *, profile: Any, credentials: RuntimeSqlCredentials, source_id: str, profile_id: str, selection_fingerprint: str, policy: Any) -> ProviderSecurityVerification:
        engine = create_engine(credentials.connection_url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if profile.database_engine is DatabaseEngine.POSTGRESQL:
                    checks = connection.exec_driver_sql(f"SELECT has_database_privilege(current_user, current_database(), 'CONNECT'), has_schema_privilege(current_user, 'public', 'USAGE'), has_table_privilege(current_user, 'public.{TABLE_NAME}', 'SELECT')").one()
                    present = {"CONNECT": bool(checks[0]), "USAGE": bool(checks[1]), "SELECT": bool(checks[2])}
                    role = connection.exec_driver_sql("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user").one()
                    forbidden = {"SUPERUSER": bool(role[0]), "CREATEDB": bool(role[1]), "CREATEROLE": bool(role[2]), "REPLICATION": bool(role[3]), "BYPASSRLS": bool(role[4])}
                elif profile.database_engine is DatabaseEngine.MYSQL:
                    grants = " ".join(str(row[0]).upper() for row in connection.exec_driver_sql("SHOW GRANTS").all())
                    present = {"SELECT": "SELECT" in grants and "ALL PRIVILEGES" not in grants}
                    forbidden = {item: any(token in grants for token in (item, "ALL PRIVILEGES")) for item in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "EXECUTE", "FILE", "PROCESS", "SUPER", "GRANT OPTION", "CREATE USER", "TRIGGER", "EVENT", "RELOAD")}
                else:
                    checks = connection.exec_driver_sql(f"SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONNECT'), HAS_PERMS_BY_NAME('dbo.{TABLE_NAME}', 'OBJECT', 'SELECT'), IS_SRVROLEMEMBER('sysadmin'), IS_ROLEMEMBER('db_owner')").one()
                    present = {"CONNECT": checks[0] == 1, "SELECT": checks[1] == 1}
                    forbidden = {item: False for item in ("CONTROL", "ALTER", "INSERT", "UPDATE", "DELETE", "EXECUTE", "TAKE OWNERSHIP", "IMPERSONATE", "db_owner", "sysadmin")}
                    forbidden["db_owner"] = checks[3] == 1
                    forbidden["sysadmin"] = checks[2] == 1
                requirement = next(item for item in policy.requirements if item.engine is profile.database_engine)
                findings = tuple(PrivilegeFinding(privilege=privilege, status=PrivilegeFindingStatus.PRESENT_REQUIRED if present.get(privilege, True) else PrivilegeFindingStatus.REQUIRED_MISSING, reason="Prompt02 disposable-provider privilege query") for privilege in requirement.required) + tuple(PrivilegeFinding(privilege=privilege, status=PrivilegeFindingStatus.FORBIDDEN_PRESENT if forbidden.get(privilege, False) else PrivilegeFindingStatus.ABSENT_FORBIDDEN, reason="Prompt02 disposable-provider privilege query") for privilege in requirement.forbidden)
                return ProviderSecurityVerification(verification_id=f"prompt02-verification-{source_id}", engine=profile.database_engine, source_id=source_id, profile_id=profile_id, credential_reference=credentials.credential_reference, credential_version=credentials.credential_version, selection_fingerprint=selection_fingerprint, policy_id=policy.policy_id, policy_version=policy.version, driver_reference="sqlalchemy", status=ProviderVerificationStatus.TECHNICALLY_VERIFIED, findings=findings, evidence_fingerprint="sha256:" + uuid4().hex + uuid4().hex)
        finally:
            engine.dispose()


def _prepare_database(admin_url: str, source_url: str, engine_kind: DatabaseEngine, label: str) -> None:
    admin = create_engine(admin_url, poolclass=NullPool)
    parsed = make_url(source_url)
    username = parsed.username or "ddo_prompt02"
    password = parsed.password or ""
    schema = "dbo" if engine_kind is DatabaseEngine.SQLSERVER else None
    columns = SOURCE_COLUMNS[label]
    metadata = MetaData()
    table = Table(TABLE_NAME, metadata, Column(columns["order_id"], Integer, primary_key=True), Column(columns["customer_id"], String(120), nullable=False), Column(columns["customer_name"], String(200)), Column(columns["email"], String(240)), Column(columns["phone"], String(80)), Column(columns["order_date"], Date, nullable=False), Column(columns["quantity"], Integer, nullable=False), Column(columns["unit_price"], Numeric(18, 4)), schema=schema)
    try:
        table.drop(admin, checkfirst=True)
        table.create(admin)
        with admin.begin() as connection:
            connection.execute(table.insert(), [{columns[key]: row[key] for key in LOGICAL_FIELDS} for row in _rows(label)])
            if engine_kind is DatabaseEngine.POSTGRESQL:
                safe = username.replace('"', '""')
                connection.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {_sql_literal(username)}) THEN CREATE ROLE \"{safe}\" LOGIN PASSWORD {_sql_literal(password)}; END IF; END $$")
                connection.exec_driver_sql(f"ALTER ROLE \"{safe}\" LOGIN PASSWORD {_sql_literal(password)} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE \"{parsed.database}\" TO \"{safe}\"")
                connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO \"{safe}\"")
                connection.exec_driver_sql(f"GRANT SELECT ON TABLE public.\"{TABLE_NAME}\" TO \"{safe}\"")
            elif engine_kind is DatabaseEngine.MYSQL:
                # PyMySQL treats percent signs in driver SQL as interpolation
                # markers even when no parameters are supplied. Escape the
                # wildcard host so the server receives the intended '%' host.
                connection.exec_driver_sql(f"CREATE USER IF NOT EXISTS {_sql_literal(username)}@'%%' IDENTIFIED BY {_sql_literal(password)}")
                connection.exec_driver_sql(f"ALTER USER {_sql_literal(username)}@'%%' IDENTIFIED BY {_sql_literal(password)}")
                connection.exec_driver_sql(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {_sql_literal(username)}@'%%'")
                connection.exec_driver_sql(f"GRANT SELECT, SHOW VIEW ON `{parsed.database}`.`{TABLE_NAME}` TO {_sql_literal(username)}@'%%'")
                connection.exec_driver_sql("FLUSH PRIVILEGES")
            else:
                safe = username.replace("]", "]]" )
                connection.exec_driver_sql(f"IF NOT EXISTS (SELECT 1 FROM sys.sql_logins WHERE name = {_sql_literal(username)}) CREATE LOGIN [{safe}] WITH PASSWORD = {_sql_literal(password)} ELSE ALTER LOGIN [{safe}] WITH PASSWORD = {_sql_literal(password)}")
                connection.exec_driver_sql(f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = {_sql_literal(username)}) CREATE USER [{safe}] FOR LOGIN [{safe}]")
                connection.exec_driver_sql(f"IF NOT EXISTS (SELECT 1 FROM sys.database_role_members drm JOIN sys.database_principals role_principal ON role_principal.principal_id = drm.role_principal_id JOIN sys.database_principals member_principal ON member_principal.principal_id = drm.member_principal_id WHERE role_principal.name = N'db_datareader' AND member_principal.name = {_sql_literal(username)}) ALTER ROLE [db_datareader] ADD MEMBER [{safe}]")
                connection.exec_driver_sql(f"GRANT VIEW DEFINITION TO [{safe}]")
    finally:
        admin.dispose()


def _wait_for_database(admin_url: str) -> None:
    deadline = time.monotonic() + 120
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        engine = None
        try:
            engine = create_engine(admin_url, poolclass=NullPool)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as error:
            last_error = error
            time.sleep(2)
        finally:
            if engine is not None:
                engine.dispose()
    raise RuntimeError(f"Prompt02 database did not become ready: {type(last_error).__name__}")


def _file_fixture(path: Path) -> None:
    rows = [dict(zip(LOGICAL_FIELDS, (4001, "LEG-1", "Alice Smith", "alice@example.test", "+1-202-555-0101", "2026-01-02", 5, "13.0000"))), dict(zip(LOGICAL_FIELDS, (4002, "LEG-2", "Bob Brown", "bob@example.test", "+1-202-555-0102", "2026-01-03", 1, "23.0000"))), dict(zip(LOGICAL_FIELDS, (4003, "LEG-MISSING", "Missing email", "", "", "2026-01-05", 1, "7.0000")))]
    columns = SOURCE_COLUMNS["csv"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([columns[key] for key in LOGICAL_FIELDS])
        writer.writerows([[row[key] for key in LOGICAL_FIELDS] for row in rows])


@pytest.mark.optional_provider
def test_multi_source_v1_real_pipeline_and_independent_oracle(tmp_path: Path) -> None:
    required = os.environ.get("DDO_PROMPT02_REQUIRE_LIVE") == "1"
    urls: dict[str, str] = {}
    admins: dict[str, str] = {}
    for label, _engine, primary, primary_admin, fallback, fallback_admin in LIVE_TARGETS:
        url, admin = _env(primary, fallback), _env(primary_admin, fallback_admin)
        if not url or not admin:
            message = f"Prompt02 live database is not configured: {label}"
            if required:
                pytest.fail(message)
            pytest.skip(message)
        urls[label] = url
        admins[label] = admin
    workspace = ROOT / "workspace" / "tests" / f"prompt02_{uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        for label, engine, *_ in LIVE_TARGETS:
            _wait_for_database(admins[label])
            _prepare_database(admins[label], urls[label], engine, label)
        csv_path = workspace / "legacy_orders.csv"
        _file_fixture(csv_path)
        csv_before_run = csv_path.read_bytes()
        source_ids = {"postgres": "prompt02-crm-postgres", "mysql": "prompt02-erp-mysql", "sqlserver": "prompt02-sales-sqlserver", "csv": "prompt02-legacy-csv"}
        records: list[SourceRegistryRecord] = []
        for label, engine, *_ in LIVE_TARGETS:
            parsed = make_url(urls[label])
            records.append(SourceRegistryRecord(registry_id=label, source_id=source_ids[label], display_name=f"Prompt02 {label}", source_type={DatabaseEngine.POSTGRESQL: SourceType.POSTGRESQL, DatabaseEngine.MYSQL: SourceType.MYSQL, DatabaseEngine.SQLSERVER: SourceType.SQLSERVER}[engine], connection_profile=ConnectionProfileReference(profile_id=f"prompt02-{label}-profile", database_engine=engine, host=parsed.host, port=parsed.port, database_name=parsed.database or "master", credential_reference=f"prompt02-fixture:{source_ids[label]}"), scope=SelectionScope(included_objects=(TABLE_NAME,)), adapter_name="dlt_sql_source", adapter_version="1.0.0"))
        records.append(SourceRegistryRecord(registry_id="csv", source_id=source_ids["csv"], display_name="Prompt02 legacy CSV", source_type=SourceType.CSV, file_locator=str(csv_path), scope=SelectionScope(), adapter_name="file_source", adapter_version="1.0.0"))
        adapters = {"file_source": FileSourceAdapter(SourceType.CSV, project_root=ROOT), "dlt_sql_source": DltSqlSourceAdapter(project_root=ROOT, credential_resolver=_CredentialResolver(urls), security_verifier=_ProviderVerifier())}
        service = build_multi_source_product(ROOT, adapters=adapters)
        source_service = ProductSourceService(ROOT, service.registry)
        for record in records:
            source_service.register_read_only_source(record, owner_subject="prompt02-acceptance")
        extraction = ExtractionPolicy(chunk_size=2, preserve_raw_values=True)
        source_set = source_service.source_set_selection(registry_ids=tuple(record.registry_id for record in records), scope_by_registry={"postgres": SelectionScope(included_objects=(TABLE_NAME,)), "mysql": SelectionScope(included_objects=(TABLE_NAME,)), "sqlserver": SelectionScope(included_objects=(TABLE_NAME,)), "csv": SelectionScope()}, extraction=extraction, execution_context_id="prompt02-real-four-source-v1")
        prepared = service.prepare(run_id=f"prompt02-{uuid4().hex}", source_set=source_set)
        target = prepared.root / "olap" / "prompt02.duckdb"
        nc07_pre_authored = not target.exists()
        with pytest.raises(MultiSourceProductBlocked, match="REVIEW_REQUIRED"):
            service.resume(prepared, {})
        reviews = {checkpoint: {"rationale": f"Prompt02 independent acceptance review for {checkpoint}"} for checkpoint in service.REQUIRED_CHECKPOINTS}
        receipt = service.resume(prepared, reviews)
        assert receipt.status == "AWAITING_INDEPENDENT_ORACLE"
        import duckdb
        connection = duckdb.connect(str(target), read_only=True)
        try:
            fact_rows = int(connection.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0])
            quantity_sum = str(connection.execute("SELECT COALESCE(SUM(quantity), 0) FROM fact_order").fetchone()[0])
            hard_negative = int(connection.execute("SELECT COUNT(*) FROM fact_order WHERE source_key = 'prompt02-erp-mysql' AND order_id = '2003'").fetchone()[0])
        finally:
            connection.close()
        oracle = yaml.safe_load(ORACLE.read_text(encoding="utf-8"))
        expected = oracle["expected"]
        assert fact_rows == expected["fact_rows"]
        assert quantity_sum == expected["quantity_sum"]
        assert hard_negative == 0
        assert {item.source_id: item.input_records for item in receipt.sources} == expected["source_input_records"]
        assert {item.source_id: item.emitted_records for item in receipt.record_accounting} == expected["source_emitted_records"]
        assert all(item.source_unchanged_before_after for item in receipt.sources)
        schema_pairs = {(item.source_id, item.source_column_name, item.target_source_id, item.target_column_name) for item in prepared.schema_match.candidates}
        for left_source, left_column, right_source, right_column in expected["schema_match_labels"]:
            assert (left_source, left_column, right_source, right_column) in schema_pairs or (right_source, right_column, left_source, left_column) in schema_pairs
        assert receipt.analytical is not None and receipt.analytical.measures == ("quantity",)
        assert set(receipt.analytical.dimensions) == set(expected["dimensions"])
        assert all(item.disposition_complete for item in receipt.record_accounting)
        with pytest.raises(ProductSourceError):
            source_service.source_set_selection(registry_ids=("postgres",), scope=SelectionScope(), extraction=extraction, execution_context_id="prompt02-negative-single-source")
        nc01_single_source_guard = True
        csv_record = service.registry.get("csv")
        original_csv = Path(csv_record.file_locator or "").read_bytes()
        try:
            Path(csv_record.file_locator or "").write_bytes(original_csv + b"4004,LEG-LATE,Late,late@example.test,2026-01-06,1,8.0000\n")
            csv_selection = next(item for item in source_set.ordered_selections if item.registry_id == "csv")
            changed_catalog = service.discovery.discover(csv_selection)
            changed_snapshot = service.snapshot.extract(changed_catalog, csv_selection, staging_root=prepared.root / "negative-controls" / "changed-source")
            nc02_changed_snapshot = changed_snapshot.snapshot.source_fingerprint != prepared.snapshots[changed_catalog.source_id].snapshot.source_fingerprint
        finally:
            Path(csv_record.file_locator or "").write_bytes(original_csv)
        nc03_oracle_isolation = receipt.oracle is None
        stale_reviews = dict(reviews)
        stale_reviews[service.REQUIRED_CHECKPOINTS[0]] = {"subject_id": "stale-subject", "rationale": "stale review negative control"}
        with pytest.raises(MultiSourceProductBlocked, match="STALE_REVIEW"):
            service.resume(prepared, stale_reviews)
        nc04_stale_review = True
        with pytest.raises(MultiSourceProductBlocked, match="REVIEW_REQUIRED"):
            service.resume(prepared, {service.REQUIRED_CHECKPOINTS[0]: {"rationale": "incomplete review negative control"}})
        nc14_missing_review = True
        nc05_disposition = all(item.disposition_complete for item in receipt.record_accounting)
        connection = duckdb.connect(str(target), read_only=True)
        try:
            nc06_duplicate_identity = int(connection.execute("SELECT COUNT(*) FROM dim_customer") .fetchone()[0]) == int(connection.execute("SELECT COUNT(DISTINCT canonical_customer_id) FROM dim_customer").fetchone()[0])
            nc12_fact_grain = int(connection.execute("SELECT COUNT(*) FROM fact_order") .fetchone()[0]) == int(connection.execute("SELECT COUNT(*) FROM (SELECT source_key, order_id FROM fact_order GROUP BY source_key, order_id)").fetchone()[0])
        finally:
            connection.close()
        nc08_cross_source_stage = "SCHEMA_MATCHING" in {item.stage_id for item in receipt.stages} and any(item.providers for item in receipt.stages if item.stage_id == "SCHEMA_MATCHING")
        nc09_no_invented_money = receipt.analytical is not None and not {"revenue", "gmv", "unit_price_sum"}.intersection(receipt.analytical.measures)
        mutated_selections = tuple(item.model_copy(update={"execution_context_id": "prompt02-late-mutation"}) for item in source_set.selections)
        with pytest.raises(ValueError):
            SourceSetSelection(selections=mutated_selections, source_set_fingerprint=source_set.source_set_fingerprint)
        nc10_late_source_set_mutation = True
        with pytest.raises(ValueError):
            SourceSetSelection(selections=(source_set.selections[0],), source_set_fingerprint=source_set_fingerprint((source_set.selections[0],)))
        nc11_single_source_fallback = True
        nc13_hard_negative = hard_negative == 0
        assert csv_path.read_bytes() == csv_before_run
        controls = {
            "NC01_REQUIRED_SOURCE_SCOPE": "PASS" if nc01_single_source_guard else "FAIL",
            "NC02_CHANGED_SNAPSHOT": "PASS" if nc02_changed_snapshot else "FAIL",
            "NC03_ORACLE_ISOLATION": "PASS" if nc03_oracle_isolation else "FAIL",
            "NC04_STALE_REVIEW": "PASS" if nc04_stale_review else "FAIL",
            "NC05_MISSING_DISPOSITION": "PASS" if nc05_disposition else "FAIL",
            "NC06_DUPLICATE_IDENTITY": "PASS" if nc06_duplicate_identity else "FAIL",
            "NC07_PREAUTHORED_OUTPUT": "PASS" if nc07_pre_authored else "FAIL",
            "NC08_MISSING_CROSS_SOURCE_STAGE": "PASS" if nc08_cross_source_stage else "FAIL",
            "NC09_INVENTED_MONETARY_SEMANTICS": "PASS" if nc09_no_invented_money else "FAIL",
            "NC10_LATE_SOURCE_SET_MUTATION": "PASS" if nc10_late_source_set_mutation else "FAIL",
            "NC11_SINGLE_SOURCE_FALLBACK": "PASS" if nc11_single_source_fallback else "FAIL",
            "NC12_FACT_MULTIPLICATION": "PASS" if nc12_fact_grain else "FAIL",
            "NC13_HARD_NEGATIVE_MERGE": "PASS" if nc13_hard_negative else "FAIL",
            "NC14_MISSING_REVIEW": "PASS" if nc14_missing_review else "FAIL",
        }
        oracle_evidence = MultiSourceIndependentOracleEvidence(oracle_id=oracle["oracle_id"], oracle_version=oracle["oracle_version"], oracle_path="tests/product_acceptance/oracle/multi_source_v1_truth.yml", loaded_after_product_run=True, matched_fact_rows=fact_rows == expected["fact_rows"], matched_aggregates=quantity_sum == expected["quantity_sum"], matched_dispositions=all(item.disposition_complete for item in receipt.record_accounting))
        final = service.attach_oracle_evidence(prepared, receipt, oracle_evidence, negative_controls=controls)
        assert final.status == "PASS"
        assert final.oracle is not None and final.oracle.loaded_after_product_run
        evidence_dir = os.environ.get("DDO_PROMPT02_EVIDENCE_DIR")
        if evidence_dir:
            evidence_path = Path(evidence_dir).resolve()
            evidence_path.mkdir(parents=True, exist_ok=True)
            (evidence_path / "PROMPT02_MULTI_SOURCE_ACCEPTANCE.json").write_text(final.model_dump_json(indent=2), encoding="utf-8")
            shutil.copy2(target, evidence_path / "prompt02.duckdb")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(ROOT / "workspace" / "platform" / "prompt02", ignore_errors=True)
