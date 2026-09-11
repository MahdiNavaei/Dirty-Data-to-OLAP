"""Fail-closed validator for the Step20 analytical package and DuckDB target."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalPlan,
    AggregationClass,
    CompiledPlan,
    DimensionSpec,
    FactSpec,
    GeneratedSQL,
    GrainSpec,
    MaterializationArtifact,
    MaterializationStatus,
    MeasureSpec,
    TargetConfig,
)
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecision
from dirty_data_to_olap.domain.contracts.source import stable_digest


RUN = ROOT / "workspace" / "runs" / "step20-reference-run" / "olap"


class ValidationFailure(RuntimeError):
    pass


def load(name: str):
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def main() -> int:
    required = (
        "analytical_model.yml", "analytical_plan.json", "input_binding.json", "fact_specs.json", "dimension_specs.json",
        "grain_specs.json", "measure_specs.json", "review_analytical_plan.json", "compiled_plan.json", "generated_sql_manifest.json",
        "create_schema.sql", "load_date.sql", "load_dimensions.sql", "load_facts.sql", "review_materialization_plan.json",
        "materialization_artifact.json", "validation_tests.yml", "target_inspection.json", "canonical_model_ref.json", "lineage_refs.json", "run_manifest.json", "target.duckdb",
    )
    for name in required:
        check((RUN / name).is_file(), "missing Step20 artifact: " + name)
    plan = AnalyticalPlan.model_validate(load("analytical_plan.json"))
    binding = AnalyticalInputBinding.model_validate(load("input_binding.json"))
    dimensions = tuple(DimensionSpec.model_validate(item) for item in load("dimension_specs.json"))
    fact = FactSpec.model_validate(load("fact_specs.json")[0])
    grain = GrainSpec.model_validate(load("grain_specs.json")[0])
    measures = tuple(MeasureSpec.model_validate(item) for item in load("measure_specs.json"))
    analytical_review = ReviewDecision.model_validate(load("review_analytical_plan.json"))
    compiled = CompiledPlan.model_validate(load("compiled_plan.json"))
    generated_manifest = load("generated_sql_manifest.json")
    generated = GeneratedSQL(
        generated_sql_id=generated_manifest["generated_sql_id"],
        plan_id=generated_manifest["plan_id"],
        plan_content_hash=generated_manifest["plan_content_hash"],
        compiler_version=generated_manifest["compiler_version"],
        create_schema_sql=(RUN / "create_schema.sql").read_text(encoding="utf-8"),
        load_date_sql=(RUN / "load_date.sql").read_text(encoding="utf-8"),
        load_dimensions_sql=(RUN / "load_dimensions.sql").read_text(encoding="utf-8"),
        load_facts_sql=(RUN / "load_facts.sql").read_text(encoding="utf-8"),
        statement_counts=generated_manifest["statement_counts"],
        provenance_refs=("validator:step20",),
    )
    material_review = ReviewDecision.model_validate(load("review_materialization_plan.json"))
    artifact = MaterializationArtifact.model_validate(load("materialization_artifact.json"))
    manifest = load("run_manifest.json")
    target_config = TargetConfig(relative_path="target.duckdb")
    checks = 0
    check(plan.plan_id == manifest["analytical_plan_id"] and plan.content_hash == manifest["analytical_plan_content_hash"], "plan manifest binding mismatch"); checks += 1
    check(binding.canonical_model_id == plan.canonical_model_id and binding.canonical_model_content_hash == plan.canonical_model_content_hash, "binding/canonical model mismatch"); checks += 1
    check(analytical_review.is_compatible(ReviewPolicyService().analytical_plan_context(plan)), "analytical review is stale or incompatible"); checks += 1
    check(compiled.plan_id == plan.plan_id and compiled.plan_content_hash == plan.content_hash, "compiled plan is not bound to plan"); checks += 1
    check(generated.plan_id == plan.plan_id and generated.plan_content_hash == plan.content_hash and generated.sql_hash == generated_manifest["sql_hash"], "generated SQL manifest/hash mismatch"); checks += 1
    check(compiled.generated_sql_hash == generated.sql_hash and compiled.target_config_fingerprint == target_config.config_fingerprint, "compiled SQL or target binding mismatch"); checks += 1
    check(material_review.is_compatible(ReviewPolicyService().materialization_context(compiled, generated, target_config)), "materialization review is stale or incompatible"); checks += 1
    check(artifact.status is MaterializationStatus.SUCCEEDED and artifact.usable, "target artifact is not consumable"); checks += 1
    check(artifact.target_relative_path == "workspace/runs/step20-reference-run/olap/target.duckdb", "target is outside controlled reference path"); checks += 1
    check(set(item.table_name for item in dimensions) == {"dim_customer", "dim_product", "dim_branch", "dim_date"}, "dimension table set is incomplete"); checks += 1
    check(fact.table_name == "fact_order_line" and grain.validated and grain.key_columns == ("order_event_id", "line_sequence"), "fact grain contract is incomplete"); checks += 1
    classes = {item.field_name: item.aggregation_class for item in measures}
    check(classes.get("quantity") is AggregationClass.ADDITIVE, "quantity is not explicitly additive"); checks += 1
    check(classes.get("unit_price") is AggregationClass.NON_ADDITIVE and classes.get("discount_rate") is AggregationClass.NON_ADDITIVE, "non-additive measures were reclassified"); checks += 1
    check(not any("revenue" in item.semantic_name.casefold() or "revenue" in item.aggregation_rule.casefold() for item in measures), "revenue was inferred without a domain contract"); checks += 1
    check(fact.grain_spec_id == grain.grain_id and set(fact.measure_ids) == {item.measure_id for item in measures}, "fact references are incomplete"); checks += 1

    connection = duckdb.connect(str(RUN / "target.duckdb"), read_only=True)
    try:
        tables = tuple(sorted(row[0] for row in connection.execute("SHOW TABLES").fetchall()))
        check(tables == tuple(sorted(("dim_customer", "dim_product", "dim_branch", "dim_date", "fact_order_line"))), "DuckDB table set mismatch"); checks += 1
        counts = {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}
        check(counts == {"dim_branch": 2, "dim_customer": 2, "dim_date": 2, "dim_product": 2, "fact_order_line": 3}, "DuckDB row counts mismatch"); checks += 1
        check(connection.execute("SELECT SUM(quantity) FROM fact_order_line").fetchone()[0] == 6, "quantity reconciliation failed"); checks += 1
        check(connection.execute("SELECT COUNT(*) FROM (SELECT order_event_id, line_sequence FROM fact_order_line GROUP BY 1,2 HAVING COUNT(*) > 1)").fetchone()[0] == 0, "fact grain is not unique"); checks += 1
        check(connection.execute("SELECT COUNT(*) FROM (SELECT customer_key FROM dim_customer GROUP BY 1 HAVING COUNT(*) > 1)").fetchone()[0] == 0, "customer warehouse keys are not unique"); checks += 1
        check(connection.execute("SELECT COUNT(*) FROM (SELECT product_key FROM dim_product GROUP BY 1 HAVING COUNT(*) > 1)").fetchone()[0] == 0, "product warehouse keys are not unique"); checks += 1
        check(connection.execute("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_customer d ON f.customer_key=d.customer_key LEFT JOIN dim_product p ON f.product_key=p.product_key LEFT JOIN dim_branch b ON f.branch_key=b.branch_key LEFT JOIN dim_date dt ON f.date_key=dt.date_key WHERE d.customer_key IS NULL OR p.product_key IS NULL OR b.branch_key IS NULL OR dt.date_key IS NULL").fetchone()[0] == 0, "fact FK resolution failed"); checks += 1
    finally:
        connection.close()
    check(manifest["step21_semantic_layer"] == "NOT_IMPLEMENTED" and manifest["g6_data_correctness"] == "PENDING_STEP22", "Step20 claims a later step or gate"); checks += 1
    check(stable_digest(generated_manifest) == stable_digest(load("generated_sql_manifest.json")), "generated SQL manifest is not stable"); checks += 1
    print(f"PASS: step20_olap_checks={checks} plan={plan.plan_id} compiled={compiled.compiled_plan_id} artifact={artifact.artifact_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationFailure, KeyError, ValueError, duckdb.Error) as exc:
        print("FAIL: step20_olap_validator=" + str(exc))
        raise SystemExit(1)
