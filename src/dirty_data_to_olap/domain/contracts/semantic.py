"""Project-owned semantic-layer and bounded query contracts.

The semantic layer is a deterministic, read-only projection of reviewed
Step20 analytical artifacts.  It may add business-readable labels and safe
metadata, but it cannot alter fact grain, warehouse keys or aggregation
semantics.  No DuckDB connection or third-party semantic-engine object crosses
this boundary.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping

from pydantic import Field, field_validator, model_validator

from .analytical import AggregationClass, DimensionRole
from .source import _SourceModel, stable_digest, stable_id


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe semantic identifier: {value!r}")
    return value


def _text(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("semantic text must be non-empty and NUL-free")
    return value


def _unique_casefold(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = [value.casefold() for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must be unique case-insensitively")
    return values


class SemanticModelStatus(str, Enum):
    READY = "READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INCOMPLETE = "INCOMPLETE"
    STALE = "STALE"
    INVALID = "INVALID"


class SemanticAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


MetricAvailability = SemanticAvailability


class SemanticExposureState(str, Enum):
    EXPOSED = "EXPOSED"
    IMPLEMENTATION_ONLY = "IMPLEMENTATION_ONLY"


class SemanticKeyExposurePolicy(str, Enum):
    IMPLEMENTATION_ONLY = "IMPLEMENTATION_ONLY"
    EXPOSE_ALTERNATE_KEY = "EXPOSE_ALTERNATE_KEY"


class SemanticRelationshipScope(str, Enum):
    CANONICAL_ACCEPTED = "CANONICAL_ACCEPTED"
    ANALYTICAL_TIME_ROLE = "ANALYTICAL_TIME_ROLE"


class SemanticRelationshipKind(str, Enum):
    FACT_TO_DIMENSION = "FACT_TO_DIMENSION"
    TIME_ROLE = "TIME_ROLE"


class SemanticMetricKind(str, Enum):
    BASE_AGGREGATE = "BASE_AGGREGATE"
    DERIVED = "DERIVED"


MetricKind = SemanticMetricKind


class SemanticExpressionType(str, Enum):
    AGGREGATE_MEASURE = "AGGREGATE_MEASURE"
    RATIO = "RATIO"


class SemanticFilterOperator(str, Enum):
    EQUALS = "EQUALS"
    IN = "IN"
    DATE_RANGE = "DATE_RANGE"


class ZeroDenominatorBehavior(str, Enum):
    NULL = "NULL"
    UNDEFINED = "UNDEFINED"
    ERROR = "ERROR"


class SemanticValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class SemanticHierarchyValidationState(str, Enum):
    VALIDATED = "VALIDATED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INVALID = "INVALID"


SemanticValue = str | int | float | Decimal | date | datetime | bool | None


class SemanticAttribute(_SourceModel):
    semantic_attribute_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    physical_column_ref: str = Field(min_length=1)
    logical_type: str = Field(min_length=1)
    canonical_attribute_refs: tuple[str, ...] = ()
    source_lineage_refs: tuple[str, ...] = ()
    nullable: bool = True
    exposure_state: SemanticExposureState = SemanticExposureState.EXPOSED
    aliases: tuple[str, ...] = ()
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("semantic_attribute_id", "dimension_id", "physical_column_ref")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name", "description", "logical_type")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        return _text(value)

    @field_validator("aliases")
    @classmethod
    def aliases_are_unambiguous(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or "\x00" in item for item in value):
            raise ValueError("semantic aliases must be non-empty and NUL-free")
        return _unique_casefold(value, "semantic attribute aliases")


class SemanticDimension(_SourceModel):
    semantic_dimension_id: str = Field(min_length=1)
    business_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    physical_dimension_spec_id: str = Field(min_length=1)
    physical_table_ref: str = Field(min_length=1)
    canonical_concept_ref: str = Field(min_length=1)
    canonical_entity_type_id: str = Field(min_length=1)
    role: DimensionRole
    attributes: tuple[SemanticAttribute, ...] = Field(min_length=1)
    key_exposure_policy: SemanticKeyExposurePolicy = SemanticKeyExposurePolicy.IMPLEMENTATION_ONLY
    hierarchy_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    unknown_member_policy: str = Field(min_length=1)
    scd_mode: str = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("semantic_dimension_id", "physical_dimension_spec_id", "physical_table_ref")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("business_name", "description", "canonical_concept_ref", "canonical_entity_type_id", "unknown_member_policy", "scd_mode")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        return _text(value)

    @field_validator("aliases")
    @classmethod
    def aliases_are_unambiguous(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or "\x00" in item for item in value):
            raise ValueError("semantic dimension aliases must be non-empty and NUL-free")
        return _unique_casefold(value, "semantic dimension aliases")

    @model_validator(mode="after")
    def attributes_belong_to_dimension(self) -> "SemanticDimension":
        if any(item.dimension_id != self.semantic_dimension_id for item in self.attributes):
            raise ValueError("semantic attribute belongs to a different dimension")
        ids = [item.semantic_attribute_id for item in self.attributes]
        if len(set(ids)) != len(ids):
            raise ValueError("semantic attribute IDs must be unique within a dimension")
        names = tuple(name for item in self.attributes for name in (item.name, *item.aliases))
        return self if _unique_casefold(names, "semantic dimension attribute names") else self


class HierarchyLevel(_SourceModel):
    level_id: str = Field(min_length=1)
    semantic_attribute_id: str = Field(min_length=1)
    ordinal: int = Field(gt=0)
    name: str = Field(min_length=1)

    @field_validator("level_id", "semantic_attribute_id")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        return _text(value)


class DimensionHierarchy(_SourceModel):
    hierarchy_id: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    levels: tuple[HierarchyLevel, ...] = Field(min_length=1)
    description: str = Field(min_length=1)
    strict_rollup: bool = False
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    validation_state: SemanticHierarchyValidationState = SemanticHierarchyValidationState.REVIEW_REQUIRED

    @field_validator("hierarchy_id", "dimension_id")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("description")
    @classmethod
    def description_is_safe(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def levels_are_ordered(self) -> "DimensionHierarchy":
        ordinals = [item.ordinal for item in self.levels]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError("hierarchy levels require explicit contiguous ordering")
        if len({item.semantic_attribute_id for item in self.levels}) != len(self.levels):
            raise ValueError("hierarchy levels cannot repeat an attribute")
        if self.strict_rollup and not self.evidence_refs:
            raise ValueError("strict hierarchy roll-up requires evidence refs")
        return self


class TimeRole(_SourceModel):
    time_role_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    relationship_ref: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    physical_date_column: str = Field(min_length=1)
    date_dimension_id: str = Field(min_length=1)
    date_dimension_attribute_id: str = Field(min_length=1)
    grain_id: str = Field(min_length=1)
    time_semantics: str = Field(min_length=1)
    supported_calendar: str = "GREGORIAN"
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("time_role_id", "fact_id", "relationship_ref", "physical_date_column", "date_dimension_id", "date_dimension_attribute_id", "grain_id")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("semantic_name", "time_semantics", "supported_calendar")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        return _text(value)


class SemanticMeasure(_SourceModel):
    semantic_measure_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    measure_spec_id: str = Field(min_length=1)
    measure_spec_content_hash: str = Field(min_length=1)
    physical_fact_table_ref: str = Field(min_length=1)
    physical_field_ref: str = Field(min_length=1)
    grain_id: str = Field(min_length=1)
    aggregation_class: AggregationClass
    allowed_aggregation_operations: tuple[str, ...] = ()
    unit_semantics: str = Field(min_length=1)
    currency_semantics: str = Field(min_length=1)
    nullable: bool = True
    compatible_dimension_ids: tuple[str, ...] = ()
    compatible_time_role_ids: tuple[str, ...] = ()
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    availability: SemanticAvailability = SemanticAvailability.AVAILABLE
    failure_reason: str | None = None

    @field_validator("semantic_measure_id", "fact_id", "measure_spec_id", "physical_fact_table_ref", "physical_field_ref", "grain_id")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name", "description", "unit_semantics", "currency_semantics")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        return _text(value)

    @field_validator("allowed_aggregation_operations")
    @classmethod
    def operations_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not item or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", item) for item in normalized):
            raise ValueError("semantic aggregation operations must be safe operation names")
        return _unique_casefold(normalized, "semantic aggregation operations")

    @model_validator(mode="after")
    def aggregation_is_not_loosened(self) -> "SemanticMeasure":
        operations = set(self.allowed_aggregation_operations)
        if self.aggregation_class is AggregationClass.NON_ADDITIVE and "SUM" in operations:
            raise ValueError("NON_ADDITIVE measures cannot expose SUM")
        if self.availability is SemanticAvailability.AVAILABLE and not operations:
            raise ValueError("available semantic measures require an explicit operation")
        if self.availability is not SemanticAvailability.AVAILABLE and not self.failure_reason:
            raise ValueError("unavailable/review-required measures need a failure reason")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    @property
    def semantic_content_hash(self) -> str:
        return self.content_hash


class MetricExpression(_SourceModel):
    expression_type: SemanticExpressionType
    measure_id: str | None = None
    aggregation: str | None = None
    numerator_metric_id: str | None = None
    denominator_metric_id: str | None = None
    zero_denominator_behavior: ZeroDenominatorBehavior | None = None
    result_unit_semantics: str | None = None

    @field_validator("measure_id", "numerator_metric_id", "denominator_metric_id")
    @classmethod
    def optional_identifiers_are_safe(cls, value: str | None) -> str | None:
        return _safe_identifier(value) if value is not None else None

    @field_validator("aggregation")
    @classmethod
    def optional_operation_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", value):
            raise ValueError("metric aggregation must be a safe operation name")
        return value

    @model_validator(mode="after")
    def expression_shape_is_bounded(self) -> "MetricExpression":
        if self.expression_type is SemanticExpressionType.AGGREGATE_MEASURE:
            if not self.measure_id or not self.aggregation:
                raise ValueError("aggregate expressions require a measure and operation")
            if any((self.numerator_metric_id, self.denominator_metric_id, self.zero_denominator_behavior)):
                raise ValueError("aggregate expressions cannot contain ratio fields")
        elif self.expression_type is SemanticExpressionType.RATIO:
            if not self.numerator_metric_id or not self.denominator_metric_id or self.zero_denominator_behavior is None or not self.result_unit_semantics:
                raise ValueError("ratio expressions require explicit numerator, denominator, zero and unit semantics")
            if self.measure_id or self.aggregation:
                raise ValueError("ratio expressions cannot contain aggregate fields")
        return self


class MetricSpec(_SourceModel):
    metric_id: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    metric_kind: SemanticMetricKind
    fact_ids: tuple[str, ...] = Field(min_length=1)
    grain_ids: tuple[str, ...] = Field(min_length=1)
    measure_ids: tuple[str, ...] = Field(min_length=1)
    expression: MetricExpression
    aggregation_class: AggregationClass | None = None
    allowed_aggregation_operations: tuple[str, ...] = ()
    compatible_dimension_ids: tuple[str, ...] = ()
    compatible_time_role_ids: tuple[str, ...] = ()
    unit_semantics: str = Field(min_length=1)
    currency_semantics: str = Field(min_length=1)
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    availability: SemanticAvailability = SemanticAvailability.AVAILABLE
    failure_reason: str | None = None

    @field_validator("metric_id", "semantic_name")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("display_name", "description", "unit_semantics", "currency_semantics")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        return _text(value)

    @field_validator("fact_ids", "grain_ids", "measure_ids", "compatible_dimension_ids", "compatible_time_role_ids")
    @classmethod
    def reference_ids_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_identifier(item) for item in value)

    @field_validator("allowed_aggregation_operations")
    @classmethod
    def metric_operations_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", item) for item in normalized):
            raise ValueError("metric operations must be safe operation names")
        return _unique_casefold(normalized, "metric operations")

    @model_validator(mode="after")
    def metric_expression_is_consistent(self) -> "MetricSpec":
        if self.metric_kind is SemanticMetricKind.BASE_AGGREGATE:
            if self.expression.expression_type is not SemanticExpressionType.AGGREGATE_MEASURE or self.expression.measure_id not in self.measure_ids:
                raise ValueError("base metrics must aggregate one referenced measure")
        if self.metric_kind is SemanticMetricKind.DERIVED and self.availability is SemanticAvailability.AVAILABLE:
            raise ValueError("DERIVED_METRIC_NOT_EXECUTABLE_V1: derived metrics cannot be AVAILABLE in V1")
        if self.availability is SemanticAvailability.AVAILABLE and not self.allowed_aggregation_operations:
            raise ValueError("available metrics require allowed aggregation operations")
        if self.availability is not SemanticAvailability.AVAILABLE and not self.failure_reason:
            raise ValueError("unavailable/review-required metrics need a failure reason")
        if self.aggregation_class is AggregationClass.NON_ADDITIVE and "SUM" in self.allowed_aggregation_operations:
            raise ValueError("NON_ADDITIVE metrics cannot expose SUM")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    @property
    def semantic_content_hash(self) -> str:
        return self.content_hash


class SemanticRelationship(_SourceModel):
    relationship_id: str = Field(min_length=1)
    source_semantic_object_id: str = Field(min_length=1)
    target_semantic_object_id: str = Field(min_length=1)
    relationship_kind: SemanticRelationshipKind
    relationship_scope: SemanticRelationshipScope
    cardinality: str = Field(min_length=1)
    physical_fact_id: str = Field(min_length=1)
    physical_fact_table: str = Field(min_length=1)
    physical_fact_column: str = Field(min_length=1)
    physical_dimension_id: str = Field(min_length=1)
    physical_dimension_table: str = Field(min_length=1)
    physical_dimension_key_column: str = Field(min_length=1)
    upstream_relationship_ref: str = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("relationship_id", "source_semantic_object_id", "target_semantic_object_id", "physical_fact_id", "physical_fact_table", "physical_fact_column", "physical_dimension_id", "physical_dimension_table", "physical_dimension_key_column")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("cardinality", "upstream_relationship_ref")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def relationship_kind_matches_scope(self) -> "SemanticRelationship":
        if self.relationship_scope is SemanticRelationshipScope.ANALYTICAL_TIME_ROLE and self.relationship_kind is not SemanticRelationshipKind.TIME_ROLE:
            raise ValueError("analytical time scope requires TIME_ROLE relationship kind")
        if self.relationship_scope is SemanticRelationshipScope.CANONICAL_ACCEPTED and self.relationship_kind is not SemanticRelationshipKind.FACT_TO_DIMENSION:
            raise ValueError("canonical scope requires FACT_TO_DIMENSION relationship kind")
        return self


class SemanticFilter(_SourceModel):
    attribute_id: str = Field(min_length=1)
    operator: SemanticFilterOperator
    values: tuple[SemanticValue, ...] = Field(min_length=1)

    @field_validator("attribute_id")
    @classmethod
    def attribute_id_is_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def filter_shape_is_bounded(self) -> "SemanticFilter":
        if self.operator is SemanticFilterOperator.EQUALS and len(self.values) != 1:
            raise ValueError("EQUALS requires one value")
        if self.operator is SemanticFilterOperator.DATE_RANGE and len(self.values) != 2:
            raise ValueError("DATE_RANGE requires start and end values")
        if self.operator is SemanticFilterOperator.IN and not self.values:
            raise ValueError("IN requires at least one value")
        return self


class SemanticQuerySort(_SourceModel):
    field_id: str = Field(min_length=1)
    descending: bool = False

    @field_validator("field_id")
    @classmethod
    def field_id_is_safe(cls, value: str) -> str:
        return _safe_identifier(value)


class SemanticQueryFilterShape(_SourceModel):
    """A filter's executable shape without retaining its bound values."""

    attribute_id: str = Field(min_length=1)
    operator: SemanticFilterOperator
    value_count: int = Field(ge=1, le=100)
    logical_type: str = Field(min_length=1)
    null_value: bool = False

    @field_validator("attribute_id")
    @classmethod
    def filter_shape_attribute_is_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("logical_type")
    @classmethod
    def filter_shape_type_is_safe(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def filter_shape_is_bounded(self) -> "SemanticQueryFilterShape":
        if self.operator is SemanticFilterOperator.EQUALS and self.value_count != 1:
            raise ValueError("EQUALS filter shapes require one value")
        if self.operator is SemanticFilterOperator.DATE_RANGE and self.value_count != 2:
            raise ValueError("DATE_RANGE filter shapes require two values")
        if self.operator is SemanticFilterOperator.IN and self.value_count < 1:
            raise ValueError("IN filter shapes require at least one value")
        if self.null_value and self.operator is not SemanticFilterOperator.EQUALS:
            raise ValueError("only EQUALS filter shapes may represent NULL")
        return self


class SemanticQueryRequest(_SourceModel):
    request_id: str = Field(min_length=1)
    metric_ids: tuple[str, ...] = Field(min_length=1)
    dimension_ids: tuple[str, ...] = ()
    group_by_attribute_ids: tuple[str, ...] = ()
    time_role_id: str | None = None
    aggregation_overrides: Mapping[str, str] = Field(default_factory=dict)
    filters: tuple[SemanticFilter, ...] = ()
    sort: tuple[SemanticQuerySort, ...] = ()
    limit: int = Field(default=1000, gt=0, le=10000)

    @field_validator("request_id", "time_role_id")
    @classmethod
    def request_identifiers_are_safe(cls, value: str | None) -> str | None:
        return _safe_identifier(value) if value is not None else None

    @field_validator("metric_ids", "dimension_ids", "group_by_attribute_ids")
    @classmethod
    def query_ids_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("semantic query IDs must be unique")
        return tuple(_safe_identifier(item) for item in value)

    @field_validator("aggregation_overrides")
    @classmethod
    def aggregation_overrides_are_safe(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        normalized = { _safe_identifier(key): operation.strip().upper() for key, operation in value.items() }
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", operation) for operation in normalized.values()):
            raise ValueError("aggregation overrides must be safe operation names")
        return normalized


class SemanticQueryPlan(_SourceModel):
    query_plan_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    semantic_model_id: str = Field(min_length=1)
    semantic_model_content_hash: str = Field(min_length=1)
    metric_ids: tuple[str, ...] = Field(min_length=1)
    dimension_ids: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = Field(min_length=1)
    grain_ids: tuple[str, ...] = Field(min_length=1)
    join_path_relationship_ids: tuple[str, ...] = ()
    group_by_attribute_ids: tuple[str, ...] = ()
    time_role_id: str | None = None
    aggregation_operations: Mapping[str, str] = Field(default_factory=dict)
    physical_bindings: Mapping[str, str] = Field(default_factory=dict)
    filter_shapes: tuple[SemanticQueryFilterShape, ...] = ()
    sort_specs: tuple[SemanticQuerySort, ...] = ()
    limit: int = Field(default=1000, gt=0, le=10000)
    parameter_count: int = Field(ge=0)
    parameter_logical_types: tuple[str, ...] = ()
    sql_template: str = Field(min_length=1)
    query_hash: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sql_is_read_only(self) -> "SemanticQueryPlan":
        statement = self.sql_template.strip()
        upper = statement.upper()
        if self.parameter_count != len(self.parameter_logical_types):
            raise ValueError("semantic query parameter metadata does not match parameter count")
        if not upper.startswith("SELECT ") or ";" in statement:
            raise ValueError("semantic query plans must contain one SELECT without a semicolon")
        return self


class SemanticQueryResult(_SourceModel):
    query_plan_id: str = Field(min_length=1)
    semantic_model_id: str = Field(min_length=1)
    columns: tuple[str, ...] = ()
    rows: tuple[Mapping[str, SemanticValue], ...] = ()
    row_count: int = Field(ge=0)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SemanticQueryCompilation(_SourceModel):
    query_plan: SemanticQueryPlan
    parameters: tuple[SemanticValue, ...] = ()

    @model_validator(mode="after")
    def parameter_count_matches(self) -> "SemanticQueryCompilation":
        if len(self.parameters) != self.query_plan.parameter_count:
            raise ValueError("semantic query parameter count does not match its plan")
        return self


class SemanticValidationCheck(_SourceModel):
    check_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: SemanticValidationStatus
    details: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SemanticValidationResult(_SourceModel):
    validation_id: str = Field(min_length=1)
    semantic_model_id: str = Field(min_length=1)
    semantic_model_content_hash: str = Field(min_length=1)
    status: SemanticValidationStatus
    checks: tuple[SemanticValidationCheck, ...] = Field(min_length=1)
    comparison_scope: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class SemanticModel(_SourceModel):
    semantic_model_id: str = Field(pattern=r"^smodel_[a-f0-9]{32}$")
    semantic_model_version: str = Field(min_length=1)
    analytical_plan_id: str = Field(min_length=1)
    analytical_plan_content_hash: str = Field(min_length=1)
    analytical_spec_package_hash: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    compiled_plan_id: str = Field(min_length=1)
    compiled_plan_content_hash: str = Field(min_length=1)
    materialization_artifact_id: str = Field(min_length=1)
    materialization_artifact_content_hash: str = Field(min_length=1)
    target_config_fingerprint: str = Field(min_length=1)
    target_relative_path: str = Field(min_length=1)
    target_file_sha256: str | None = None
    status: SemanticModelStatus
    dimensions: tuple[SemanticDimension, ...] = Field(min_length=1)
    measures: tuple[SemanticMeasure, ...] = Field(min_length=1)
    metrics: tuple[MetricSpec, ...] = ()
    hierarchies: tuple[DimensionHierarchy, ...] = ()
    time_roles: tuple[TimeRole, ...] = ()
    relationships: tuple[SemanticRelationship, ...] = ()
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    unresolved_semantic_items: tuple[str, ...] = ()

    @field_validator("analytical_plan_id", "canonical_model_id", "compiled_plan_id", "materialization_artifact_id")
    @classmethod
    def upstream_ids_are_safe(cls, value: str) -> str:
        if not value or "\x00" in value:
            raise ValueError("upstream artifact IDs must be non-empty and NUL-free")
        return value

    @field_validator("target_relative_path")
    @classmethod
    def target_path_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value.lower().endswith(".duckdb"):
            raise ValueError("semantic target path must be a relative .duckdb path")
        return str(path)

    @field_validator("semantic_model_version", "analytical_plan_content_hash", "analytical_spec_package_hash", "canonical_model_content_hash", "compiled_plan_content_hash", "materialization_artifact_content_hash", "target_config_fingerprint", "policy_version")
    @classmethod
    def hashes_and_versions_are_safe(cls, value: str) -> str:
        return _text(value)

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"semantic_model_id"}))

    @property
    def semantic_content_hash(self) -> str:
        return self.content_hash

    @model_validator(mode="after")
    def semantic_references_are_closed(self) -> "SemanticModel":
        dimension_ids = {item.semantic_dimension_id for item in self.dimensions}
        fact_ids = {item.fact_id for item in self.measures}
        measure_ids = {item.measure_spec_id for item in self.measures}
        metric_ids = {item.metric_id for item in self.metrics}
        attr_by_id = {item.semantic_attribute_id: item for dimension in self.dimensions for item in dimension.attributes}
        attribute_ids = [item.semantic_attribute_id for dimension in self.dimensions for item in dimension.attributes]
        if len(dimension_ids) != len(self.dimensions) or len(measure_ids) != len(self.measures) or len(metric_ids) != len(self.metrics) or len(set(attribute_ids)) != len(attribute_ids):
            raise ValueError("semantic object IDs must be unique")
        namespace_names: list[str] = []
        for item in self.dimensions:
            namespace_names.extend((item.business_name, *item.aliases))
        for item in self.metrics:
            namespace_names.append(item.display_name)
        _unique_casefold(tuple(namespace_names), "semantic object names and aliases")
        for hierarchy in self.hierarchies:
            if hierarchy.dimension_id not in dimension_ids:
                raise ValueError("hierarchy references an unknown dimension")
            dimension = next(item for item in self.dimensions if item.semantic_dimension_id == hierarchy.dimension_id)
            attribute_ids = {item.semantic_attribute_id for item in dimension.attributes}
            if any(level.semantic_attribute_id not in attribute_ids for level in hierarchy.levels):
                raise ValueError("hierarchy level is not an attribute of its dimension")
        for role in self.time_roles:
            if role.fact_id not in fact_ids or role.date_dimension_id not in dimension_ids:
                raise ValueError("time role references an unknown fact or dimension")
            dimension = next(item for item in self.dimensions if item.semantic_dimension_id == role.date_dimension_id)
            if dimension.role is not DimensionRole.DATE or role.date_dimension_attribute_id not in {item.semantic_attribute_id for item in dimension.attributes}:
                raise ValueError("time role requires a DATE dimension and existing date attribute")
        for metric in self.metrics:
            if any(item not in fact_ids for item in metric.fact_ids) or any(item not in metric_ids and item not in {measure.fact_id for measure in self.measures} for item in metric.fact_ids):
                raise ValueError("metric references an unknown fact")
            if any(item not in measure_ids for item in metric.measure_ids):
                raise ValueError("metric references an unknown measure")
            if metric.expression.measure_id and metric.expression.measure_id not in metric.measure_ids:
                raise ValueError("metric expression measure is not declared")
            if metric.expression.numerator_metric_id and metric.expression.numerator_metric_id not in metric_ids:
                raise ValueError("ratio numerator metric is unknown")
            if metric.expression.denominator_metric_id and metric.expression.denominator_metric_id not in metric_ids:
                raise ValueError("ratio denominator metric is unknown")
        for relationship in self.relationships:
            if relationship.source_semantic_object_id not in fact_ids or relationship.target_semantic_object_id not in dimension_ids:
                raise ValueError("semantic relationship endpoint is unknown")
        return self


def semantic_model_id(payload: Mapping[str, object]) -> str:
    return stable_id("smodel", payload)


def metric_id(payload: Mapping[str, object]) -> str:
    return stable_id("metric", payload)


def semantic_query_plan_id(payload: Mapping[str, object]) -> str:
    return stable_id("squery", payload)


def semantic_validation_id(payload: Mapping[str, object]) -> str:
    return stable_id("sval", payload)
