"""Project-owned quality, evidence and repair contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, model_validator

from .profiling import ProfileMode, ProfileObservationScope, ProfileResult, PatternType
from .source import AdapterReference, SourceTableKind, stable_digest
from .source import _SourceModel as _QualityModel


class QualityRuleScope(str, Enum):
    GENERIC_TECHNICAL = "GENERIC_TECHNICAL"
    SOURCE_DECLARED = "SOURCE_DECLARED"
    DOMAIN_ASSERTION = "DOMAIN_ASSERTION"
    REFERENCE_BENCHMARK = "REFERENCE_BENCHMARK"
    USER_POLICY = "USER_POLICY"


class QualityRuleType(str, Enum):
    REQUIRED_VALUE = "REQUIRED_VALUE"
    UNIQUE_VALUES = "UNIQUE_VALUES"
    EXACT_ROW_DUPLICATION = "EXACT_ROW_DUPLICATION"
    EXPECTED_PATTERN = "EXPECTED_PATTERN"
    EXPECTED_PRIMITIVE_TYPE = "EXPECTED_PRIMITIVE_TYPE"
    ALLOWED_DOMAIN = "ALLOWED_DOMAIN"
    NUMERIC_RANGE = "NUMERIC_RANGE"
    DECLARED_REFERENTIAL_INTEGRITY = "DECLARED_REFERENTIAL_INTEGRITY"
    NORMALIZATION_OPPORTUNITY = "NORMALIZATION_OPPORTUNITY"


class RuleApplicability(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INCONCLUSIVE = "INCONCLUSIVE"


class MeasurementSemantics(str, Enum):
    EXACT_ON_FULL_SNAPSHOT_SCOPE = "EXACT_ON_FULL_SNAPSHOT_SCOPE"
    EXACT_ON_OBSERVED_SCOPE = "EXACT_ON_OBSERVED_SCOPE"
    SAMPLE_OBSERVATION = "SAMPLE_OBSERVATION"
    ESTIMATED_WITH_METHOD = "ESTIMATED_WITH_METHOD"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNMEASURED = "UNMEASURED"


class QualitySeverity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DetectionBasis(str, Enum):
    EXACT_MEASUREMENT = "EXACT_MEASUREMENT"
    SAMPLED_OBSERVATION = "SAMPLED_OBSERVATION"
    DECLARED_CONSTRAINT_CONTRADICTION = "DECLARED_CONSTRAINT_CONTRADICTION"
    DOMAIN_ASSERTED_RULE = "DOMAIN_ASSERTED_RULE"
    USER_RULE = "USER_RULE"
    INCONCLUSIVE = "INCONCLUSIVE"


class Repairability(str, Enum):
    AUTO_SAFE = "AUTO_SAFE"
    AUTO_WITH_VALIDATION = "AUTO_WITH_VALIDATION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    MANUAL_BUSINESS_DECISION = "MANUAL_BUSINESS_DECISION"
    NOT_REPAIRABLE = "NOT_REPAIRABLE"


class QualityIssueStatus(str, Enum):
    OPEN = "OPEN"
    INCONCLUSIVE = "INCONCLUSIVE"


class QualityFailureKind(str, Enum):
    INPUT_INTEGRITY_FAILED = "INPUT_INTEGRITY_FAILED"
    RULE_INVALID = "RULE_INVALID"
    RULE_PREREQUISITE_MISSING = "RULE_PREREQUISITE_MISSING"
    DETECTOR_FAILED = "DETECTOR_FAILED"
    ARTIFACT_FAILED = "ARTIFACT_FAILED"
    INCOMPLETE_PROFILE = "INCOMPLETE_PROFILE"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"


class QualityDimension(str, Enum):
    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    REFERENTIAL_INTEGRITY = "referential_integrity"


class QualityDimensionStatus(str, Enum):
    MEASURED = "MEASURED"
    UNMEASURED = "UNMEASURED"
    INCONCLUSIVE = "INCONCLUSIVE"


class RepairProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"


class QualityRule(_QualityModel):
    rule_id: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    rule_type: QualityRuleType
    scope: QualityRuleScope
    entity_type: str = Field(min_length=1)
    source_id: str | None = None
    table_id: str | None = None
    column_ids: tuple[str, ...] = ()
    applicability_conditions: Mapping[str, Any] = Field(default_factory=dict)
    evidence_prerequisites: tuple[str, ...] = ()
    severity: QualitySeverity
    repairability: Repairability
    detector_config: Mapping[str, Any] = Field(default_factory=dict)
    expected_pattern: PatternType | None = None
    expected_primitive_type: str | None = None
    allowed_values: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    declared_constraint_ref: str | None = None
    domain_assertion_refs: tuple[str, ...] = ()
    enabled: bool = True
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rule(self) -> "QualityRule":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("quality rule minimum cannot exceed maximum")
        if self.rule_type is QualityRuleType.EXPECTED_PATTERN and self.expected_pattern is None:
            raise ValueError("expected-pattern rules require expected_pattern")
        if self.rule_type is QualityRuleType.EXPECTED_PRIMITIVE_TYPE and not self.expected_primitive_type:
            raise ValueError("expected-type rules require expected_primitive_type")
        if self.rule_type is QualityRuleType.ALLOWED_DOMAIN and not self.allowed_values:
            raise ValueError("allowed-domain rules require an explicit domain")
        if self.rule_type is QualityRuleType.NUMERIC_RANGE and self.minimum is None and self.maximum is None:
            raise ValueError("range rules require an explicit bound")
        if self.rule_type is QualityRuleType.DECLARED_REFERENTIAL_INTEGRITY and not self.declared_constraint_ref:
            raise ValueError("referential rules require a declared constraint reference")
        if self.scope is QualityRuleScope.DOMAIN_ASSERTION and not self.domain_assertion_refs:
            raise ValueError("domain-assertion rules require explicit domain assertion references")
        if self.scope is QualityRuleScope.REFERENCE_BENCHMARK and self.rule_id.startswith("generic."):
            raise ValueError("benchmark rules need a benchmark-specific identity")
        return self


class QualityRuleSet(_QualityModel):
    rule_set_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[QualityRule, ...]
    provenance: str = Field(min_length=1)
    runtime_default: bool = False

    @model_validator(mode="after")
    def validate_rules(self) -> "QualityRuleSet":
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("quality rule IDs must be unique")
        if self.runtime_default and any(rule.scope is QualityRuleScope.REFERENCE_BENCHMARK for rule in self.rules):
            raise ValueError("benchmark rules cannot enter the universal runtime default")
        return self


class QualityEvidenceRef(_QualityModel):
    evidence_type: str
    evidence_id: str
    semantics: str


class QualityIssue(_QualityModel):
    issue_id: str
    issue_type: str
    quality_dimension: QualityDimension
    entity_type: str
    entity_id: str
    source_id: str
    snapshot_id: str
    table_id: str
    column_ids: tuple[str, ...] = ()
    rule_id: str
    severity: QualitySeverity
    detection_basis: DetectionBasis
    measurement_semantics: MeasurementSemantics
    observation_scope: ProfileObservationScope
    affected_count: int = Field(ge=0)
    affected_ratio: float | None = Field(default=None, ge=0, le=1)
    affected_record_refs: tuple[str, ...] = ()
    affected_rows_estimate: int | None = Field(default=None, ge=0)
    evidence_refs: tuple[QualityEvidenceRef, ...] = ()
    profile_refs: tuple[str, ...] = ()
    declared_constraint_refs: tuple[str, ...] = ()
    domain_assertion_refs: tuple[str, ...] = ()
    repairability: Repairability
    repair_proposal_refs: tuple[str, ...] = ()
    status: QualityIssueStatus
    provenance: str


class RepairValidationPlan(_QualityModel):
    plan_id: str
    remeasure_metrics: tuple[str, ...]
    invariants: tuple[str, ...]
    row_accounting_expectation: str
    abort_conditions: tuple[str, ...]
    lineage_requirements: tuple[str, ...]
    expected_improvement: tuple[str, ...]
    forbidden_new_issues: tuple[str, ...]


class RepairProposal(_QualityModel):
    proposal_id: str
    issue_refs: tuple[str, ...] = Field(min_length=1)
    repair_type: str
    repairability: Repairability
    target_layer: str
    affected_entity_type: str
    table_id: str
    column_ids: tuple[str, ...] = ()
    affected_record_refs: tuple[str, ...] = ()
    transform_id: str
    transform_version: str
    preconditions: tuple[str, ...]
    expected_effect: str
    risk_notes: tuple[str, ...]
    lineage_requirement: str
    row_accounting_requirement: str
    validation_plan: RepairValidationPlan
    review_required: bool
    status: RepairProposalStatus = RepairProposalStatus.PROPOSED
    provenance: str


class QualityRuleEvaluation(_QualityModel):
    rule_id: str
    applicability: RuleApplicability
    measurement_semantics: MeasurementSemantics
    evaluated_count: int = Field(default=0, ge=0)
    affected_count: int = Field(default=0, ge=0)
    affected_ratio: float | None = Field(default=None, ge=0, le=1)
    issue_refs: tuple[str, ...] = ()
    evidence_refs: tuple[QualityEvidenceRef, ...] = ()
    evaluated_record_refs: tuple[str, ...] = ()
    failure_ref: str | None = None


class QualityFailure(_QualityModel):
    failure_id: str
    kind: QualityFailureKind
    detail: str
    rule_id: str | None = None
    table_id: str | None = None
    source_id: str
    snapshot_id: str
    retryable: bool = False


class QualityDimensionSummary(_QualityModel):
    dimension: QualityDimension
    status: QualityDimensionStatus
    measurement_semantics: MeasurementSemantics
    applicable_rule_count: int = Field(ge=0)
    evaluated_rule_count: int = Field(ge=0)
    inconclusive_rule_count: int = Field(ge=0)
    affected_record_count: int | None = Field(default=None, ge=0)
    measured_row_count: int = Field(ge=0)
    denominator_semantics: str = "UNSPECIFIED"
    affected_ratio: float | None = Field(default=None, ge=0, le=1)
    issue_refs: tuple[str, ...] = ()


class QualityRequest(_QualityModel):
    quality_run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    rule_set: QualityRuleSet
    profile_result_fingerprint: str
    profile_refs: tuple[str, ...] = ()
    batch_ids: tuple[str, ...] = ()
    batch_hashes: tuple[str, ...] = ()
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_batch_binding(self) -> "QualityRequest":
        if self.batch_hashes and len(self.batch_ids) != len(self.batch_hashes):
            raise ValueError("quality batch IDs and hashes must have matching lengths")
        return self


class QualityResult(_QualityModel):
    quality_run_id: str
    source_id: str
    snapshot_id: str
    rule_set_id: str
    rule_set_version: str
    issues: tuple[QualityIssue, ...]
    repair_proposals: tuple[RepairProposal, ...]
    rule_evaluations: tuple[QualityRuleEvaluation, ...]
    failures: tuple[QualityFailure, ...]
    dimension_summaries: tuple[QualityDimensionSummary, ...]
    input_profile_refs: tuple[str, ...]
    input_batch_ids: tuple[str, ...]
    input_batch_hashes: tuple[str, ...]
    provenance: str
    completeness: str
    artifacts: tuple[str, ...] = ()


class QualityStagedRow(_QualityModel):
    record_ref: str
    extraction_ordinal: int = Field(ge=0)
    values: Mapping[str, Any]


def quality_profile_fingerprint(profile_result: ProfileResult) -> str:
    return stable_digest(profile_result)


def quality_rule_dimension(rule_type: QualityRuleType) -> QualityDimension:
    return {
        QualityRuleType.REQUIRED_VALUE: QualityDimension.COMPLETENESS,
        QualityRuleType.UNIQUE_VALUES: QualityDimension.UNIQUENESS,
        QualityRuleType.EXACT_ROW_DUPLICATION: QualityDimension.UNIQUENESS,
        QualityRuleType.EXPECTED_PATTERN: QualityDimension.VALIDITY,
        QualityRuleType.EXPECTED_PRIMITIVE_TYPE: QualityDimension.VALIDITY,
        QualityRuleType.ALLOWED_DOMAIN: QualityDimension.VALIDITY,
        QualityRuleType.NUMERIC_RANGE: QualityDimension.VALIDITY,
        QualityRuleType.DECLARED_REFERENTIAL_INTEGRITY: QualityDimension.REFERENTIAL_INTEGRITY,
        QualityRuleType.NORMALIZATION_OPPORTUNITY: QualityDimension.CONSISTENCY,
    }[rule_type]


def constraint_ref_for(constraint: Any) -> str:
    """Return a stable identity for a declared constraint with no native ID field."""
    return "constraint_" + stable_digest({
        "source_id": constraint.source_id,
        "table_id": constraint.table_id,
        "constraint_type": constraint.constraint_type,
        "columns": constraint.columns,
        "referenced_table_id": constraint.referenced_table_id,
        "referenced_table_name": constraint.referenced_table_name,
        "referenced_columns": constraint.referenced_columns,
    })[:32]
