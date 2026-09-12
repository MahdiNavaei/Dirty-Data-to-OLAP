"""Run the deterministic, review-gated Step20 OLAP reference flow."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
from dirty_data_to_olap.application.compiler import AnalyticalCompilerService
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalInputBinding,
    AnalyticalInputFixture,
    AnalyticalPlanningRequest,
    BranchFixtureRow,
    DimensionAttributeSpec,
    DimensionRole,
    DimensionSpec,
    FactForeignKeySpec,
    FactRelationshipScope,
    FactSpec,
    FactType,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    CustomerFixtureRow,
    OrderFixtureRow,
    OrderLineFixtureRow,
    ProductFixtureRow,
    SCDMode,
    SCDPolicySpec,
    TargetConfig,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
    WarehouseKeySpec,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityInstance,
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalModel,
    CanonicalRelationship,
    CanonicalSourceTable,
    canonical_entity_id,
)
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id


RUN = ROOT / "workspace" / "runs" / "step20-reference-run" / "olap"
STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)
RUN_ID = "step20-reference-run"


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def jdump(value: object) -> object:
    return value.model_dump(mode="json")


def build_canonical_model() -> CanonicalModel:
    entities = (
        CanonicalEntityType(canonical_entity_type_id="cet_customer", semantic_id="customer", business_name="Customer", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="customer", identity_strategy="domain-reviewed-benchmark-membership", source_table_refs=(CanonicalSourceTable(source_id="crm", snapshot_id="snapshot-step20", table_id="customers", schema_fingerprint="schema-crm-step20"),), relationship_refs=("rel_order_customer",), review_state="FINALIZED", provenance_refs=("benchmark:domain-reviewed",)),
        CanonicalEntityType(canonical_entity_type_id="cet_product", semantic_id="product", business_name="Product", kind=CanonicalEntityKind.IDENTITY, identity_strategy="domain-reviewed-benchmark-membership", source_table_refs=(CanonicalSourceTable(source_id="erp", snapshot_id="snapshot-step20", table_id="products", schema_fingerprint="schema-erp-step20"),), relationship_refs=("rel_orderline_product",), review_state="FINALIZED", provenance_refs=("benchmark:domain-reviewed",)),
        CanonicalEntityType(canonical_entity_type_id="cet_branch", semantic_id="branch", business_name="Branch", kind=CanonicalEntityKind.IDENTITY, identity_strategy="domain-reviewed-benchmark-membership", source_table_refs=(CanonicalSourceTable(source_id="erp", snapshot_id="snapshot-step20", table_id="branches", schema_fingerprint="schema-erp-step20"),), relationship_refs=("rel_order_branch",), review_state="FINALIZED", provenance_refs=("benchmark:domain-reviewed",)),
        CanonicalEntityType(canonical_entity_type_id="cet_order", semantic_id="order", business_name="Order", kind=CanonicalEntityKind.EVENT, identity_strategy="source-local-event-identity", source_table_refs=(CanonicalSourceTable(source_id="sales", snapshot_id="snapshot-step20", table_id="orders", schema_fingerprint="schema-sales-step20"),), relationship_refs=("rel_order_customer", "rel_order_branch"), review_state="FINALIZED", provenance_refs=("benchmark:domain-reviewed",)),
        CanonicalEntityType(canonical_entity_type_id="cet_order_line", semantic_id="orderline", business_name="OrderLine", kind=CanonicalEntityKind.EVENT, identity_strategy="order-event-plus-line-sequence", source_table_refs=(CanonicalSourceTable(source_id="sales", snapshot_id="snapshot-step20", table_id="order_items", schema_fingerprint="schema-sales-step20"),), relationship_refs=("rel_orderline_order", "rel_orderline_product"), review_state="FINALIZED", provenance_refs=("benchmark:domain-reviewed",)),
        CanonicalEntityType(canonical_entity_type_id="cet_payment", semantic_id="payment", business_name="Payment", kind=CanonicalEntityKind.EVENT, identity_strategy="source-local-event-identity", source_table_refs=(CanonicalSourceTable(source_id="sales", snapshot_id="snapshot-step20", table_id="payments", schema_fingerprint="schema-sales-step20"),), relationship_refs=("rel_payment_order",), review_state="FINALIZED", provenance_refs=("benchmark:domain-reviewed",)),
    )
    relation_specs = (
        ("rel_order_customer", "cet_order", "cet_customer", "MANY_TO_ONE"),
        ("rel_orderline_order", "cet_order_line", "cet_order", "MANY_TO_ONE"),
        ("rel_orderline_product", "cet_order_line", "cet_product", "MANY_TO_ONE"),
        ("rel_order_branch", "cet_order", "cet_branch", "MANY_TO_ONE"),
        ("rel_payment_order", "cet_payment", "cet_order", "MANY_TO_ONE"),
    )
    relationships = tuple(CanonicalRelationship(relationship_id=rid, from_entity_type_id=left, to_entity_type_id=right, cardinality=cardinality, upstream_decision_ref="domain-reviewed-relationship-labels", review_decision_ref="domain-reviewed-step20", provenance_refs=("benchmarks/labels/domain-reviewed/relationships.yml", "benchmarks/labels/domain-reviewed/business_rules.yml")) for rid, left, right, cardinality in relation_specs)
    model_payload = {"version": "canonical-v1", "entities": [item.model_dump(mode="json") for item in entities], "relationships": [item.model_dump(mode="json") for item in relationships], "scope": "step20-typed-reference"}
    model_id = stable_id("cmodel", model_payload)
    return CanonicalModel(
        model_id=model_id,
        model_version="canonical-v1",
        hypothesis_artifact_id="step19-reference-hypothesis",
        finalized_at=STAMP,
        review_decision_refs=("domain-reviewed-step20",),
        entity_types=entities,
        relationships=relationships,
        lineage_refs=("benchmarks/labels/domain-reviewed/entities.yml", "benchmarks/labels/domain-reviewed/relationships.yml", "benchmarks/labels/domain-reviewed/business_rules.yml"),
        record_accounting_refs=("step19-reference-accounting",),
        provenance_refs=("step19-finalized-canonical-boundary", "step20-typed-reference-fixture"),
    )


def build_fixture(model: CanonicalModel) -> AnalyticalInputFixture:
    c1 = canonical_entity_id("cet_customer", ("crm-customer-1",), "canonical-v1")
    c2 = canonical_entity_id("cet_customer", ("crm-customer-2",), "canonical-v1")
    p1 = canonical_entity_id("cet_product", ("erp-product-1",), "canonical-v1")
    p2 = canonical_entity_id("cet_product", ("erp-product-2",), "canonical-v1")
    b1 = canonical_entity_id("cet_branch", ("erp-branch-1",), "canonical-v1")
    b2 = canonical_entity_id("cet_branch", ("erp-branch-2",), "canonical-v1")
    o1 = canonical_entity_id("cet_order", ("sales-order-1",), "canonical-v1")
    o2 = canonical_entity_id("cet_order", ("sales-order-2",), "canonical-v1")
    l11 = canonical_entity_id("cet_order_line", ("sales-order-line-1-1",), "canonical-v1")
    l12 = canonical_entity_id("cet_order_line", ("sales-order-line-1-2",), "canonical-v1")
    l21 = canonical_entity_id("cet_order_line", ("sales-order-line-2-1",), "canonical-v1")
    return AnalyticalInputFixture(
        fixture_id="step20-typed-reference-fixture-v1",
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        customers=(
            CustomerFixtureRow(canonical_entity_id=c1, customer_code="C-001", display_name="Alice", source_record_refs=("crm-customer-1",)),
            CustomerFixtureRow(canonical_entity_id=c2, customer_code="C-002", display_name="Bardia", source_record_refs=("crm-customer-2",)),
        ),
        products=(
            ProductFixtureRow(canonical_entity_id=p1, product_code="P-001", product_name="Notebook", category="Stationery", source_record_refs=("erp-product-1",)),
            ProductFixtureRow(canonical_entity_id=p2, product_code="P-002", product_name="Pen", category="Stationery", source_record_refs=("erp-product-2",)),
        ),
        branches=(
            BranchFixtureRow(canonical_entity_id=b1, branch_code="B-001", branch_name="Central", source_record_refs=("erp-branch-1",)),
            BranchFixtureRow(canonical_entity_id=b2, branch_code="B-002", branch_name="North", source_record_refs=("erp-branch-2",)),
        ),
        orders=(
            OrderFixtureRow(canonical_entity_id=o1, order_event_id="order-event-1", customer_entity_id=c1, branch_entity_id=b1, order_date=date(2026, 1, 2), source_record_refs=("sales-order-1",)),
            OrderFixtureRow(canonical_entity_id=o2, order_event_id="order-event-2", customer_entity_id=c2, branch_entity_id=b2, order_date=date(2026, 1, 3), source_record_refs=("sales-order-2",)),
        ),
        order_lines=(
            OrderLineFixtureRow(canonical_entity_id=l11, order_event_id="order-event-1", line_sequence=1, product_entity_id=p1, quantity=Decimal("2"), unit_price=Decimal("10.00"), discount_rate=Decimal("0.10"), source_record_refs=("sales-order-line-1-1",)),
            OrderLineFixtureRow(canonical_entity_id=l12, order_event_id="order-event-1", line_sequence=2, product_entity_id=p2, quantity=Decimal("1"), unit_price=Decimal("20.00"), discount_rate=Decimal("0.05"), source_record_refs=("sales-order-line-1-2",)),
            OrderLineFixtureRow(canonical_entity_id=l21, order_event_id="order-event-2", line_sequence=1, product_entity_id=p1, quantity=Decimal("3"), unit_price=Decimal("12.00"), discount_rate=Decimal("0"), source_record_refs=("sales-order-line-2-1",)),
        ),
        provenance_refs=("benchmarks/labels/domain-reviewed", "synthetic:typed-fixture", "no-raw-source-values"),
    )


def build_reference_plan(model: CanonicalModel, binding: AnalyticalInputBinding, fixture: AnalyticalInputFixture, *, created_at: datetime | None = None):
    """Build the domain-reviewed retail request outside the generic app layer."""

    def entity_type(semantic_id: str) -> str:
        return next(item.canonical_entity_type_id for item in model.entity_types if item.semantic_id == semantic_id)

    customer_type = entity_type("customer")
    product_type = entity_type("product")
    branch_type = entity_type("branch")
    order_type = entity_type("order")
    order_line_type = entity_type("orderline")
    payment_type = entity_type("payment")
    provenance = ("benchmark:domain-reviewed", "step20:typed-fixture", "canonical:finalized-model")
    type1 = SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="V1 rebuilds a current snapshot; no history is fabricated")
    unknown = UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unresolved dimension references are quarantined rather than silently assigned")

    def attribute(attribute_id: str, logical_type: str, *, nullable: bool = False, derivation: str | None = None) -> DimensionAttributeSpec:
        return DimensionAttributeSpec(attribute_id=attribute_id, column_name=attribute_id, logical_type=logical_type, nullable=nullable, derivation=derivation, lineage_refs=provenance)

    dimensions = (
        DimensionSpec(dimension_id="dim_customer", table_name="dim_customer", input_table_id="customers", canonical_entity_type_id=customer_type, canonical_entity_refs=(customer_type,), role=DimensionRole.CONFORMED, eligibility_reason="descriptive customer entity referenced by reviewed order events", surrogate_key=WarehouseKeySpec(key_name="customer_key", namespace="dim_customer"), alternate_key_columns=("customer_code",), attributes=(attribute("customer_code", "STRING"), attribute("display_name", "STRING"), attribute("source_record_refs", "STRING_LIST")), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dim_customer", provenance_refs=provenance),
        DimensionSpec(dimension_id="dim_product", table_name="dim_product", input_table_id="products", canonical_entity_type_id=product_type, canonical_entity_refs=(product_type,), role=DimensionRole.CONFORMED, eligibility_reason="descriptive product entity referenced by reviewed order lines", surrogate_key=WarehouseKeySpec(key_name="product_key", namespace="dim_product"), alternate_key_columns=("product_code",), attributes=(attribute("product_code", "STRING"), attribute("product_name", "STRING"), attribute("category", "STRING"), attribute("source_record_refs", "STRING_LIST")), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dim_product", provenance_refs=provenance),
        DimensionSpec(dimension_id="dim_branch", table_name="dim_branch", input_table_id="branches", canonical_entity_type_id=branch_type, canonical_entity_refs=(branch_type,), role=DimensionRole.CONFORMED, eligibility_reason="descriptive branch entity referenced by reviewed order events", surrogate_key=WarehouseKeySpec(key_name="branch_key", namespace="dim_branch"), alternate_key_columns=("branch_code",), attributes=(attribute("branch_code", "STRING"), attribute("branch_name", "STRING"), attribute("source_record_refs", "STRING_LIST")), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dim_branch", provenance_refs=provenance),
        DimensionSpec(dimension_id="dim_date", table_name="dim_date", canonical_entity_type_id="synthetic_date_dimension", canonical_entity_refs=("order_date",), role=DimensionRole.DATE, eligibility_reason="bounded deterministic Gregorian date role for the reviewed order event", surrogate_key=WarehouseKeySpec(key_name="date_key", namespace="dim_date"), alternate_key_columns=("full_date",), attributes=(attribute("full_date", "DATE", derivation="FULL_DATE"), attribute("year", "INTEGER", derivation="YEAR"), attribute("quarter", "INTEGER", derivation="QUARTER"), attribute("month", "INTEGER", derivation="MONTH"), attribute("day", "INTEGER", derivation="DAY"), attribute("weekday", "INTEGER", derivation="WEEKDAY")), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dim_date", provenance_refs=provenance),
    )
    fact_id = "fact_order_line"
    grain = GrainSpec(grain_id="grain_order_line_event_v1", fact_id=fact_id, human_readable_grain="one product line in one order event, identified by (order_event_id, line_sequence)", key_columns=("order_event_id", "line_sequence"), null_policy=GrainNullPolicy.REJECT_NULLS, observed_row_count=0, duplicate_key_count=0, evidence_refs=("domain-reviewed:orderline-grain", "fixture:unique-composite-grain"), provenance_refs=provenance)
    measures = (
        MeasureSpec(measure_id="measure_quantity", fact_id=fact_id, field_name="quantity", semantic_name="units ordered", aggregation_class=AggregationClass.ADDITIVE, aggregation_rule="SUM(quantity) at validated OrderLine grain", unit_semantics="counted product units", currency_semantics="NOT_APPLICABLE", logical_type="DECIMAL", nullable=False, domain_assertion_refs=("domain-reviewed:orderline-quantity-additive",), provenance_refs=provenance),
        MeasureSpec(measure_id="measure_unit_price", fact_id=fact_id, field_name="unit_price", semantic_name="unit price", aggregation_class=AggregationClass.NON_ADDITIVE, aggregation_rule="preserve at line grain; never SUM across lines", unit_semantics="price per product unit; currency not established", currency_semantics="UNSPECIFIED_NOT_REVENUE", logical_type="DECIMAL", domain_assertion_refs=("domain-reviewed:unit-price-non-additive",), provenance_refs=provenance),
        MeasureSpec(measure_id="measure_discount_rate", fact_id=fact_id, field_name="discount_rate", semantic_name="discount rate", aggregation_class=AggregationClass.NON_ADDITIVE, aggregation_rule="preserve at line grain; no additive aggregation", unit_semantics="rate", currency_semantics="NOT_APPLICABLE", logical_type="DECIMAL", domain_assertion_refs=("domain-reviewed:discount-rate-non-additive",), provenance_refs=provenance),
    )
    fact = FactSpec(
        fact_id=fact_id,
        table_name=fact_id,
        input_table_id="order_lines",
        fact_type=FactType.TRANSACTION,
        canonical_event_type_id=order_line_type,
        canonical_event_refs=(order_line_type, order_type),
        grain_spec_id=grain.grain_id,
        dimension_foreign_keys=(
            FactForeignKeySpec(relationship_ref="rel_order_customer", dimension_id="dim_customer", fact_column="customer_key", dimension_key_column="customer_key", canonical_entity_type_id=customer_type, input_reference_column="customer_entity_id"),
            FactForeignKeySpec(relationship_ref="rel_orderline_product", dimension_id="dim_product", fact_column="product_key", dimension_key_column="product_key", canonical_entity_type_id=product_type, input_reference_column="product_entity_id"),
            FactForeignKeySpec(relationship_ref="rel_order_branch", dimension_id="dim_branch", fact_column="branch_key", dimension_key_column="branch_key", canonical_entity_type_id=branch_type, input_reference_column="branch_entity_id"),
            FactForeignKeySpec(relationship_ref="analytical_order_date", relationship_scope=FactRelationshipScope.ANALYTICAL_TIME_ROLE, dimension_id="dim_date", fact_column="date_key", dimension_key_column="date_key", canonical_entity_type_id="synthetic_date_dimension", input_reference_column="order_date"),
        ),
        degenerate_dimension_columns=("order_event_id",),
        measure_ids=tuple(item.measure_id for item in measures),
        date_role_columns=("order_date",),
        relationship_refs=("rel_order_customer", "rel_orderline_product", "rel_order_branch", "analytical_order_date"),
        provenance_refs=provenance,
    )
    request = AnalyticalPlanningRequest(
        request_id="step20-retail-domain-reviewed-request-v2",
        dimensions=dimensions,
        facts=(fact,),
        grains=(grain,),
        measures=measures,
        accepted_relationship_refs=("rel_order_customer", "rel_orderline_order", "rel_orderline_product", "rel_order_branch", "rel_payment_order"),
        deferred_concept_refs=(payment_type,),
        deferred_concept_reasons={payment_type: "payment remains a candidate/review-required concept; no automatic fact is materialized in Step20"},
        domain_assertion_refs=("domain-reviewed:customer-order-product-branch", "domain-reviewed:orderline-quantity-additive", "domain-reviewed:measure-semantics"),
        provenance_refs=provenance,
    )
    from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
    plan, dimensions, facts, grains, measures = AnalyticalPlannerService().build_plan(model, binding, fixture.to_analytical_dataset(), request, created_at=created_at or STAMP)
    return plan, dimensions, facts[0], grains[0], measures


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    model = build_canonical_model()
    fixture = build_fixture(model)
    binding = AnalyticalInputBinding(
        binding_id=stable_id("abind", {"fixture_id": fixture.fixture_id, "fixture_hash": fixture.content_hash, "model": model.content_hash}),
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        fixture_id=fixture.fixture_id,
        fixture_content_hash=fixture.content_hash,
        source_schema_fingerprints={"crm": "schema-crm-step20", "erp": "schema-erp-step20", "sales": "schema-sales-step20"},
        row_counts=fixture.row_counts,
        provenance_refs=("typed-fixture-binding", "canonical-model-content-hash"),
    )
    plan, dimensions, fact, grain, measures = build_reference_plan(model, binding, fixture, created_at=STAMP)
    policy = ReviewPolicyService()
    analytical_context = policy.analytical_plan_context(plan)
    analytical_review = policy.create_decision(analytical_context, decision=ReviewDecisionStatus.ACCEPTED, actor="step20-domain-review", actor_source="DOMAIN_REVIEWED_BENCHMARK", rationale="Customer, Product, Branch and Date dimensions; OrderLine fact grain and measure classes are explicitly reviewed.", reviewed_at=STAMP)
    target = TargetConfig(relative_path="target.duckdb")
    compiled, generated_sql = AnalyticalCompilerService().compile(plan, dimensions, fact, grain, measures, binding, fixture, target, analytical_review, reviewed_at=STAMP)
    material_context = policy.materialization_context(compiled, generated_sql, target)
    material_review = policy.create_decision(material_context, decision=ReviewDecisionStatus.ACCEPTED, actor="step20-materialization-review", actor_source="CONTROLLED_TARGET_REVIEW", rationale="Exact compiled DuckDB SQL and controlled reference target are approved for the synthetic Step20 run.", reviewed_at=STAMP)
    materializer = DuckDBMaterializer(RUN, repository_root=ROOT)
    artifact = MaterializationService(materializer).materialize(compiled, generated_sql, material_review, binding, fixture, target, run_id=RUN_ID)
    if not artifact.usable:
        raise SystemExit("Step20 reference materialization failed: " + (artifact.failure_reason or "unknown"))

    target_path = ROOT / artifact.target_relative_path
    connection = duckdb.connect(str(target_path), read_only=True)
    try:
        quantity_total = connection.execute("SELECT SUM(quantity) FROM fact_order_line").fetchone()[0]
        unresolved_fk_count = connection.execute("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_customer d ON f.customer_key=d.customer_key LEFT JOIN dim_product p ON f.product_key=p.product_key LEFT JOIN dim_branch b ON f.branch_key=b.branch_key LEFT JOIN dim_date dt ON f.date_key=dt.date_key WHERE d.customer_key IS NULL OR p.product_key IS NULL OR b.branch_key IS NULL OR dt.date_key IS NULL").fetchone()[0]
        duplicate_grain_count = connection.execute("SELECT COUNT(*) - COUNT(DISTINCT order_event_id || ':' || CAST(line_sequence AS VARCHAR)) FROM fact_order_line").fetchone()[0]
        inspection = {"quantity_sum": str(quantity_total), "unresolved_fact_foreign_keys": int(unresolved_fk_count), "duplicate_fact_grain": int(duplicate_grain_count), "table_names": [row[0] for row in connection.execute("SHOW TABLES").fetchall()]}
    finally:
        connection.close()
    if inspection["quantity_sum"] not in {"6", "6.0000", "6.000000000"} or inspection["unresolved_fact_foreign_keys"] != 0 or inspection["duplicate_fact_grain"] != 0:
        raise SystemExit("Step20 target inspection failed: " + json.dumps(inspection, sort_keys=True))

    domain_refs = {}
    for path in sorted((ROOT / "benchmarks" / "labels" / "domain-reviewed").glob("*.yml")):
        domain_refs[path.name] = {"relative_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    dump(RUN / "canonical_model_ref.json", {"model_id": model.model_id, "content_hash": model.content_hash, "model_version": model.model_version, "unresolved_items": list(model.unresolved_items), "source_record_maps_preserved": True})
    dump(RUN / "lineage_refs.json", {"canonical_model": model.lineage_refs, "fixture_provenance": fixture.provenance_refs, "source_record_refs": sorted({ref for row in (*fixture.customers, *fixture.products, *fixture.branches, *fixture.orders, *fixture.order_lines) for ref in row.source_record_refs})})
    dump(RUN / "domain_reviewed_benchmark_refs.json", domain_refs)
    dump(RUN / "analytical_plan.json", jdump(plan))
    dump(RUN / "analytical_model.yml", {"authoritative_source": "analytical_plan.json plus typed specs", "plan_id": plan.plan_id, "fact_tables": [fact.table_name], "dimension_tables": [item.table_name for item in dimensions], "semantic_layer": "NOT_IMPLEMENTED_IN_STEP20"})
    dump(RUN / "fact_specs.json", [jdump(fact)])
    dump(RUN / "dimension_specs.json", [jdump(item) for item in dimensions])
    dump(RUN / "grain_specs.json", [jdump(grain)])
    dump(RUN / "measure_specs.json", [jdump(item) for item in measures])
    dump(RUN / "input_binding.json", jdump(binding))
    dump(RUN / "review_analytical_plan.json", jdump(analytical_review))
    dump(RUN / "compiled_plan.json", jdump(compiled))
    dump(RUN / "generated_sql_manifest.json", {"generated_sql_id": generated_sql.generated_sql_id, "plan_id": generated_sql.plan_id, "plan_content_hash": generated_sql.plan_content_hash, "compiler_version": generated_sql.compiler_version, "dialect": generated_sql.dialect, "sql_hash": generated_sql.sql_hash, "statement_counts": generated_sql.statement_counts})
    (RUN / "create_schema.sql").write_text(generated_sql.create_schema_sql, encoding="utf-8")
    (RUN / "load_dimensions.sql").write_text(generated_sql.load_dimensions_sql, encoding="utf-8")
    (RUN / "load_facts.sql").write_text(generated_sql.load_facts_sql, encoding="utf-8")
    (RUN / "load_date.sql").write_text(generated_sql.load_date_sql, encoding="utf-8")
    dump(RUN / "review_materialization_plan.json", jdump(material_review))
    dump(RUN / "materialization_artifact.json", jdump(artifact))
    dump(RUN / "validation_tests.yml", {"authoritative_source": "step20_reference_runner", "checks": {"dimension_key_uniqueness": True, "fact_grain_uniqueness": True, "foreign_keys_resolve": True, "quantity_sum": inspection["quantity_sum"], "non_additive_measures": ["unit_price", "discount_rate"], "scd_mode": "TYPE1_SNAPSHOT", "date_strategy": "bounded_gregorian"}})
    dump(RUN / "target_inspection.json", inspection)
    dump(RUN / "run_manifest.json", {"run_id": RUN_ID, "flow": ["CANONICAL_FINALIZATION_REF", "ANALYTICAL_PLANNING", "REVIEW_ANALYTICAL_PLAN", "COMPILATION", "REVIEW_MATERIALIZATION_PLAN", "MATERIALIZATION", "VALIDATION_RECONCILIATION_REF"], "canonical_model_id": model.model_id, "canonical_model_content_hash": model.content_hash, "analytical_plan_id": plan.plan_id, "analytical_plan_content_hash": plan.content_hash, "compiled_plan_id": compiled.compiled_plan_id, "compiled_plan_content_hash": compiled.content_hash, "generated_sql_hash": generated_sql.sql_hash, "materialization_artifact_id": artifact.artifact_id, "materialization_artifact_content_hash": artifact.content_hash, "target_relative_path": artifact.target_relative_path, "target_inspection": inspection, "step21_semantic_layer": "NOT_IMPLEMENTED", "g6_data_correctness": "PENDING_STEP22", "g5_inference_validity": "PASS"})
    print(json.dumps({"run": str(RUN), "plan_id": plan.plan_id, "plan_hash": plan.content_hash, "compiled_plan_id": compiled.compiled_plan_id, "sql_hash": generated_sql.sql_hash, "materialization_artifact_id": artifact.artifact_id, "tables": inspection["table_names"], "quantity_sum": inspection["quantity_sum"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
