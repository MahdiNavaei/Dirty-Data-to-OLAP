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
    canonical_attribute_refs: tuple[str, ...] = ()
    nullable: bool = False
    lineage_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("column_name")
    @classmethod
    def column_is_safe(cls, value: str) -> str:
        return _identifier(value)


class DimensionSpec(_SourceModel):
    dimension_id: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
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
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    review_decision_ref: str | None = None

    @field_validator("table_name")
    @classmethod
    def table_is_safe(cls, value: str) -> str:
        return _identifier(value)

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
        return self


class FactForeignKeySpec(_SourceModel):
    relationship_ref: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    fact_column: str = Field(min_length=1)
    dimension_key_column: str = Field(min_length=1)
    canonical_entity_type_id: str = Field(min_length=1)
    required: bool = True

    @field_validator("fact_column", "dimension_key_column")
    @classmethod
    def fk_columns_are_safe(cls, value: str) -> str:
        return _identifier(value)


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


class MeasureSpec(_SourceModel):
    measure_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    aggregation_class: AggregationClass
    aggregation_rule: str = Field(min_length=1)
    unit_semantics: str = Field(min_length=1)
    currency_semantics: str = Field(min_length=1)
    nullable: bool = True
    review_state: AnalyticalReviewState = AnalyticalReviewState.REVIEW_REQUIRED
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("field_name")
    @classmethod
    def measure_field_is_safe(cls, value: str) -> str:
        return _identifier(value)


class FactSpec(_SourceModel):
    fact_id: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
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


class AnalyticalInputBinding(_SourceModel):
    binding_id: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    canonical_model_content_hash: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    fixture_content_hash: str = Field(min_length=1)
    source_schema_fingerprints: Mapping[str, str] = Field(default_factory=dict)
    row_counts: Mapping[str, int] = Field(default_factory=dict)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


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


def deterministic_warehouse_key(namespace: str, canonical_reference: str) -> int:
    """Return an order-independent positive BIGINT derived from a namespaced ref."""

    digest = stable_digest({"namespace": namespace, "canonical_reference": canonical_reference})
    value = int(digest[:16], 16) & 0x7FFFFFFFFFFFFFFF
    return value or 1
