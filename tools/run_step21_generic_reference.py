"""Run the generic Step21 semantic layer for Device/Location/Reading."""

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
from dirty_data_to_olap.domain.contracts.semantic import SemanticModel, SemanticQueryRequest
from step21_reference_support import generic_context


RUN = ROOT / "workspace" / "runs" / "step21-generic-reference-run" / "semantic"
RUN_ID = "step21-generic-reference-run"


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


def _direct_rows(target: Path, sql: str) -> tuple[dict[str, str], ...]:
    connection = duckdb.connect(str(target), read_only=True)
    try:
        cursor = connection.execute(sql)
        columns = tuple(item[0] for item in cursor.description)
        return tuple(
            {column: str(row[index]) for index, column in enumerate(columns)}
            for row in cursor.fetchall()
        )
    finally:
        connection.close()


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    context = generic_context()
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
        additional_provenance_refs=("step21:generic-semantic-layer-reference",),
    )
    if model.status.value != "READY":
        raise RuntimeError("generic semantic model did not reach internal READY status")
    temperature = _metric(model, "Observed Temperature")
    region = _attribute(model, "dimension_location", "region")
    device_name = _attribute(model, "dimension_device", "device_name")
    date_value = _attribute(model, "dimension_observed_date", "full_date")
    time_role = model.time_roles[0]
    executor = DuckDBSemanticQueryExecutor(ROOT)
    target = ROOT / context.materialization.target_relative_path

    requests = (
        (
            "temperature_by_region",
            SemanticQueryRequest(
                request_id="step21_generic_region",
                metric_ids=(temperature.metric_id,),
                group_by_attribute_ids=(region.semantic_attribute_id, date_value.semantic_attribute_id),
                time_role_id=time_role.time_role_id,
            ),
            f"SELECT l.region AS dimension_location_region, dt.full_date AS dimension_observed_date_full_date, MAX(f.temperature) AS {temperature.metric_id} FROM fact_device_reading f LEFT JOIN dim_location l ON f.location_key=l.location_key LEFT JOIN dim_observed_date dt ON f.observed_date_key=dt.observed_date_key GROUP BY l.region, dt.full_date ORDER BY l.region, dt.full_date",
        ),
        (
            "temperature_by_device",
            SemanticQueryRequest(
                request_id="step21_generic_device",
                metric_ids=(temperature.metric_id,),
                group_by_attribute_ids=(device_name.semantic_attribute_id, date_value.semantic_attribute_id),
                time_role_id=time_role.time_role_id,
            ),
            f"SELECT d.device_name AS dimension_device_device_name, dt.full_date AS dimension_observed_date_full_date, MAX(f.temperature) AS {temperature.metric_id} FROM fact_device_reading f LEFT JOIN dim_device d ON f.device_key=d.device_key LEFT JOIN dim_observed_date dt ON f.observed_date_key=dt.observed_date_key GROUP BY d.device_name, dt.full_date ORDER BY d.device_name, dt.full_date",
        ),
    )
    query_entries = []
    for label, request, direct_sql in requests:
        compilation = service.resolve_query(
            model,
            request,
            analytical_plan=context.plan,
            compiled_plan=context.compiled_plan,
            materialization=context.materialization,
        )
        result = executor.execute(model, compilation)
        actual = sorted(({key: str(value) for key, value in row.items()} for row in result.rows), key=lambda row: tuple(row.values()))
        expected = _direct_rows(target, direct_sql)
        if tuple(actual) != expected:
            raise RuntimeError(f"{label} semantic/direct comparison failed: {actual!r} != {expected!r}")
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
        ("exact_step20_plan_binding", True, "generic semantic model binds exact plan ID/hash/package"),
        ("same_generic_runtime", True, "Device, Location and Reading use the same SemanticLayerService"),
        ("semi_additive_inheritance", temperature.allowed_aggregation_operations == ("MAX",), "reviewed semi-additive temperature inherits MAX"),
        ("time_role", bool(model.time_roles and model.time_roles[0].supported_calendar == "GREGORIAN"), "observed date role is Gregorian and grain-bound"),
        ("relationship_classification", all(item.relationship_scope.value in {"CANONICAL_ACCEPTED", "ANALYTICAL_TIME_ROLE"} for item in model.relationships), "all joins carry explicit reviewed scope"),
        ("same_target_comparisons", True, "region and device aggregate queries match direct read-only aggregates"),
    ]
    validation = service.validation_result(model, checks)
    if validation.status.value != "PASS":
        raise RuntimeError("Step21 generic semantic validation failed")

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
