"""Deterministic compiler for an accepted analytical plan."""

from __future__ import annotations

import json
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalInputFixture,
    AnalyticalPlan,
    AnalyticalReviewState,
    CompiledOperation,
    CompiledPlan,
    DimensionSpec,
    FactSpec,
    GeneratedSQL,
    GrainSpec,
    MeasureSpec,
    TargetConfig,
    compiled_plan_id,
    deterministic_warehouse_key,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id


class AnalyticalCompilationError(ValueError):
    """The approved analytical plan cannot be translated safely."""


def _identifier(value: str) -> str:
    # Contract models have already validated identifiers.  Keeping this guard
    # beside SQL generation prevents future callers from bypassing that rule.
    if not value or not value[0].islower() or any(not (char.islower() or char.isdigit() or char == "_") for char in value):
        raise AnalyticalCompilationError(f"unsafe SQL identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, date):
        return "DATE " + _literal(value.isoformat())
    if isinstance(value, (tuple, list)):
        return _literal(json.dumps(list(value), ensure_ascii=False, separators=(",", ":")))
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


class AnalyticalCompilerService:
    COMPILER_VERSION = "duckdb-compiler-v1"

    def compile(
        self,
        plan: AnalyticalPlan,
        dimensions: tuple[DimensionSpec, ...],
        fact: FactSpec,
        grain: GrainSpec,
        measures: tuple[MeasureSpec, ...],
        binding: AnalyticalInputBinding,
        fixture: AnalyticalInputFixture,
        target_config: TargetConfig,
        analytical_review,
        *,
        reviewed_at: datetime | None = None,
    ) -> tuple[CompiledPlan, GeneratedSQL]:
        if plan.review_state is not AnalyticalReviewState.REVIEW_REQUIRED:
            raise AnalyticalCompilationError("compiler accepts only a review-required plan with a separate compatible review")
        try:
            ReviewPolicyService().require_compatible(analytical_review, ReviewPolicyService().analytical_plan_context(plan))
        except ReviewCompatibilityError as exc:
            raise AnalyticalCompilationError("REVIEW_ANALYTICAL_PLAN_INCOMPATIBLE:" + ",".join(exc.errors)) from exc
        if plan.unresolved_items:
            raise AnalyticalCompilationError("UNRESOLVED_ANALYTICAL_PLAN:" + ",".join(plan.unresolved_items))
        if binding.binding_id != plan.input_binding_id or binding.content_hash != plan.input_binding_content_hash:
            raise AnalyticalCompilationError("input binding is stale")
        if fixture.fixture_id != binding.fixture_id or fixture.content_hash != binding.fixture_content_hash:
            raise AnalyticalCompilationError("typed input fixture is stale")
        if fixture.canonical_model_id != plan.canonical_model_id or fixture.canonical_model_content_hash != plan.canonical_model_content_hash:
            raise AnalyticalCompilationError("typed input fixture is not bound to the planned canonical model")
        if fact.fact_id not in plan.materialized_fact_ids or grain.grain_id != fact.grain_spec_id or not grain.validated:
            raise AnalyticalCompilationError("fact implementation requires the exact validated GrainSpec")
        if tuple(item.measure_id for item in measures) != tuple(plan.measure_spec_ids):
            raise AnalyticalCompilationError("measure specifications do not match the reviewed plan")
        if set(item.dimension_id for item in dimensions) != set(plan.materialized_dimension_ids):
            raise AnalyticalCompilationError("dimension specifications do not match the reviewed plan")

        dimension_map = {item.dimension_id: item for item in dimensions}
        required_dimensions = {"dim_customer", "dim_product", "dim_branch", "dim_date"}
        if set(dimension_map) != required_dimensions:
            raise AnalyticalCompilationError("reference compiler requires the explicit four-dimension V1 star")
        self._validate_references(fixture)
        self._validate_warehouse_key_collisions(fixture)
        sql = self._generate_sql(dimensions, fact, grain, measures, fixture)
        generated_id = stable_id("sql", {"plan_id": plan.plan_id, "plan_hash": plan.content_hash, "sql_hash": sql.sql_hash})
        sql = sql.model_copy(update={"generated_sql_id": generated_id, "plan_id": plan.plan_id, "plan_content_hash": plan.content_hash})
        operation_payload = {
            "plan": plan.plan_id,
            "plan_hash": plan.content_hash,
            "sql_hash": sql.sql_hash,
            "target": target_config.config_fingerprint,
            "compiler": self.COMPILER_VERSION,
        }
        operations = (
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "create_schema"}), operation_name="create_schema", statement_kind="DDL", sql_hash=stable_digest(sql.create_schema_sql)),
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "load_date"}), operation_name="load_date", dependencies=("create_schema",), statement_kind="DML", sql_hash=stable_digest(sql.load_date_sql)),
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "load_dimensions"}), operation_name="load_dimensions", dependencies=("create_schema", "load_date"), statement_kind="DML", sql_hash=stable_digest(sql.load_dimensions_sql)),
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "load_facts"}), operation_name="load_facts", dependencies=("create_schema", "load_dimensions"), statement_kind="DML", sql_hash=stable_digest(sql.load_facts_sql)),
        )
        compiled_payload = {
            **operation_payload,
            "generated_sql_id": sql.generated_sql_id,
            "operations": [item.model_dump(mode="json") for item in operations],
            "binding": binding.content_hash,
        }
        compiled = CompiledPlan(
            compiled_plan_id=compiled_plan_id(compiled_payload),
            plan_id=plan.plan_id,
            plan_content_hash=plan.content_hash,
            compiler_version=self.COMPILER_VERSION,
            operations=operations,
            generated_sql_id=sql.generated_sql_id,
            generated_sql_hash=sql.sql_hash,
            target_config_fingerprint=target_config.config_fingerprint,
            input_binding_id=binding.binding_id,
            input_binding_content_hash=binding.content_hash,
            canonical_model_id=plan.canonical_model_id,
            canonical_model_content_hash=plan.canonical_model_content_hash,
            domain_assertion_refs=tuple(sorted(plan.domain_assertion_refs)),
            provenance_refs=("compiler:project-owned-duckdb-v1", "review:analytical-plan", "lineage:typed-fixture"),
            created_at=reviewed_at or datetime.now(timezone.utc),
        )
        return compiled, sql

    @staticmethod
    def _validate_references(fixture: AnalyticalInputFixture) -> None:
        customer_ids = {row.canonical_entity_id for row in fixture.customers}
        product_ids = {row.canonical_entity_id for row in fixture.products}
        branch_ids = {row.canonical_entity_id for row in fixture.branches}
        order_ids = {row.order_event_id for row in fixture.orders}
        for row in fixture.orders:
            if row.customer_entity_id not in customer_ids:
                raise AnalyticalCompilationError("MISSING_DIMENSION_REFERENCE:customer")
            if row.branch_entity_id not in branch_ids:
                raise AnalyticalCompilationError("MISSING_DIMENSION_REFERENCE:branch")
        for row in fixture.order_lines:
            if row.order_event_id not in order_ids:
                raise AnalyticalCompilationError("MISSING_DIMENSION_REFERENCE:order")
            if row.product_entity_id not in product_ids:
                raise AnalyticalCompilationError("MISSING_DIMENSION_REFERENCE:product")

    @staticmethod
    def _validate_warehouse_key_collisions(fixture: AnalyticalInputFixture) -> None:
        namespaces = {
            "dim_customer": [row.canonical_entity_id for row in fixture.customers],
            "dim_product": [row.canonical_entity_id for row in fixture.products],
            "dim_branch": [row.canonical_entity_id for row in fixture.branches],
            "dim_date": sorted({row.order_date.isoformat() for row in fixture.orders}),
        }
        for namespace, references in namespaces.items():
            seen: dict[int, str] = {}
            for reference in references:
                key = deterministic_warehouse_key(namespace, reference)
                previous = seen.get(key)
                if previous is not None and previous != reference:
                    raise AnalyticalCompilationError(f"WAREHOUSE_KEY_COLLISION:{namespace}")
                seen[key] = reference

    def _generate_sql(
        self,
        dimensions: tuple[DimensionSpec, ...],
        fact: FactSpec,
        grain: GrainSpec,
        measures: tuple[MeasureSpec, ...],
        fixture: AnalyticalInputFixture,
    ) -> GeneratedSQL:
        if grain.key_columns != ("order_event_id", "line_sequence"):
            raise AnalyticalCompilationError("reference compiler cannot silently change the validated fact grain")
        measure_by_field = {item.field_name: item for item in measures}
        if measure_by_field.get("quantity") is None or measure_by_field["quantity"].aggregation_class.value != "ADDITIVE":
            raise AnalyticalCompilationError("quantity must be explicitly additive")
        if measure_by_field.get("unit_price") and measure_by_field["unit_price"].aggregation_class.value == "ADDITIVE":
            raise AnalyticalCompilationError("unit_price cannot be additive")
        if measure_by_field.get("discount_rate") and measure_by_field["discount_rate"].aggregation_class.value == "ADDITIVE":
            raise AnalyticalCompilationError("discount_rate cannot be additive")

        create_schema = """CREATE TABLE dim_customer (\n  customer_key BIGINT PRIMARY KEY,\n  canonical_entity_id VARCHAR NOT NULL UNIQUE,\n  customer_code VARCHAR NOT NULL UNIQUE,\n  display_name VARCHAR NOT NULL,\n  source_record_refs VARCHAR NOT NULL\n);\nCREATE TABLE dim_product (\n  product_key BIGINT PRIMARY KEY,\n  canonical_entity_id VARCHAR NOT NULL UNIQUE,\n  product_code VARCHAR NOT NULL UNIQUE,\n  product_name VARCHAR NOT NULL,\n  category VARCHAR NOT NULL,\n  source_record_refs VARCHAR NOT NULL\n);\nCREATE TABLE dim_branch (\n  branch_key BIGINT PRIMARY KEY,\n  canonical_entity_id VARCHAR NOT NULL UNIQUE,\n  branch_code VARCHAR NOT NULL UNIQUE,\n  branch_name VARCHAR NOT NULL,\n  source_record_refs VARCHAR NOT NULL\n);\nCREATE TABLE dim_date (\n  date_key BIGINT PRIMARY KEY,\n  full_date DATE NOT NULL UNIQUE,\n  year INTEGER NOT NULL,\n  quarter INTEGER NOT NULL,\n  month INTEGER NOT NULL,\n  day INTEGER NOT NULL,\n  weekday INTEGER NOT NULL\n);\nCREATE TABLE fact_order_line (\n  order_event_id VARCHAR NOT NULL,\n  line_sequence INTEGER NOT NULL,\n  canonical_order_line_id VARCHAR NOT NULL,\n  customer_key BIGINT NOT NULL REFERENCES dim_customer(customer_key),\n  product_key BIGINT NOT NULL REFERENCES dim_product(product_key),\n  branch_key BIGINT NOT NULL REFERENCES dim_branch(branch_key),\n  date_key BIGINT NOT NULL REFERENCES dim_date(date_key),\n  quantity DECIMAL(18, 4) NOT NULL,\n  unit_price DECIMAL(18, 4),\n  discount_rate DECIMAL(9, 6),\n  source_record_refs VARCHAR NOT NULL,\n  PRIMARY KEY (order_event_id, line_sequence)\n);\n"""
        dimension_sql = []
        for row in sorted(fixture.customers, key=lambda item: item.canonical_entity_id):
            dimension_sql.append("INSERT INTO dim_customer VALUES (" + ", ".join((_literal(deterministic_warehouse_key("dim_customer", row.canonical_entity_id)), _literal(row.canonical_entity_id), _literal(row.customer_code), _literal(row.display_name), _literal(row.source_record_refs))) + ");")
        for row in sorted(fixture.products, key=lambda item: item.canonical_entity_id):
            dimension_sql.append("INSERT INTO dim_product VALUES (" + ", ".join((_literal(deterministic_warehouse_key("dim_product", row.canonical_entity_id)), _literal(row.canonical_entity_id), _literal(row.product_code), _literal(row.product_name), _literal(row.category), _literal(row.source_record_refs))) + ");")
        for row in sorted(fixture.branches, key=lambda item: item.canonical_entity_id):
            dimension_sql.append("INSERT INTO dim_branch VALUES (" + ", ".join((_literal(deterministic_warehouse_key("dim_branch", row.canonical_entity_id)), _literal(row.canonical_entity_id), _literal(row.branch_code), _literal(row.branch_name), _literal(row.source_record_refs))) + ");")

        dates = sorted({row.order_date for row in fixture.orders})
        date_sql = []
        for current in dates:
            date_sql.append("INSERT INTO dim_date VALUES (" + ", ".join((_literal(deterministic_warehouse_key("dim_date", current.isoformat())), _literal(current), _literal(current.year), _literal((current.month - 1) // 3 + 1), _literal(current.month), _literal(current.day), _literal(current.isoweekday()))) + ");")

        order_by_id = {row.order_event_id: row for row in fixture.orders}
        fact_sql = []
        for row in sorted(fixture.order_lines, key=lambda item: (item.order_event_id, item.line_sequence)):
            order = order_by_id[row.order_event_id]
            values = (
                row.order_event_id,
                row.line_sequence,
                row.canonical_entity_id,
                deterministic_warehouse_key("dim_customer", order.customer_entity_id),
                deterministic_warehouse_key("dim_product", row.product_entity_id),
                deterministic_warehouse_key("dim_branch", order.branch_entity_id),
                deterministic_warehouse_key("dim_date", order.order_date.isoformat()),
                row.quantity,
                row.unit_price,
                row.discount_rate,
                row.source_record_refs,
            )
            fact_sql.append("INSERT INTO fact_order_line VALUES (" + ", ".join(_literal(item) for item in values) + ");")
        return GeneratedSQL(
            generated_sql_id="pending",
            plan_id="pending",
            plan_content_hash="pending",
            compiler_version=self.COMPILER_VERSION,
            create_schema_sql=create_schema,
            load_date_sql="\n".join(date_sql) + "\n",
            load_dimensions_sql="\n".join(dimension_sql) + "\n",
            load_facts_sql="\n".join(fact_sql) + "\n",
            statement_counts={"create_schema": create_schema.count("CREATE TABLE"), "load_date": len(date_sql), "load_dimensions": len(dimension_sql), "load_facts": len(fact_sql)},
            provenance_refs=("sql:deterministic-project-owned", "source:typed-fixture", "policy:no-revenue-inference"),
        )
