"""Executable Step11 database-security and source-safety validator."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    checks: list[tuple[str, bool]] = []
    required_docs = (
        "DATABASE_ACCESS_SECURITY.md", "LEAST_PRIVILEGE_ROLES.md", "QUERY_GUARDRAILS.md",
        "CREDENTIAL_ISOLATION.md", "DATABASE_THREAT_MODEL.md", "SOURCE_DATABASE_SECURITY_MATRIX.md",
    )
    checks.append(("database security docs exist", all((ROOT / "docs" / "security" / name).is_file() for name in required_docs)))
    config = yaml.safe_load((ROOT / "config" / "database-security-defaults.yml").read_text(encoding="utf-8"))
    checks.append(("security config is fail closed", config.get("source_purpose") == "SOURCE_READ_ONLY" and config.get("read_only") is True and config.get("require_assurance_before_dlt") is True and config.get("unknown_privileges_block") is True))
    checks.append(("role templates contain no real credentials", all(not any(token in (ROOT / "scripts" / "create_readonly_roles" / name).read_text(encoding="utf-8").lower() for token in ("password=", "secret=", "actual_username")) for name in ("README.md", "postgresql.template.sql", "mysql.template.sql", "sqlserver.template.sql"))))
    try:
        from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
        from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource
        from dirty_data_to_olap.application.database_security import DatabaseSecurityService, classify_query
        from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseAccessError, DatabaseEngine
        from dirty_data_to_olap.domain.contracts.database_security import CredentialPurpose, QueryClass, SecurityAssuranceStatus
        from dirty_data_to_olap.domain.contracts.privacy import AggregateSafeMetric
        from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService

        checks.append(("project-owned security contracts import", True))
        checks.append(("no arbitrary SQL public surface", not ({name for name in dir(SQLiteReadOnlySource) if not name.startswith("_")} & {"execute", "query", "run_sql", "raw_sql", "arbitrary_sql"})))
        checks.append(("query guard blocks unknown and mutation", not classify_query("UPDATE t SET x=1").allowed and classify_query("SELECT 1").query_class is QueryClass.READ_QUERY and not classify_query("/*x*/ SELECT 1; SELECT 2").allowed))
        checks.append(("non-SQLite unverified provider blocks", DatabaseSecurityService().assess_source(source_id="s", profile_id="p", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="sqlalchemy", provider_verified=False).status is SecurityAssuranceStatus.BLOCKED))
        runtime = RuntimeSqlCredentials("postgresql://u:password=FAKE@h/db")
        checks.append(("runtime credential representation is redacted", "FAKE" not in repr(runtime) and "FAKE" not in str(runtime)))
        metric = AggregateSafeMetric(metric_id="row_count", aggregate_kind="count", value=1, derivation_scope="table", privacy_classification="aggregate", provenance="test")
        privacy = PrivacyPolicyService()
        checks.append(("numeric identifier is not aggregate-safe", not privacy.prepare_external_payload({"customer_id": 123456}, aggregate_only=True).allowed and privacy.prepare_external_payload({"row_count": metric}, aggregate_only=True).allowed))
        checks.append(("unknown log values are redacted", privacy.sanitize_log_value("unknown unicode: تهران") == "<REDACTED_UNKNOWN>"))
        with tempfile.TemporaryDirectory(prefix="step11-security-") as directory:
            path = Path(directory) / "source.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO t VALUES (1, 'stable')")
            connection.commit()
            connection.close()
            before = path.read_bytes()
            profile = ConnectionProfileReference(profile_id="validator", database_engine=DatabaseEngine.SQLITE, database_name=str(path))
            with SQLiteReadOnlySource(profile).open_readonly_session() as session:
                session.inspect_database_metadata()
                try:
                    session._run("DELETE FROM t", operation="validator-attack")
                except DatabaseAccessError:
                    pass
            checks.append(("SQLite source remains unchanged after attack", path.read_bytes() == before))
    except Exception as error:
        checks.append((f"security runtime checks ({error.__class__.__name__})", False))
    state = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state.get("specialist_execution", {})
    g3 = state.get("gates", {}).get("G3_SOURCE_SAFETY")
    checks.append(("execution remains at Step11 until G3 evidence is complete", (execution.get("current_step") == 11 and g3 in {"PENDING", "BLOCKED"}) or (execution.get("current_step") == 12 and g3 == "PASS")))
    checks.append(("Step12 implementation has not begun", not (ROOT / "src" / "dirty_data_to_olap" / "application" / "dependency_discovery.py").exists()))
    failed = [name for name, passed in checks if not passed]
    if failed:
        print("FAIL")
        print("\n".join(f"- {name}" for name in failed))
        return 1
    print(f"PASS: database_security_checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
