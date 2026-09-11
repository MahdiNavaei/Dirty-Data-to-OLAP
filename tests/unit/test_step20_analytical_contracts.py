from decimal import Decimal

import pytest

from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalInputFixture,
    DimensionAttributeSpec,
    DimensionRole,
    DimensionSpec,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    SCDMode,
    SCDPolicySpec,
    TargetConfig,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
    WarehouseKeySpec,
    deterministic_warehouse_key,
)

from tests.step20_support import planned_flow


def test_warehouse_keys_are_deterministic_and_namespaced():
    assert deterministic_warehouse_key("dim_customer", "customer-1") == deterministic_warehouse_key("dim_customer", "customer-1")
    assert deterministic_warehouse_key("dim_customer", "customer-1") != deterministic_warehouse_key("dim_product", "customer-1")
    assert deterministic_warehouse_key("dim_customer", "customer-1") != hash("customer-1")


def test_reference_plan_has_explicit_validated_grain_and_measure_classes():
    _, _, _, plan, dimensions, fact, grain, measures = planned_flow()
    assert grain.validated is True
    assert grain.human_readable_grain.startswith("one product line")
    assert grain.key_columns == ("order_event_id", "line_sequence")
    assert {item.field_name: item.aggregation_class for item in measures} == {
        "quantity": AggregationClass.ADDITIVE,
        "unit_price": AggregationClass.NON_ADDITIVE,
        "discount_rate": AggregationClass.NON_ADDITIVE,
    }
    assert {item.table_name for item in dimensions} == {"dim_customer", "dim_product", "dim_branch", "dim_date"}
    assert fact.table_name == "fact_order_line"
    assert "payment" in plan.deferred_concept_reasons[next(iter(plan.deferred_concept_reasons))]
    assert not any("revenue" in item.semantic_name.casefold() for item in measures)


def test_duplicate_grain_is_rejected_before_contract_acceptance():
    _, fixture, _, _, _, _, _, _ = planned_flow()
    with pytest.raises(ValueError, match="duplicate order-line fixture grain"):
        AnalyticalInputFixture.model_validate({**fixture.model_dump(mode="python"), "order_lines": fixture.order_lines + (fixture.order_lines[0],)})


def test_scd_and_unknown_policies_are_explicit():
    policy = SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="snapshot rebuild")
    unknown = UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="fail closed")
    assert policy.overwrite_current_snapshot is True
    assert unknown.unknown_member_key is None
    with pytest.raises(ValueError):
        SCDPolicySpec(mode=SCDMode.TYPE2_PREPARED, rationale="missing history fields")


def test_contracts_reject_unsafe_identifiers_and_target_traversal():
    with pytest.raises(ValueError):
        TargetConfig(relative_path="../outside.duckdb")
    with pytest.raises(ValueError):
        WarehouseKeySpec(key_name="CustomerKey", namespace="dim_customer")
    with pytest.raises(ValueError):
        DimensionAttributeSpec(attribute_id="x", column_name="display-name", logical_type="STRING", lineage_refs=("x",))
