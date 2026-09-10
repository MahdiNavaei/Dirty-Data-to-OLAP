"""Project-owned privacy, sensitivity, minimization and exposure contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from .source import _SourceModel as _PrivacyModel


class SensitiveDataCategory(str, Enum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    PERSON_NAME = "PERSON_NAME"
    POSTAL_ADDRESS = "POSTAL_ADDRESS"
    GOVERNMENT_IDENTIFIER = "GOVERNMENT_IDENTIFIER"
    ACCOUNT_IDENTIFIER = "ACCOUNT_IDENTIFIER"
    FINANCIAL_IDENTIFIER = "FINANCIAL_IDENTIFIER"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    PRECISE_LOCATION = "PRECISE_LOCATION"
    AUTHENTICATION_SECRET = "AUTHENTICATION_SECRET"
    FREE_TEXT_POTENTIALLY_SENSITIVE = "FREE_TEXT_POTENTIALLY_SENSITIVE"
    LINKABLE_RECORD_REFERENCE = "LINKABLE_RECORD_REFERENCE"


class SensitivityLevel(str, Enum):
    RESTRICTED = "RESTRICTED"
    SENSITIVE = "SENSITIVE"
    INTERNAL = "INTERNAL"
    PUBLIC = "PUBLIC"


class ClassificationState(str, Enum):
    CONFIRMED_SENSITIVE = "CONFIRMED_SENSITIVE"
    POTENTIALLY_SENSITIVE = "POTENTIALLY_SENSITIVE"
    EXPLICITLY_NON_SENSITIVE = "EXPLICITLY_NON_SENSITIVE"
    UNKNOWN = "UNKNOWN"


class PrivacyEvidenceType(str, Enum):
    EXPLICIT_PRIVACY_RULE = "EXPLICIT_PRIVACY_RULE"
    SOURCE_METADATA = "SOURCE_METADATA"
    DETERMINISTIC_PATTERN = "DETERMINISTIC_PATTERN"
    USER_POLICY = "USER_POLICY"
    ARTIFACT_POLICY = "ARTIFACT_POLICY"


class ExposureContext(str, Enum):
    RAW_STAGING = "RAW_STAGING"
    CATALOG = "CATALOG"
    RECORD_REFERENCE = "RECORD_REFERENCE"
    PROFILE = "PROFILE"
    QUALITY = "QUALITY"
    LOG = "LOG"
    DEBUG = "DEBUG"
    EXPORT = "EXPORT"
    EXTERNAL_PROCESSING = "EXTERNAL_PROCESSING"
    UI_PREVIEW = "UI_PREVIEW"
    DEPENDENCY_LOCAL_ANALYSIS = "DEPENDENCY_LOCAL_ANALYSIS"
    SCHEMA_MATCHING_LOCAL_ANALYSIS = "SCHEMA_MATCHING_LOCAL_ANALYSIS"
    ENTITY_RESOLUTION_LOCAL_ANALYSIS = "ENTITY_RESOLUTION_LOCAL_ANALYSIS"


class PrivacyAction(str, Enum):
    ALLOW_RAW = "ALLOW_RAW"
    ALLOW_MASKED = "ALLOW_MASKED"
    ALLOW_AGGREGATE = "ALLOW_AGGREGATE"
    ALLOW_PSEUDONYM = "ALLOW_PSEUDONYM"
    BLOCK = "BLOCK"
    RETAIN_RESTRICTED = "RETAIN_RESTRICTED"
    DELETE_EPHEMERAL = "DELETE_EPHEMERAL"


class PrivacyEvidence(_PrivacyModel):
    evidence_id: str = Field(min_length=1)
    evidence_type: PrivacyEvidenceType
    subject_ref: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    detector_version: str | None = None
    provenance: str = Field(min_length=1)


class PrivacyRule(_PrivacyModel):
    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_id: str | None = None
    table_id: str | None = None
    column_id: str | None = None
    category: SensitiveDataCategory
    state: ClassificationState = ClassificationState.CONFIRMED_SENSITIVE
    sensitivity: SensitivityLevel = SensitivityLevel.SENSITIVE
    reason: str = Field(min_length=1)


class PrivacyClassification(_PrivacyModel):
    classification_id: str = Field(min_length=1)
    source_id: str | None = None
    table_id: str | None = None
    column_id: str | None = None
    category: SensitiveDataCategory | None = None
    state: ClassificationState
    sensitivity: SensitivityLevel
    evidence_refs: tuple[str, ...] = ()
    raw_value_allowed: bool = False
    log_allowed: bool = False
    external_processing_allowed: bool = False
    pseudonymization_required: bool = False
    retention_class: str = "restricted_ephemeral"


class PrivacyClassificationResult(_PrivacyModel):
    """Classification graph with resolvable evidence, not only evidence IDs."""

    classifications: tuple[PrivacyClassification, ...] = ()
    evidence: tuple[PrivacyEvidence, ...] = ()
    failures: tuple["PrivacyFailure", ...] = ()

    @model_validator(mode="after")
    def validate_evidence_graph(self) -> "PrivacyClassificationResult":
        evidence_ids = {item.evidence_id for item in self.evidence}
        if any(ref not in evidence_ids for item in self.classifications for ref in item.evidence_refs):
            raise ValueError("privacy classification evidence references must resolve")
        return self


class PrivacyPolicy(_PrivacyModel):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[PrivacyRule, ...] = ()
    unknown_state: ClassificationState = ClassificationState.UNKNOWN
    unknown_sensitivity: SensitivityLevel = SensitivityLevel.SENSITIVE
    raw_staging_sensitivity: SensitivityLevel = SensitivityLevel.RESTRICTED
    raw_staging_allowed: bool = True
    logs_allow_raw: bool = False
    debug_allow_raw_staging: bool = False
    export_allow_raw: bool = False
    external_allow_raw_sensitive: bool = False
    external_allow_unknown: bool = False
    future_llm_requires_guard: bool = True


class ArtifactSensitivity(_PrivacyModel):
    artifact_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    sensitivity: SensitivityLevel
    classification_state: ClassificationState
    raw_value_allowed: bool = False
    log_allowed: bool = False
    debug_allowed: bool = False
    export_allowed: bool = False
    external_processing_allowed: bool = False
    retention_class: str = "restricted_ephemeral"
    evidence_refs: tuple[str, ...] = ()


class MaskingPolicy(_PrivacyModel):
    policy_id: str = "masking-v1"
    method: str = "category_specific_redaction"
    preserve_prefix: int = Field(default=0, ge=0, le=8)
    preserve_suffix: int = Field(default=0, ge=0, le=8)
    replacement: str = "<REDACTED>"
    fail_closed: bool = True


class PseudonymizationPolicy(_PrivacyModel):
    policy_id: str = "pseudonymization-v1"
    algorithm: str = "HMAC-SHA256"
    key_reference: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    version: str = Field(min_length=1)
    output_prefix: str = "psn_v1_"
    fail_closed: bool = True


class RetentionPolicy(_PrivacyModel):
    policy_id: str = "retention-v1"
    restricted_staging: str = "until_run_completion_or_explicit_cleanup"
    debug_bundles: str = "ephemeral_only"
    exports: str = "no_raw_sensitive_values"
    cleanup_root_name: str = "privacy_ephemeral"
    source_artifacts_deletable_by_privacy_service: bool = False


class ExposureRequest(_PrivacyModel):
    context: ExposureContext
    purpose: str = Field(min_length=1)
    requested_mode: str = "raw"
    aggregate_only: bool = False


class PrivacyDecision(_PrivacyModel):
    allowed: bool
    action: PrivacyAction
    reason: str = Field(min_length=1)
    classification_id: str | None = None
    required_transformation: str | None = None
    failure_ref: str | None = None
    authorization_id: str | None = None


class ExternalProcessingDecision(_PrivacyModel):
    allowed: bool
    action: PrivacyAction
    reason: str = Field(min_length=1)
    aggregate_only: bool
    blocked_fields: tuple[str, ...] = ()
    policy_id: str


class AggregateSafeMetric(_PrivacyModel):
    """Explicit aggregate representation permitted at the external boundary."""

    metric_id: str = Field(min_length=1)
    aggregate_kind: str = Field(min_length=1)
    value: int | float
    derivation_scope: str = Field(min_length=1)
    contains_raw_identifier: Literal[False] = False
    privacy_classification: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def reject_identifier_metrics(self) -> "AggregateSafeMetric":
        if any(token in self.metric_id.lower() for token in ("customer_id", "account_id", "phone", "email", "identifier")):
            raise ValueError("aggregate metric ID must not identify a raw field")
        return self


class PrivacyFailureKind(str, Enum):
    CLASSIFICATION_UNAVAILABLE = "CLASSIFICATION_UNAVAILABLE"
    EXPOSURE_BLOCKED = "EXPOSURE_BLOCKED"
    MASKING_FAILED = "MASKING_FAILED"
    PSEUDONYMIZATION_KEY_UNAVAILABLE = "PSEUDONYMIZATION_KEY_UNAVAILABLE"
    UNSAFE_RETENTION_PATH = "UNSAFE_RETENTION_PATH"
    RAW_VALUE_DETECTED = "RAW_VALUE_DETECTED"


class PrivacyFailure(_PrivacyModel):
    failure_id: str = Field(min_length=1)
    kind: PrivacyFailureKind
    detail: str = Field(min_length=1)
    context: ExposureContext | None = None
    retryable: bool = False


class PrivacyScanResult(_PrivacyModel):
    scan_id: str = Field(min_length=1)
    policy_id: str
    scanned_artifacts: int = Field(ge=0)
    scanned_values: int = Field(ge=0)
    raw_sensitive_matches: int = Field(ge=0)
    raw_unknown_matches: int = Field(ge=0)
    restricted_staging_exceptions: int = Field(ge=0)
    violations: tuple[str, ...] = ()
    clean: bool
    created_at: datetime


class PrivacyManifest(_PrivacyModel):
    manifest_id: str = Field(min_length=1)
    policy_id: str
    artifacts: tuple[ArtifactSensitivity, ...] = ()
    created_at: datetime
    raw_staging_exception: str = "source-faithful restricted staging only"


PrivacyClassificationResult.model_rebuild()
