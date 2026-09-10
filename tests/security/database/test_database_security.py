from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource
from dirty_data_to_olap.application.database_security import (
    DatabaseSecurityService,
    classify_query,
)
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.database import (
    BoundedSampleRequest,
    ConnectionProfileReference,
    DatabaseAccessError,
    DatabaseEngine,
    DatabaseIdentifier,
    SamplingPolicy,
    DatabaseFailure,
    DatabaseFailureKind,
)
from dirty_data_to_olap.domain.contracts.database_security import (
    CredentialPurpose,
    DatabaseSecurityFailureKind,
    ProviderSecurityVerification,
    ProviderVerificationStatus,
    PrivilegeFinding,
    PrivilegeFindingStatus,
    QueryClass,
    SecurityAssuranceStatus,
)
from dirty_data_to_olap.domain.contracts.privacy import AggregateSafeMetric
from dirty_data_to_olap.domain.contracts.source import SelectionScope, SourceFailure, SourceFailureKind, SourceRegistryRecord, SourceSelection, SourceType


def test_sqlite_defense_in_depth_and_bounded_reads(sqlite_fixture_path: Path) -> None:
    profile = ConnectionProfileReference(profile_id="security-sqlite", database_engine=DatabaseEngine.SQLITE, database_name=str(sqlite_fixture_path))
    with SQLiteReadOnlySource(profile).open_readonly_session() as session:
        assert session.sample_rows_bounded(BoundedSampleRequest(table=DatabaseIdentifier(name="parent"), columns=("parent_id",), sampling_policy=SamplingPolicy(max_rows=1))).rows_observed == 1
        attacks = (
            "INSERT INTO parent VALUES (9, 'blocked', NULL)",
            "UPDATE parent SET parent_code = 'blocked'",
            "DELETE FROM parent",
            "CREATE TABLE attack(value TEXT)",
            "ATTACH DATABASE ':memory:' AS other",
            "PRAGMA writable_schema = ON",
            "VACUUM INTO 'export.db'",
            "SELECT 1; SELECT 2",
            "/* safe */ SELECT 1; -- second statement\n SELECT 2",
        )
        for statement in attacks:
            with pytest.raises(DatabaseAccessError):
                session._run(statement, operation="security-negative")
    assert not (sqlite_fixture_path.parent / "export.db").exists()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SELECT parent_id FROM parent", QueryClass.READ_QUERY),
        ("PRAGMA table_info(parent)", QueryClass.METADATA_QUERY),
        ("BEGIN", QueryClass.TRANSACTION_CONTROL_ALLOWED),
        ("UPDATE parent SET parent_code='x'", QueryClass.WRITE_DML),
        ("CREATE TABLE x(a int)", QueryClass.DDL),
        ("CALL dangerous_proc()", QueryClass.PROCEDURE_EXECUTION),
        ("SELECT * FROM parent INTO OUTFILE 'x'", QueryClass.FILESYSTEM_EXPORT),
        ("SELECT 1; SELECT 2", QueryClass.MULTI_STATEMENT),
        ("nonsense source command", QueryClass.UNKNOWN),
    ],
)
def test_query_guard_is_fail_closed(statement: str, expected: QueryClass) -> None:
    decision = classify_query(statement)
    assert decision.query_class is expected
    assert decision.allowed is (expected in {QueryClass.READ_QUERY, QueryClass.METADATA_QUERY, QueryClass.TRANSACTION_CONTROL_ALLOWED})
    assert statement not in decision.model_dump_json()


def test_assurance_requires_source_purpose_and_verified_non_sql_provider() -> None:
    service = DatabaseSecurityService()
    blocked = service.assess_source(source_id="src-1", profile_id="profile-1", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlalchemy", technical_verification=None)
    assert blocked.status is SecurityAssuranceStatus.BLOCKED
    assert blocked.failures[0].kind is DatabaseSecurityFailureKind.VERIFIER_UNAVAILABLE
    wrong = service.assess_source(source_id="src-1", profile_id="profile-1", engine=DatabaseEngine.SQLITE, credential_reference="vault://target", credential_purpose=CredentialPurpose.TARGET_WRITE, credential_version="1", selection_fingerprint="sha256:selection", driver_reference="sqlite3", technical_verification=None)
    assert wrong.status is SecurityAssuranceStatus.BLOCKED


def test_privilege_findings_block_forbidden_missing_and_unknown() -> None:
    service = DatabaseSecurityService()
    for status in (PrivilegeFindingStatus.FORBIDDEN_PRESENT, PrivilegeFindingStatus.REQUIRED_MISSING, PrivilegeFindingStatus.UNKNOWN):
        verification = _verification(service, findings=(PrivilegeFinding(privilege="UPDATE", status=status, reason="fixture"),))
        assessment = service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=verification)
        assert assessment.status is SecurityAssuranceStatus.BLOCKED


