from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor, SemanticQueryExecutionError
from dirty_data_to_olap.application.semantic_layer import SemanticLayerError, SemanticLayerService
from dirty_data_to_olap.domain.contracts.semantic import SemanticQueryPlan, SemanticQueryRequest
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
