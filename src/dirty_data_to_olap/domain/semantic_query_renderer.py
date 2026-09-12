"""Deterministic SQL renderer for the bounded semantic query structure."""

from __future__ import annotations

from dirty_data_to_olap.domain.contracts.semantic import (
    SemanticAvailability,
    SemanticExpressionType,
    SemanticExposureState,
    SemanticFilterOperator,
    SemanticMetricKind,
    SemanticModel,
    SemanticQueryPlan,
    SemanticRelationshipScope,
)


_AGGREGATE_SQL = {"SUM", "MAX", "MIN", "AVG", "COUNT"}


class SemanticQueryRenderError(ValueError):
    """A structured semantic query cannot be rendered safely."""


def _quoted(identifier: str) -> str:
    if not identifier or '"' in identifier or "\x00" in identifier:
        raise SemanticQueryRenderError("unsafe physical identifier in semantic query structure")
    return f'"{identifier}"'


def render_semantic_query_sql(model: SemanticModel, plan: SemanticQueryPlan) -> str:
    """Render only the SQL implied by a bound model and structured query plan."""

    try:
        model = SemanticModel.model_validate(model.model_dump(mode="python"))
        plan = SemanticQueryPlan.model_validate(plan.model_dump(mode="python"))
    except (TypeError, ValueError) as exc:
        raise SemanticQueryRenderError("INVALID_SEMANTIC_QUERY_STRUCTURE") from exc
    if plan.semantic_model_id != model.semantic_model_id or plan.semantic_model_content_hash != model.content_hash:
        raise SemanticQueryRenderError("STALE_SEMANTIC_MODEL: query plan is not bound to this model")
    if len(set(plan.metric_ids)) != len(plan.metric_ids):
        raise SemanticQueryRenderError("DUPLICATE_METRIC: metric IDs must be unique")
    if len(set(plan.dimension_ids)) != len(plan.dimension_ids):
        raise SemanticQueryRenderError("DUPLICATE_DIMENSION: dimension IDs must be unique")
    if len(set(plan.group_by_attribute_ids)) != len(plan.group_by_attribute_ids):
        raise SemanticQueryRenderError("DUPLICATE_GROUP_ATTRIBUTE: group attributes must be unique")
    if len(set(plan.join_path_relationship_ids)) != len(plan.join_path_relationship_ids):
        raise SemanticQueryRenderError("DUPLICATE_RELATIONSHIP: join paths must be unique")

    metrics = {item.metric_id: item for item in model.metrics}
    dimensions = {item.semantic_dimension_id: item for item in model.dimensions}
    attributes = {item.semantic_attribute_id: item for dimension in model.dimensions for item in dimension.attributes}
    relationships = {item.relationship_id: item for item in model.relationships}

    try:
        selected_metrics = tuple(metrics[item] for item in plan.metric_ids)
    except KeyError as exc:
        raise SemanticQueryRenderError("UNKNOWN_METRIC: query references an undeclared metric") from exc
    if any(item.metric_kind is SemanticMetricKind.DERIVED for item in selected_metrics):
        raise SemanticQueryRenderError("DERIVED_METRIC_NOT_EXECUTABLE_V1: derived metrics are not executable")
    unavailable = next((item for item in selected_metrics if item.availability is not SemanticAvailability.AVAILABLE), None)
    if unavailable is not None:
        raise SemanticQueryRenderError(unavailable.failure_reason or "UNAVAILABLE_METRIC")
    if len(plan.fact_ids) != 1 or len(plan.grain_ids) != 1:
        raise SemanticQueryRenderError("INCOMPATIBLE_GRAIN: query must bind one fact and grain")
    fact_id = plan.fact_ids[0]
    grain_id = plan.grain_ids[0]
    if any(metric.fact_ids != (fact_id,) or metric.grain_ids != (grain_id,) for metric in selected_metrics):
        raise SemanticQueryRenderError("INCOMPATIBLE_GRAIN: metric bindings do not match the query plan")

    try:
        selected_dimensions = tuple(dimensions[item] for item in plan.dimension_ids)
    except KeyError as exc:
        raise SemanticQueryRenderError("UNKNOWN_DIMENSION: query references an undeclared dimension") from exc
    try:
        group_attributes = tuple(attributes[item] for item in plan.group_by_attribute_ids)
    except KeyError as exc:
        raise SemanticQueryRenderError("UNKNOWN_ATTRIBUTE: query references an undeclared attribute") from exc
    if any(item.exposure_state is not SemanticExposureState.EXPOSED for item in group_attributes):
        raise SemanticQueryRenderError("IMPLEMENTATION_ONLY_ATTRIBUTE: query cannot expose implementation fields")
    if any(item.dimension_id not in plan.dimension_ids for item in group_attributes):
        raise SemanticQueryRenderError("ATTRIBUTE_DIMENSION_MISMATCH: group attribute is outside the query dimensions")

    join_path = []
    for relationship_id in plan.join_path_relationship_ids:
        relationship = relationships.get(relationship_id)
        if relationship is None:
            raise SemanticQueryRenderError("UNKNOWN_RELATIONSHIP: query references an undeclared join path")
        join_path.append(relationship)
    if tuple(item.target_semantic_object_id for item in join_path) != tuple(plan.dimension_ids):
        raise SemanticQueryRenderError("UNDECLARED_JOIN_PATH: join path does not exactly cover selected dimensions")
    if any(item.source_semantic_object_id != fact_id for item in join_path):
        raise SemanticQueryRenderError("UNDECLARED_JOIN_PATH: join path does not originate at the selected fact")
    if any(item.relationship_scope not in {SemanticRelationshipScope.CANONICAL_ACCEPTED, SemanticRelationshipScope.ANALYTICAL_TIME_ROLE} for item in join_path):
        raise SemanticQueryRenderError("UNCLASSIFIED_RELATIONSHIP: join path scope is not allowed")

    time_role = None
    if plan.time_role_id is not None:
        time_role = next((item for item in model.time_roles if item.time_role_id == plan.time_role_id), None)
        if time_role is None or time_role.fact_id != fact_id:
            raise SemanticQueryRenderError("INCOMPATIBLE_TIME_ROLE: query time role is not bound to the selected fact")
        if time_role.date_dimension_id in plan.dimension_ids:
            matching = [item for item in join_path if item.target_semantic_object_id == time_role.date_dimension_id and item.upstream_relationship_ref == time_role.relationship_ref]
            if len(matching) != 1:
                raise SemanticQueryRenderError("INCOMPATIBLE_TIME_ROLE: query join does not use the declared time role")

    aliases = {dimension.semantic_dimension_id: f"d{index}" for index, dimension in enumerate(selected_dimensions)}
    select_parts: list[str] = []
    group_parts: list[str] = []
    expected_bindings: dict[str, str] = {}
    for attribute in group_attributes:
        expression = f'{aliases[attribute.dimension_id]}.{_quoted(attribute.physical_column_ref)}'
        select_parts.append(f'{expression} AS {_quoted(attribute.semantic_attribute_id)}')
        group_parts.append(expression)
        dimension = dimensions[attribute.dimension_id]
        expected_bindings[attribute.semantic_attribute_id] = f"{dimension.physical_table_ref}.{attribute.physical_column_ref}"

    measure_by_spec = {item.measure_spec_id: item for item in model.measures}
    aggregation_operations: dict[str, str] = {}
    fact_table: str | None = None
    for metric in selected_metrics:
        if metric.expression.expression_type is not SemanticExpressionType.AGGREGATE_MEASURE or not metric.expression.measure_id:
            raise SemanticQueryRenderError("DERIVED_METRIC_NOT_EXECUTABLE_V1: executable metrics require an aggregate measure")
        measure = measure_by_spec.get(metric.expression.measure_id)
        if measure is None:
            raise SemanticQueryRenderError("UNKNOWN_MEASURE: metric references an undeclared semantic measure")
        if measure.fact_id != fact_id or measure.grain_id != grain_id:
            raise SemanticQueryRenderError("INCOMPATIBLE_GRAIN: measure binding does not match the query plan")
        operation = plan.aggregation_operations.get(metric.metric_id)
        if operation not in metric.allowed_aggregation_operations or operation not in _AGGREGATE_SQL:
            raise SemanticQueryRenderError("UNSUPPORTED_AGGREGATION: query operation is not reviewed for the metric")
        if fact_table is None:
            fact_table = measure.physical_fact_table_ref
        elif fact_table != measure.physical_fact_table_ref:
            raise SemanticQueryRenderError("INCOMPATIBLE_FACT: selected metrics use different physical facts")
        select_parts.append(f'{operation}(f.{_quoted(measure.physical_field_ref)}) AS {_quoted(metric.metric_id)}')
        aggregation_operations[metric.metric_id] = operation
        expected_bindings[metric.metric_id] = f"{measure.physical_fact_table_ref}.{measure.physical_field_ref}"
    if fact_table is None or set(plan.aggregation_operations) != set(plan.metric_ids):
        raise SemanticQueryRenderError("INCOMPLETE_AGGREGATION: every selected metric needs one reviewed operation")
    if dict(plan.physical_bindings) != expected_bindings:
        raise SemanticQueryRenderError("PHYSICAL_BINDING_MISMATCH: plan bindings do not match the semantic model")
    for relationship in join_path:
        dimension = dimensions[relationship.target_semantic_object_id]
        if relationship.physical_fact_id != fact_id or relationship.physical_fact_table != fact_table:
            raise SemanticQueryRenderError("PHYSICAL_BINDING_MISMATCH: relationship fact binding differs from the selected fact")
        if relationship.physical_dimension_id != dimension.semantic_dimension_id or relationship.physical_dimension_table != dimension.physical_table_ref:
            raise SemanticQueryRenderError("PHYSICAL_BINDING_MISMATCH: relationship dimension binding differs from the selected dimension")

    sql_parts = ["SELECT " + ", ".join(select_parts), f'FROM {_quoted(fact_table)} AS f']
    for relationship in join_path:
        alias = aliases[relationship.target_semantic_object_id]
        sql_parts.append(
            f'LEFT JOIN {_quoted(relationship.physical_dimension_table)} AS {alias} '
            f'ON f.{_quoted(relationship.physical_fact_column)} = {alias}.{_quoted(relationship.physical_dimension_key_column)}'
        )

    where_parts: list[str] = []
    expected_parameter_types: list[str] = []
    for shape in plan.filter_shapes:
        attribute = attributes.get(shape.attribute_id)
        if attribute is None:
            raise SemanticQueryRenderError("UNKNOWN_FILTER_ATTRIBUTE: filter shape references an undeclared attribute")
        if attribute.exposure_state is not SemanticExposureState.EXPOSED:
            raise SemanticQueryRenderError("IMPLEMENTATION_ONLY_ATTRIBUTE: filter cannot use implementation fields")
        if attribute.dimension_id not in plan.dimension_ids:
            raise SemanticQueryRenderError("FILTER_DIMENSION_MISMATCH: filter dimension is outside the query path")
        if attribute.logical_type != shape.logical_type:
            raise SemanticQueryRenderError("FILTER_TYPE_MISMATCH: filter shape type differs from the semantic attribute")
        expression = f'{aliases[attribute.dimension_id]}.{_quoted(attribute.physical_column_ref)}'
        if shape.operator is SemanticFilterOperator.EQUALS and shape.null_value:
            where_parts.append(f"{expression} IS NULL")
        elif shape.operator is SemanticFilterOperator.EQUALS:
            where_parts.append(f"{expression} = ?")
            expected_parameter_types.append(shape.logical_type)
        elif shape.operator is SemanticFilterOperator.IN:
            where_parts.append(f"{expression} IN ({', '.join('?' for _ in range(shape.value_count))})")
            expected_parameter_types.extend(shape.logical_type for _ in range(shape.value_count))
        elif shape.operator is SemanticFilterOperator.DATE_RANGE:
            where_parts.append(f"{expression} >= ? AND {expression} <= ?")
            expected_parameter_types.extend(shape.logical_type for _ in range(2))
        else:
            raise SemanticQueryRenderError("UNSUPPORTED_FILTER: filter operator is not allowlisted")
    if tuple(expected_parameter_types) != tuple(plan.parameter_logical_types) or plan.parameter_count != len(expected_parameter_types):
        raise SemanticQueryRenderError("PARAMETER_SHAPE_MISMATCH: parameter metadata does not match filter structure")
    if where_parts:
        sql_parts.append("WHERE " + " AND ".join(where_parts))
    if group_parts:
        sql_parts.append("GROUP BY " + ", ".join(group_parts))

    selected_fields = set(plan.group_by_attribute_ids) | set(plan.metric_ids)
    sort_parts: list[str] = []
    for sort in plan.sort_specs:
        if sort.field_id not in selected_fields:
            raise SemanticQueryRenderError("UNKNOWN_SORT_FIELD: sort must use a selected semantic field")
        sort_parts.append(f'{_quoted(sort.field_id)} {"DESC" if sort.descending else "ASC"}')
    if sort_parts:
        sql_parts.append("ORDER BY " + ", ".join(sort_parts))
    sql_parts.append(f"LIMIT {plan.limit}")
    return "\n".join(sql_parts)