def _verification(service: DatabaseSecurityService, *, findings: tuple[PrivilegeFinding, ...], status: ProviderVerificationStatus = ProviderVerificationStatus.TECHNICALLY_VERIFIED, credential_reference: str = "vault://source", credential_version: str = "1", evidence_fingerprint: str = "sha256:technical-evidence") -> ProviderSecurityVerification:
    return ProviderSecurityVerification(
        verification_id="verification-fixture",
        engine=DatabaseEngine.POSTGRESQL,
        source_id="src",
        profile_id="profile",
        credential_reference=credential_reference,
        credential_version=credential_version,
        selection_fingerprint="sha256:x",
        policy_id=service.policy.policy_id,
        policy_version=service.policy.version,
        driver_reference="psycopg",
        status=status,
        findings=findings,
        evidence_fingerprint=evidence_fingerprint,
    )


def _complete_postgresql_findings(service: DatabaseSecurityService) -> tuple[PrivilegeFinding, ...]:
    requirement = next(item for item in service.policy.requirements if item.engine is DatabaseEngine.POSTGRESQL)
    return tuple(
        PrivilegeFinding(privilege=privilege, status=PrivilegeFindingStatus.PRESENT_REQUIRED, reason="technical fixture")
        for privilege in requirement.required
    ) + tuple(
        PrivilegeFinding(privilege=privilege, status=PrivilegeFindingStatus.ABSENT_FORBIDDEN, reason="technical fixture")
        for privilege in requirement.forbidden
    )


def test_non_sql_assurance_requires_complete_required_and_forbidden_coverage() -> None:
    service = DatabaseSecurityService()
    incomplete = _verification(service, findings=(PrivilegeFinding(privilege="CONNECT", status=PrivilegeFindingStatus.PRESENT_REQUIRED, reason="technical fixture"),))
    assert service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=incomplete).status is SecurityAssuranceStatus.BLOCKED
    complete = _verification(service, findings=_complete_postgresql_findings(service))
    passed = service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=complete)
    assert passed.status is SecurityAssuranceStatus.PASS
    forbidden = _verification(service, findings=_complete_postgresql_findings(service) + (PrivilegeFinding(privilege="UPDATE", status=PrivilegeFindingStatus.FORBIDDEN_PRESENT, reason="technical fixture"),))
    assert service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=forbidden).status is SecurityAssuranceStatus.BLOCKED


def test_self_assertion_and_non_closed_finding_states_cannot_authorize() -> None:
    with pytest.raises(TypeError):
        RuntimeSqlCredentials("postgresql://runtime", provider_security_verified=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        PrivilegeFinding(privilege="SELECT", status="SAFE", reason="not a closed state")
    service = DatabaseSecurityService()
    unknown = _verification(service, findings=_complete_postgresql_findings(service) + (PrivilegeFinding(privilege="UNPARSEABLE", status=PrivilegeFindingStatus.UNKNOWN, reason="technical fixture"),))
    assert service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=unknown).status is SecurityAssuranceStatus.BLOCKED


def test_assurance_identity_binds_credential_and_verification_evidence() -> None:
    service = DatabaseSecurityService()
    findings = _complete_postgresql_findings(service)
    first = service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=_verification(service, findings=findings, evidence_fingerprint="sha256:evidence-a")).assurance
    changed_evidence = service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="1", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=_verification(service, findings=findings, evidence_fingerprint="sha256:evidence-b")).assurance
    changed_credential = service.assess_source(source_id="src", profile_id="profile", engine=DatabaseEngine.POSTGRESQL, credential_reference="vault://source-rotated", credential_purpose=CredentialPurpose.SOURCE_READ_ONLY, credential_version="2", selection_fingerprint="sha256:x", driver_reference="psycopg", technical_verification=_verification(service, findings=findings, credential_reference="vault://source-rotated", credential_version="2", evidence_fingerprint="sha256:evidence-a")).assurance
    assert first.assurance_id != changed_evidence.assurance_id
    assert first.assurance_id != changed_credential.assurance_id


