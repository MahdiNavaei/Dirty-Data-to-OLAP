"""Executable Step11 database-security and source-safety validator."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tools.execution_state import is_authorized_specialist_handoff


def main() -> int:
    from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
    from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource
    from dirty_data_to_olap.application.database_security import DatabaseSecurityService
    from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
    from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseAccessError, DatabaseEngine
    from dirty_data_to_olap.domain.contracts.database_security import (
        CredentialPurpose,
        DatabaseSecurityFailureKind,
        PrivilegeFinding,
        PrivilegeFindingStatus,
        ProviderSecurityVerification,
        ProviderVerificationStatus,
        QueryClass,
        SecurityAssuranceStatus,
    )
    from dirty_data_to_olap.domain.contracts.privacy import AggregateSafeMetric
    from dirty_data_to_olap.domain.contracts.source import SelectionScope, SourceRegistryRecord, SourceSelection, SourceType

    checks: list[tuple[str, bool]] = []
    required_docs = (
        "DATABASE_ACCESS_SECURITY.md", "LEAST_PRIVILEGE_ROLES.md", "QUERY_GUARDRAILS.md",
        "CREDENTIAL_ISOLATION.md", "DATABASE_THREAT_MODEL.md", "SOURCE_DATABASE_SECURITY_MATRIX.md",
    )
    checks.append(("database security docs exist", all((ROOT / "docs" / "security" / name).is_file() for name in required_docs)))
    config = yaml.safe_load((ROOT / "config" / "database-security-defaults.yml").read_text(encoding="utf-8"))
    checks.append(("security config is fail closed", config.get("source_purpose") == "SOURCE_READ_ONLY" and config.get("read_only") is True and config.get("require_assurance_before_dlt") is True and config.get("unknown_privileges_block") is True))
    checks.append(("role templates contain no real credentials", all(not any(token in (ROOT / "scripts" / "create_readonly_roles" / name).read_text(encoding="utf-8").lower() for token in ("password=", "secret=", "actual_username")) for name in ("README.md", "postgresql.template.sql", "mysql.template.sql", "sqlserver.template.sql"))))
    checks.append(("runtime security contracts import", True))
    checks.append(("no arbitrary SQL public surface", not ({name for name in dir(SQLiteReadOnlySource) if not name.startswith("_")} & {"execute", "query", "run_sql", "raw_sql", "arbitrary_sql"})))

    guard = DatabaseSecurityService()
    checks.append(("query guard blocks unknown and mutation", not guard.guard_query("UPDATE t SET x=1").allowed and guard.guard_query("SELECT 1").query_class is QueryClass.READ_QUERY and not guard.guard_query("/*x*/ SELECT 1; SELECT 2").allowed))

    def verification(*, service: DatabaseSecurityService, findings: tuple[PrivilegeFinding, ...], status: ProviderVerificationStatus = ProviderVerificationStatus.TECHNICALLY_VERIFIED, credential_reference: str = "vault://source", credential_version: str = "1", evidence: str = "sha256:validator-evidence") -> ProviderSecurityVerification:
        return ProviderSecurityVerification(
            verification_id="validator-verification", engine=DatabaseEngine.POSTGRESQL, source_id="src", profile_id="profile",
            credential_reference=credential_reference, credential_version=credential_version, selection_fingerprint="sha256:selection",
            policy_id=service.policy.policy_id, policy_version=service.policy.version, driver_reference="sqlalchemy",
            status=status, findings=findings, evidence_fingerprint=evidence,
        )

    requirement = next(item for item in guard.policy.requirements if item.engine is DatabaseEngine.POSTGRESQL)
    complete_findings = tuple(PrivilegeFinding(privilege=item, status=PrivilegeFindingStatus.PRESENT_REQUIRED, reason="validator technical evidence") for item in requirement.required) + tuple(PrivilegeFinding(privilege=item, status=PrivilegeFindingStatus.ABSENT_FORBIDDEN, reason="validator technical evidence") for item in requirement.forbidden)
    complete = guard.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=verification(service=guard, findings=complete_findings))
    incomplete = guard.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=verification(service=guard, findings=(complete_findings[0],)))
    forbidden = guard.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=verification(service=guard, findings=complete_findings[:-1] + (PrivilegeFinding(privilege=requirement.forbidden[-1], status=PrivilegeFindingStatus.FORBIDDEN_PRESENT, reason="validator negative evidence"),)))
    unknown = guard.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=verification(service=guard, findings=complete_findings + (PrivilegeFinding(privilege="UNPARSEABLE", status=PrivilegeFindingStatus.UNKNOWN, reason="validator negative evidence"),)))
    unavailable = guard.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=None)
    checks.append(("complete provider evidence passes", complete.status is SecurityAssuranceStatus.PASS))
    checks.append(("incomplete required evidence blocks", incomplete.status is SecurityAssuranceStatus.BLOCKED and incomplete.failures[0].kind is DatabaseSecurityFailureKind.MISSING_REQUIRED_PRIVILEGE))
    checks.append(("forbidden privilege blocks", forbidden.status is SecurityAssuranceStatus.BLOCKED))
    checks.append(("unknown privilege blocks", unknown.status is SecurityAssuranceStatus.BLOCKED))
    checks.append(("missing verifier blocks", unavailable.status is SecurityAssuranceStatus.BLOCKED and unavailable.failures[0].kind is DatabaseSecurityFailureKind.VERIFIER_UNAVAILABLE))
    try:
        RuntimeSqlCredentials("postgresql://runtime", provider_security_verified=True)  # type: ignore[call-arg]
        self_assertion_blocked = False
    except TypeError:
        self_assertion_blocked = True
    empty = guard.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=verification(service=guard, findings=()))
    checks.append(("credential cannot self-certify provider safety", self_assertion_blocked and empty.status is SecurityAssuranceStatus.BLOCKED))

    class CountingResolver:
        calls = 0
        last: RuntimeSqlCredentials | None = None

        def resolve(self, profile, *, required_purpose, source_id):
            self.calls += 1
            self.last = RuntimeSqlCredentials("postgresql://runtime", credential_reference="vault://source", credential_purpose=required_purpose, credential_version="2", source_id=source_id)
            return self.last

    class CompleteVerifier:
        def verify(self, *, profile, credentials, source_id, profile_id, selection_fingerprint, policy):
            findings = tuple(PrivilegeFinding(privilege=item, status=PrivilegeFindingStatus.PRESENT_REQUIRED, reason="validator technical evidence") for item in requirement.required) + tuple(PrivilegeFinding(privilege=item, status=PrivilegeFindingStatus.ABSENT_FORBIDDEN, reason="validator technical evidence") for item in requirement.forbidden)
            return ProviderSecurityVerification(verification_id="validator-complete", engine=profile.database_engine, source_id=source_id, profile_id=profile_id, credential_reference=credentials.credential_reference, credential_version=credentials.credential_version, selection_fingerprint=selection_fingerprint, policy_id=policy.policy_id, policy_version=policy.version, driver_reference="sqlalchemy", status=ProviderVerificationStatus.TECHNICALLY_VERIFIED, findings=findings, evidence_fingerprint="sha256:validator-complete")

    record = SourceRegistryRecord(registry_id="validator-postgres", source_id="validator-source", display_name="validator provider", source_type=SourceType.POSTGRESQL, connection_profile=ConnectionProfileReference(profile_id="validator-profile", database_engine=DatabaseEngine.POSTGRESQL, host="db.example", port=5432, database_name="source", credential_reference="vault://source"), scope=SelectionScope(included_objects=("customers",)), adapter_name="dlt_sql_source", adapter_version="1.0.0")

    class CaptureAdapter(DltSqlSourceAdapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.captured_credentials = None
            self.captured_context = None

        def _engine(self, profile, *, source_id=None, runtime_credentials=None):
            self.captured_credentials = runtime_credentials
            return SimpleNamespace(dispose=lambda: None)

        def _dlt_database(self, engine, table_names, selection, chunk_size, *, security_context):
            self.captured_context = security_context
            return SimpleNamespace(resources={})

    resolver = CountingResolver()
    adapter = CaptureAdapter(project_root=ROOT, credential_resolver=resolver, security_verifier=CompleteVerifier())
    adapter.discover_source(SourceSelection(registry_id=record.registry_id), record)
    checks.append(("one protected operation resolves credentials once", resolver.calls == 1 and adapter.captured_credentials is resolver.last and adapter.captured_context.runtime_credentials is resolver.last))

    class FailingVerifier:
        def verify(self, **kwargs):
            raise RuntimeError("verifier failure")

    class SentinelAdapter(DltSqlSourceAdapter):
        dlt_reached = False

        def _dlt_database(self, *args, **kwargs):
            self.dlt_reached = True
            raise AssertionError("dlt was reached after verifier failure")

    failing = SentinelAdapter(project_root=ROOT, credential_resolver=CountingResolver(), security_verifier=FailingVerifier())
    try:
        failing.discover_source(SourceSelection(registry_id=record.registry_id), record)
    except Exception:
        pass
    checks.append(("verifier failure blocks before dlt", failing.dlt_reached is False))

    runtime = RuntimeSqlCredentials("postgresql://runtime")
    checks.append(("runtime credential representation is redacted", "postgresql://runtime" not in repr(runtime)))
    metric = AggregateSafeMetric(metric_id="row_count", aggregate_kind="count", value=1, derivation_scope="table", privacy_classification="aggregate", provenance="validator")
    privacy = PrivacyPolicyService()
    checks.append(("numeric identifier is not aggregate-safe", not privacy.prepare_external_payload({"customer_id": 123456}, aggregate_only=True).allowed and privacy.prepare_external_payload({"row_count": metric}, aggregate_only=True).allowed))
    checks.append(("unknown log values are redacted", privacy.sanitize_log_value("unknown unicode: تهران") == "<REDACTED_UNKNOWN>"))

    scratch_root = ROOT / "workspace" / "test-temp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="step11-security-", dir=scratch_root) as directory:
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
    if scratch_root.exists() and not any(scratch_root.iterdir()):
        scratch_root.rmdir()

    state = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state.get("specialist_execution", {})
    g3 = state.get("gates", {}).get("G3_SOURCE_SAFETY")
    gate_text = (ROOT / "docs" / "execution" / "gates" / "G3_SOURCE_SAFETY.md").read_text(encoding="utf-8")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    checks.append(("execution is at a later specialist handoff", is_authorized_specialist_handoff(state, minimum_current_step=15, maximum_current_step=30)))
    checks.append(("G3 state and canonical gate agree", g3 == "PASS" and "Status: `PASS`" in gate_text))
    checks.append(("README agrees with G3 and specialist handoff", ("Steps 01-14 complete" in readme_text or "Steps 01-15 complete" in readme_text or "Steps 01-16 complete" in readme_text or "Steps 01-17 complete" in readme_text or "Steps 01-18 complete" in readme_text or "Steps 01-19 complete" in readme_text or "Steps 01-20" in readme_text or "Steps 01-21" in readme_text) and "G0/G1/G2/G3 PASS" in readme_text and "Step15" in readme_text and "formal G3" not in readme_text.lower()))
    checks.append(("Step14 entity-resolution implementation is present", execution.get("last_completed_step", 0) >= 13 and (ROOT / "src" / "dirty_data_to_olap" / "adapters" / "entity_resolution" / "splink.py").exists()))
    failed = [name for name, passed in checks if not passed]
    if failed:
        print("FAIL")
        print("\n".join(f"- {name}" for name in failed))
        return 1
    print(f"PASS: database_security_checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
