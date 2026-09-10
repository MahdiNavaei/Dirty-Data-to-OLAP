"""Fail-closed source database security policy and query guardrails."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.domain.contracts.database import DatabaseEngine
from dirty_data_to_olap.domain.contracts.database_security import (
    CredentialPurpose,
    DatabaseSecurityAssessment,
    DatabaseSecurityAuditEvent,
    DatabaseSecurityAssurance,
    DatabaseSecurityFailure,
    DatabaseSecurityFailureKind,
    DatabaseSecurityPolicy,
    DriverSecurityPolicy,
    PrivilegeFinding,
    QueryClass,
    QueryGuardDecision,
    ReadOnlyEnforcementMethod,
    SecurityAssuranceStatus,
    SourcePrivilegeRequirement,
)


class DatabaseSecurityOperationError(ValueError):
    def __init__(self, failure: DatabaseSecurityFailure) -> None:
        self.failure = failure
        super().__init__(failure.detail)


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _default_requirements() -> tuple[SourcePrivilegeRequirement, ...]:
    return (
        SourcePrivilegeRequirement(engine=DatabaseEngine.POSTGRESQL, required=("CONNECT", "USAGE", "SELECT"), allowed_optional=("VIEW_DEFINITION",), forbidden=("SUPERUSER", "CREATEDB", "CREATEROLE", "REPLICATION", "BYPASSRLS", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "EXECUTE", "TRUNCATE", "TEMP")),
        SourcePrivilegeRequirement(engine=DatabaseEngine.MYSQL, required=("SELECT",), allowed_optional=("SHOW VIEW", "METADATA"), forbidden=("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "EXECUTE", "FILE", "PROCESS", "SUPER", "GRANT OPTION", "CREATE USER", "TRIGGER", "EVENT", "RELOAD")),
        SourcePrivilegeRequirement(engine=DatabaseEngine.MARIADB, required=("SELECT",), allowed_optional=("SHOW VIEW", "METADATA"), forbidden=("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "EXECUTE", "FILE", "PROCESS", "SUPER", "GRANT OPTION", "CREATE USER", "TRIGGER", "EVENT", "RELOAD")),
        SourcePrivilegeRequirement(engine=DatabaseEngine.SQLSERVER, required=("CONNECT", "SELECT"), allowed_optional=("VIEW DEFINITION",), forbidden=("CONTROL", "ALTER", "INSERT", "UPDATE", "DELETE", "EXECUTE", "TAKE OWNERSHIP", "IMPERSONATE", "db_owner", "sysadmin")),
    )


def load_database_security_policy(path: Path | None = None) -> DatabaseSecurityPolicy:
    """Load the runtime-authoritative policy; unsafe or malformed config fails."""
    config_path = path or Path(__file__).resolve().parents[3] / "config" / "database-security-defaults.yml"
    try:
        import yaml

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ValueError("database security configuration must be a mapping")
        raw = dict(raw)
        requirements = raw.get("requirements")
        if requirements is None:
            raw["requirements"] = [item.model_dump(mode="json", exclude={"schema_version"}) for item in _default_requirements()]
        return DatabaseSecurityPolicy.model_validate(raw)
    except Exception as error:
        raise DatabaseSecurityOperationError(DatabaseSecurityFailure(failure_id="database-security-config", kind=DatabaseSecurityFailureKind.SECURITY_CONFIGURATION_INVALID, operation="load_security_policy", detail=f"security policy could not be loaded: {error.__class__.__name__}")) from None


_COMMENT = re.compile(r"(--[^\r\n]*|/\*.*?\*/)", re.S)


def classify_query(sql: str, *, policy_id: str = "database-security-v1") -> QueryGuardDecision:
    """Classify a query for the narrow internal guard; literals are not retained."""
    text = str(sql)
    normalized = _COMMENT.sub(" ", text).strip()
    upper = normalized.upper()
    fingerprint = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    query_class = QueryClass.UNKNOWN
    reason = "query shape is not in the source-read allowlist"
    if not normalized or ";" in normalized:
        query_class, reason = QueryClass.MULTI_STATEMENT, "multi-statement execution is blocked"
    elif re.search(r"\b(ATTACH|DETACH)\b", upper) or re.search(r"\bVACUUM\s+INTO\b", upper) or re.search(r"\b(PRAGMA\s+WRITABLE_SCHEMA|PRAGMA\s+LOAD_EXTENSION)\b", upper):
        query_class, reason = QueryClass.DDL, "database attachment, export or dangerous pragma is blocked"
    elif re.search(r"\b(OUTFILE|DUMPFILE)\b", upper) or re.search(r"\bCOPY\s+.+\s+TO\b", upper) or re.search(r"\bSELECT\b.+\bINTO\b", upper, re.S):
        query_class, reason = QueryClass.FILESYSTEM_EXPORT, "filesystem export is blocked"
    elif re.match(r"^(CALL|EXEC(?:UTE)?|DO)\b", upper) or re.search(r"\b(LOAD_EXTENSION|XP_CMDSHELL)\b", upper):
        query_class, reason = QueryClass.PROCEDURE_EXECUTION, "procedure or extension execution is blocked"
    elif re.match(r"^(CREATE|ALTER|DROP|TRUNCATE|REINDEX|GRANT|REVOKE)\b", upper):
        query_class, reason = QueryClass.DDL, "DDL or privilege mutation is blocked"
    elif re.match(r"^(INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT)\b", upper) or re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT)\b", upper.split("SELECT", 1)[0]) or (upper.startswith("WITH") and re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT)\b", upper)):
        query_class, reason = QueryClass.WRITE_DML, "write DML is blocked"
    elif re.match(r"^(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b", upper):
        query_class, reason = QueryClass.TRANSACTION_CONTROL_ALLOWED, "transaction control is limited to the read-only session"
    elif re.match(r"^(PRAGMA|SHOW|DESCRIBE|DESC|EXPLAIN)\b", upper) or "SQLITE_MASTER" in upper or "INFORMATION_SCHEMA" in upper or "SYS." in upper:
        query_class, reason = QueryClass.METADATA_QUERY, "metadata query"
    elif re.match(r"^(SELECT|WITH)\b", upper):
        query_class, reason = QueryClass.READ_QUERY, "read query"
    allowed = query_class in {QueryClass.READ_QUERY, QueryClass.METADATA_QUERY, QueryClass.TRANSACTION_CONTROL_ALLOWED}
    return QueryGuardDecision(decision_id="query-decision-" + fingerprint[7:23], query_class=query_class, allowed=allowed, reason=reason, query_fingerprint=fingerprint, policy_id=policy_id)


class DatabaseSecurityService:
    def __init__(self, policy: DatabaseSecurityPolicy | None = None, *, policy_path: Path | None = None) -> None:
        self.policy = policy or load_database_security_policy(policy_path)

    def failure(self, kind: DatabaseSecurityFailureKind, operation: str, *, source_id: str | None = None, profile_id: str | None = None) -> DatabaseSecurityOperationError:
        return DatabaseSecurityOperationError(DatabaseSecurityFailure(failure_id=f"database-security-{kind.value.lower()}", kind=kind, operation=operation, detail=f"database security blocked operation: {kind.value.lower()}", source_id=source_id, profile_id=profile_id))

    def assess_source(
        self,
        *,
        source_id: str,
        profile_id: str,
        engine: DatabaseEngine,
        credential_reference: str,
        credential_purpose: CredentialPurpose,
        credential_version: str,
        selection_fingerprint: str,
        driver_reference: str,
        provider_verified: bool,
        privilege_findings: Sequence[PrivilegeFinding] = (),
        enforcement_methods: Sequence[ReadOnlyEnforcementMethod] = (),
    ) -> DatabaseSecurityAssessment:
        findings = tuple(privilege_findings)
        status = SecurityAssuranceStatus.PASS
        if credential_purpose is not CredentialPurpose.SOURCE_READ_ONLY:
            status = SecurityAssuranceStatus.BLOCKED
        elif engine is not DatabaseEngine.SQLITE and not provider_verified:
            status = SecurityAssuranceStatus.BLOCKED
        elif any(item.status.upper() in {"FORBIDDEN", "MISSING", "UNKNOWN", "BLOCKED"} for item in findings):
            status = SecurityAssuranceStatus.BLOCKED
        assurance = DatabaseSecurityAssurance(assurance_id="assurance-" + _digest((source_id, profile_id, selection_fingerprint, self.policy.version))[:24], source_id=source_id, profile_id=profile_id, credential_reference=credential_reference, credential_purpose=credential_purpose, credential_version=credential_version, engine=engine, selection_fingerprint=selection_fingerprint, policy_id=self.policy.policy_id, policy_version=self.policy.version, driver_reference=driver_reference, status=status, enforcement_methods=tuple(enforcement_methods), findings=findings, created_at=datetime.now(timezone.utc))
        failures: tuple[DatabaseSecurityFailure, ...] = () if status is SecurityAssuranceStatus.PASS else (DatabaseSecurityFailure(failure_id="database-security-assurance", kind=DatabaseSecurityFailureKind.CREDENTIAL_PURPOSE_MISMATCH if credential_purpose is not CredentialPurpose.SOURCE_READ_ONLY else DatabaseSecurityFailureKind.UNVERIFIED_PROVIDER if engine is not DatabaseEngine.SQLITE and not provider_verified else DatabaseSecurityFailureKind.UNSAFE_PRIVILEGE, operation="assess_source", detail="source security assurance did not pass", source_id=source_id, profile_id=profile_id),)
        return DatabaseSecurityAssessment(assessment_id="assessment-" + _digest((source_id, profile_id, self.policy.version))[:24], source_id=source_id, profile_id=profile_id, engine=engine, policy_id=self.policy.policy_id, status=status, assurance=assurance, privilege_findings=findings, failures=failures)

    def require_pass(self, assessment: DatabaseSecurityAssessment) -> DatabaseSecurityAssurance:
        if assessment.status is not SecurityAssuranceStatus.PASS:
            raise DatabaseSecurityOperationError(assessment.failures[0] if assessment.failures else DatabaseSecurityFailure(failure_id="database-security-assurance", kind=DatabaseSecurityFailureKind.SECURITY_ENFORCEMENT_FAILED, operation="require_assurance", detail="source security assurance did not pass"))
        return assessment.assurance

    def guard_query(self, sql: str) -> QueryGuardDecision:
        return classify_query(sql, policy_id=self.policy.policy_id)

    def audit_event(self, *, source_id: str, profile_id: str, engine: DatabaseEngine, credential_reference: str, assurance_status: SecurityAssuranceStatus, sql: str) -> DatabaseSecurityAuditEvent:
        decision = self.guard_query(sql)
        return DatabaseSecurityAuditEvent(event_id="db-audit-" + decision.query_fingerprint[7:23], source_id=source_id, profile_id=profile_id, engine=engine, query_class=decision.query_class, decision="ALLOW" if decision.allowed else "BLOCK", policy_id=self.policy.policy_id, credential_reference=credential_reference, assurance_status=assurance_status, query_fingerprint=decision.query_fingerprint, created_at=datetime.now(timezone.utc))

    @staticmethod
    def driver_policy(engine: DatabaseEngine, driver_name: str, driver_version: str) -> DriverSecurityPolicy:
        return DriverSecurityPolicy(engine=engine, driver_name=driver_name, driver_version=driver_version, required_connect_hooks=("read_only_initialization",), callbacks_allowed=False)