def test_audit_and_runtime_credentials_are_safe() -> None:
    service = DatabaseSecurityService()
    event = service.audit_event(source_id="src", profile_id="profile", engine=DatabaseEngine.SQLITE, credential_reference="vault://source", assurance_status=SecurityAssuranceStatus.PASS, sql="SELECT * FROM customers WHERE email='secret@example.test'")
    serialized = event.model_dump_json()
    assert "secret@example.test" not in serialized
    assert "SELECT" not in serialized
    runtime = RuntimeSqlCredentials("postgresql://user:password=FAKE_SECRET@host/db", credential_reference="vault://source")
    assert "FAKE_SECRET" not in repr(runtime) and "FAKE_SECRET" not in str(runtime)


def test_failure_contracts_redact_secret_and_dsn_canaries() -> None:
    secret = "STEP11_FAKE_DB_SECRET"
    database_failure = DatabaseFailure(kind=DatabaseFailureKind.ACCESS_DENIED, database_engine=DatabaseEngine.POSTGRESQL, operation="connect", detail=f"password={secret} postgresql://user:{secret}@host/db", retryable=False, cause_category="authorization")
    source_failure = SourceFailure(kind=SourceFailureKind.ACCESS_FAILED, operation="connect", detail=f"password={secret} postgresql://user:{secret}@host/db", retryable=False)
    assert secret not in database_failure.model_dump_json() and secret not in str(database_failure)
    assert secret not in source_failure.model_dump_json() and secret not in str(source_failure)
    from dirty_data_to_olap.domain.contracts.database_security import DatabaseSecurityFailure
    security_failure = DatabaseSecurityFailure(failure_id="f", kind=DatabaseSecurityFailureKind.SECURITY_ENFORCEMENT_FAILED, operation="connect", detail=f"token={secret}")
    assert secret not in security_failure.model_dump_json() and secret not in str(security_failure)


def test_aggregate_contract_and_privacy_unknown_canaries(tmp_path: Path) -> None:
    metric = AggregateSafeMetric(metric_id="row_count", aggregate_kind="count", value=4, derivation_scope="fixture-table", privacy_classification="NON_SENSITIVE_AGGREGATE", provenance="quality-result")
    service = PrivacyPolicyService(project_root=tmp_path)
    assert service.prepare_external_payload({"row_count": metric}, aggregate_only=True).allowed
    assert not service.prepare_external_payload({"customer_id": 123456}, aggregate_only=True).allowed
    unknown = service.scan_values("scan", ("ordinary free text: تهران",), allow_raw_staging=False)
    assert not unknown.clean and unknown.raw_unknown_matches == 1
    safe = service.sanitize_log_value("ordinary free text: تهران")
    assert safe == "<REDACTED_UNKNOWN>"


def test_privacy_config_is_runtime_authoritative(tmp_path: Path) -> None:
    source = Path("config/privacy-defaults.yml")
    changed = tmp_path / "privacy.yml"
    text = source.read_text(encoding="utf-8").replace("raw_staging_allowed: true", "raw_staging_allowed: false")
    changed.write_text(text, encoding="utf-8")
    service = PrivacyPolicyService(config_path=changed)
    assert service.policy.raw_staging_allowed is False


def test_sqlite_public_surface_has_no_arbitrary_sql_method() -> None:
    public = {name for name in dir(SQLiteReadOnlySource) if not name.startswith("_")}
    assert not public & {"execute", "query", "run_sql", "raw_sql", "arbitrary_sql"}


class _UnverifiedResolver:
    def resolve(self, profile, *, required_purpose, source_id):
        return RuntimeSqlCredentials("postgresql://runtime", credential_reference="vault://source", credential_purpose=required_purpose, source_id=source_id)


