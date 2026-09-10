"""Project-owned contracts for source database security assurance.

The models in this module contain references, fingerprints and decisions only.
They deliberately never carry passwords, DSNs, live drivers or caller SQL.
"""

from __future__ import annotations

from datetime import datetime
import re
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .database import DatabaseEngine, _ContractModel, _reject_secret_material


class CredentialPurpose(str, Enum):
    SOURCE_READ_ONLY = "SOURCE_READ_ONLY"
    TARGET_WRITE = "TARGET_WRITE"
    ADMINISTRATION = "ADMINISTRATION"
    TEST_ONLY = "TEST_ONLY"


class SecurityAssuranceStatus(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class ReadOnlyEnforcementMethod(str, Enum):
    SQLITE_URI_MODE_RO = "SQLITE_URI_MODE_RO"
    SQLITE_QUERY_ONLY = "SQLITE_QUERY_ONLY"
    SQLITE_AUTHORIZE_DENY = "SQLITE_AUTHORIZE_DENY"
    SQLALCHEMY_CONNECT_HOOK = "SQLALCHEMY_CONNECT_HOOK"
    PROVIDER_ROLE = "PROVIDER_ROLE"
    PROVIDER_SESSION_POLICY = "PROVIDER_SESSION_POLICY"
    NONE = "NONE"


class QueryClass(str, Enum):
    READ_QUERY = "READ_QUERY"
    METADATA_QUERY = "METADATA_QUERY"
    TRANSACTION_CONTROL_ALLOWED = "TRANSACTION_CONTROL_ALLOWED"
    WRITE_DML = "WRITE_DML"
    DDL = "DDL"
    PROCEDURE_EXECUTION = "PROCEDURE_EXECUTION"
    FILESYSTEM_EXPORT = "FILESYSTEM_EXPORT"
    MULTI_STATEMENT = "MULTI_STATEMENT"
    UNKNOWN = "UNKNOWN"


class DatabaseSecurityFailureKind(str, Enum):
    CREDENTIAL_PURPOSE_MISMATCH = "CREDENTIAL_PURPOSE_MISMATCH"
    SOURCE_TARGET_ISOLATION = "SOURCE_TARGET_ISOLATION"
    UNVERIFIED_PROVIDER = "UNVERIFIED_PROVIDER"
    UNSAFE_PRIVILEGE = "UNSAFE_PRIVILEGE"
    MISSING_REQUIRED_PRIVILEGE = "MISSING_REQUIRED_PRIVILEGE"
    UNKNOWN_PRIVILEGE = "UNKNOWN_PRIVILEGE"
    UNSAFE_QUERY = "UNSAFE_QUERY"
    SECURITY_CONFIGURATION_INVALID = "SECURITY_CONFIGURATION_INVALID"
    SECURITY_ENFORCEMENT_FAILED = "SECURITY_ENFORCEMENT_FAILED"
    UNSAFE_CALLBACK = "UNSAFE_CALLBACK"


class DatabasePrincipalReference(_ContractModel):
    principal_id: str = Field(min_length=1)
    engine: DatabaseEngine
    credential_reference: str = Field(min_length=1)
    purpose: CredentialPurpose
    source_id: str | None = None
    role_reference: str | None = None

    @field_validator("principal_id", "credential_reference", "source_id", "role_reference")
    @classmethod
    def safe_reference(cls, value: str | None, info: Any) -> str | None:
        return _reject_secret_material(value, info.field_name)


class SourcePrivilegeRequirement(_ContractModel):
    engine: DatabaseEngine
    required: tuple[str, ...] = ()
    allowed_optional: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    unknown_grants_block: bool = True

    @model_validator(mode="after")
    def validate_sets(self) -> "SourcePrivilegeRequirement":
        if set(self.required) & set(self.forbidden):
            raise ValueError("a privilege cannot be both required and forbidden")
        if set(self.required) & set(self.allowed_optional):
            raise ValueError("a privilege cannot be both required and optional")
        return self


class PrivilegeFinding(_ContractModel):
    privilege: str = Field(min_length=1)
    status: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    safe_reference: str | None = None


class DatabaseSecurityPolicy(_ContractModel):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_purpose: CredentialPurpose = CredentialPurpose.SOURCE_READ_ONLY
    read_only: bool = True
    require_assurance_before_dlt: bool = True
    unknown_privileges_block: bool = True
    allowed_callbacks: tuple[str, ...] = ()
    requirements: tuple[SourcePrivilegeRequirement, ...] = ()
    sqlite_authorizer_required: bool = True
    sqlite_query_only_required: bool = True
    sqlite_uri_mode_ro_required: bool = True
    oracle_status: str = "DEFERRED"

    @model_validator(mode="after")
    def fail_closed(self) -> "DatabaseSecurityPolicy":
        if self.source_purpose is not CredentialPurpose.SOURCE_READ_ONLY or not self.read_only:
            raise ValueError("source database policy must be read-only and SOURCE_READ_ONLY")
        if not self.require_assurance_before_dlt:
            raise ValueError("dlt requires database security assurance")
        if not (self.sqlite_authorizer_required and self.sqlite_query_only_required and self.sqlite_uri_mode_ro_required):
            raise ValueError("SQLite source security controls are mandatory")
        return self


class DatabaseSecurityAssurance(_ContractModel):
    assurance_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    credential_reference: str = Field(min_length=1)
    credential_purpose: CredentialPurpose
    credential_version: str = Field(min_length=1)
    engine: DatabaseEngine
    selection_fingerprint: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    driver_reference: str = Field(min_length=1)
    status: SecurityAssuranceStatus
    enforcement_methods: tuple[ReadOnlyEnforcementMethod, ...] = ()
    findings: tuple[PrivilegeFinding, ...] = ()
    created_at: datetime

    @field_validator("credential_reference")
    @classmethod
    def reference_only(cls, value: str) -> str:
        return _reject_secret_material(value, "credential_reference")


class DatabaseSecurityAssessment(_ContractModel):
    assessment_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    engine: DatabaseEngine
    policy_id: str = Field(min_length=1)
    status: SecurityAssuranceStatus
    assurance: DatabaseSecurityAssurance
    privilege_findings: tuple[PrivilegeFinding, ...] = ()
    failures: tuple["DatabaseSecurityFailure", ...] = ()


class DatabaseSecurityFailure(_ContractModel):
    failure_id: str = Field(min_length=1)
    kind: DatabaseSecurityFailureKind
    operation: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    source_id: str | None = None
    profile_id: str | None = None
    retryable: bool = False

    @field_validator("detail", "operation", "source_id", "profile_id")
    @classmethod
    def safe_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return value
        if info.field_name == "detail":
            value = re.sub(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", value)
            return re.sub(r"(?i)(?:[a-z][a-z0-9+.-]*)://[^\s]+", "<redacted-connection>", value)
        return _reject_secret_material(value, info.field_name)


class QueryGuardDecision(_ContractModel):
    decision_id: str = Field(min_length=1)
    query_class: QueryClass
    allowed: bool
    reason: str = Field(min_length=1)
    query_fingerprint: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)


class DatabaseSecurityAuditEvent(_ContractModel):
    event_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    engine: DatabaseEngine
    query_class: QueryClass
    decision: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    credential_reference: str = Field(min_length=1)
    assurance_status: SecurityAssuranceStatus
    query_fingerprint: str = Field(min_length=1)
    created_at: datetime

    @field_validator("credential_reference")
    @classmethod
    def no_secret(cls, value: str) -> str:
        return _reject_secret_material(value, "credential_reference")


class DriverSecurityPolicy(_ContractModel):
    engine: DatabaseEngine
    driver_name: str = Field(min_length=1)
    driver_version: str = Field(min_length=1)
    required_connect_hooks: tuple[str, ...] = ()
    forbidden_options: tuple[str, ...] = ()
    callbacks_allowed: bool = False
