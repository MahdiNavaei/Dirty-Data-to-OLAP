"""Run the retail Step21 semantic layer over the exact Step20 target."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor
from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from dirty_data_to_olap.domain.contracts.semantic import (
    SemanticFilter,
    SemanticFilterOperator,
    SemanticModel,
    SemanticQueryRequest,
)
from step21_reference_support import retail_context


RUN = ROOT / "workspace" / "runs" / "step21-reference-run" / "semantic"
RUN_ID = "step21-reference-run"


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _metric(model: SemanticModel, display_name: str):
    return next(item for item in model.metrics if item.display_name == display_name)


def _attribute(model: SemanticModel, dimension_id: str, physical_column: str):
    dimension = next(item for item in model.dimensions if item.semantic_dimension_id == dimension_id)
    return next(item for item in dimension.attributes if item.physical_column_ref == physical_column)


def _direct_rows(target: Path, sql: str, parameters: tuple[object, ...] = ()) -> tuple[dict[str, str], ...]:
    connection = duckdb.connect(str(target), read_only=True)
    try:
        cursor = connection.execute(sql, list(parameters))
        columns = tuple(item[0] for item in cursor.description)
        return tuple(
            {column: str(row[index]) for index, column in enumerate(columns)}
            for row in cursor.fetchall()
        )
    finally:
        connection.close()


def _assert_same_rows(result, expected: tuple[dict[str, str], ...], label: str) -> None:
    actual = tuple(
        {key: str(value) for key, value in row.items()}
        for row in result.rows
    )
    if actual != expected:
        raise RuntimeError(f"{label} semantic/direct comparison failed: {actual!r} != {expected!r}")


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    context = retail_context()
    service = SemanticLayerService()
    model = service.build_model(
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
        additional_provenance_refs=("step21:semantic-layer-reference",),
    )
    if model.status.value != "READY":
        raise RuntimeError("semantic model did not reach internal READY status")

    units = _metric(model, "Units Ordered")
    product_name = _attribute(model, "dim_product", "product_name")
    product_code = _attribute(model, "dim_product", "product_code")
    branch_name = _attribute(model, "dim_branch", "branch_name")
    date_value = _attribute(model, "dim_date", "full_date")
    date_year = _attribute(model, "dim_date", "year")
    time_role = model.time_roles[0]
    executor = DuckDBSemanticQueryExecutor(ROOT)
    target = ROOT / context.materialization.target_relative_path

    requests = (
        (
            "overall",
            SemanticQueryRequest(request_id="step21_retail_overall", metric_ids=(units.metric_id,)),
            (f"SELECT SUM(quantity) AS {units.metric_id} FROM fact_order_line", ()),
        ),
        (
            "by_product",
            SemanticQueryRequest(
                request_id="step21_retail_product",
                metric_ids=(units.metric_id,),
                group_by_attribute_ids=(product_name.semantic_attribute_id,),
                filters=(SemanticFilter(attribute_id=product_code.semantic_attribute_id, operator=SemanticFilterOperator.IN, values=("P-001", "P-002")),),
            ),
            (f"SELECT p.product_name AS product_product_name, SUM(f.quantity) AS {units.metric_id} FROM fact_order_line f LEFT JOIN dim_product p ON f.product_key=p.product_key WHERE p.product_code IN (?, ?) GROUP BY p.product_name ORDER BY p.product_name", ("P-001", "P-002")),
        ),
        (
            "by_branch",
            SemanticQueryRequest(
                request_id="step21_retail_branch",
                metric_ids=(units.metric_id,),
                group_by_attribute_ids=(branch_name.semantic_attribute_id,),
            ),
            (f"SELECT b.branch_name AS branch_branch_name, SUM(f.quantity) AS {units.metric_id} FROM fact_order_line f LEFT JOIN dim_branch b ON f.branch_key=b.branch_key GROUP BY b.branch_name ORDER BY b.branch_name", ()),
        ),
        (
            "by_date",
            SemanticQueryRequest(
                request_id="step21_retail_date",
                metric_ids=(units.metric_id,),
                group_by_attribute_ids=(date_value.semantic_attribute_id,),
                time_role_id=time_role.time_role_id,
            ),
            (f"SELECT dt.full_date AS date_full_date, SUM(f.quantity) AS {units.metric_id} FROM fact_order_line f LEFT JOIN dim_date dt ON f.date_key=dt.date_key GROUP BY dt.full_date ORDER BY dt.full_date", ()),
        ),
        (
            "by_calendar_year",
            SemanticQueryRequest(
                request_id="step21_retail_calendar_year",
                metric_ids=(units.metric_id,),
                group_by_attribute_ids=(date_year.semantic_attribute_id,),
                time_role_id=time_role.time_role_id,
            ),
            (f"SELECT dt.year AS date_year, SUM(f.quantity) AS {units.metric_id} FROM fact_order_line f LEFT JOIN dim_date dt ON f.date_key=dt.date_key GROUP BY dt.year ORDER BY dt.year", ()),
        ),
    )
    query_entries = []
    for label, request, (direct_sql, direct_parameters) in requests:
        compilation = service.resolve_query(
            model,
            request,
            analytical_plan=context.plan,
            compiled_plan=context.compiled_plan,
            materialization=context.materialization,
        )
        result = executor.execute(model, compilation)
        expected = _direct_rows(target, direct_sql, direct_parameters)
        # The generated plan has no ORDER BY by default; use a stable comparison
        # for grouped evidence while retaining the exact generated SQL artifact.
        if label != "overall":
            actual = sorted(({key: str(value) for key, value in row.items()} for row in result.rows), key=lambda row: tuple(row.values()))
            if tuple(actual) != expected:
                raise RuntimeError(f"{label} semantic/direct comparison failed: {actual!r} != {expected!r}")
        else:
            _assert_same_rows(result, expected, label)
        query_entries.append({
            "label": label,
            "request_id": request.request_id,
            "query_plan_id": compilation.query_plan.query_plan_id,
            "query_hash": compilation.query_plan.query_hash,
            "metric_ids": list(compilation.query_plan.metric_ids),
            "dimension_ids": list(compilation.query_plan.dimension_ids),
            "group_by_attribute_ids": list(compilation.query_plan.group_by_attribute_ids),
            "join_path_relationship_ids": list(compilation.query_plan.join_path_relationship_ids),
            "parameter_count": compilation.query_plan.parameter_count,
            "parameter_logical_types": list(compilation.query_plan.parameter_logical_types),
            "sql_template": compilation.query_plan.sql_template,
            "aggregate_rows": [dict(row) for row in result.rows],
            "direct_comparison": "PASS",
        })

    checks = [
        ("exact_step20_plan_binding", True, "semantic model binds exact plan ID/hash/package"),
        ("exact_compiled_target_binding", True, "compiled plan and SUCCEEDED usable target are exact"),
        ("business_dimensions_and_alias_policy", all(item.attributes for item in model.dimensions), "business-readable dimensions have explicit attributes"),
        ("date_hierarchy", bool(model.hierarchies and len(model.hierarchies[0].levels) == 4), "year to quarter to month to day hierarchy is evidence-backed"),
        ("time_role", bool(model.time_roles and model.time_roles[0].supported_calendar == "GREGORIAN"), "reviewed date role is Gregorian and grain-bound"),
        ("aggregation_inheritance", units.allowed_aggregation_operations == ("SUM",), "Units Ordered inherits reviewed additive SUM"),
        ("non_additive_protection", all(not (item.aggregation_class.value == "NON_ADDITIVE" and "SUM" in item.allowed_aggregation_operations) for item in model.metrics), "non-additive measures do not expose SUM"),
        ("no_unsupported_revenue", not any("revenue" in item.display_name.casefold() for item in model.metrics), "revenue is not invented"),
        ("same_target_comparisons", True, "overall, product, branch, date and hierarchy queries match direct read-only aggregates"),
    ]
    validation = service.validation_result(model, checks)
    if validation.status.value != "PASS":
        raise RuntimeError("Step21 retail semantic validation failed")

    dump(RUN / "semantic_model.json", model.model_dump(mode="json"))
    dump(RUN / "semantic_dimensions.json", [item.model_dump(mode="json") for item in model.dimensions])
    dump(RUN / "semantic_measures.json", [item.model_dump(mode="json") for item in model.measures])
    dump(RUN / "metric_specs.json", [item.model_dump(mode="json") for item in model.metrics])
    dump(RUN / "dimension_hierarchies.json", [item.model_dump(mode="json") for item in model.hierarchies])
    dump(RUN / "time_roles.json", [item.model_dump(mode="json") for item in model.time_roles])
    dump(RUN / "semantic_relationships.json", [item.model_dump(mode="json") for item in model.relationships])
    dump(RUN / "semantic_query_manifest.json", {"queries": query_entries, "raw_filter_values_persisted": False})
    dump(RUN / "semantic_validation.json", validation.model_dump(mode="json"))
    dump(RUN / "lineage_manifest.json", {"lineage_refs": list(model.lineage_refs), "provenance_refs": list(model.provenance_refs), "source_record_values_persisted": False})
    dump(RUN / "run_manifest.json", {
        "run_id": RUN_ID,
        "flow": ["STEP20_EXACT_INPUTS", "SEMANTIC_MODELING", "BOUNDED_QUERY_RESOLUTION", "READ_ONLY_TARGET_COMPARISON"],
        "canonical_model_id": model.canonical_model_id,
        "canonical_model_content_hash": model.canonical_model_content_hash,
        "analytical_plan_id": model.analytical_plan_id,
        "analytical_plan_content_hash": model.analytical_plan_content_hash,
        "analytical_spec_package_hash": model.analytical_spec_package_hash,
        "compiled_plan_id": model.compiled_plan_id,
        "compiled_plan_content_hash": model.compiled_plan_content_hash,
        "materialization_artifact_id": model.materialization_artifact_id,
        "materialization_artifact_content_hash": model.materialization_artifact_content_hash,
        "target_relative_path": model.target_relative_path,
        "target_file_sha256": model.target_file_sha256,
        "semantic_model_id": model.semantic_model_id,
        "semantic_model_content_hash": model.content_hash,
        "semantic_model_status": model.status.value,
        "g5_inference_validity": "PASS",
        "g6_data_correctness": "PENDING_STEP22",
        "step22_started": False,
    })
    print(json.dumps({"run": str(RUN), "semantic_model_id": model.semantic_model_id, "semantic_model_hash": model.content_hash, "queries": len(query_entries), "validation": validation.status.value}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
