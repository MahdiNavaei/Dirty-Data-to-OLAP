"""Project-owned analytical planning and materialization contracts.

The analytical layer is intentionally downstream from the canonical graph.  It
references canonical identities and accepted relationships, but it owns its
own grain, warehouse keys, aggregation semantics, SQL and target artifacts.
No provider-native database or semantic-layer objects cross this boundary.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping

from pydantic import Field, field_validator, model_validator

from .source import _SourceModel, stable_digest, stable_id, utc_now


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe analytical identifier: {value!r}")
    return value


class AnalyticalReviewState(str, Enum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ACCEPTED = "ACCEPTED"
    DEFERRED = "DEFERRED"


class FactType(str, Enum):
    TRANSACTION = "TRANSACTION"
    SNAPSHOT = "SNAPSHOT"


class DimensionRole(str, Enum):
    CONFORMED = "CONFORMED"
    ROLE_PLAYING = "ROLE_PLAYING"
    DATE = "DATE"


class AggregationClass(str, Enum):
    ADDITIVE = "ADDITIVE"
    SEMI_ADDITIVE = "SEMI_ADDITIVE"
    NON_ADDITIVE = "NON_ADDITIVE"


class SCDMode(str, Enum):
    TYPE1_SNAPSHOT = "TYPE1_SNAPSHOT"
    TYPE2_PREPARED = "TYPE2_PREPARED"


class UnknownMemberPolicy(str, Enum):
    QUARANTINE_FACT = "QUARANTINE_FACT"
    NULLABLE_FK = "NULLABLE_FK"
    EXPLICIT_UNKNOWN_MEMBER = "EXPLICIT_UNKNOWN_MEMBER"


class GrainNullPolicy(str, Enum):
    REJECT_NULLS = "REJECT_NULLS"
    QUARANTINE_NULLS = "QUARANTINE_NULLS"


class MaterializationStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NON_CONSUMABLE = "NON_CONSUMABLE"


class WarehouseKeySpec(_SourceModel):
    key_name: str = Field(min_length=1)
    sql_type: str = "BIGINT"
    algorithm: str = "SHA256_NAMESPACE_INT64_V1"
    namespace: str = Field(min_length=1)
    collision_policy: str = "FAIL"

    @field_validator("key_name")
    @classmethod
    def key_name_is_safe(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def fixed_key_policy(self) -> "WarehouseKeySpec":
        if self.sql_type != "BIGINT":
            raise ValueError("V1 warehouse keys must use BIGINT")
        if self.algorithm != "SHA256_NAMESPACE_INT64_V1":
            raise ValueError("V1 warehouse keys must use the deterministic SHA-256 strategy")
        if self.collision_policy != "FAIL":
            raise ValueError("warehouse key collisions must fail closed")
        return self


class SCDPolicySpec(_SourceModel):
    mode: SCDMode
    overwrite_current_snapshot: bool = True
    valid_from_column: str | None = None
    valid_to_column: str | None = None
    is_current_column: str | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_mode(self) -> "SCDPolicySpec":
        if self.mode is SCDMode.TYPE1_SNAPSHOT:
            if not self.overwrite_current_snapshot:
                raise ValueError("TYPE1_SNAPSHOT requires current snapshot overwrite semantics")
        elif not all((self.valid_from_column, self.valid_to_column, self.is_current_column)):
            raise ValueError("TYPE2_PREPARED must declare future history columns")
        return self


class UnknownMemberPolicySpec(_SourceModel):
    policy: UnknownMemberPolicy
    rationale: str = Field(min_length=1)
    unknown_member_key: int | None = None

    @model_validator(mode="after")
    def explicit_member_has_key(self) -> "UnknownMemberPolicySpec":
        if self.policy is UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER and self.unknown_member_key is None:
            raise ValueError("EXPLICIT_UNKNOWN_MEMBER requires an explicit key")
        if self.policy is not UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER and self.unknown_member_key is not None:
            raise ValueError("only EXPLICIT_UNKNOWN_MEMBER may declare an unknown key")
        return self


class DimensionAttributeSpec(_SourceModel):
    attribute_id: str = Field(min_length=1)
    column_name: str = Field(min_length=1)
    logical_type: str = Field(min_length=1)
    input_column_name: str | None = None
    derivation: str | None = None
    canonical_attribute_refs: tuple[str, ...] = ()
    nullable: bool = False
    lineage_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("column_name")
    @classmethod
    def column_is_safe(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("input_column_name")
    @classmethod
    def input_column_is_safe(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None


class DimensionSpec(_SourceModel):
    dimension_id: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    input_table_id: str | None = None
    canonical_reference_column: str = "canonical_entity_id"
    canonical_entity_type_id: str = Field(min_length=1)
    canonical_entity_refs: tuple[str, ...] = Field(min_length=1)
    role: DimensionRole
    eligibility_reason: str = Field(min_length=1)
    surrogate_key: WarehouseKeySpec
    alternate_key_columns: tuple[str, ...] = Field(min_length=1)
    attributes: tuple[DimensionAttributeSpec, ...] = Field(min_length=1)
    scd_policy: SCDPolicySpec
    unknown_member_policy: UnknownMemberPolicySpec
    conformed_dimension_id: str | None = None
    date_range_start: date | None = None
    date_range_end: date | None = None
    calendar_policy: str | None = None
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    review_decision_ref: str | None = None

    @field_validator("table_name")
    @classmethod
    def table_is_safe(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("input_table_id", "canonical_reference_column")
    @classmethod
    def input_refs_are_safe(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None

    @field_validator("alternate_key_columns")
    @classmethod
    def alternate_keys_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("dimension alternate keys must be unique")
        return tuple(_identifier(item) for item in value)

    @model_validator(mode="after")
    def conformance_is_explicit(self) -> "DimensionSpec":
        if self.role is DimensionRole.CONFORMED and self.conformed_dimension_id not in {None, self.dimension_id}:
            raise ValueError("a conformed dimension must reference itself or omit the conformance reference")
        if self.surrogate_key.key_name in self.alternate_key_columns:
            raise ValueError("surrogate key cannot also be an alternate key")
        if self.role is not DimensionRole.DATE and not self.input_table_id:
            raise ValueError("non-date dimensions require an input table")
        if (self.date_range_start is None) != (self.date_range_end is None):
            raise ValueError("date dimensions must declare both date range endpoints")
        if self.date_range_start and self.date_range_end and self.date_range_end < self.date_range_start:
            raise ValueError("date dimension range must be ordered")
        return self

    @property
    def semantic_content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version", "review_state", "review_decision_ref"}))


class FactForeignKeySpec(_SourceModel):
    relationship_ref: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    fact_column: str = Field(min_length=1)
    dimension_key_column: str = Field(min_length=1)
    canonical_entity_type_id: str = Field(min_length=1)
    input_reference_column: str | None = None
    required: bool = True

    @field_validator("fact_column", "dimension_key_column")
    @classmethod
    def fk_columns_are_safe(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("input_reference_column")
    @classmethod
    def input_reference_is_safe(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None


class GrainSpec(_SourceModel):
    grain_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    human_readable_grain: str = Field(min_length=1)
    key_columns: tuple[str, ...] = Field(min_length=1)
    null_policy: GrainNullPolicy
    validated: bool = False
    observed_row_count: int = Field(ge=0)
    duplicate_key_count: int = Field(ge=0)
    validation_fingerprint: str | None = None
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("key_columns")
    @classmethod
    def grain_columns_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("grain columns must be unique")
        return tuple(_identifier(item) for item in value)

    @model_validator(mode="after")
    def validated_grain_is_proven(self) -> "GrainSpec":
        if self.validated and self.duplicate_key_count != 0:
            raise ValueError("a validated grain cannot contain duplicate composite keys")
        if self.validated and not self.validation_fingerprint:
            raise ValueError("a validated grain requires a validation fingerprint")
        return self

    @property
    def semantic_content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version"}))


class MeasureSpec(_SourceModel):
    measure_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    aggregation_class: AggregationClass
    aggregation_rule: str = Field(min_length=1)
    unit_semantics: str = Field(min_length=1)
    currency_semantics: str = Field(min_length=1)
    logical_type: str = "DECIMAL"
    nullable: bool = True
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("field_name")
    @classmethod
    def measure_field_is_safe(cls, value: str) -> str:
        return _identifier(value)

    @property
    def semantic_content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version", "review_state"}))


class FactSpec(_SourceModel):
    fact_id: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    input_table_id: str = Field(min_length=1)
    fact_type: FactType
    canonical_event_type_id: str = Field(min_length=1)
    canonical_event_refs: tuple[str, ...] = Field(min_length=1)
    grain_spec_id: str = Field(min_length=1)
    dimension_foreign_keys: tuple[FactForeignKeySpec, ...] = Field(min_length=1)
    degenerate_dimension_columns: tuple[str, ...] = ()
    measure_ids: tuple[str, ...] = Field(min_length=1)
    date_role_columns: tuple[str, ...] = Field(min_length=1)
    relationship_refs: tuple[str, ...] = Field(min_length=1)
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    review_decision_ref: str | None = None

    @field_validator("table_name")
    @classmethod
    def fact_table_is_safe(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("degenerate_dimension_columns", "date_role_columns")
    @classmethod
    def fact_columns_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("fact columns must be unique")
        return tuple(_identifier(item) for item in value)

    @model_validator(mode="after")
    def fact_has_measures_and_grain(self) -> "FactSpec":
        if self.grain_spec_id in self.measure_ids:
            raise ValueError("grain spec reference cannot be a measure reference")
        return self

    @property
    def semantic_content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"schema_version", "review_state", "review_decision_ref"}))


AnalyticalValue = str | int | float | Decimal | date | datetime | bool | None | tuple[str, ...]


class AnalyticalColumnBinding(_SourceModel):
    column_id: str = Field(min_length=1)
    column_name: str = Field(min_length=1)
    logical_type: str = Field(min_length=1)
    nullable: bool = True
    canonical_attribute_refs: tuple[str, ...] = ()
    lineage_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("column_name")
    @classmethod
    def column_name_is_safe(cls, value: str) -> str:
        return _identifier(value)


class AnalyticalCell(_SourceModel):
    column_name: str = Field(min_length=1)
    value: AnalyticalValue

    @field_validator("column_name")
    @classmethod
    def cell_column_is_safe(cls, value: str) -> str:
        return _identifier(value)


class AnalyticalInputRow(_SourceModel):
    row_ref: str = Field(min_length=1)
    canonical_reference: str = Field(min_length=1)
    values: tuple[AnalyticalCell, ...] = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def columns_are_unique(self) -> "AnalyticalInputRow":
        names = [item.column_name for item in self.values]
        if len(set(names)) != len(names):
            raise ValueError("analytical input row columns must be unique")
        return self

    def value_for(self, column_name: str) -> AnalyticalValue:
        for item in self.values:
            if item.column_name == column_name:
                return item.value
        raise KeyError(column_name)

    @property
    def value_map(self) -> Mapping[str, AnalyticalValue]:
        return {item.column_name: item.value for item in self.values}


class AnalyticalRowBatch(_SourceModel):
    batch_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    rows: tuple[AnalyticalInputRow, ...] = ()
    source_batch_refs: tuple[str, ...] = Field(min_length=1)
    source_snapshot_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    lineage_refs: tuple[str, ...] = Field(min_length=1)

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class AnalyticalInputTable(_SourceModel):
    table_id: str = Field(min_length=1)
    canonical_concept_ref: str = Field(min_length=1)
    columns: tuple[AnalyticalColumnBinding, ...] = Field(min_length=1)
    batches: tuple[AnalyticalRowBatch, ...] = ()
    source_table_refs: tuple[str, ...] = Field(min_length=1)
    lineage_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def table_batches_match(self) -> "AnalyticalInputTable":
        if any(batch.table_id != self.table_id for batch in self.batches):
            raise ValueError("analytical input batch belongs to a different table")
        names = [item.column_name for item in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("analytical input table columns must be unique")
        return self

    @property
    def rows(self) -> tuple[AnalyticalInputRow, ...]:
        return tuple(row for batch in self.batches for row in batch.rows)

    def column_names(self) -> frozenset[str]:
        return frozenset(item.column_name for item in self.columns)


class AnalyticalInputDataset(_SourceModel):
    dataset_id: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    tables: tuple[AnalyticalInputTable, ...] = Field(min_length=1)
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    source_snapshot_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    allow_literal_sql: bool = False
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def tables_are_unique(self) -> "AnalyticalInputDataset":
        ids = [item.table_id for item in self.tables]
        if len(set(ids)) != len(ids):
            raise ValueError("analytical input table IDs must be unique")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    def table(self, table_id: str) -> AnalyticalInputTable:
        for item in self.tables:
            if item.table_id == table_id:
                return item
        raise KeyError(table_id)

    @property
    def row_counts(self) -> Mapping[str, int]:
        return {item.table_id: len(item.rows) for item in self.tables}


class AnalyticalPlanningRequest(_SourceModel):
    request_id: str = Field(min_length=1)
    dimensions: tuple[DimensionSpec, ...] = ()
    facts: tuple[FactSpec, ...] = Field(min_length=1)
    grains: tuple[GrainSpec, ...] = Field(min_length=1)
    measures: tuple[MeasureSpec, ...] = Field(min_length=1)
    accepted_relationship_refs: tuple[str, ...] = ()
    deferred_concept_refs: tuple[str, ...] = ()
    deferred_concept_reasons: Mapping[str, str] = Field(default_factory=dict)
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED

    @model_validator(mode="after")
    def request_is_review_required(self) -> "AnalyticalPlanningRequest":
        if self.review_state is not AnalyticalReviewState.REVIEW_REQUIRED:
            raise ValueError("analytical planning requests require a separate review decision")
        return self


class AnalyticalInputBinding(_SourceModel):
    binding_id: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    fixture_id: str | None = None
    fixture_content_hash: str | None = None
    dataset_id: str | None = None
    dataset_content_hash: str | None = None
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    source_snapshot_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    row_counts: Mapping[str, int] = Field(default_factory=dict)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def binds_one_input(self) -> "AnalyticalInputBinding":
        fixture_bound = self.fixture_id is not None or self.fixture_content_hash is not None
        dataset_bound = self.dataset_id is not None or self.dataset_content_hash is not None
        if fixture_bound and (self.fixture_id is None or self.fixture_content_hash is None):
            raise ValueError("fixture binding requires fixture ID and content hash")
        if dataset_bound and (self.dataset_id is None or self.dataset_content_hash is None):
            raise ValueError("dataset binding requires dataset ID and content hash")
        if fixture_bound == dataset_bound:
            raise ValueError("analytical input binding must bind exactly one fixture or dataset")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    @property
    def input_id(self) -> str:
        return self.dataset_id or self.fixture_id or ""

    @property
    def input_content_hash(self) -> str:
        return self.dataset_content_hash or self.fixture_content_hash or ""


class AnalyticalPlan(_SourceModel):
    plan_id: str = Field(pattern=r"^aplan_[a-f0-9]{32}$")
    plan_version: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    canonical_model_fingerprint: str = Field(min_length=1)
    input_binding_id: str = Field(min_length=1)
    input_binding_content_hash: str = Field(min_length=1)
    canonical_entity_type_ids: tuple[str, ...] = Field(min_length=1)
    canonical_event_type_ids: tuple[str, ...] = Field(min_length=1)
    accepted_relationship_refs: tuple[str, ...] = Field(min_length=1)
    materialized_dimension_ids: tuple[str, ...] = Field(min_length=1)
    materialized_fact_ids: tuple[str, ...] = Field(min_length=1)
    grain_spec_ids: tuple[str, ...] = Field(min_length=1)
    measure_spec_ids: tuple[str, ...] = Field(min_length=1)
    dimension_spec_content_hashes: Mapping[str, str] = Field(default_factory=dict)
    fact_spec_content_hashes: Mapping[str, str] = Field(default_factory=dict)
    grain_spec_content_hashes: Mapping[str, str] = Field(default_factory=dict)
    measure_spec_content_hashes: Mapping[str, str] = Field(default_factory=dict)
    source_record_lineage_refs: tuple[str, ...] = Field(min_length=1)
    canonical_conflict_refs: tuple[str, ...] = ()
    deferred_concept_refs: tuple[str, ...] = ()
    deferred_concept_reasons: Mapping[str, str] = Field(default_factory=dict)
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    policy_version: str = Field(min_length=1)
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    unresolved_items: tuple[str, ...] = ()
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"created_at"}))

    @property
    def analytical_spec_package_hash(self) -> str:
        return stable_digest({
            "dimension": dict(sorted(self.dimension_spec_content_hashes.items())),
            "fact": dict(sorted(self.fact_spec_content_hashes.items())),
            "grain": dict(sorted(self.grain_spec_content_hashes.items())),
            "measure": dict(sorted(self.measure_spec_content_hashes.items())),
        })

    @model_validator(mode="after")
    def spec_hash_maps_match_ids(self) -> "AnalyticalPlan":
        expected = (
            (self.dimension_spec_content_hashes, self.materialized_dimension_ids, "dimension"),
            (self.fact_spec_content_hashes, self.materialized_fact_ids, "fact"),
            (self.grain_spec_content_hashes, self.grain_spec_ids, "grain"),
            (self.measure_spec_content_hashes, self.measure_spec_ids, "measure"),
        )
        for hashes, ids, label in expected:
            if hashes and set(hashes) != set(ids):
                raise ValueError(f"{label} spec content hashes must match plan IDs")
        return self


class CustomerFixtureRow(_SourceModel):
    canonical_entity_id: str = Field(min_length=1)
    customer_code: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)


class ProductFixtureRow(_SourceModel):
    canonical_entity_id: str = Field(min_length=1)
    product_code: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)


class BranchFixtureRow(_SourceModel):
    canonical_entity_id: str = Field(min_length=1)
    branch_code: str = Field(min_length=1)
    branch_name: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)


class OrderFixtureRow(_SourceModel):
    canonical_entity_id: str = Field(min_length=1)
    order_event_id: str = Field(min_length=1)
    customer_entity_id: str = Field(min_length=1)
    branch_entity_id: str = Field(min_length=1)
    order_date: date
    source_record_refs: tuple[str, ...] = Field(min_length=1)


class OrderLineFixtureRow(_SourceModel):
    canonical_entity_id: str = Field(min_length=1)
    order_event_id: str = Field(min_length=1)
    line_sequence: int = Field(gt=0)
    product_entity_id: str = Field(min_length=1)
    quantity: Decimal
    unit_price: Decimal | None = None
    discount_rate: Decimal | None = None
    source_record_refs: tuple[str, ...] = Field(min_length=1)


class AnalyticalInputFixture(_SourceModel):
    fixture_id: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    customers: tuple[CustomerFixtureRow, ...] = ()
    products: tuple[ProductFixtureRow, ...] = ()
    branches: tuple[BranchFixtureRow, ...] = ()
    orders: tuple[OrderFixtureRow, ...] = ()
    order_lines: tuple[OrderLineFixtureRow, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    @property
    def row_counts(self) -> Mapping[str, int]:
        return {
            "customer": len(self.customers),
            "product": len(self.products),
            "branch": len(self.branches),
            "order": len(self.orders),
            "order_line": len(self.order_lines),
        }

    def to_analytical_dataset(self) -> AnalyticalInputDataset:
        """Convert the historical retail fixture into the generic row plane.

        This is a compatibility helper for the synthetic reference only.  The
        application planner/compiler consume AnalyticalInputDataset and never
        import these fixture row classes.
        """

        def table(
            table_id: str,
            concept: str,
            columns: tuple[AnalyticalColumnBinding, ...],
            rows: tuple[AnalyticalInputRow, ...],
        ) -> AnalyticalInputTable:
            batch = AnalyticalRowBatch(
                batch_id=f"{self.fixture_id}:{table_id}:0",
                table_id=table_id,
                rows=rows,
                source_batch_refs=(f"synthetic:{table_id}",),
                lineage_refs=self.provenance_refs,
            )
            return AnalyticalInputTable(
                table_id=table_id,
                canonical_concept_ref=concept,
                columns=columns,
                batches=(batch,),
                source_table_refs=(table_id,),
                lineage_refs=self.provenance_refs,
            )

        def cell(name: str, value: AnalyticalValue) -> AnalyticalCell:
            return AnalyticalCell(column_name=name, value=value)

        def refs_row(row: _SourceModel, canonical: str, values: tuple[AnalyticalCell, ...]) -> AnalyticalInputRow:
            source_refs = tuple(getattr(row, "source_record_refs"))
            return AnalyticalInputRow(
                row_ref=source_refs[0],
                canonical_reference=canonical,
                values=values,
                source_record_refs=source_refs,
                lineage_refs=source_refs,
            )

        def columns(names: tuple[tuple[str, str], ...]) -> tuple[AnalyticalColumnBinding, ...]:
            return tuple(
                AnalyticalColumnBinding(
                    column_id=f"{name}-column",
                    column_name=name,
                    logical_type=logical_type,
                    nullable=True,
                    lineage_refs=self.provenance_refs,
                )
                for name, logical_type in names
            )

        order_by_event = {row.order_event_id: row for row in self.orders}
        customer_ids = {row.canonical_entity_id for row in self.customers}
        branch_ids = {row.canonical_entity_id for row in self.branches}
        product_ids = {row.canonical_entity_id for row in self.products}
        for row in self.orders:
            if row.customer_entity_id not in customer_ids:
                raise ValueError("MISSING_DIMENSION_REFERENCE:customer")
            if row.branch_entity_id not in branch_ids:
                raise ValueError("MISSING_DIMENSION_REFERENCE:branch")
        order_line_columns = (
            ("order_event_id", "STRING"),
            ("line_sequence", "INTEGER"),
            ("product_entity_id", "STRING"),
            ("customer_entity_id", "STRING"),
            ("branch_entity_id", "STRING"),
            ("order_date", "DATE"),
            ("quantity", "DECIMAL"),
            ("unit_price", "DECIMAL"),
            ("discount_rate", "DECIMAL"),
            ("source_record_refs", "STRING_LIST"),
        )
        order_line_rows = []
        for row in self.order_lines:
            order = order_by_event.get(row.order_event_id)
            if order is None:
                raise ValueError("order-line fixture references an unknown order")
            if row.product_entity_id not in product_ids:
                raise ValueError("MISSING_DIMENSION_REFERENCE:product")
            order_line_rows.append(refs_row(row, row.canonical_entity_id, (
                cell("order_event_id", row.order_event_id),
                cell("line_sequence", row.line_sequence),
                cell("product_entity_id", row.product_entity_id),
                cell("customer_entity_id", order.customer_entity_id),
                cell("branch_entity_id", order.branch_entity_id),
                cell("order_date", order.order_date),
                cell("quantity", row.quantity),
                cell("unit_price", row.unit_price),
                cell("discount_rate", row.discount_rate),
                cell("source_record_refs", row.source_record_refs),
            )))

        tables = (
            table(
                "customers",
                "customer",
                columns((("customer_code", "STRING"), ("display_name", "STRING"), ("source_record_refs", "STRING_LIST"))),
                tuple(refs_row(row, row.canonical_entity_id, (cell("customer_code", row.customer_code), cell("display_name", row.display_name), cell("source_record_refs", row.source_record_refs))) for row in self.customers),
            ),
            table(
                "products",
                "product",
                columns((("product_code", "STRING"), ("product_name", "STRING"), ("category", "STRING"), ("source_record_refs", "STRING_LIST"))),
                tuple(refs_row(row, row.canonical_entity_id, (cell("product_code", row.product_code), cell("product_name", row.product_name), cell("category", row.category), cell("source_record_refs", row.source_record_refs))) for row in self.products),
            ),
            table(
                "branches",
                "branch",
                columns((("branch_code", "STRING"), ("branch_name", "STRING"), ("source_record_refs", "STRING_LIST"))),
                tuple(refs_row(row, row.canonical_entity_id, (cell("branch_code", row.branch_code), cell("branch_name", row.branch_name), cell("source_record_refs", row.source_record_refs))) for row in self.branches),
            ),
            table(
                "orders",
                "order",
                columns((("order_event_id", "STRING"), ("customer_entity_id", "STRING"), ("branch_entity_id", "STRING"), ("order_date", "DATE"), ("source_record_refs", "STRING_LIST"))),
                tuple(refs_row(row, row.canonical_entity_id, (cell("order_event_id", row.order_event_id), cell("customer_entity_id", row.customer_entity_id), cell("branch_entity_id", row.branch_entity_id), cell("order_date", row.order_date), cell("source_record_refs", row.source_record_refs))) for row in self.orders),
            ),
            table(
                "order_lines",
                "orderline",
                columns(order_line_columns),
                tuple(order_line_rows),
            ),
        )
        return AnalyticalInputDataset(
            dataset_id=self.fixture_id,
            canonical_model_id=self.canonical_model_id,
            canonical_model_content_hash=self.canonical_model_content_hash,
            tables=tables,
            allow_literal_sql=True,
            provenance_refs=self.provenance_refs,
        )

    @model_validator(mode="after")
    def fixture_keys_are_unique(self) -> "AnalyticalInputFixture":
        if len({row.canonical_entity_id for row in self.customers}) != len(self.customers):
            raise ValueError("duplicate customer canonical identity")
        if len({row.canonical_entity_id for row in self.products}) != len(self.products):
            raise ValueError("duplicate product canonical identity")
        if len({row.canonical_entity_id for row in self.branches}) != len(self.branches):
            raise ValueError("duplicate branch canonical identity")
        if len({row.order_event_id for row in self.orders}) != len(self.orders):
            raise ValueError("duplicate order event identity")
        grain = [(row.order_event_id, row.line_sequence) for row in self.order_lines]
        if len(set(grain)) != len(grain):
            raise ValueError("duplicate order-line fixture grain")
        return self


class CompiledOperation(_SourceModel):
    operation_id: str = Field(min_length=1)
    operation_name: str = Field(min_length=1)
    dependencies: tuple[str, ...] = ()
    statement_kind: str = Field(min_length=1)
    sql_hash: str = Field(min_length=1)


class TargetConfig(_SourceModel):
    target_type: str = "duckdb"
    relative_path: str = Field(min_length=1)
    config_version: str = "duckdb-target-v1"

    @field_validator("relative_path")
    @classmethod
    def target_path_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value.lower().endswith(".duckdb"):
            raise ValueError("target path must be a relative .duckdb path without traversal")
        return str(path)

    @model_validator(mode="after")
    def only_duckdb_v1(self) -> "TargetConfig":
        if self.target_type != "duckdb":
            raise ValueError("Step20 supports only a DuckDB target")
        return self

    @property
    def config_fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class CompiledPlan(_SourceModel):
    compiled_plan_id: str = Field(pattern=r"^cplan_[a-f0-9]{32}$")
    plan_id: str = Field(min_length=1)
    plan_content_hash: str = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    dialect: str = "duckdb"
    operations: tuple[CompiledOperation, ...] = Field(min_length=1)
    generated_sql_id: str = Field(min_length=1)
    generated_sql_hash: str = Field(min_length=1)
    target_config_fingerprint: str = Field(min_length=1)
    input_binding_id: str = Field(min_length=1)
    input_binding_content_hash: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    analytical_spec_package_hash: str = Field(default="")
    table_names: tuple[str, ...] = ()
    dimension_specs: tuple[DimensionSpec, ...] = ()
    fact_specs: tuple[FactSpec, ...] = ()
    grain_specs: tuple[GrainSpec, ...] = ()
    measure_specs: tuple[MeasureSpec, ...] = ()
    domain_assertion_refs: tuple[str, ...] = ()
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def fixed_dialect(self) -> "CompiledPlan":
        if self.dialect != "duckdb":
            raise ValueError("V1 compilation dialect must be DuckDB")
        names = [item.operation_name for item in self.operations]
        if names != ["create_schema", "load_date", "load_dimensions", "load_facts"]:
            raise ValueError("compiled operations must use the deterministic V1 order")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"created_at"}))


class QuarantineRecord(_SourceModel):
    fact_id: str = Field(min_length=1)
    fact_table_name: str = Field(min_length=1)
    row_ref: str = Field(min_length=1)
    source_record_refs: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)
    missing_dimension_id: str | None = None
    missing_reference: str | None = None
    policy: UnknownMemberPolicy
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class GeneratedSQL(_SourceModel):
    generated_sql_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_content_hash: str = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    dialect: str = "duckdb"
    create_schema_sql: str = Field(min_length=1)
    load_date_sql: str = Field(min_length=1)
    load_dimensions_sql: str = Field(min_length=1)
    load_facts_sql: str = Field(min_length=1)
    statement_counts: Mapping[str, int] = Field(default_factory=dict)
    quarantine_records: tuple["QuarantineRecord", ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @property
    def sql_hash(self) -> str:
        return stable_digest({
            "dialect": self.dialect,
            "compiler_version": self.compiler_version,
            "create_schema_sql": self.create_schema_sql,
            "load_date_sql": self.load_date_sql,
            "load_dimensions_sql": self.load_dimensions_sql,
            "load_facts_sql": self.load_facts_sql,
        })

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class MaterializationArtifact(_SourceModel):
    artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_content_hash: str = Field(min_length=1)
    compiled_plan_id: str = Field(min_length=1)
    compiled_plan_content_hash: str = Field(min_length=1)
    target_type: str
    target_relative_path: str = Field(min_length=1)
    target_config_fingerprint: str = Field(min_length=1)
    generated_sql_hash: str = Field(min_length=1)
    table_names: tuple[str, ...] = Field(min_length=1)
    row_counts: Mapping[str, int] = Field(default_factory=dict)
    status: MaterializationStatus
    usable: bool
    attempted_at: datetime
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    target_file_sha256: str | None = None
    failure_reason: str | None = None
    quarantined_record_count: int = Field(default=0, ge=0)
    quarantine_records: tuple[QuarantineRecord, ...] = ()

    @field_validator("target_relative_path")
    @classmethod
    def artifact_target_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value.lower().endswith(".duckdb"):
            raise ValueError("materialization artifact target must be a relative .duckdb path")
        return str(path)

    @field_validator("table_names")
    @classmethod
    def table_names_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_identifier(item) for item in value)

    @model_validator(mode="after")
    def status_matches_usability(self) -> "MaterializationArtifact":
        if self.status is MaterializationStatus.SUCCEEDED and not self.usable:
            raise ValueError("successful materialization must be usable")
        if self.status is not MaterializationStatus.SUCCEEDED and self.usable:
            raise ValueError("failed materialization cannot be usable")
        if self.status is not MaterializationStatus.SUCCEEDED and not self.failure_reason:
            raise ValueError("failed materialization requires a failure reason")
        if self.quarantined_record_count != len(self.quarantine_records):
            raise ValueError("quarantine count must match quarantine records")
        return self

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json", exclude={"attempted_at"}))


def analytical_plan_id(payload: Mapping[str, object]) -> str:
    return stable_id("aplan", payload)


def compiled_plan_id(payload: Mapping[str, object]) -> str:
    return stable_id("cplan", payload)


def materialization_artifact_id(payload: Mapping[str, object]) -> str:
    return stable_id("mat", payload)


def as_analytical_dataset(value: object) -> AnalyticalInputDataset:
    """Normalize generic input and the historical fixture compatibility path."""

    if isinstance(value, AnalyticalInputDataset):
        return value
    converter = getattr(value, "to_analytical_dataset", None)
    if callable(converter):
        dataset = converter()
        if isinstance(dataset, AnalyticalInputDataset):
            return dataset
    raise TypeError("analytical runtime requires an AnalyticalInputDataset")


def deterministic_warehouse_key(namespace: str, canonical_reference: str) -> int:
    """Return an order-independent positive BIGINT derived from a namespaced ref."""

    digest = stable_digest({"namespace": namespace, "canonical_reference": canonical_reference})
    value = int(digest[:16], 16) & 0x7FFFFFFFFFFFFFFF
    return value or 1
