from pathlib import Path

import duckdb
import pytest

from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor, SemanticQueryExecutionError
from dirty_data_to_olap.application.semantic_layer import SemanticLayerError, SemanticLayerService
from dirty_data_to_olap.domain.contracts.semantic import (
    MetricExpression,
    MetricSpec,
    SemanticAvailability,
    SemanticExpressionType,
    SemanticMetricKind,
    SemanticQueryPlan,
    SemanticQueryRequest,
    ZeroDenominatorBehavior,
)
from tests.step21_support import retail_semantic_flow


def test_query_contract_rejects_raw_sql_and_query_plan_rejects_injection():
    with pytest.raises(ValueError):
        SemanticQueryRequest(request_id="q", metric_ids=("metric_units",), raw_sql="SELECT 1")
    with pytest.raises(ValueError, match="semicolon"):
        SemanticQueryPlan(query_plan_id="squery_bad", request_id="q", semantic_model_id="smodel_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", semantic_model_content_hash="hash", metric_ids=("metric_units",), fact_ids=("fact",), grain_ids=("grain",), parameter_count=0, sql_template="SELECT 1; DROP TABLE fact", query_hash="hash", provenance_refs=("test",))


def test_unknown_fields_dimensions_metrics_and_filter_types_fail_closed():
    context, model = retail_semantic_flow()
    service = SemanticLayerService()
    with pytest.raises(SemanticLayerError, match="UNKNOWN_METRIC"):
        service.resolve_query(model, SemanticQueryRequest(request_id="q_unknown_metric", metric_ids=("metric_unknown",)), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    with pytest.raises(SemanticLayerError, match="UNKNOWN_ATTRIBUTE"):
        service.resolve_query(model, SemanticQueryRequest(request_id="q_unknown_attribute", metric_ids=(model.metrics[0].metric_id,), group_by_attribute_ids=("not_an_attribute",)), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    with pytest.raises(SemanticLayerError, match="UNKNOWN_DIMENSION"):
        service.resolve_query(model, SemanticQueryRequest(request_id="q_unknown_dimension", metric_ids=(model.metrics[0].metric_id,), dimension_ids=("not_a_dimension",)), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)


def test_filter_values_are_parameters_and_path_target_hash_is_enforced():
    context, model = retail_semantic_flow()
    service = SemanticLayerService()
    product_code = next(attribute for dimension in model.dimensions if dimension.semantic_dimension_id == "dim_product" for attribute in dimension.attributes if attribute.physical_column_ref == "product_code")
    injection_like = "P-001' OR 1=1 --"
    request = SemanticQueryRequest(request_id="q_parameter", metric_ids=(model.metrics[0].metric_id,), filters=({"attribute_id": product_code.semantic_attribute_id, "operator": "EQUALS", "values": (injection_like,)},))
    compilation = service.resolve_query(model, request, analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    assert injection_like not in compilation.query_plan.sql_template
    assert compilation.parameters == (injection_like,)
    bad_model = model.model_copy(update={"target_file_sha256": "stale-target-hash"})
    bad_compilation = compilation.model_copy(update={"query_plan": compilation.query_plan.model_copy(update={"semantic_model_content_hash": bad_model.content_hash})})
    with pytest.raises(SemanticQueryExecutionError, match="STALE_TARGET"):
        DuckDBSemanticQueryExecutor(Path(".")).execute(bad_model, bad_compilation)


def test_executor_rejects_tampered_sql_unknown_tables_and_file_functions_before_duckdb():
    context, model = retail_semantic_flow()
    metric = next(item for item in model.metrics if item.display_name == "Units Ordered")
    compilation = SemanticLayerService().resolve_query(
        model,
        SemanticQueryRequest(request_id="q_structural", metric_ids=(metric.metric_id,)),
        analytical_plan=context.plan,
        compiled_plan=context.compiled_plan,
        materialization=context.materialization,
    )
    malicious_templates = (
        'SELECT SUM(f."quantity") AS "metric" FROM "undeclared_table" AS f LIMIT 1000',
        'SELECT SUM(f."quantity") AS "metric" FROM "fact_order_line" AS f LEFT JOIN "dim_customer" AS d0 ON f."customer_key" = d0."customer_key" LIMIT 1000',
        "SELECT (SELECT 1) AS \"metric\" FROM \"fact_order_line\" AS f LIMIT 1000",
        "SELECT * FROM read_csv('C:/blocked.csv')",
        "SELECT * FROM read_parquet('C:/blocked.parquet')",
        "SELECT * FROM parquet_scan('C:/blocked.parquet')",
        "SELECT * FROM csv_scan('C:/blocked.csv')",
        "SELECT glob('C:/blocked/*')",
        "SELECT * FROM sqlite_scan('C:/blocked.sqlite', 'items')",
        "SELECT read_text('C:/blocked.txt')",
        "SELECT read_blob('C:/blocked.bin')",
    )
    for statement in malicious_templates:
        forged = compilation.model_copy(update={"query_plan": compilation.query_plan.model_copy(update={"sql_template": statement})})
        with pytest.raises(SemanticQueryExecutionError, match="STRUCTURAL_QUERY_REJECTED"):
            DuckDBSemanticQueryExecutor(Path(".")).execute(model, forged)


def test_executor_rejects_undeclared_join_path_and_keeps_target_read_only():
    context, model = retail_semantic_flow()
    metric = next(item for item in model.metrics if item.display_name == "Units Ordered")
    product = next(attribute for dimension in model.dimensions if dimension.semantic_dimension_id == "dim_product" for attribute in dimension.attributes if attribute.physical_column_ref == "product_name")
    compilation = SemanticLayerService().resolve_query(
        model,
        SemanticQueryRequest(request_id="q_join_path", metric_ids=(metric.metric_id,), group_by_attribute_ids=(product.semantic_attribute_id,)),
        analytical_plan=context.plan,
        compiled_plan=context.compiled_plan,
        materialization=context.materialization,
    )
    forged_plan = compilation.query_plan.model_copy(update={"join_path_relationship_ids": ("srel_unknown",)})
    with pytest.raises(SemanticQueryExecutionError, match="STRUCTURAL_QUERY_REJECTED"):
        DuckDBSemanticQueryExecutor(Path(".")).execute(model, compilation.model_copy(update={"query_plan": forged_plan}))

    connection = duckdb.connect(str(Path(context.materialization.target_relative_path)), read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            connection.execute("CREATE TABLE semantic_read_only_probe(value INTEGER)")
    finally:
        connection.close()


def test_available_derived_ratio_is_rejected_at_the_semantic_boundary():
    context, model = retail_semantic_flow()
    units = next(item for item in model.metrics if item.display_name == "Units Ordered")
    reviewed_ratio = MetricSpec(
        metric_id="metric_units_ratio",
        semantic_name="units_ratio",
        display_name="Units Ratio",
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
        domain_assertion_refs=("review-required:ratio",),
        lineage_refs=("test:step21",),
        provenance_refs=("test:step21",),
        availability=SemanticAvailability.REVIEW_REQUIRED,
        failure_reason="REVIEW_REQUIRED: V1 has no derived metric compiler",
    )
    available_ratio = reviewed_ratio.model_copy(update={"availability": SemanticAvailability.AVAILABLE, "failure_reason": None, "allowed_aggregation_operations": ("DIVIDE",)})
    forged_model = model.model_copy(update={"metrics": (*model.metrics, available_ratio)})
    with pytest.raises(SemanticLayerError, match="DERIVED_METRIC_NOT_EXECUTABLE_V1"):
        SemanticLayerService().resolve_query(
            forged_model,
            SemanticQueryRequest(request_id="q_derived", metric_ids=(available_ratio.metric_id,)),
            analytical_plan=context.plan,
            compiled_plan=context.compiled_plan,
            materialization=context.materialization,
        )
