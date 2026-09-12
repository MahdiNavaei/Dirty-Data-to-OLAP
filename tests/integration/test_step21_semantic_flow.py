from pathlib import Path

from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor
from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from dirty_data_to_olap.domain.contracts.semantic import SemanticQueryRequest
from tests.step21_support import generic_semantic_flow, retail_semantic_flow


def _attribute(model, dimension_id, physical_column):
    dimension = next(item for item in model.dimensions if item.semantic_dimension_id == dimension_id)
    return next(item for item in dimension.attributes if item.physical_column_ref == physical_column)


def test_retail_semantic_query_matches_same_materialized_target_and_does_not_rewrite_it():
    context, model = retail_semantic_flow()
    metric = next(item for item in model.metrics if item.display_name == "Units Ordered")
    before = Path(context.materialization.target_relative_path).read_bytes()
    compilation = SemanticLayerService().resolve_query(model, SemanticQueryRequest(request_id="integration_overall", metric_ids=(metric.metric_id,)), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    result = DuckDBSemanticQueryExecutor(Path(".")).execute(model, compilation)
    assert result.rows[0][metric.metric_id] == 6
    assert Path(context.materialization.target_relative_path).read_bytes() == before


def test_retail_filter_is_parameterized_and_date_hierarchy_query_is_generated():
    context, model = retail_semantic_flow()
    service = SemanticLayerService()
    metric = next(item for item in model.metrics if item.display_name == "Units Ordered")
    product = _attribute(model, "dim_product", "product_name")
    compilation = service.resolve_query(model, SemanticQueryRequest(request_id="integration_product", metric_ids=(metric.metric_id,), group_by_attribute_ids=(product.semantic_attribute_id,)), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    assert "SELECT " in compilation.query_plan.sql_template
    assert "product_name" in compilation.query_plan.sql_template
    assert "SUM" in compilation.query_plan.sql_template
    hierarchy = model.hierarchies[0]
    year_attribute = next(level.semantic_attribute_id for level in hierarchy.levels if level.name == "Year")
    year_plan = service.resolve_query(model, SemanticQueryRequest(request_id="integration_year", metric_ids=(metric.metric_id,), group_by_attribute_ids=(year_attribute,), time_role_id=model.time_roles[0].time_role_id), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    assert year_attribute in year_plan.query_plan.group_by_attribute_ids
    assert year_plan.query_plan.join_path_relationship_ids


def test_generic_device_location_reading_uses_same_service_and_semi_additive_max():
    context, model = generic_semantic_flow()
    metric = next(item for item in model.metrics if item.display_name == "Observed Temperature")
    region = _attribute(model, "dimension_location", "region")
    date_value = _attribute(model, "dimension_observed_date", "full_date")
    compilation = SemanticLayerService().resolve_query(model, SemanticQueryRequest(request_id="integration_region", metric_ids=(metric.metric_id,), group_by_attribute_ids=(region.semantic_attribute_id, date_value.semantic_attribute_id), time_role_id=model.time_roles[0].time_role_id), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    assert "MAX" in compilation.query_plan.sql_template
    result = DuckDBSemanticQueryExecutor(Path(".")).execute(model, compilation)
    assert result.row_count == 3
    assert {str(row[metric.metric_id]) for row in result.rows} == {"11.000000000", "10.500000000", "9.500000000"}
