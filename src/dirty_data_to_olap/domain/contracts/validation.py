"""Project-owned contracts for source-to-OLAP data correctness validation.

Validation is deliberately downstream of the canonical and analytical
contracts.  The models in this module describe independent truth, scoped
accounting, exact artifact bindings, deterministic checks and discrepancies;
they do not contain a database connection or provider-native objects.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .canonical import RecordDisposition
from .source import _SourceModel, stable_digest, stable_id


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe validation identifier: {value!r}")
    return value


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class ValidationSeverity(str, Enum):
    G6_BLOCKING = "G6_BLOCKING"
    WARNING = "WARNING"
    INFORMATIONAL = "INFORMATIONAL"


class ValidationScope(str, Enum):
    SOURCE_SNAPSHOT = "SOURCE_SNAPSHOT"
    SOURCE_ACCOUNTING = "SOURCE_ACCOUNTING"
    CANONICALIZATION = "CANONICALIZATION"
    RELATIONSHIPS = "RELATIONSHIPS"
    ANALYTICAL_PLAN = "ANALYTICAL_PLAN"
    MATERIALIZATION = "MATERIALIZATION"
    DIMENSIONS = "DIMENSIONS"
    FACT = "FACT"
    GRAIN = "GRAIN"
    REFERENTIAL_INTEGRITY = "REFERENTIAL_INTEGRITY"
    AGGREGATE = "AGGREGATE"
    DATES = "DATES"
    LINEAGE = "LINEAGE"
    SEMANTIC_PROJECTION = "SEMANTIC_PROJECTION"
    CROSS_STAGE = "CROSS_STAGE"


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class AccountingBoundary(str, Enum):
    SOURCE_TO_CANONICAL = "SOURCE_TO_CANONICAL"
    CANONICAL_TO_ANALYTICAL = "CANONICAL_TO_ANALYTICAL"


class SourceTruthRecord(_SourceModel):
    """One independently reviewed source record and its expected terminal fate."""

    record_ref: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    extraction_ordinal: int = Field(ge=0)
    subject_type: str = Field(min_length=1)
    canonical_entity_id: str | None = None
    terminal_disposition: RecordDisposition
    output_reference: str | None = None
    values: Mapping[str, Any] = Field(default_factory=dict)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def output_reference_matches_disposition(self) -> "SourceTruthRecord":
        if self.terminal_disposition in {
            RecordDisposition.EMITTED_DIRECT,
            RecordDisposition.CONSOLIDATED,
            RecordDisposition.AGGREGATED,
        } and not self.output_reference:
            raise ValueError("source truth output reference is required for contributing records")
        if self.terminal_disposition in {
            RecordDisposition.FILTERED_EXPLICIT,
            RecordDisposition.QUARANTINED,
            RecordDisposition.UNRESOLVED,
        } and self.output_reference is not None:
            raise ValueError("non-contributing source truth record cannot claim an output reference")
        return self


class SourceTruthEntity(_SourceModel):
    entity_type: str = Field(min_length=1)
    canonical_entity_id: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)
    expected_attributes: Mapping[str, Any] = Field(default_factory=dict)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SourceTruthRelationship(_SourceModel):
    relationship_ref: str = Field(min_length=1)
    from_record_ref: str = Field(min_length=1)
    to_canonical_entity_id: str = Field(min_length=1)
    required: bool = True
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SourceTruthFact(_SourceModel):
    fact_ref: str = Field(min_length=1)
    fact_id: str | None = None
    source_record_refs: tuple[str, ...] = Field(min_length=1)
    canonical_event_id: str = Field(min_length=1)
    grain_values: Mapping[str, Any] = Field(min_length=1)
    dimension_entity_ids: Mapping[str, str] = Field(min_length=1)
    measure_values: Mapping[str, Any] = Field(min_length=1)
    date_value: str | None = None
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SourceTruthAccountingExpectation(_SourceModel):
    """Independent expected accounting for one explicit transformation boundary."""

    boundary: AccountingBoundary
    input_record_ref: str = Field(min_length=1)
    expected_disposition: RecordDisposition
    expected_output_or_group_ref: str | None = None
    reason_contains: str | None = None
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def output_matches_disposition(self) -> "SourceTruthAccountingExpectation":
        contributing = {
            RecordDisposition.EMITTED_DIRECT,
            RecordDisposition.CONSOLIDATED,
            RecordDisposition.AGGREGATED,
        }
        if self.expected_disposition in contributing and not self.expected_output_or_group_ref:
            raise ValueError("contributing accounting expectations require an output or group reference")
        if self.expected_disposition not in contributing and self.expected_output_or_group_ref is not None:
            raise ValueError("non-contributing accounting expectations cannot claim an output reference")
        return self


class SourceTruthAggregateExpectation(_SourceModel):
    """Typed, fact-owned aggregate expectation; never an arbitrary SQL oracle."""

    aggregate_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    measure_field: str = Field(min_length=1)
    semantic_measure_ref: str | None = None
    operation: str = Field(pattern=r"^(SUM|MAX)$")
    group_by: tuple[str, ...] = ()
    expected: Any = None
    expected_by_key: Mapping[str, Any] = Field(default_factory=dict)
    unit_semantics: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def has_expected_values(self) -> "SourceTruthAggregateExpectation":
        if self.expected is None and not self.expected_by_key:
            raise ValueError("aggregate expectation requires expected or expected_by_key")
        return self


class SourceTruthDuplicateGroup(_SourceModel):
    """A positive deduplication control, kept separate from row-loss accounting."""

    control_id: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=2)
    canonical_entity_id: str = Field(min_length=1)
    expected_canonical_entity_count: int = Field(ge=1)
    rationale: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SourceTruthManifest(_SourceModel):
    """Independent source/domain oracle used only by the QA boundary."""

    truth_id: str = Field(min_length=1)
    truth_version: str = Field(min_length=1)
    source_snapshot_id: str = Field(min_length=1)
    # V1 keeps ``source_snapshot_id`` as the stable binding identity used by
    # ValidationArtifactBindings.  Multi-source truth additionally records the
    # exact snapshot for each source so provenance checks do not collapse four
    # independent observations into one fictitious snapshot.
    source_snapshot_ids: Mapping[str, str] = Field(default_factory=dict)
    source_schema_fingerprints: Mapping[str, str] = Field(min_length=1)
    records: tuple[SourceTruthRecord, ...] = Field(min_length=1)
    entities: tuple[SourceTruthEntity, ...] = ()
    relationships: tuple[SourceTruthRelationship, ...] = ()
    facts: tuple[SourceTruthFact, ...] = ()
    accounting_expectations: tuple[SourceTruthAccountingExpectation, ...] = ()
    aggregate_expectations: tuple[SourceTruthAggregateExpectation, ...] = ()
    expected_aggregates: Mapping[str, Any] = Field(default_factory=dict)
    duplicate_groups: tuple[SourceTruthDuplicateGroup, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def source_truth_is_closed(self) -> "SourceTruthManifest":
        refs = [item.record_ref for item in self.records]
        if len(set(refs)) != len(refs):
            raise ValueError("source truth record references must be unique")
        if self.source_snapshot_ids:
            if set(self.source_snapshot_ids) != set(self.source_schema_fingerprints):
                raise ValueError("multi-source truth snapshot and schema maps must cover the same sources")
            if any(record.source_id not in self.source_snapshot_ids for record in self.records):
                raise ValueError("multi-source truth record references an unknown source snapshot")
            if any(record.snapshot_id != self.source_snapshot_ids[record.source_id] for record in self.records):
                raise ValueError("multi-source truth record snapshot does not match its source snapshot map")
        entity_ids = [item.canonical_entity_id for item in self.entities]
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("source truth entity references must be unique")
        for group in self.duplicate_groups:
            if not set(group.source_record_refs).issubset(refs):
                raise ValueError("duplicate control references an unknown source record")
        for relationship in self.relationships:
            if relationship.from_record_ref not in refs:
                raise ValueError("source truth relationship references an unknown record")
        fact_refs = set(refs)
        for fact in self.facts:
            if not set(fact.source_record_refs).issubset(fact_refs):
                raise ValueError("source truth fact references an unknown record")
        accounting_keys = [(item.boundary, item.input_record_ref) for item in self.accounting_expectations]
        if len(accounting_keys) != len(set(accounting_keys)):
            raise ValueError("source truth accounting expectations must be unique per boundary and input")
        if any(item.boundary is AccountingBoundary.SOURCE_TO_CANONICAL and item.input_record_ref not in fact_refs for item in self.accounting_expectations):
            raise ValueError("source-to-canonical accounting expectation references an unknown source record")
        canonical_refs = {item.canonical_entity_id for item in self.entities}
        canonical_refs.update(item.canonical_event_id for item in self.facts)
        if any(item.boundary is AccountingBoundary.CANONICAL_TO_ANALYTICAL and item.input_record_ref not in canonical_refs for item in self.accounting_expectations):
            raise ValueError("canonical-to-analytical accounting expectation references an unknown canonical record")
        aggregate_ids = [item.aggregate_id for item in self.aggregate_expectations]
        if len(aggregate_ids) != len(set(aggregate_ids)):
            raise ValueError("source truth aggregate IDs must be unique")
        fact_ids = {item.fact_id or item.fact_ref for item in self.facts}
        if any(item.fact_id not in fact_ids for item in self.aggregate_expectations):
            raise ValueError("aggregate expectation references an unknown fact ID")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    @property
    def source_snapshot_fingerprint(self) -> str:
        return stable_digest({
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_ids": dict(sorted(self.source_snapshot_ids.items())),
            "source_schema_fingerprints": dict(sorted(self.source_schema_fingerprints.items())),
            "record_refs": sorted(item.record_ref for item in self.records),
        })

    @property
    def record_refs(self) -> frozenset[str]:
        return frozenset(item.record_ref for item in self.records)


class RecordAccountingEntry(_SourceModel):
    input_record_ref: str = Field(min_length=1)
    disposition: RecordDisposition
    output_or_group_ref: str | None = None
    transformation_or_policy_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def contributor_reference_is_explicit(self) -> "RecordAccountingEntry":
        if self.disposition in {
            RecordDisposition.EMITTED_DIRECT,
            RecordDisposition.CONSOLIDATED,
            RecordDisposition.AGGREGATED,
        } and not self.output_or_group_ref:
            raise ValueError("contributing accounting entries require output_or_group_ref")
        if self.disposition in {
            RecordDisposition.FILTERED_EXPLICIT,
            RecordDisposition.QUARANTINED,
            RecordDisposition.UNRESOLVED,
        } and self.output_or_group_ref is not None:
            raise ValueError("non-contributing accounting entries cannot claim output_or_group_ref")
        return self


class RecordAccountingScope(_SourceModel):
    scope_id: str = Field(min_length=1)
    boundary: AccountingBoundary
    input_object_ref: str = Field(min_length=1)
    input_record_refs: tuple[str, ...] = Field(min_length=1)
    entries: tuple[RecordAccountingEntry, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def accounting_reconciles(self) -> "RecordAccountingScope":
        input_refs = set(self.input_record_refs)
        entry_refs = [item.input_record_ref for item in self.entries]
        if len(input_refs) != len(self.input_record_refs) or len(entry_refs) != len(set(entry_refs)):
            raise ValueError("accounting input references must be unique")
        if set(entry_refs) != input_refs:
            raise ValueError("accounting entries must cover the exact input record universe")
        return self

    @property
    def counts(self) -> Mapping[str, int]:
        return {
            disposition.value: sum(item.disposition is disposition for item in self.entries)
            for disposition in RecordDisposition
        }

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class RecordAccountingArtifact(_SourceModel):
    accounting_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scopes: tuple[RecordAccountingScope, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def scope_ids_are_unique(self) -> "RecordAccountingArtifact":
        ids = [item.scope_id for item in self.scopes]
        if len(ids) != len(set(ids)):
            raise ValueError("accounting scope IDs must be unique")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class TargetTableSnapshot(_SourceModel):
    table_name: str = Field(min_length=1)
    columns: tuple[str, ...] = Field(min_length=1)
    rows: tuple[Mapping[str, Any], ...] = ()

    @field_validator("table_name")
    @classmethod
    def table_name_is_safe(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def row_columns_match(self) -> "TargetTableSnapshot":
        expected = set(self.columns)
        if len(expected) != len(self.columns):
            raise ValueError("target columns must be unique")
        if any(set(row) != expected for row in self.rows):
            raise ValueError("target row columns must match target table columns")
        return self

    @property
    def row_count(self) -> int:
        return len(self.rows)


class TargetSnapshot(_SourceModel):
    target_relative_path: str = Field(min_length=1)
    target_file_sha256: str = Field(min_length=64, max_length=64)
    available_table_names: tuple[str, ...] = Field(min_length=1)
    tables: tuple[TargetTableSnapshot, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("target_relative_path")
    @classmethod
    def target_path_is_safe(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value.casefold().endswith(".duckdb"):
            raise ValueError("target path must be a relative .duckdb path")
        return str(path)

    @model_validator(mode="after")
    def tables_are_unique(self) -> "TargetSnapshot":
        names = [item.table_name for item in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("target table names must be unique")
        if not set(names).issubset(self.available_table_names):
            raise ValueError("inspected target tables must be available in the target")
        return self

    @property
    def table_map(self) -> Mapping[str, TargetTableSnapshot]:
        return {item.table_name: item for item in self.tables}


class ValidationArtifactBindings(_SourceModel):
    source_snapshot_id: str = Field(min_length=1)
    source_snapshot_hash: str = Field(min_length=1)
    source_truth_id: str = Field(min_length=1)
    source_truth_content_hash: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    record_accounting_id: str = Field(min_length=1)
    record_accounting_content_hash: str = Field(min_length=1)
    analytical_plan_id: str = Field(min_length=1)
    analytical_plan_content_hash: str = Field(min_length=1)
    analytical_spec_package_hash: str = Field(min_length=1)
    analytical_dataset_id: str = Field(min_length=1)
    analytical_dataset_content_hash: str = Field(min_length=1)
    analytical_input_binding_id: str = Field(min_length=1)
    analytical_input_binding_content_hash: str = Field(min_length=1)
    analytical_input_source_snapshot_fingerprints: Mapping[str, str] = Field(min_length=1)
    compiled_plan_id: str = Field(min_length=1)
    compiled_plan_content_hash: str = Field(min_length=1)
    materialization_artifact_id: str = Field(min_length=1)
    materialization_artifact_content_hash: str = Field(min_length=1)
    target_relative_path: str = Field(min_length=1)
    target_config_fingerprint: str = Field(min_length=1)
    target_file_sha256: str = Field(min_length=64, max_length=64)
    semantic_model_id: str = Field(min_length=1)
    semantic_model_content_hash: str = Field(min_length=1)
    semantic_validation_id: str = Field(min_length=1)
    semantic_validation_content_hash: str = Field(min_length=1)
    validation_policy_id: str = Field(min_length=1)
    validation_policy_version: str = Field(min_length=1)
    benchmark_truth_hash: str | None = None

    @field_validator("target_relative_path")
    @classmethod
    def binding_target_path_is_safe(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value.casefold().endswith(".duckdb"):
            raise ValueError("bound target path must be a relative .duckdb path")
        return str(path)


class ValidationPolicy(_SourceModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    required_check_ids: tuple[str, ...] = Field(min_length=1)
    allowed_terminal_dispositions: tuple[RecordDisposition, ...] = Field(min_length=1)
    orphan_policy: Mapping[str, str] = Field(min_length=1)
    require_bidirectional_lineage: bool = True
    exact_numeric_comparison: bool = True
    monetary_status: ValidationStatus = ValidationStatus.NOT_APPLICABLE
    monetary_reason: str = Field(min_length=1)
    deferred_concepts: Mapping[str, str] = Field(default_factory=dict)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def policy_is_deterministic(self) -> "ValidationPolicy":
        if len(set(self.required_check_ids)) != len(self.required_check_ids):
            raise ValueError("required validation check IDs must be unique")
        if self.monetary_status is ValidationStatus.NOT_APPLICABLE and not self.monetary_reason.strip():
            raise ValueError("NOT_APPLICABLE monetary validation requires a reason")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class ValidationDiscrepancy(_SourceModel):
    discrepancy_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    severity: ValidationSeverity
    boundary: ValidationScope
    summary: str = Field(min_length=1)
    expected: Any = None
    observed: Any = None
    affected_refs: tuple[str, ...] = ()
    likely_stage: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class ValidationCheck(_SourceModel):
    check_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ValidationStatus
    severity: ValidationSeverity
    scope: ValidationScope
    required: bool = True
    details: str = Field(min_length=1)
    expected: Any = None
    observed: Any = None
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    discrepancy_ids: tuple[str, ...] = ()


class ReconciliationMetric(_SourceModel):
    metric_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ValidationStatus
    expected: Any = None
    observed: Any = None
    difference: Any = None
    tolerance: str = "0"
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class ValidationReport(_SourceModel):
    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    bindings: ValidationArtifactBindings
    policy: ValidationPolicy
    checks: tuple[ValidationCheck, ...] = Field(min_length=1)
    discrepancies: tuple[ValidationDiscrepancy, ...] = ()
    overall_status: ValidationStatus
    g6_status: GateStatus
    g6_eligible: bool
    generated_at: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def report_status_is_derived(self) -> "ValidationReport":
        check_ids = [item.check_id for item in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("validation check IDs must be unique")
        discrepancy_ids = [item.discrepancy_id for item in self.discrepancies]
        if len(discrepancy_ids) != len(set(discrepancy_ids)):
            raise ValueError("validation discrepancy IDs must be unique")
        if any(item.check_id not in check_ids for item in self.discrepancies):
            raise ValueError("discrepancy references an unknown check")
        required = [item for item in self.checks if item.required and item.check_id in self.policy.required_check_ids]
        if {item.check_id for item in required} != set(self.policy.required_check_ids):
            raise ValueError("all policy-required checks must be present and required")
        blocking = [item for item in required if item.severity is ValidationSeverity.G6_BLOCKING]
        has_fail = any(item.status is ValidationStatus.FAIL for item in blocking)
        has_pending = any(item.status in {ValidationStatus.REVIEW_REQUIRED, ValidationStatus.NOT_EVALUATED} for item in blocking)
        expected_gate = GateStatus.FAIL if has_fail else GateStatus.PENDING if has_pending else GateStatus.PASS
        expected_overall = ValidationStatus.FAIL if has_fail else ValidationStatus.REVIEW_REQUIRED if has_pending else ValidationStatus.PASS
        if self.g6_status is not expected_gate or self.overall_status is not expected_overall:
            raise ValueError("validation status does not match required check statuses")
        if self.g6_eligible != (expected_gate is GateStatus.PASS):
            raise ValueError("G6 eligibility must be derived from required blocking checks")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"report_id", "generated_at"}))


class ReconciliationResult(_SourceModel):
    result_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    validation_report_id: str = Field(min_length=1)
    bindings: ValidationArtifactBindings
    metrics: tuple[ReconciliationMetric, ...] = Field(min_length=1)
    status: ValidationStatus
    no_blocking_discrepancy: bool
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def status_matches_metrics(self) -> "ReconciliationResult":
        blocking_failure = any(item.status is ValidationStatus.FAIL for item in self.metrics)
        pending = any(item.status in {ValidationStatus.REVIEW_REQUIRED, ValidationStatus.NOT_EVALUATED} for item in self.metrics)
        expected = ValidationStatus.FAIL if blocking_failure else ValidationStatus.REVIEW_REQUIRED if pending else ValidationStatus.PASS
        if self.status is not expected or self.no_blocking_discrepancy != (not blocking_failure):
            raise ValueError("reconciliation status does not match metrics")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"result_id"}))


def record_accounting_id(payload: Mapping[str, Any]) -> str:
    return stable_id("racc", payload)


def validation_policy_id(payload: Mapping[str, Any]) -> str:
    return stable_id("vpol", payload)


def validation_report_id(payload: Mapping[str, Any]) -> str:
    return stable_id("vreport", payload)


def reconciliation_result_id(payload: Mapping[str, Any]) -> str:
    return stable_id("recon", payload)
