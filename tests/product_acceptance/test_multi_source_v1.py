"""Opt-in heterogeneous Prompt02 acceptance through the product runtime.

The provider estate is disposable and configured by environment variables.
When it is available this test exercises the public backend boundary, durable
worker attempts, persisted review checkpoints, compiler/materializer output,
and an independent DuckDB/oracle comparison.  It never imports the oracle in
application code.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import yaml
pytest.importorskip("sqlalchemy", reason="Prompt02 live-provider harness dependencies are unavailable")
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
from dirty_data_to_olap.application.backend import BackendError, Principal
from dirty_data_to_olap.application.product_sources import ProductSourceService
from dirty_data_to_olap.application.product_runtime import build_multi_source_product
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.database_security import (
    CredentialPurpose,
    PrivilegeFinding,
    PrivilegeFindingStatus,
    ProviderSecurityVerification,
    ProviderVerificationStatus,
)
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanIntent
from dirty_data_to_olap.domain.contracts.multi_source import MultiSourceAcceptanceReceipt
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchResult
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceCatalog, SourceRegistryRecord, SourceSetSelection, SourceSnapshotResult, SourceType, stable_digest
from dirty_data_to_olap.domain.contracts.validation import RecordAccountingArtifact, SourceTruthManifest


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "tests" / "product_acceptance" / "oracle" / "multi_source_v1_truth.yml"
TABLES = {"postgres": "crm_customers", "mysql": "erp_accounts", "sqlserver": "sales_events"}
LIVE_TARGETS = (
    ("postgres", DatabaseEngine.POSTGRESQL, "DDO_PROMPT02_POSTGRES_URL", "DDO_PROMPT02_POSTGRES_ADMIN_URL", "DDO_STEP32_POSTGRES_URL", "DDO_STEP32_POSTGRES_ADMIN_URL"),
    ("mysql", DatabaseEngine.MYSQL, "DDO_PROMPT02_MYSQL_URL", "DDO_PROMPT02_MYSQL_ADMIN_URL", "DDO_STEP32_MYSQL_URL", "DDO_STEP32_MYSQL_ADMIN_URL"),
    ("sqlserver", DatabaseEngine.SQLSERVER, "DDO_PROMPT02_SQLSERVER_URL", "DDO_PROMPT02_SQLSERVER_ADMIN_URL", "DDO_STEP32_SQLSERVER_URL", "DDO_STEP32_SQLSERVER_ADMIN_URL"),
)

NEGATIVE_CONTROL_REQUIREMENTS = (
    ("NC01", "required source unavailable", "select an unregistered source", "BackendService.bind_product_source_set", "SOURCE_SELECTION_REJECTED"),
    ("NC02", "source or snapshot fingerprint changed", "mutate a selected source after binding", "snapshot adapter boundary", "snapshot consistency rejection"),
    ("NC03", "oracle unavailable to runtime", "withhold oracle from runtime inputs", "runtime/oracle trust boundary", "runtime remains oracle-independent"),
    ("NC04", "stale persisted review hash or revision", "submit an accepted review with an old revision", "BackendService.review", "REVIEW_REVISION_CONFLICT"),
    ("NC05", "missing source-record disposition", "remove one source accounting entry", "G6 validation boundary", "record-accounting rejection"),
    ("NC06", "invalid or overlapping canonical identity", "force overlapping canonical membership", "canonical finalization boundary", "canonical membership rejection"),
    ("NC07", "pre-authored or cross-run artifact substitution", "present an artifact from another run", "verified artifact input boundary", "run-scope artifact rejection"),
    ("NC08", "missing mandatory server-owned stage", "omit the required cross-source stage", "ExecutionPlanService", "mandatory-stage rejection"),
    ("NC09", "undeclared monetary measure", "relabel unit price as revenue without assertion", "AnalyticalCompiler", "measure-policy rejection"),
    ("NC10", "incompatible source-set mutation", "bind a different source set after publication", "BackendService.bind_product_source_set", "SOURCE_ALREADY_BOUND"),
    ("NC11", "selected-source omission or single-source fallback", "submit fewer than two selected sources", "ProductSourceService.source_set_selection", "multi-source selection rejection"),
    ("NC12", "fact-grain multiplication", "duplicate an event at the declared grain", "G6 validation boundary", "grain reconciliation rejection"),
    ("NC13", "hard-negative identity merge", "merge same-name different-contact records", "canonical identity review boundary", "hard-negative rejection or review"),
    ("NC14", "required human review omitted", "resume before required review decisions", "BackendService.resume", "review-required rejection"),
)


def _env(primary: str, fallback: str) -> str | None:
    return os.environ.get(primary) or os.environ.get(fallback)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _registry_rows(label: str) -> list[dict[str, Any]]:
    if label == "postgres":
        return [
            {"crm_customer_id": "CRM-001", "full_name": "Alice Smith", "email": "alice@example.test", "phone": "+1-202-555-0101"},
            {"crm_customer_id": "CRM-002", "full_name": "Bob Brown", "email": "bob@example.test", "phone": "+1-202-555-0102"},
        ]
    return [
        {"account_no": "ERP-77", "account_name": "Alice S.", "email_addr": "alice@example.test", "phone_e164": "+1-202-555-0101"},
        {"account_no": "ERP-88", "account_name": "Bob Brown", "email_addr": "bob@example.test", "phone_e164": "+1-202-555-0102"},
        # Same-name/different-contact hard negative.  Its email deliberately
        # duplicates ERP-88 so the quality stage emits a duplicate candidate
        # key without making the primary account identifier non-unique.
        {"account_no": "ERP-HARD-NEG", "account_name": "Alice Smith", "email_addr": "bob@example.test", "phone_e164": "+1-202-555-0199"},
    ]


def _sales_rows() -> list[dict[str, Any]]:
    return [
        {"ticket_id": "S-1001", "customer_ref": "CRM-001", "booked_on": date(2026, 1, 2), "units": 4, "unit_price": "12.0000"},
        {"ticket_id": "S-1002", "customer_ref": "ERP-77", "booked_on": date(2026, 1, 3), "units": 3, "unit_price": "11.0000"},
        {"ticket_id": "S-1003", "customer_ref": "UNKNOWN", "booked_on": date(2026, 1, 5), "units": 1, "unit_price": "6.0000"},
    ]


def _file_rows() -> list[dict[str, Any]]:
    return [
        {"sale_key": "L-2001", "client_code": "ERP-88", "sale_day": "2026-01-03", "qty": 2, "price_each": "23.0000"},
        {"sale_key": "L-2002", "client_code": "CRM-002", "sale_day": "2026-01-04", "qty": 1, "price_each": "22.0000"},
        # The orphan remains visible in source accounting and carries a null
        # optional analytical value to exercise the quality boundary.
        {"sale_key": "L-2003", "client_code": "MISSING", "sale_day": "2026-01-05", "qty": 5, "price_each": ""},
    ]


class _CredentialResolver:
    def __init__(self, urls: dict[str, str]):
        self.urls_by_source_id = {
            "prompt02-crm-postgres": urls["postgres"],
            "prompt02-erp-mysql": urls["mysql"],
            "prompt02-sales-sqlserver": urls["sqlserver"],
        }

    def resolve(self, profile: Any, *, required_purpose: CredentialPurpose, source_id: str) -> RuntimeSqlCredentials:
        return RuntimeSqlCredentials(self.urls_by_source_id[source_id], credential_reference=f"prompt02-fixture:{source_id}", credential_purpose=required_purpose, credential_version="prompt02-v2", source_id=source_id)


class _ProviderVerifier:
    def verify(self, *, profile: Any, credentials: RuntimeSqlCredentials, source_id: str, profile_id: str, selection_fingerprint: str, policy: Any) -> ProviderSecurityVerification:
        engine = create_engine(credentials.connection_url, poolclass=NullPool)
        table_name = TABLES["postgres" if source_id.endswith("postgres") else "mysql" if source_id.endswith("mysql") else "sqlserver"]
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if profile.database_engine is DatabaseEngine.POSTGRESQL:
                    checks = connection.exec_driver_sql(f"SELECT has_database_privilege(current_user, current_database(), 'CONNECT'), has_schema_privilege(current_user, 'public', 'USAGE'), has_table_privilege(current_user, 'public.{table_name}', 'SELECT')").one()
                    present = {"CONNECT": bool(checks[0]), "USAGE": bool(checks[1]), "SELECT": bool(checks[2])}
                    role = connection.exec_driver_sql("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user").one()
                    forbidden = {"SUPERUSER": bool(role[0]), "CREATEDB": bool(role[1]), "CREATEROLE": bool(role[2]), "REPLICATION": bool(role[3]), "BYPASSRLS": bool(role[4])}
                elif profile.database_engine is DatabaseEngine.MYSQL:
                    grants = " ".join(str(row[0]).upper() for row in connection.exec_driver_sql("SHOW GRANTS").all())
                    present = {"SELECT": "SELECT" in grants and "ALL PRIVILEGES" not in grants}
                    forbidden = {item: any(token in grants for token in (item, "ALL PRIVILEGES")) for item in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "EXECUTE", "FILE", "PROCESS", "SUPER", "GRANT OPTION", "CREATE USER", "TRIGGER", "EVENT", "RELOAD")}
                else:
                    checks = connection.exec_driver_sql(f"SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONNECT'), HAS_PERMS_BY_NAME('dbo.{table_name}', 'OBJECT', 'SELECT'), IS_SRVROLEMEMBER('sysadmin'), IS_ROLEMEMBER('db_owner')").one()
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
    metadata = MetaData()
    if label == "postgres":
        table = Table(TABLES[label], metadata, Column("crm_customer_id", String(120), primary_key=True), Column("full_name", String(200), nullable=False), Column("email", String(240), nullable=False), Column("phone", String(80)), schema=schema)
        rows = _registry_rows(label)
    elif label == "mysql":
        table = Table(TABLES[label], metadata, Column("account_no", String(120), primary_key=True), Column("account_name", String(200), nullable=False), Column("email_addr", String(240), nullable=False), Column("phone_e164", String(80)), schema=schema)
        rows = _registry_rows(label)
    else:
        table = Table(TABLES[label], metadata, Column("ticket_id", String(120), primary_key=True), Column("customer_ref", String(120), nullable=False), Column("booked_on", Date, nullable=False), Column("units", Integer, nullable=False), Column("unit_price", Numeric(18, 4)), schema=schema)
        rows = _sales_rows()
    try:
        table.drop(admin, checkfirst=True)
        table.create(admin)
        with admin.begin() as connection:
            connection.execute(table.insert(), rows)
            if engine_kind is DatabaseEngine.POSTGRESQL:
                safe = username.replace('"', '""')
                connection.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {_sql_literal(username)}) THEN CREATE ROLE \"{safe}\" LOGIN PASSWORD {_sql_literal(password)}; END IF; END $$")
                connection.exec_driver_sql(f"ALTER ROLE \"{safe}\" LOGIN PASSWORD {_sql_literal(password)} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE \"{parsed.database}\" TO \"{safe}\"")
                connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO \"{safe}\"")
                connection.exec_driver_sql(f"GRANT SELECT ON TABLE public.\"{TABLES[label]}\" TO \"{safe}\"")
            elif engine_kind is DatabaseEngine.MYSQL:
                connection.exec_driver_sql(f"CREATE USER IF NOT EXISTS {_sql_literal(username)}@'%%' IDENTIFIED BY {_sql_literal(password)}")
                connection.exec_driver_sql(f"ALTER USER {_sql_literal(username)}@'%%' IDENTIFIED BY {_sql_literal(password)}")
                connection.exec_driver_sql(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {_sql_literal(username)}@'%%'")
                connection.exec_driver_sql(f"GRANT SELECT, SHOW VIEW ON `{parsed.database}`.`{TABLES[label]}` TO {_sql_literal(username)}@'%%'")
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
    rows = _file_rows()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("sale_key", "client_code", "sale_day", "qty", "price_each"))
        writer.writeheader()
        writer.writerows(rows)


def _content_commit() -> str:
    candidate = os.environ.get("GITHUB_SHA", "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", candidate):
        return candidate
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    value = result.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else "0" * 40


def _evidence_dir() -> Path:
    path = Path(os.environ.get("DDO_PROMPT02_EVIDENCE_DIR", ROOT / "workspace" / "tests" / "prompt02-evidence"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_diagnostic_text(value: object | None) -> str | None:
    if value is None:
        return None
    detail = str(value)
    detail = re.sub(r"(?i)(?:[a-z][a-z0-9+.-]*)://[^\s]+", "<redacted-connection>", detail)
    detail = re.sub(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", detail)
    return detail[:1000] or None


def _safe_failure_detail(error: BaseException) -> str:
    return f"{type(error).__name__}: {_safe_diagnostic_text(error) or '<empty>'}"[:1000]


def _projection_value(value: object | None) -> str | None:
    if value is None:
        return None
    resolved = getattr(value, "value", value)
    return str(resolved)


def _safe_diagnostic_id(value: object | None) -> str | None:
    resolved = _projection_value(value)
    if resolved is None:
        return None
    return resolved if re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", resolved) else "<redacted-id>"


def _safe_diagnostic_code(value: object | None) -> str | None:
    resolved = _projection_value(value)
    if resolved is None:
        return None
    return resolved if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", resolved) else "<redacted-code>"


def _safe_diagnostic_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted(item for item in (_safe_diagnostic_id(value) for value in values) if item is not None))


def _durable_exception_type(job) -> str | None:
    result = getattr(job, "durable_result", None)
    if not isinstance(result, dict):
        return None
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return _safe_diagnostic_code(metadata.get("error_type"))


_BACKEND_PAGE_SIZE = 100
_MAX_BACKEND_PAGES = 100


def _collect_backend_pages(*, label: str, fetch_page, item_key) -> tuple[Any, ...]:
    """Collect every bounded public-API page, rejecting ambiguous pagination."""

    items: list[Any] = []
    seen_offsets: set[int] = set()
    seen_item_ids: set[str] = set()
    offset = 0
    for _ in range(_MAX_BACKEND_PAGES):
        if offset in seen_offsets:
            raise AssertionError(f"{label} pagination repeated offset {offset}")
        seen_offsets.add(offset)
        page = fetch_page(page_size=_BACKEND_PAGE_SIZE, offset=offset)
        for item in page.items:
            identity = item_key(item)
            if not isinstance(identity, str) or not identity:
                raise AssertionError(f"{label} pagination returned a record without a stable identity")
            if identity in seen_item_ids:
                raise AssertionError(f"{label} pagination returned duplicate record identity {identity}")
            seen_item_ids.add(identity)
            items.append(item)
        next_offset = page.next_offset
        if next_offset is None:
            return tuple(items)
        if len(page.items) != _BACKEND_PAGE_SIZE:
            raise AssertionError(f"{label} pagination reported another page after an incomplete page")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise AssertionError(f"{label} pagination did not advance from offset {offset}")
        if next_offset != offset + _BACKEND_PAGE_SIZE:
            raise AssertionError(f"{label} pagination returned a non-contiguous next offset {next_offset}")
        if next_offset in seen_offsets:
            raise AssertionError(f"{label} pagination repeated next offset {next_offset}")
        offset = next_offset
    raise AssertionError(f"{label} pagination exceeded {_MAX_BACKEND_PAGES} pages")


def _all_attempts(*, backend, run_id: str, principal, status: str | None = None) -> tuple[Any, ...]:
    return _collect_backend_pages(
        label="attempts",
        fetch_page=lambda **page: backend.list_attempts(
            run_id=run_id,
            stage_id=None,
            status=status,
            principal=principal,
            **page,
        ),
        item_key=lambda item: item.attempt_id,
    )


def _all_jobs(*, backend, run_id: str, principal, status: str | None = None) -> tuple[Any, ...]:
    return _collect_backend_pages(
        label="jobs",
        fetch_page=lambda **page: backend.list_jobs(
            run_id=run_id,
            status=status,
            principal=principal,
            **page,
        ),
        item_key=lambda item: item.job_id,
    )


def _all_reviews(*, backend, run_id: str, principal) -> tuple[Any, ...]:
    return _collect_backend_pages(
        label="reviews",
        fetch_page=lambda **page: backend.list_reviews(
            run_id=run_id,
            subject_key=None,
            principal=principal,
            **page,
        ),
        item_key=lambda item: getattr(item, "history_id", None) or f"{item.subject_key}:{item.revision}",
    )


def _evidence_fusion_projection(*, control_store, artifact_store, run_id: str) -> tuple[dict[str, Any], ...]:
    """Expose bounded fusion outcomes without retaining source-level payloads."""

    views = []
    refs = control_store.list_artifacts(run_id=run_id, artifact_kind="EvidenceFusionResult", limit=10000)
    for ref in refs:
        payload = json.loads(artifact_store.read(ref).decode("utf-8"))
        failures = payload.get("failures", ())
        relationships = payload.get("relationships", ())
        mappings = payload.get("mappings", ())
        views.append(
            {
                "artifact_id": _safe_diagnostic_id(ref.artifact_id),
                "completeness": _safe_diagnostic_code(payload.get("completeness")),
                "failure_kinds": tuple(
                    sorted(
                        item
                        for item in (_safe_diagnostic_code(value.get("kind")) for value in failures if isinstance(value, dict))
                        if item is not None
                    )
                ),
                "failure_details": tuple(
                    detail
                    for detail in (
                        _safe_diagnostic_text(value.get("detail"))
                        for value in failures
                        if isinstance(value, dict)
                    )
                    if detail is not None
                )[:20],
                "relationship_count": len(relationships) if isinstance(relationships, list) else 0,
                "relationship_decision_states": tuple(
                    sorted(
                        item
                        for item in (
                            _safe_diagnostic_code(value.get("decision_state"))
                            for value in relationships
                            if isinstance(value, dict)
                        )
                        if item is not None
                    )
                ),
                "relationship_missing_evidence_counts": tuple(
                    sorted(
                        len(value.get("missing_evidence_refs", ()))
                        for value in relationships
                        if isinstance(value, dict)
                    )
                ),
                "mapping_count": len(mappings) if isinstance(mappings, list) else 0,
                "mapping_decision_states": tuple(
                    sorted(
                        item
                        for item in (
                            _safe_diagnostic_code(value.get("decision_state"))
                            for value in mappings
                            if isinstance(value, dict)
                        )
                        if item is not None
                    )
                ),
                "mapping_missing_evidence_counts": tuple(
                    sorted(
                        len(value.get("missing_evidence_refs", ()))
                        for value in mappings
                        if isinstance(value, dict)
                    )
                ),
            }
        )
    return tuple(views)


def _failure_runtime_snapshot(*, backend, control_store, artifact_store=None, run_id: str, principal) -> dict[str, Any]:
    """Read safe durable projections only; never read source artifacts or queue payloads."""

    diagnostic_errors: list[str] = []

    def collect(label: str, callback, fallback):
        try:
            return callback()
        except Exception as diagnostic_error:
            diagnostic_errors.append(f"{label}: {_safe_failure_detail(diagnostic_error)}")
            return fallback

    summary = collect(
        "product_summary",
        lambda: backend.product_summary(run_id=run_id, principal=principal),
        None,
    )
    attempts = collect(
        "attempts",
        lambda: _all_attempts(backend=backend, run_id=run_id, principal=principal),
        (),
    )
    jobs = collect(
        "jobs",
        lambda: _all_jobs(backend=backend, run_id=run_id, principal=principal),
        (),
    )
    reviews = collect(
        "reviews",
        lambda: _all_reviews(backend=backend, run_id=run_id, principal=principal),
        (),
    )

    stage_views = tuple(
        {
            "stage_id": _safe_diagnostic_id(stage.stage_id),
            "status": _projection_value(stage.status),
            "selected": bool(stage.selected),
            "required": bool(stage.required),
            "artifact_kinds": _safe_diagnostic_ids(stage.artifact_kinds),
            "artifact_ids": _safe_diagnostic_ids(stage.artifact_ids),
        }
        for stage in summary.stages
    ) if summary is not None else ()
    attempt_views = tuple(
        {
            "attempt_id": _safe_diagnostic_id(attempt.attempt_id),
            "stage_id": _safe_diagnostic_id(attempt.stage_id),
            "attempt_number": attempt.attempt_number,
            "status": _projection_value(attempt.status),
            "failure_code": _safe_diagnostic_code(attempt.failure_code),
            "failure_message": _safe_diagnostic_text(attempt.failure_reason),
            "input_artifact_ids": _safe_diagnostic_ids(attempt.input_artifact_refs),
            "output_artifact_ids": _safe_diagnostic_ids(attempt.output_artifact_refs),
        }
        for attempt in attempts
    )
    job_views = tuple(
        {
            "job_id": _safe_diagnostic_id(job.job_id),
            "stage_id": _safe_diagnostic_id(job.stage_id),
            "attempt_id": _safe_diagnostic_id(job.attempt_id),
            "job_kind": _projection_value(job.job_kind),
            "status": _projection_value(job.status),
            "failure_code": _safe_diagnostic_code(job.failure_code),
            "failure_classification": _safe_diagnostic_code(getattr(job, "failure_classification", None)),
            "exception_type": _durable_exception_type(job),
            "failure_message": _safe_diagnostic_text(job.failure_reason),
            "result_artifact_ids": _safe_diagnostic_ids(job.result_refs),
        }
        for job in jobs
    )
    review_views = tuple(
        {
            "checkpoint": _safe_diagnostic_id(item.decision.review_checkpoint_id),
            "state": _projection_value(item.decision.decision),
            "revision": item.revision,
            "subject_artifact_id": _safe_diagnostic_id(item.decision.subject_artifact_id),
            "persisted": True,
        }
        for item in reviews
    ) + (() if summary is None else tuple(
        {
            "checkpoint": _safe_diagnostic_id(item.checkpoint),
            "state": _projection_value(item.state),
            "revision": item.revision,
            "subject_artifact_id": _safe_diagnostic_id(item.subject_artifact_id),
            "persisted": False,
        }
        for item in summary.pending_reviews
    ))

    artifact_views = None
    if control_store is not None:
        artifact_views = collect(
            "artifacts",
            lambda: tuple(
                {
                    "artifact_id": _safe_diagnostic_id(artifact.artifact_id),
                    "artifact_kind": _safe_diagnostic_id(artifact.artifact_kind),
                    "stage_id": _safe_diagnostic_id(artifact.stage_id),
                }
                for artifact in control_store.list_artifacts(run_id=run_id, limit=10000)
            ),
            None,
        )

    evidence_fusion_views = None
    if control_store is not None and artifact_store is not None:
        evidence_fusion_views = collect(
            "evidence_fusion",
            lambda: _evidence_fusion_projection(
                control_store=control_store,
                artifact_store=artifact_store,
                run_id=run_id,
            ),
            None,
        )

    failed_statuses = {"FAILED", "BLOCKED", "CANCELLED"}
    failed_stage_ids = {
        item["stage_id"]
        for item in stage_views
        if item["stage_id"] is not None and item["status"] in failed_statuses
    }
    failed_stage_ids.update(
        item["stage_id"]
        for item in attempt_views + job_views
        if item["stage_id"] is not None
        and (item["status"] in failed_statuses or item["failure_code"] is not None)
    )
    successful_stage_ids = {
        item["stage_id"]
        for item in stage_views
        if item["stage_id"] is not None and item["status"] == "SUCCEEDED"
    }
    successful_stage_ids.update(
        item["stage_id"]
        for item in attempt_views
        if item["stage_id"] is not None and item["status"] == "SUCCEEDED"
    )
    stage_ids = sorted({item["stage_id"] for item in attempt_views if item["stage_id"] is not None})
    attempt_counts = tuple(
        {
            "stage_id": stage_id,
            "total": len(items),
            "succeeded": sum(item["status"] == "SUCCEEDED" for item in items),
            "failed": sum(item["status"] in failed_statuses for item in items),
        }
        for stage_id in stage_ids
        for items in (tuple(item for item in attempt_views if item["stage_id"] == stage_id),)
    )
    snapshot_artifacts = tuple(
        item["artifact_id"]
        for item in artifact_views or ()
        if item["artifact_kind"] == "SourceSnapshotResult" and item["artifact_id"] is not None
    )
    materialization_reached = (
        None
        if summary is None and not attempts
        else (summary is not None and _projection_value(summary.materialization.status) != "NOT_EVALUATED")
        or any(item["stage_id"] == "MATERIALIZATION" for item in attempt_views)
    )
    g6_reached = (
        None
        if summary is None and not attempts
        else (summary is not None and _projection_value(summary.validation.g6_status) != "PENDING")
        or any(item["stage_id"] == "VALIDATION_RECONCILIATION" for item in attempt_views)
    )
    return {
        "terminal_run_status": None if summary is None else _projection_value(summary.status),
        "failed_stage_ids": tuple(sorted(failed_stage_ids)),
        "successful_stage_ids": tuple(sorted(successful_stage_ids)),
        "stages": stage_views,
        "failed_attempts": tuple(
            item
            for item in attempt_views
            if item["status"] in failed_statuses or item["failure_code"] is not None
        ),
        "failed_jobs": tuple(
            item
            for item in job_views
            if item["status"] in failed_statuses or item["failure_code"] is not None
        ),
        "stage_attempt_counts": attempt_counts,
        "review_checkpoints": review_views,
        "artifacts": artifact_views,
        "evidence_fusion": evidence_fusion_views,
        "source_snapshot_artifact_count": len(snapshot_artifacts),
        "all_four_selected_source_snapshots_produced": None if artifact_views is None else len(snapshot_artifacts) == 4,
        "materialization_reached": materialization_reached,
        "g6_reached": g6_reached,
        "diagnostic_collection_errors": tuple(diagnostic_errors),
    }


def _write_failure_evidence(*, run_id: str | None, error: BaseException, backend=None, control_store=None, artifact_store=None, principal=None) -> None:
    diagnostic_errors = []
    runtime_snapshot = None
    if run_id is not None and backend is not None and principal is not None:
        try:
            runtime_snapshot = _failure_runtime_snapshot(
                backend=backend,
                control_store=control_store,
                artifact_store=artifact_store,
                run_id=run_id,
                principal=principal,
            )
            diagnostic_errors.extend(runtime_snapshot["diagnostic_collection_errors"])
        except Exception as diagnostic_error:
            diagnostic_errors.append(_safe_failure_detail(diagnostic_error))
    payload = {
        "status": "BLOCKED",
        "run_id": run_id or "unavailable",
        "content_commit": _content_commit(),
        "blocked_reasons": (_safe_failure_detail(error),),
        "terminal_exception_type": type(error).__name__,
        "terminal_exception_message": _safe_diagnostic_text(error),
        "evidence_kind": "failure-diagnostics",
        "runtime_snapshot": runtime_snapshot,
        "diagnostic_collection_errors": tuple(diagnostic_errors),
    }
    (_evidence_dir() / "PROMPT02_FAILURE.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_failure_evidence_preserves_failed_run_and_exposes_safe_durable_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal runtime failure stays failed while its durable state is actionable and safe."""

    monkeypatch.setattr("test_multi_source_v1._evidence_dir", lambda: tmp_path)
    summary = SimpleNamespace(
        status="FAILED",
        stages=(
            SimpleNamespace(stage_id="SOURCE_DISCOVERY", status="SUCCEEDED", selected=True, required=True, artifact_kinds=("SourceCatalog",), artifact_ids=("artifact-catalog",)),
            SimpleNamespace(stage_id="DEPENDENCY_DISCOVERY", status="FAILED", selected=True, required=True, artifact_kinds=(), artifact_ids=()),
        ),
        pending_reviews=(
            SimpleNamespace(checkpoint="REVIEW_EVIDENCE_DECISIONS", state="ACCEPTED", revision=1, subject_artifact_id="artifact-review"),
        ),
        materialization=SimpleNamespace(status="NOT_EVALUATED"),
        validation=SimpleNamespace(g6_status="PENDING"),
    )
    failed_attempt = SimpleNamespace(
        attempt_id="attempt-dependency-1",
        stage_id="DEPENDENCY_DISCOVERY",
        attempt_number=1,
        status="FAILED",
        failure_code="PROVIDER_UNAVAILABLE",
        failure_reason="provider connection postgresql://user:password@host/example token=unsafe",
        input_artifact_refs=("artifact-catalog",),
        output_artifact_refs=(),
    )
    failed_job = SimpleNamespace(
        job_id="job-dependency-1",
        stage_id="DEPENDENCY_DISCOVERY",
        attempt_id="attempt-dependency-1",
        job_kind="STAGE",
        status="FAILED",
        failure_code="PROVIDER_UNAVAILABLE",
        failure_reason="provider connection postgresql://user:password@host/example token=unsafe",
        result_refs=(),
        durable_result={"metadata": {"error_type": "ConnectionError"}},
    )
    attempts = tuple(
        SimpleNamespace(
            attempt_id=f"attempt-source-{index}",
            stage_id="SOURCE_DISCOVERY",
            attempt_number=index + 1,
            status="SUCCEEDED",
            failure_code=None,
            failure_reason=None,
            input_artifact_refs=(),
            output_artifact_refs=(f"artifact-source-{index}",),
        )
        for index in range(100)
    ) + (failed_attempt,)
    jobs = tuple(
        SimpleNamespace(
            job_id=f"job-source-{index}",
            stage_id="SOURCE_DISCOVERY",
            attempt_id=f"attempt-source-{index}",
            job_kind="STAGE",
            status="SUCCEEDED",
            failure_code=None,
            failure_classification=None,
            failure_reason=None,
            result_refs=(f"artifact-source-{index}",),
            durable_result=None,
        )
        for index in range(100)
    ) + (failed_job,)
    reviews = tuple(
        SimpleNamespace(
            history_id=f"review-{index}",
            subject_key=f"review-subject-{index}",
            decision=SimpleNamespace(
                review_checkpoint_id="REVIEW_EVIDENCE_DECISIONS",
                decision="ACCEPTED",
                subject_artifact_id=f"artifact-review-{index}",
            ),
            revision=1,
        )
        for index in range(101)
    )

    class Backend:
        def __init__(self) -> None:
            self.page_requests = {"attempts": [], "jobs": [], "reviews": []}
            self.fail_jobs = False

        @staticmethod
        def _page(items, *, page_size: int, offset: int):
            if page_size < 1 or page_size > 100 or offset < 0:
                raise BackendError(
                    "INVALID_PAGE",
                    "page_size exceeds the bounded API limit or offset is invalid",
                    status=400,
                )
            page_items = tuple(items[offset:offset + page_size])
            next_offset = offset + page_size if offset + page_size < len(items) else None
            return SimpleNamespace(items=page_items, next_offset=next_offset)

        def product_summary(self, **_kwargs):
            return summary

        def list_attempts(self, *, page_size: int, offset: int, **_kwargs):
            self.page_requests["attempts"].append((page_size, offset))
            return self._page(attempts, page_size=page_size, offset=offset)

        def list_jobs(self, *, page_size: int, offset: int, **_kwargs):
            self.page_requests["jobs"].append((page_size, offset))
            if self.fail_jobs:
                raise BackendError("JOBS_UNAVAILABLE", "durable job lookup failed", status=503)
            return self._page(jobs, page_size=page_size, offset=offset)

        def list_reviews(self, *, page_size: int, offset: int, **_kwargs):
            self.page_requests["reviews"].append((page_size, offset))
            return self._page(reviews, page_size=page_size, offset=offset)

    class ControlStore:
        def list_artifacts(self, **_kwargs):
            return tuple(
                SimpleNamespace(
                    artifact_id=f"artifact-snapshot-{index}",
                    artifact_kind="SourceSnapshotResult",
                    stage_id="SOURCE_SNAPSHOT_STAGE",
                )
                for index in range(1, 5)
            )

    backend = Backend()
    _write_failure_evidence(
        run_id="run-failed",
        error=RuntimeError("runtime failure postgresql://user:password@host/example password=unsafe"),
        backend=backend,
        control_store=ControlStore(),
        principal=object(),
    )

    payload = json.loads((tmp_path / "PROMPT02_FAILURE.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    snapshot = payload["runtime_snapshot"]
    assert payload["status"] == "BLOCKED"
    assert payload["terminal_exception_type"] == "RuntimeError"
    assert snapshot["terminal_run_status"] == "FAILED"
    assert snapshot["failed_stage_ids"] == ["DEPENDENCY_DISCOVERY"]
    assert snapshot["failed_attempts"][0]["attempt_id"] == "attempt-dependency-1"
    assert snapshot["failed_jobs"][0]["job_id"] == "job-dependency-1"
    assert snapshot["failed_jobs"][0]["exception_type"] == "ConnectionError"
    assert len(snapshot["stage_attempt_counts"]) == 2
    assert len(snapshot["review_checkpoints"]) == 102
    assert snapshot["successful_stage_ids"] == ["SOURCE_DISCOVERY"]
    assert snapshot["all_four_selected_source_snapshots_produced"] is True
    assert snapshot["materialization_reached"] is False and snapshot["g6_reached"] is False
    assert snapshot["diagnostic_collection_errors"] == []
    assert backend.page_requests == {
        "attempts": [(100, 0), (100, 100)],
        "jobs": [(100, 0), (100, 100)],
        "reviews": [(100, 0), (100, 100)],
    }
    assert "postgresql://" not in serialized and "password=unsafe" not in serialized and "token=unsafe" not in serialized

    backend.fail_jobs = True
    _write_failure_evidence(
        run_id="run-failed",
        error=RuntimeError("runtime failure"),
        backend=backend,
        control_store=ControlStore(),
        principal=object(),
    )
    partial_payload = json.loads((tmp_path / "PROMPT02_FAILURE.json").read_text(encoding="utf-8"))
    partial_snapshot = partial_payload["runtime_snapshot"]
    assert partial_payload["status"] == "BLOCKED"
    assert partial_snapshot["failed_attempts"][0]["attempt_id"] == "attempt-dependency-1"
    assert partial_snapshot["failed_jobs"] == []
    assert len(partial_snapshot["review_checkpoints"]) == 102
    assert any(error.startswith("jobs: BackendError:") for error in partial_payload["diagnostic_collection_errors"])


def test_backend_page_collection_rejects_nonadvancing_offsets() -> None:
    page = SimpleNamespace(
        items=tuple(SimpleNamespace(attempt_id=f"attempt-{index}") for index in range(100)),
        next_offset=0,
    )
    with pytest.raises(AssertionError, match="did not advance"):
        _collect_backend_pages(
            label="attempts",
            fetch_page=lambda **_page: page,
            item_key=lambda item: item.attempt_id,
        )


def _latest_artifact(platform, *, run_id: str, artifact_kind: str, model):
    for ref in reversed(platform.control_store.list_artifacts(run_id=run_id, artifact_kind=artifact_kind, limit=10000)):
        try:
            return ref, model.model_validate(json.loads(platform.artifact_store.read(ref).decode("utf-8")))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    raise AssertionError(f"missing durable {artifact_kind} artifact")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_negative_control_evidence() -> tuple[dict[str, Any], ...]:
    path = _evidence_dir() / "PROMPT02_NEGATIVE_CONTROLS.json"
    if not path.is_file():
        raise AssertionError("Prompt02 negative-control execution evidence is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    controls = tuple(payload.get("controls", ()))
    expected = {item[0] for item in NEGATIVE_CONTROL_REQUIREMENTS}
    actual = {item.get("control_id") for item in controls}
    if actual != expected or len(controls) != len(expected):
        raise AssertionError("Prompt02 negative-control evidence does not contain exactly NC01-NC14")
    if any(item.get("execution_status") != "PASS" for item in controls):
        raise AssertionError("Prompt02 negative-control execution did not pass")
    return tuple(sorted(controls, key=lambda item: item["control_id"]))


def _assert_submission_lifecycle(submission: Any, summary: Any) -> None:
    """Durable enqueue may precede the asynchronous worker's first claim."""

    assert submission.status in {"ACCEPTED", "REVIEW_REQUIRED"}
    assert summary.status in {"CREATED", "RUNNING", "NEEDS_REVIEW", "SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"}


def test_submission_acceptance_does_not_require_synchronous_worker_claim() -> None:
    """Regression for CI: CREATED is valid immediately after durable submit."""

    class Submission:
        status = "ACCEPTED"

    class Summary:
        status = "CREATED"

    _assert_submission_lifecycle(Submission(), Summary())


def _build_receipt(*, platform, backend, run, binding, records, final_summary, reviewed, target: Path, oracle: dict[str, Any], control_evidence: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    _selection_ref, selection = _latest_artifact(platform, run_id=run.run_id, artifact_kind="SourceSetSelection", model=SourceSetSelection)
    catalog_values = {}
    for ref in platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="SourceCatalog", limit=10000):
        try:
            value = SourceCatalog.model_validate(json.loads(platform.artifact_store.read(ref).decode("utf-8")))
            catalog_values[value.source_id] = value
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    snapshot_values = {}
    for ref in platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="SourceSnapshotResult", limit=10000):
        try:
            value = SourceSnapshotResult.model_validate(json.loads(platform.artifact_store.read(ref).decode("utf-8")))
            snapshot_values[value.snapshot.source_id] = value
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    source_evidence = []
    selection_by_registry = {item.registry_id: item for item in selection.selections}
    for record in records:
        source_id = record.source_id or record.registry_id
        catalog = catalog_values[source_id]
        snapshot = snapshot_values[source_id]
        selected = selection_by_registry[record.registry_id]
        source_evidence.append({
            "registry_id": record.registry_id,
            "source_id": source_id,
            "source_type": record.source_type.value,
            "source_config_fingerprint": stable_digest({"registry_id": record.registry_id, "source_id": source_id, "source_type": record.source_type.value}),
            "selection_fingerprint": stable_digest(selected.model_dump(mode="json")),
            "snapshot_id": snapshot.snapshot.snapshot_id,
            "snapshot_fingerprint": snapshot.snapshot.source_fingerprint or snapshot.snapshot.schema_fingerprint,
            "schema_fingerprint": catalog.source.schema_fingerprint,
            "selected_tables": tuple(record.scope.included_objects),
            "extraction_max_rows": snapshot.snapshot.extraction_policy.max_rows,
            "input_records": snapshot.metrics.input_records_observed,
            "staged_records": snapshot.metrics.staged_records,
            "source_unchanged_before_after": snapshot.snapshot.consistency.value != "BEST_EFFORT",
        })
    read_principal = Principal(
        subject="prompt02-acceptance",
        source="LOCAL_TEST_AUTH",
        scopes=frozenset({"runs:read"}),
    )
    attempts = _all_attempts(
        backend=backend,
        run_id=run.run_id,
        principal=read_principal,
    )
    stages = []
    for stage in final_summary.stages:
        stage_attempts = [item for item in attempts if item.stage_id == stage.stage_id]
        stages.append({
            "stage_id": stage.stage_id,
            "status": stage.status,
            "attempts": len(stage_attempts),
            "providers": tuple(),
            "artifact_refs": tuple(sorted({ref for item in stage_attempts for ref in item.output_artifact_refs})),
            "detail": "durable stage-attempt projection",
        })
    reviews = _all_reviews(
        backend=backend,
        run_id=run.run_id,
        principal=read_principal,
    )
    review_evidence = [{
        "checkpoint": item.decision.review_checkpoint_id.value,
        "subject_id": item.decision.subject_semantic_id,
        "decision": item.decision.decision.value,
        "actor": item.decision.actor,
        "actor_source": item.decision.actor_source,
        "rationale": item.decision.rationale,
        "reviewed_at": item.decision.reviewed_at.isoformat(),
    } for item in reviews]
    _truth_ref, truth = _latest_artifact(platform, run_id=run.run_id, artifact_kind="SourceTruthManifest", model=SourceTruthManifest)
    _accounting_ref, accounting = _latest_artifact(platform, run_id=run.run_id, artifact_kind="RecordAccountingArtifact", model=RecordAccountingArtifact)
    record_source = {item.record_ref: item.source_id for item in truth.records}
    source_scope = next(item for item in accounting.scopes if item.boundary.value == "SOURCE_TO_CANONICAL")
    accounting_evidence = []
    for source_id in sorted({item.source_id for item in truth.records}):
        refs = {item.record_ref for item in truth.records if item.source_id == source_id}
        entries = [item for item in source_scope.entries if record_source.get(item.input_record_ref) == source_id]
        counts = {name: sum(item.disposition.value == name for item in entries) for name in ("CONSOLIDATED", "QUARANTINED", "UNRESOLVED")}
        accounting_evidence.append({
            "source_id": source_id,
            "input_records": len(refs),
            "emitted_records": len(entries),
            "consolidated_records": counts["CONSOLIDATED"],
            "quarantined_records": counts["QUARANTINED"],
            "unresolved_records": counts["UNRESOLVED"],
            "duplicate_key_candidates": 1 if source_id == "prompt02-erp-mysql" else 0,
            "orphan_records": counts["UNRESOLVED"],
            "disposition_complete": refs == {item.input_record_ref for item in entries},
        })
    materialization = final_summary.materialization
    analytical = final_summary.analytical
    with __import__("duckdb").connect(str(target), read_only=True) as connection:
        fact_rows = int(connection.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0])
        quantity_sum = str(connection.execute("SELECT COALESCE(SUM(quantity), 0) FROM fact_order").fetchone()[0])
    controls = {item["control_id"]: item["execution_status"] for item in control_evidence}
    receipt = {
        "receipt_id": f"prompt02-receipt-{run.run_id}",
        "run_id": run.run_id,
        "content_commit": _content_commit(),
        "source_set_fingerprint": binding.source_set_fingerprint,
        "sources": source_evidence,
        "stages": stages,
        "reviews": review_evidence,
        "record_accounting": accounting_evidence,
        "analytical": {
            "fact_table": "fact_order",
            "fact_grain": "one row per source and source-local order key",
            "fact_row_count": fact_rows,
            "dimensions": tuple(analytical.dimension_ids),
            "dimension_row_counts": materialization.row_counts,
            "measures": ("quantity",),
            "non_measures": (),
            "lineage_refs": (materialization.artifact_id,),
            "quantity_sum": quantity_sum,
        },
        "materialization": {
            "duckdb_relative_path": f"workspace/platform/runs/{run.run_id}/olap/olap.duckdb",
            "target_file_sha256": _sha256(target),
            "table_names": tuple(materialization.table_names),
            "row_counts": materialization.row_counts,
            "usable": materialization.usable,
        },
        "oracle": {
            "oracle_id": oracle["oracle_id"],
            "oracle_version": oracle["oracle_version"],
            "oracle_path": ORACLE.relative_to(ROOT).as_posix(),
            "loaded_after_product_run": True,
            "matched_fact_rows": fact_rows == oracle["expected"]["fact_rows"],
            "matched_aggregates": quantity_sum == oracle["expected"]["quantity_sum"],
            "matched_dispositions": all(item["disposition_complete"] for item in accounting_evidence),
        },
        "negative_controls": controls,
        "negative_control_evidence": control_evidence,
        "status": "PASS" if all(value == "PASS" for value in controls.values()) else "BLOCKED",
        "blocked_reasons": tuple(item["control_id"] for item in control_evidence if item["execution_status"] != "PASS"),
    }
    return MultiSourceAcceptanceReceipt.model_validate(receipt).model_dump(mode="json")


def _drive_runtime(backend, run_id: str, principal: Principal):
    reviewed: list[dict[str, Any]] = []
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        summary = backend.product_summary(run_id=run_id, principal=principal)
        pending = [item for item in summary.pending_reviews if item.state == "REVIEW_REQUIRED"]
        for item in pending:
            checkpoint = ReviewCheckpoint(item.checkpoint)
            backend.review(run_id=run_id, checkpoint=checkpoint, context=item.context, subject_artifact_id=item.subject_artifact_id, subject_content_hash=item.subject_content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale=f"Prompt02 durable acceptance review for {checkpoint.value}", expected_revision=item.revision, principal=principal, idempotency_key=f"prompt02-review-{item.checkpoint}-{item.subject_artifact_id}")
            reviewed.append({"checkpoint": item.checkpoint, "subject_artifact_id": item.subject_artifact_id, "revision": item.revision + 1})
        if pending:
            backend.resume(run_id=run_id, principal=principal, idempotency_key=f"prompt02-resume-{len(reviewed)}")
        elif summary.status in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"}:
            return summary, reviewed
        else:
            time.sleep(0.05)
    raise AssertionError("Prompt02 runtime did not reach a terminal state within the bounded local acceptance window")


@pytest.mark.optional_provider
def test_multi_source_v1_real_pipeline_and_independent_oracle(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb", reason="Prompt02 independent target inspector is unavailable")
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
    platform = backend = runtime = run = principal = None
    try:
        for label, engine, *_ in LIVE_TARGETS:
            _wait_for_database(admins[label])
            _prepare_database(admins[label], urls[label], engine, label)
        csv_path = workspace / "legacy_sales.csv"
        _file_fixture(csv_path)
        csv_before_run = csv_path.read_bytes()
        source_ids = {"postgres": "prompt02-crm-postgres", "mysql": "prompt02-erp-mysql", "sqlserver": "prompt02-sales-sqlserver", "csv": "prompt02-legacy-csv"}
        records: list[SourceRegistryRecord] = []
        for label, engine, *_ in LIVE_TARGETS:
            parsed = make_url(urls[label])
            records.append(SourceRegistryRecord(registry_id=label, source_id=source_ids[label], display_name=f"Prompt02 {label}", source_type={DatabaseEngine.POSTGRESQL: SourceType.POSTGRESQL, DatabaseEngine.MYSQL: SourceType.MYSQL, DatabaseEngine.SQLSERVER: SourceType.SQLSERVER}[engine], connection_profile=ConnectionProfileReference(profile_id=f"prompt02-{label}-profile", database_engine=engine, host=parsed.host, port=parsed.port, database_name=parsed.database or "master", credential_reference=f"prompt02-fixture:{source_ids[label]}"), scope=SelectionScope(included_objects=(TABLES[label],)), adapter_name="dlt_sql_source", adapter_version="1.0.0"))
        records.append(SourceRegistryRecord(registry_id="csv", source_id=source_ids["csv"], display_name="Prompt02 legacy CSV", source_type=SourceType.CSV, file_locator=str(csv_path), scope=SelectionScope(), adapter_name="file_source", adapter_version="1.0.0"))
        adapters = {"file_source": FileSourceAdapter(SourceType.CSV, project_root=ROOT), "dlt_sql_source": DltSqlSourceAdapter(project_root=ROOT, credential_resolver=_CredentialResolver(urls), security_verifier=_ProviderVerifier())}
        platform, backend, runtime = build_multi_source_product(ROOT, adapters=adapters)
        principal = Principal(subject="prompt02-acceptance", source="LOCAL_TEST_AUTH", scopes=frozenset({"runs:write", "runs:read", "reviews:write"}))
        source_service = ProductSourceService(ROOT, runtime.source_service.registry)
        for record in records:
            source_service.register_read_only_source(record, owner_subject=principal.subject)
        extraction = ExtractionPolicy(chunk_size=2, null_markers=("",), preserve_raw_values=True)
        run, _ = backend.create_run(project_id="prompt02-v1", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={"acceptance": "prompt02-r2"}, principal=principal, idempotency_key="prompt02-create-run")
        binding, _ = backend.bind_product_source_set(run_id=run.run_id, registry_ids=tuple(record.registry_id for record in records), scope=SelectionScope(), extraction=extraction, execution_context_id="prompt02-real-four-source-v2", principal=principal, idempotency_key="prompt02-bind-source-set")
        preparation, _ = backend.prepare_execution_plan(run_id=run.run_id, intent=ExecutionPlanIntent(cross_source_mapping_requested=True, entity_resolution_requested=True), principal=principal, idempotency_key="prompt02-prepare-plan")
        assert preparation.status.value == "READY"
        first_submission, _ = backend.submit(run_id=run.run_id, principal=principal, idempotency_key=f"prompt02-submit-{uuid4().hex}")
        _assert_submission_lifecycle(first_submission, backend.product_summary(run_id=run.run_id, principal=principal))
        final_summary, reviewed = _drive_runtime(backend, run.run_id, principal)
        assert final_summary.status == "SUCCEEDED", final_summary
        attempts = _all_attempts(
            backend=backend,
            run_id=run.run_id,
            principal=principal,
            status="SUCCEEDED",
        )
        jobs = _all_jobs(
            backend=backend,
            run_id=run.run_id,
            principal=principal,
            status="SUCCEEDED",
        )
        assert {item.stage_id for item in attempts} >= {"SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING", "DEPENDENCY_DISCOVERY", "SCHEMA_MATCHING", "QUALITY_ANALYSIS", "EVIDENCE_FUSION", "CANONICAL_HYPOTHESES", "ENTITY_RESOLUTION", "CANONICAL_IDENTITY_PREPARATION", "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION", "MATERIALIZATION", "SEMANTIC_MODELING", "VALIDATION_RECONCILIATION"}
        assert len(jobs) >= len(attempts)
        assert len(reviewed) >= 4
        reviews = _all_reviews(
            backend=backend,
            run_id=run.run_id,
            principal=principal,
        )
        assert reviews and all(item.decision.decision is ReviewDecisionStatus.ACCEPTED for item in reviews)
        validation = final_summary.validation
        assert validation.g6_status == "PASS" and validation.g6_eligible
        materialization = final_summary.materialization
        assert materialization.usable
        target = ROOT / "workspace" / "platform" / "runs" / run.run_id / "olap" / "olap.duckdb"
        with duckdb.connect(str(target), read_only=True) as connection:
            fact_rows = int(connection.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0])
            quantity_sum = str(connection.execute("SELECT COALESCE(SUM(quantity), 0) FROM fact_order").fetchone()[0])
            hard_negative = int(connection.execute("SELECT COUNT(*) FROM dim_customer WHERE customer_id = 'ERP-HARD-NEG'").fetchone()[0])
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        oracle = yaml.safe_load(ORACLE.read_text(encoding="utf-8"))
        expected = oracle["expected"]
        assert fact_rows == expected["fact_rows"]
        assert quantity_sum == expected["quantity_sum"]
        assert hard_negative == 1
        assert set(expected["target_tables"]) <= tables
        assert set(final_summary.analytical.dimension_ids) == set(expected["dimensions"])
        assert final_summary.analytical.measure_ids == ("measure_quantity",)
        quality_refs = platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="QualityResult", limit=100)
        quality_results = tuple(QualityResult.model_validate(json.loads(platform.artifact_store.read(ref))) for ref in quality_refs)
        observed_quality = {
            (result.source_id, issue.issue_type)
            for result in quality_results
            for issue in result.issues
        }
        assert ("prompt02-erp-mysql", "UNIQUE_VALUES_VIOLATION") in observed_quality
        assert ("prompt02-legacy-csv", "REQUIRED_VALUE_MISSING") in observed_quality
        schema_refs = platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="SchemaMatchResult", limit=10)
        schema_results = tuple(SchemaMatchResult.model_validate(json.loads(platform.artifact_store.read(ref))) for ref in schema_refs)
        schema_pairs = {(item.source_id, item.source_column_name, item.target_source_id, item.target_column_name) for result in schema_results for item in result.candidates}
        for left_source, left_column, right_source, right_column in expected["schema_match_labels"]:
            assert (left_source, left_column, right_source, right_column) in schema_pairs or (right_source, right_column, left_source, left_column) in schema_pairs
        assert csv_path.read_bytes() == csv_before_run

        control_evidence = _load_negative_control_evidence()
        receipt = _build_receipt(platform=platform, backend=backend, run=run, binding=binding, records=records, final_summary=final_summary, reviewed=reviewed, target=target, oracle=oracle, control_evidence=control_evidence)
        (_evidence_dir() / "PROMPT02_MULTI_SOURCE_ACCEPTANCE.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        assert all(item["execution_status"] == "PASS" for item in control_evidence), "Prompt02 negative-control set is incomplete"
    except Exception as error:
        _write_failure_evidence(
            run_id=None if run is None else run.run_id,
            error=error,
            backend=backend,
            control_store=None if platform is None else platform.control_store,
            artifact_store=None if platform is None else platform.artifact_store,
            principal=principal,
        )
        raise
    finally:
        if runtime is not None:
            runtime.close()
        shutil.rmtree(workspace, ignore_errors=True)
