"""Behavioral, fail-closed validator for the Step21 semantic layer."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor, SemanticQueryExecutionError
from dirty_data_to_olap.application.semantic_layer import SemanticLayerError, SemanticLayerService
from dirty_data_to_olap.domain.contracts.analytical import MaterializationStatus
from dirty_data_to_olap.domain.contracts.semantic import (
    MetricExpression,
    MetricSpec,
    SemanticAvailability,
    SemanticExpressionType,
    SemanticMetricKind,
    SemanticQueryRequest,
    ZeroDenominatorBehavior,
)
from tools.run_step21_generic_reference import main as run_generic
from tools.run_step21_reference import main as run_retail
from tools.step21_reference_support import generic_context, retail_context


class ValidationFailure(RuntimeError):
    pass


def check(condition: bool, message: str, counter: list[int]) -> None:
    if not condition:
        raise ValidationFailure(message)
    counter[0] += 1


def expect_failure(callback, message: str, counter: list[int]) -> None:
    try:
        callback()
    except (SemanticLayerError, SemanticQueryExecutionError, ValueError) as exc:
        if message not in str(exc):
            raise ValidationFailure(f"expected {message!r}, got {exc!r}") from exc
        counter[0] += 1
        return
    raise ValidationFailure(f"expected failure containing {message!r}")


def load(path: Path, name: str) -> dict:
    return json.loads((path / name).read_text(encoding="utf-8"))


def build_model(context):
    return SemanticLayerService().build_model(
        context.plan,
        context.dimensions,
        context.facts,
        context.grains,
        context.measures,
        context.compiled_plan,
        context.materialization,
        context.canonical_model,
        analytical_review=context.analytical_review,
        unresolved_semantic_items=("metric:revenue:NOT_DEFINED_BY_REVIEWED_EVIDENCE",),
        additional_provenance_refs=("validator:step21",),
    )


def main() -> int:
    run_retail()
    run_generic()
    retail_run = ROOT / "workspace" / "runs" / "step21-reference-run" / "semantic"
    generic_run = ROOT / "workspace" / "runs" / "step21-generic-reference-run" / "semantic"
    retail_context_value = retail_context()
    generic_context_value = generic_context()
    retail_model = build_model(retail_context_value)
    generic_model = build_model(generic_context_value)
    service = SemanticLayerService()
    executor = DuckDBSemanticQueryExecutor(ROOT)
    checks = [0]

    retail_manifest = load(retail_run, "run_manifest.json")
    generic_manifest = load(generic_run, "run_manifest.json")
    check(retail_model.analytical_plan_id == retail_context_value.plan.plan_id and retail_model.analytical_plan_content_hash == retail_context_value.plan.content_hash and retail_model.analytical_spec_package_hash == retail_context_value.plan.analytical_spec_package_hash, "retail model is not bound to exact Step20 plan/package", checks)
    check(retail_model.compiled_plan_id == retail_context_value.compiled_plan.compiled_plan_id and retail_model.compiled_plan_content_hash == retail_context_value.compiled_plan.content_hash and retail_model.materialization_artifact_id == retail_context_value.materialization.artifact_id and retail_model.materialization_artifact_content_hash == retail_context_value.materialization.content_hash, "retail model is not bound to exact compiled/materialized artifacts", checks)
    check(retail_model.status.value == "READY" and generic_model.status.value == "READY", "reference semantic models are not internally READY", checks)
    check(retail_manifest["g6_data_correctness"] == "PENDING_STEP22" and generic_manifest["g6_data_correctness"] == "PENDING_STEP22" and not retail_manifest["step22_started"], "Step21 reference claimed Step22/G6", checks)
    check(any(item.display_name == "Units Ordered" and item.allowed_aggregation_operations == ("SUM",) for item in retail_model.metrics), "Units Ordered does not resolve to reviewed additive quantity", checks)
    check(all(item.display_name not in {"Unit Price", "Discount Rate"} or not item.allowed_aggregation_operations for item in retail_model.metrics), "non-additive retail measure exposed an executable aggregate", checks)
    check(not any("revenue" in item.display_name.casefold() for item in retail_model.metrics) and any("revenue" in item.casefold() for item in retail_model.unresolved_semantic_items), "revenue was promoted into semantic truth", checks)
    check(bool(retail_model.hierarchies) and [level.name for level in retail_model.hierarchies[0].levels] == ["Year", "Quarter", "Month", "Day"], "retail Date hierarchy is not structurally valid", checks)
    check(any(ref.startswith("analytical_plan:") for ref in retail_model.lineage_refs) and any(ref.startswith("canonical_model:") for ref in retail_model.lineage_refs), "semantic lineage does not reach analytical/canonical refs", checks)
    check(all(item.relationship_scope.value in {"CANONICAL_ACCEPTED", "ANALYTICAL_TIME_ROLE"} for item in retail_model.relationships), "relationship scope was not explicit", checks)
    check(any(item.display_name == "Observed Temperature" and item.allowed_aggregation_operations == ("MAX",) for item in generic_model.metrics), "generic semi-additive metric did not inherit MAX", checks)
    check(retail_model.canonical_model_id == retail_context_value.canonical_model.model_id and generic_model.canonical_model_id == generic_context_value.canonical_model.model_id, "generic/retail canonical binding is missing", checks)

    units = next(item for item in retail_model.metrics if item.display_name == "Units Ordered")
    overall = service.resolve_query(retail_model, SemanticQueryRequest(request_id="validator_overall", metric_ids=(units.metric_id,)), analytical_plan=retail_context_value.plan, compiled_plan=retail_context_value.compiled_plan, materialization=retail_context_value.materialization)
    result = executor.execute(retail_model, overall)
    check(result.rows[0][units.metric_id] == 6, "semantic overall result does not equal direct target SUM(quantity)", checks)
    check(overall.query_plan.sql_template.startswith("SELECT ") and ";" not in overall.query_plan.sql_template and overall.parameters == (), "semantic plan is not a generated read-only SELECT", checks)

    generic_temperature = next(item for item in generic_model.metrics if item.display_name == "Observed Temperature")
    generic_region = next(attribute for dimension in generic_model.dimensions if dimension.semantic_dimension_id == "dimension_location" for attribute in dimension.attributes if attribute.physical_column_ref == "region")
    generic_date = next(attribute for dimension in generic_model.dimensions if dimension.semantic_dimension_id == "dimension_observed_date" for attribute in dimension.attributes if attribute.physical_column_ref == "full_date")
    generic_query = service.resolve_query(
        generic_model,
        SemanticQueryRequest(
            request_id="validator_generic_region",
            metric_ids=(generic_temperature.metric_id,),
            group_by_attribute_ids=(generic_region.semantic_attribute_id, generic_date.semantic_attribute_id),
            time_role_id=generic_model.time_roles[0].time_role_id,
        ),
        analytical_plan=generic_context_value.plan,
        compiled_plan=generic_context_value.compiled_plan,
        materialization=generic_context_value.materialization,
    )
    generic_result = executor.execute(generic_model, generic_query)
    check(generic_result.rows and all(generic_temperature.metric_id in row for row in generic_result.rows), "generic semantic query did not execute through the bounded executor", checks)

    tampered_templates = (
        'SELECT SUM(f."quantity") AS "metric" FROM "undeclared_table" AS f LIMIT 1000',
        'SELECT SUM(f."quantity") AS "metric" FROM "fact_order_line" AS f LEFT JOIN "dim_customer" AS d0 ON f."customer_key" = d0."customer_key" LIMIT 1000',
        'SELECT (SELECT 1) AS "metric" FROM "fact_order_line" AS f LIMIT 1000',
        "SELECT * FROM read_csv('C:/blocked.csv')",
        "SELECT * FROM read_parquet('C:/blocked.parquet')",
        "SELECT * FROM parquet_scan('C:/blocked.parquet')",
        "SELECT * FROM csv_scan('C:/blocked.csv')",
        "SELECT glob('C:/blocked/*')",
        "SELECT * FROM sqlite_scan('C:/blocked.sqlite', 'items')",
        "SELECT read_text('C:/blocked.txt')",
        "SELECT read_blob('C:/blocked.bin')",
    )
    for statement in tampered_templates:
        tampered = overall.model_copy(update={"query_plan": overall.query_plan.model_copy(update={"sql_template": statement})})
        expect_failure(lambda tampered=tampered: executor.execute(retail_model, tampered), "STRUCTURAL_QUERY_REJECTED", checks)

    product_attribute = next(attribute for dimension in retail_model.dimensions if dimension.semantic_dimension_id == "dim_product" for attribute in dimension.attributes if attribute.physical_column_ref == "product_name")
    product_query = service.resolve_query(
        retail_model,
        SemanticQueryRequest(request_id="validator_product_path", metric_ids=(units.metric_id,), group_by_attribute_ids=(product_attribute.semantic_attribute_id,)),
        analytical_plan=retail_context_value.plan,
        compiled_plan=retail_context_value.compiled_plan,
        materialization=retail_context_value.materialization,
    )
    forged_join = product_query.query_plan.model_copy(update={"join_path_relationship_ids": ("srel_unknown",)})
    expect_failure(lambda: executor.execute(retail_model, product_query.model_copy(update={"query_plan": forged_join})), "STRUCTURAL_QUERY_REJECTED", checks)

    reviewed_ratio = MetricSpec(
        metric_id="metric_validator_ratio",
        semantic_name="validator_ratio",
        display_name="Validator Ratio",
        description="Explicit ratio retained as review-only metadata.",
        metric_kind=SemanticMetricKind.DERIVED,
        fact_ids=units.fact_ids,
        grain_ids=units.grain_ids,
        measure_ids=units.measure_ids,
        expression=MetricExpression(
            expression_type=SemanticExpressionType.RATIO,
            numerator_metric_id=units.metric_id,
            denominator_metric_id=units.metric_id,
            zero_denominator_behavior=ZeroDenominatorBehavior.NULL,
            result_unit_semantics="units per event",
        ),
        unit_semantics="units per event",
        currency_semantics="NOT_APPLICABLE",
        domain_assertion_refs=("validator:ratio",),
        lineage_refs=("validator:ratio",),
        provenance_refs=("validator:ratio",),
        availability=SemanticAvailability.REVIEW_REQUIRED,
        failure_reason="REVIEW_REQUIRED: V1 has no derived metric compiler",
    )
    available_ratio = reviewed_ratio.model_copy(update={"availability": SemanticAvailability.AVAILABLE, "failure_reason": None, "allowed_aggregation_operations": ("DIVIDE",)})
    forged_ratio_model = retail_model.model_copy(update={"metrics": (*retail_model.metrics, available_ratio)})
    expect_failure(
        lambda: service.resolve_query(
            forged_ratio_model,
            SemanticQueryRequest(request_id="validator_derived", metric_ids=(available_ratio.metric_id,)),
            analytical_plan=retail_context_value.plan,
            compiled_plan=retail_context_value.compiled_plan,
            materialization=retail_context_value.materialization,
        ),
        "DERIVED_METRIC_NOT_EXECUTABLE_V1",
        checks,
    )

    expect_failure(lambda: service.resolve_query(retail_model, SemanticQueryRequest(request_id="validator_price", metric_ids=(next(item.metric_id for item in retail_model.metrics if item.display_name == "Unit Price"),)), analytical_plan=retail_context_value.plan, compiled_plan=retail_context_value.compiled_plan, materialization=retail_context_value.materialization), "UNSUPPORTED_AGGREGATION", checks)
    bad_grain_metric = units.model_copy(update={"metric_id": "metric_other_grain", "grain_ids": ("grain_other",)})
    bad_grain_model = retail_model.model_copy(update={"metrics": (*retail_model.metrics, bad_grain_metric)})
    expect_failure(lambda: service.resolve_query(bad_grain_model, SemanticQueryRequest(request_id="validator_grain", metric_ids=(units.metric_id, bad_grain_metric.metric_id)), analytical_plan=retail_context_value.plan, compiled_plan=retail_context_value.compiled_plan, materialization=retail_context_value.materialization), "INCOMPATIBLE_GRAIN", checks)
    bad_dimension_metric = units.model_copy(update={"metric_id": "metric_no_dimension", "compatible_dimension_ids": ()})
    bad_dimension_model = retail_model.model_copy(update={"metrics": (*retail_model.metrics, bad_dimension_metric)})
    product_attribute = next(attribute for dimension in retail_model.dimensions if dimension.semantic_dimension_id == "dim_product" for attribute in dimension.attributes if attribute.physical_column_ref == "product_name")
    expect_failure(lambda: service.resolve_query(bad_dimension_model, SemanticQueryRequest(request_id="validator_dimension", metric_ids=(bad_dimension_metric.metric_id,), group_by_attribute_ids=(product_attribute.semantic_attribute_id,)), analytical_plan=retail_context_value.plan, compiled_plan=retail_context_value.compiled_plan, materialization=retail_context_value.materialization), "INCOMPATIBLE_DIMENSION", checks)
    stale_materialization = retail_context_value.materialization.model_copy(update={"status": MaterializationStatus.FAILED, "usable": False})
    expect_failure(lambda: service.build_model(retail_context_value.plan, retail_context_value.dimensions, retail_context_value.facts, retail_context_value.grains, retail_context_value.measures, retail_context_value.compiled_plan, stale_materialization, retail_context_value.canonical_model, analytical_review=retail_context_value.analytical_review), "NON_CONSUMABLE_TARGET", checks)
    expect_failure(lambda: service.build_model(retail_context_value.plan, retail_context_value.dimensions, retail_context_value.facts, retail_context_value.grains, retail_context_value.measures, retail_context_value.compiled_plan, retail_context_value.materialization, retail_context_value.canonical_model), "REVIEW_REQUIRED", checks)
    unknown_fact = retail_context_value.facts[0].model_copy(update={"relationship_refs": ("unknown_relationship",)})
    expect_failure(lambda: service.build_model(retail_context_value.plan, retail_context_value.dimensions, (unknown_fact,), retail_context_value.grains, retail_context_value.measures, retail_context_value.compiled_plan, retail_context_value.materialization, retail_context_value.canonical_model, analytical_review=retail_context_value.analytical_review), "STALE_ANALYTICAL_SPEC_PACKAGE", checks)
    stale_model = retail_model.model_copy(update={"target_file_sha256": "stale-target-hash"})
    stale_plan = overall.query_plan.model_copy(update={"semantic_model_content_hash": stale_model.content_hash})
    stale_compilation = overall.model_copy(update={"query_plan": stale_plan})
    expect_failure(lambda: executor.execute(stale_model, stale_compilation), "STALE_TARGET", checks)

    check(load(retail_run, "semantic_validation.json")["status"] == "PASS" and load(generic_run, "semantic_validation.json")["status"] == "PASS", "persisted semantic validation did not pass", checks)
    check(load(retail_run, "semantic_query_manifest.json")["raw_filter_values_persisted"] is False, "raw filter values were persisted", checks)
    check(not any((ROOT / "src").rglob("step22*")), "Step22 runtime module exists", checks)
    print(f"PASS: step21_semantic_checks={checks[0]} retail={retail_model.semantic_model_id} generic={generic_model.semantic_model_id} g5=PASS g6=PENDING")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationFailure, KeyError, ValueError, duckdb.Error) as exc:
        print("FAIL: step21_semantic_validator=" + str(exc))
        raise SystemExit(1)
