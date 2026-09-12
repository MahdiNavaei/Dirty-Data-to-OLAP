from datetime import date

import pytest

from dirty_data_to_olap.application.semantic_layer import SemanticLayerError, SemanticLayerService
from dirty_data_to_olap.domain.contracts.semantic import (
    DimensionHierarchy,
    HierarchyLevel,
    MetricExpression,
    MetricSpec,
    SemanticAttribute,
    SemanticAvailability,
    SemanticDimension,
    SemanticExpressionType,
    SemanticHierarchyValidationState,
    SemanticMetricKind,
    TimeRole,
    ZeroDenominatorBehavior,
)
from tests.step21_support import retail_semantic_flow


def test_semantic_model_is_deterministic_and_binds_reviewed_inputs():
    context, first = retail_semantic_flow()
    second = SemanticLayerService().build_model(
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
        additional_provenance_refs=("test:step21",),
    )
    assert first.semantic_model_id == second.semantic_model_id
    assert first.content_hash == second.content_hash
    assert first.analytical_plan_id == context.plan.plan_id
    assert first.analytical_plan_content_hash == context.plan.content_hash
    assert first.analytical_spec_package_hash == context.plan.analytical_spec_package_hash
    assert first.compiled_plan_content_hash == context.compiled_plan.content_hash
    assert first.materialization_artifact_content_hash == context.materialization.content_hash
    assert first.status.value == "READY"


def test_alias_collisions_and_duplicate_hierarchy_levels_fail_closed():
    with pytest.raises(ValueError, match="aliases"):
        SemanticAttribute(
            semantic_attribute_id="region",
            name="Region",
            description="region",
            dimension_id="dim_location",
            physical_column_ref="region",
            logical_type="STRING",
            aliases=("North", "north"),
            lineage_refs=("test",),
            provenance_refs=("test",),
        )
    attribute = SemanticAttribute(
        semantic_attribute_id="location_region",
        name="Region",
        description="region",
        dimension_id="dimension_location",
        physical_column_ref="region",
        logical_type="STRING",
        lineage_refs=("test",),
        provenance_refs=("test",),
    )
    duplicate = attribute.model_copy(update={"semantic_attribute_id": "location_region_2", "name": "region"})
    with pytest.raises(ValueError, match="names"):
        SemanticDimension(
            semantic_dimension_id="dimension_location",
            business_name="Location",
            description="location",
            physical_dimension_spec_id="dimension_location",
            physical_table_ref="dim_location",
            canonical_concept_ref="cet_location",
            canonical_entity_type_id="cet_location",
            role="CONFORMED",
            attributes=(attribute, duplicate),
            unknown_member_policy="EXPLICIT_UNKNOWN_MEMBER",
            scd_mode="TYPE1_SNAPSHOT",
            lineage_refs=("test",),
            provenance_refs=("test",),
        )
    with pytest.raises(ValueError, match="repeat"):
        DimensionHierarchy(
            hierarchy_id="location_hierarchy",
            dimension_id="dimension_location",
            levels=(
                HierarchyLevel(level_id="level_one", semantic_attribute_id="location_region", ordinal=1, name="Region"),
                HierarchyLevel(level_id="level_two", semantic_attribute_id="location_region", ordinal=2, name="Region again"),
            ),
            description="invalid hierarchy",
            domain_assertion_refs=("test",),
            provenance_refs=("test",),
            validation_state=SemanticHierarchyValidationState.INVALID,
        )


def test_same_physical_date_dimension_can_have_distinct_explicit_roles():
    order_role = TimeRole(
        time_role_id="fact_order_order_date_time_role",
        fact_id="fact_order",
        relationship_ref="rel_order_date",
        semantic_name="order_date",
        physical_date_column="order_date",
        date_dimension_id="dim_date",
        date_dimension_attribute_id="date_full_date",
        grain_id="grain_order",
        time_semantics="order event date",
        lineage_refs=("reviewed",),
        provenance_refs=("reviewed",),
    )
    ship_role = order_role.model_copy(update={
        "time_role_id": "fact_order_ship_date_time_role",
        "relationship_ref": "rel_ship_date",
        "semantic_name": "ship_date",
        "physical_date_column": "ship_date",
    })
    assert order_role.date_dimension_id == ship_role.date_dimension_id == "dim_date"
    assert order_role.relationship_ref != ship_role.relationship_ref


def test_explicit_unavailable_ratio_preserves_zero_and_unit_semantics():
    context, model = retail_semantic_flow()
    units = next(item for item in model.metrics if item.display_name == "Units Ordered")
    ratio = MetricSpec(
        metric_id="metric_units_per_event",
        semantic_name="units_per_event",
        display_name="Units per Event",
        description="Explicit ratio held for review because no reviewed event-count metric exists.",
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
        domain_assertion_refs=("review-required:no-event-count",),
        lineage_refs=("test",),
        provenance_refs=("test",),
        availability=SemanticAvailability.REVIEW_REQUIRED,
        failure_reason="REVIEW_REQUIRED: denominator metric is not reviewed",
    )
    projected = SemanticLayerService().build_model(
        context.plan,
        context.dimensions,
        context.facts,
        context.grains,
        context.measures,
        context.compiled_plan,
        context.materialization,
        context.canonical_model,
        analytical_review=context.analytical_review,
        derived_metrics=(ratio,),
    )
    result = next(item for item in projected.metrics if item.metric_id == ratio.metric_id)
    assert result.availability is SemanticAvailability.REVIEW_REQUIRED
    assert result.expression.zero_denominator_behavior is ZeroDenominatorBehavior.NULL


def test_step20_non_additive_measures_never_become_sum_metrics():
    _, model = retail_semantic_flow()
    by_name = {item.display_name: item for item in model.metrics}
    assert by_name["Units Ordered"].allowed_aggregation_operations == ("SUM",)
    assert by_name["Unit Price"].availability is SemanticAvailability.UNAVAILABLE
    assert by_name["Discount Rate"].availability is SemanticAvailability.UNAVAILABLE
    assert all("SUM" not in item.allowed_aggregation_operations for item in model.metrics if item.display_name != "Units Ordered")
    assert not any("revenue" in item.display_name.casefold() for item in model.metrics)


def test_available_derived_metric_is_rejected_by_v1_contract():
    with pytest.raises(ValueError, match="DERIVED_METRIC_NOT_EXECUTABLE_V1"):
        MetricSpec(
            metric_id="metric_available_ratio",
            semantic_name="available_ratio",
            display_name="Available Ratio",
            description="This must remain non-executable in V1.",
            metric_kind=SemanticMetricKind.DERIVED,
            fact_ids=("fact_order_line",),
            grain_ids=("grain_order_line_event_v1",),
            measure_ids=("measure_quantity",),
            expression=MetricExpression(
                expression_type=SemanticExpressionType.RATIO,
                numerator_metric_id="metric_units_ordered",
                denominator_metric_id="metric_units_ordered",
                zero_denominator_behavior=ZeroDenominatorBehavior.NULL,
                result_unit_semantics="units per event",
            ),
            unit_semantics="units per event",
            currency_semantics="NOT_APPLICABLE",
            domain_assertion_refs=("test:ratio",),
            lineage_refs=("test:ratio",),
            provenance_refs=("test:ratio",),
            availability=SemanticAvailability.AVAILABLE,
            allowed_aggregation_operations=("DIVIDE",),
        )