def test_unverified_non_sql_provider_is_blocked_before_dlt(tmp_path: Path) -> None:
    record = _postgres_record()
    adapter = DltSqlSourceAdapter(project_root=tmp_path, credential_resolver=_UnverifiedResolver())
    selection = SourceSelection(registry_id=record.registry_id)
    with pytest.raises(Exception) as raised:
        adapter.discover_source(selection, record)
    assert "security" in str(raised.value).lower()


def _postgres_record() -> SourceRegistryRecord:
    return SourceRegistryRecord(
        registry_id="postgres-source",
        source_id="postgres-source-id",
        display_name="Provider security fixture",
        source_type=SourceType.POSTGRESQL,
        connection_profile=ConnectionProfileReference(profile_id="postgres-profile", database_engine=DatabaseEngine.POSTGRESQL, host="db.example", port=5432, database_name="source", credential_reference="vault://source"),
        scope=SelectionScope(included_objects=("customers",)),
        adapter_name="dlt_sql_source",
        adapter_version="1.0.0",
    )


class _CountingResolver:
    def __init__(self) -> None:
        self.calls = 0
        self.last_credentials: RuntimeSqlCredentials | None = None

    def resolve(self, profile, *, required_purpose, source_id):
        self.calls += 1
        self.last_credentials = RuntimeSqlCredentials("postgresql://runtime", credential_reference="vault://source", credential_purpose=required_purpose, credential_version="7", source_id=source_id)
        return self.last_credentials


class _CompleteVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(self, *, profile, credentials, source_id, profile_id, selection_fingerprint, policy):
        self.calls += 1
        requirement = next(item for item in policy.requirements if item.engine is DatabaseEngine.POSTGRESQL)
        findings = tuple(PrivilegeFinding(privilege=item, status=PrivilegeFindingStatus.PRESENT_REQUIRED, reason="technical fixture") for item in requirement.required)
        findings += tuple(PrivilegeFinding(privilege=item, status=PrivilegeFindingStatus.ABSENT_FORBIDDEN, reason="technical fixture") for item in requirement.forbidden)
        return ProviderSecurityVerification(
            verification_id="verification-complete",
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
            findings=findings,
            evidence_fingerprint="sha256:complete-technical-fixture",
        )


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _CaptureAdapter(DltSqlSourceAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.engine_credentials = None
        self.dlt_security_context = None

    def _engine(self, profile, *, source_id=None, runtime_credentials=None):
        self.engine_credentials = runtime_credentials
        return _FakeEngine()

    def _dlt_database(self, engine, table_names, selection, chunk_size, *, security_context):
        self.dlt_security_context = security_context
        return SimpleNamespace(resources={})


def test_protected_operation_resolves_once_and_flows_same_context_to_dlt(tmp_path: Path) -> None:
    resolver = _CountingResolver()
    verifier = _CompleteVerifier()
    adapter = _CaptureAdapter(project_root=tmp_path, credential_resolver=resolver, security_verifier=verifier)
    record = _postgres_record()
    adapter.discover_source(SourceSelection(registry_id=record.registry_id), record)
    assert resolver.calls == 1
    assert verifier.calls == 1
    assert adapter.engine_credentials is resolver.last_credentials
    assert adapter.dlt_security_context is not None
    assert adapter.dlt_security_context.runtime_credentials is resolver.last_credentials
    assert adapter.dlt_security_context.assurance.status is SecurityAssuranceStatus.PASS


class _FailingVerifier:
    def verify(self, **kwargs):
        raise RuntimeError("provider verifier unavailable")


class _SentinelAdapter(DltSqlSourceAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dlt_reached = False

    def _dlt_database(self, *args, **kwargs):
        self.dlt_reached = True
        raise AssertionError("dlt must not be reached")


def test_verifier_failure_blocks_before_dlt_initialization(tmp_path: Path) -> None:
    resolver = _CountingResolver()
    adapter = _SentinelAdapter(project_root=tmp_path, credential_resolver=resolver, security_verifier=_FailingVerifier())
    record = _postgres_record()
    with pytest.raises(Exception):
        adapter.discover_source(SourceSelection(registry_id=record.registry_id), record)
    assert resolver.calls == 1
    assert adapter.dlt_reached is False
