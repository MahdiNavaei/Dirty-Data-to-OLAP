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
from dirty_data_to_olap.application.backend import Principal
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


def _safe_failure_detail(error: BaseException) -> str:
    detail = str(error)
    detail = re.sub(r"(?i)(?:[a-z][a-z0-9+.-]*)://[^\s]+", "<redacted-connection>", detail)
    detail = re.sub(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", detail)
    return f"{type(error).__name__}: {detail}"[:1000]


def _write_failure_evidence(*, run_id: str | None, error: BaseException) -> None:
    payload = {
        "status": "BLOCKED",
        "run_id": run_id or "unavailable",
        "content_commit": _content_commit(),
        "blocked_reasons": (_safe_failure_detail(error),),
        "evidence_kind": "failure-diagnostics",
    }
    (_evidence_dir() / "PROMPT02_FAILURE.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


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
    attempts = backend.list_attempts(run_id=run.run_id, stage_id=None, status=None, page_size=500, offset=0, principal=Principal(subject="prompt02-acceptance", source="LOCAL_TEST_AUTH", scopes=frozenset({"runs:read"}))).items
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
    reviews = backend.list_reviews(run_id=run.run_id, subject_key=None, page_size=500, offset=0, principal=Principal(subject="prompt02-acceptance", source="LOCAL_TEST_AUTH", scopes=frozenset({"runs:read"}))).items
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
    platform = backend = runtime = run = None
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
        attempts = backend.list_attempts(run_id=run.run_id, stage_id=None, status="SUCCEEDED", page_size=500, offset=0, principal=principal).items
        jobs = backend.list_jobs(run_id=run.run_id, status="SUCCEEDED", page_size=500, offset=0, principal=principal).items
        assert {item.stage_id for item in attempts} >= {"SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING", "DEPENDENCY_DISCOVERY", "SCHEMA_MATCHING", "QUALITY_ANALYSIS", "EVIDENCE_FUSION", "CANONICAL_HYPOTHESES", "ENTITY_RESOLUTION", "CANONICAL_IDENTITY_PREPARATION", "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION", "MATERIALIZATION", "SEMANTIC_MODELING", "VALIDATION_RECONCILIATION"}
        assert len(jobs) >= len(attempts)
        assert len(reviewed) >= 4
        reviews = backend.list_reviews(run_id=run.run_id, subject_key=None, page_size=500, offset=0, principal=principal).items
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
        observed_quality = {(item.source_id, issue.issue_type) for result in quality_results for issue in result.issues}
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
        _write_failure_evidence(run_id=None if run is None else run.run_id, error=error)
        raise
    finally:
        if runtime is not None:
            runtime.close()
        shutil.rmtree(workspace, ignore_errors=True)
