"""Review-gated analytical planning over the finalized canonical graph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalInputBinding,
    AnalyticalInputFixture,
    AnalyticalPlan,
    AnalyticalReviewState,
    BranchFixtureRow,
    CustomerFixtureRow,
    DimensionAttributeSpec,
    DimensionRole,
    DimensionSpec,
    FactForeignKeySpec,
    FactSpec,
    FactType,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    OrderLineFixtureRow,
    ProductFixtureRow,
    SCDMode,
    SCDPolicySpec,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
    WarehouseKeySpec,
    analytical_plan_id,
)
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel
from dirty_data_to_olap.domain.contracts.source import stable_digest


class AnalyticalPlanningError(ValueError):
    """The canonical graph or typed input cannot support a safe plan."""


def _find_entity_type(canonical_model: CanonicalModel, semantic_id: str) -> str:
    wanted = semantic_id.casefold()
    for entity in canonical_model.entity_types:
        if entity.semantic_id.casefold() == wanted or entity.business_name.casefold() == wanted:
            return entity.canonical_entity_type_id
    raise AnalyticalPlanningError(f"required canonical concept is missing: {semantic_id}")


def _find_optional_entity_type(canonical_model: CanonicalModel, semantic_id: str) -> str | None:
    try:
        return _find_entity_type(canonical_model, semantic_id)
    except AnalyticalPlanningError:
        return None


class AnalyticalPlannerService:
    """Create explicit, review-required fact/dimension plans.

    This service deliberately does not guess a warehouse model from column
    names.  The reference plan is a typed domain fixture backed by a finalized
    canonical graph and domain-reviewed relationship references.
    """

    PLAN_VERSION = "analytical-plan-v1"
    POLICY_VERSION = "analytical-planning-policy-v1"

    @staticmethod
    def validate_order_line_grain(
        rows: Sequence[OrderLineFixtureRow],
        *,
        fact_id: str,
        grain_id: str,
        evidence_refs: tuple[str, ...],
        provenance_refs: tuple[str, ...],
    ) -> GrainSpec:
        if not rows:
            raise AnalyticalPlanningError("fact grain cannot be validated on an empty input")
        keys = [(row.order_event_id, row.line_sequence) for row in rows]
        if any(not order_id or line_sequence is None for order_id, line_sequence in keys):
            raise AnalyticalPlanningError("fact grain contains a null key")
        duplicates = len(keys) - len(set(keys))
        if duplicates:
            raise AnalyticalPlanningError(f"duplicate fact grain keys: {duplicates}")
        fingerprint = stable_digest({
            "fact_id": fact_id,
            "key_columns": ["order_event_id", "line_sequence"],
            "keys": sorted(keys),
        })
        return GrainSpec(
            grain_id=grain_id,
            fact_id=fact_id,
            human_readable_grain="one product line in one order event, identified by (order_event_id, line_sequence)",
            key_columns=("order_event_id", "line_sequence"),
            null_policy=GrainNullPolicy.REJECT_NULLS,
            validated=True,
            observed_row_count=len(rows),
            duplicate_key_count=0,
            validation_fingerprint=fingerprint,
            evidence_refs=evidence_refs,
            provenance_refs=provenance_refs,
        )

    def build_reference_plan(
        self,
        canonical_model: CanonicalModel,
        binding: AnalyticalInputBinding,
        fixture: AnalyticalInputFixture,
        *,
        created_at: datetime | None = None,
    ) -> tuple[AnalyticalPlan, tuple[DimensionSpec, ...], FactSpec, GrainSpec, tuple[MeasureSpec, ...]]:
        if binding.canonical_model_id != canonical_model.model_id or binding.canonical_model_content_hash != canonical_model.content_hash:
            raise AnalyticalPlanningError("analytical input binding is not bound to the exact canonical model")
        if fixture.fixture_id != binding.fixture_id or fixture.content_hash != binding.fixture_content_hash:
            raise AnalyticalPlanningError("analytical fixture does not match its input binding")
        if not fixture.customers or not fixture.products or not fixture.branches or not fixture.orders or not fixture.order_lines:
            raise AnalyticalPlanningError("reference analytical planning requires all typed fixture row families")

        customer_type = _find_entity_type(canonical_model, "customer")
        product_type = _find_entity_type(canonical_model, "product")
        branch_type = _find_entity_type(canonical_model, "branch")
        order_type = _find_entity_type(canonical_model, "order")
        order_line_type = _find_entity_type(canonical_model, "orderline")
        payment_type = _find_optional_entity_type(canonical_model, "payment")
        if payment_type:
            deferred = (payment_type,)
            deferred_reasons = {payment_type: "payment remains a candidate/review-required concept; no automatic fact is materialized in Step20"}
        else:
            deferred = ()
            deferred_reasons = {}

        fact_id = "fact_order_line"
        grain_id = "grain_order_line_event_v1"
        lineage = tuple(sorted({ref for row in (*fixture.customers, *fixture.products, *fixture.branches, *fixture.orders, *fixture.order_lines) for ref in row.source_record_refs}))
        common_provenance = ("benchmark:domain-reviewed", "step20:typed-fixture", "canonical:finalized-model")
        grain = self.validate_order_line_grain(
            fixture.order_lines,
            fact_id=fact_id,
            grain_id=grain_id,
            evidence_refs=("domain-reviewed:orderline-grain", "fixture:unique-composite-grain"),
            provenance_refs=common_provenance,
        )
        dim_specs = self._dimensions(customer_type, product_type, branch_type, common_provenance)
        measures = (
            MeasureSpec(
                measure_id="measure_quantity",
                fact_id=fact_id,
                field_name="quantity",
                semantic_name="units ordered",
                aggregation_class=AggregationClass.ADDITIVE,
                aggregation_rule="SUM(quantity) at validated OrderLine grain",
                unit_semantics="counted product units",
                currency_semantics="NOT_APPLICABLE",
                nullable=False,
                domain_assertion_refs=("domain-reviewed:orderline-quantity-additive",),
                provenance_refs=common_provenance,
            ),
            MeasureSpec(
                measure_id="measure_unit_price",
                fact_id=fact_id,
                field_name="unit_price",
                semantic_name="unit price",
                aggregation_class=AggregationClass.NON_ADDITIVE,
                aggregation_rule="preserve at line grain; never SUM across lines",
                unit_semantics="price per product unit; currency not established",
                currency_semantics="UNSPECIFIED_NOT_REVENUE",
                domain_assertion_refs=("domain-reviewed:unit-price-non-additive",),
                provenance_refs=common_provenance,
            ),
            MeasureSpec(
                measure_id="measure_discount_rate",
                fact_id=fact_id,
                field_name="discount_rate",
                semantic_name="discount rate",
                aggregation_class=AggregationClass.NON_ADDITIVE,
                aggregation_rule="preserve at line grain; no additive aggregation",
                unit_semantics="rate",
                currency_semantics="NOT_APPLICABLE",
                domain_assertion_refs=("domain-reviewed:discount-rate-non-additive",),
                provenance_refs=common_provenance,
            ),
        )
        facts = FactSpec(
            fact_id=fact_id,
            table_name="fact_order_line",
            fact_type=FactType.TRANSACTION,
            canonical_event_type_id=order_line_type,
            canonical_event_refs=(order_line_type, order_type),
            grain_spec_id=grain.grain_id,
            dimension_foreign_keys=(
                FactForeignKeySpec(relationship_ref="rel_order_customer", dimension_id="dim_customer", fact_column="customer_key", dimension_key_column="customer_key", canonical_entity_type_id=customer_type),
                FactForeignKeySpec(relationship_ref="rel_orderline_product", dimension_id="dim_product", fact_column="product_key", dimension_key_column="product_key", canonical_entity_type_id=product_type),
                FactForeignKeySpec(relationship_ref="rel_order_branch", dimension_id="dim_branch", fact_column="branch_key", dimension_key_column="branch_key", canonical_entity_type_id=branch_type),
                FactForeignKeySpec(relationship_ref="rel_order_date", dimension_id="dim_date", fact_column="date_key", dimension_key_column="date_key", canonical_entity_type_id="synthetic_date_dimension"),
            ),
            degenerate_dimension_columns=("order_event_id",),
            measure_ids=tuple(item.measure_id for item in measures),
            date_role_columns=("order_date",),
            relationship_refs=("rel_order_customer", "rel_orderline_order", "rel_orderline_product", "rel_order_branch", "rel_order_date"),
            provenance_refs=common_provenance,
        )
        accepted_relationships = tuple(sorted(item.relationship_id for item in canonical_model.relationships))
        required_relationships = {"rel_order_customer", "rel_orderline_order", "rel_orderline_product", "rel_order_branch"}
        if not required_relationships.issubset(set(accepted_relationships)):
            missing = sorted(required_relationships - set(accepted_relationships))
            raise AnalyticalPlanningError("accepted canonical relationships missing: " + ",".join(missing))
        if canonical_model.unresolved_items:
            unresolved = tuple(sorted(canonical_model.unresolved_items))
        else:
            unresolved = ()
        entity_ids = tuple(sorted({customer_type, product_type, branch_type, order_type, order_line_type, *deferred}))
        event_ids = tuple(sorted({order_type, order_line_type, *deferred}))
        plan_payload = {
            "version": self.PLAN_VERSION,
            "canonical_model_id": canonical_model.model_id,
            "canonical_model_content_hash": canonical_model.content_hash,
            "input_binding_id": binding.binding_id,
            "input_binding_content_hash": binding.content_hash,
            "dimensions": [item.model_dump(mode="json") for item in dim_specs],
            "fact": facts.model_dump(mode="json"),
            "grain": grain.model_dump(mode="json"),
            "measures": [item.model_dump(mode="json") for item in measures],
            "relationships": accepted_relationships,
            "deferred": deferred,
            "unresolved": unresolved,
        }
        plan = AnalyticalPlan(
            plan_id=analytical_plan_id(plan_payload),
            plan_version=self.PLAN_VERSION,
            canonical_model_id=canonical_model.model_id,
            canonical_model_content_hash=canonical_model.content_hash,
            canonical_model_fingerprint=canonical_model.content_hash,
            input_binding_id=binding.binding_id,
            input_binding_content_hash=binding.content_hash,
            canonical_entity_type_ids=entity_ids,
            canonical_event_type_ids=event_ids,
            accepted_relationship_refs=accepted_relationships,
            materialized_dimension_ids=tuple(item.dimension_id for item in dim_specs),
            materialized_fact_ids=(facts.fact_id,),
            grain_spec_ids=(grain.grain_id,),
            measure_spec_ids=tuple(item.measure_id for item in measures),
            source_record_lineage_refs=lineage or ("lineage:typed-fixture",),
            canonical_conflict_refs=tuple(sorted(item.conflict_id for item in canonical_model.conflicts)),
            deferred_concept_refs=deferred,
            deferred_concept_reasons=deferred_reasons,
            domain_assertion_refs=("domain-reviewed:customer-order-product-branch", "domain-reviewed:orderline-quantity-additive", "domain-reviewed:measure-semantics"),
            source_schema_fingerprints=dict(binding.source_schema_fingerprints),
            policy_version=self.POLICY_VERSION,
            review_state=AnalyticalReviewState.REVIEW_REQUIRED,
            unresolved_items=unresolved,
            lineage_refs=("lineage:canonical-model", "lineage:typed-input-binding", *lineage),
            provenance_refs=common_provenance,
            created_at=created_at or datetime.now(timezone.utc),
        )
        return plan, dim_specs, facts, grain, measures

    @staticmethod
    def _dimensions(customer_type: str, product_type: str, branch_type: str, provenance: tuple[str, ...]) -> tuple[DimensionSpec, ...]:
        type1 = SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="V1 rebuilds a current snapshot; no history is fabricated")
        unknown = UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unresolved dimension references are quarantined rather than silently assigned")
        return (
            DimensionSpec(
                dimension_id="dim_customer",
                table_name="dim_customer",
                canonical_entity_type_id=customer_type,
                canonical_entity_refs=(customer_type,),
                role=DimensionRole.CONFORMED,
                eligibility_reason="descriptive customer entity referenced by reviewed order events",
                surrogate_key=WarehouseKeySpec(key_name="customer_key", namespace="dim_customer"),
                alternate_key_columns=("customer_code",),
                attributes=(
                    DimensionAttributeSpec(attribute_id="customer_code", column_name="customer_code", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="display_name", column_name="display_name", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="source_record_refs", column_name="source_record_refs", logical_type="STRING_LIST", nullable=False, lineage_refs=provenance),
                ),
                scd_policy=type1,
                unknown_member_policy=unknown,
                conformed_dimension_id="dim_customer",
                provenance_refs=provenance,
            ),
            DimensionSpec(
                dimension_id="dim_product",
                table_name="dim_product",
                canonical_entity_type_id=product_type,
                canonical_entity_refs=(product_type,),
                role=DimensionRole.CONFORMED,
                eligibility_reason="descriptive product entity referenced by reviewed order lines",
                surrogate_key=WarehouseKeySpec(key_name="product_key", namespace="dim_product"),
                alternate_key_columns=("product_code",),
                attributes=(
                    DimensionAttributeSpec(attribute_id="product_code", column_name="product_code", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="product_name", column_name="product_name", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="category", column_name="category", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="source_record_refs", column_name="source_record_refs", logical_type="STRING_LIST", nullable=False, lineage_refs=provenance),
                ),
                scd_policy=type1,
                unknown_member_policy=unknown,
                conformed_dimension_id="dim_product",
                provenance_refs=provenance,
            ),
            DimensionSpec(
                dimension_id="dim_branch",
                table_name="dim_branch",
                canonical_entity_type_id=branch_type,
                canonical_entity_refs=(branch_type,),
                role=DimensionRole.CONFORMED,
                eligibility_reason="descriptive branch entity referenced by reviewed order events",
                surrogate_key=WarehouseKeySpec(key_name="branch_key", namespace="dim_branch"),
                alternate_key_columns=("branch_code",),
                attributes=(
                    DimensionAttributeSpec(attribute_id="branch_code", column_name="branch_code", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="branch_name", column_name="branch_name", logical_type="STRING", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="source_record_refs", column_name="source_record_refs", logical_type="STRING_LIST", nullable=False, lineage_refs=provenance),
                ),
                scd_policy=type1,
                unknown_member_policy=unknown,
                conformed_dimension_id="dim_branch",
                provenance_refs=provenance,
            ),
            DimensionSpec(
                dimension_id="dim_date",
                table_name="dim_date",
                canonical_entity_type_id="synthetic_date_dimension",
                canonical_entity_refs=("order_date",),
                role=DimensionRole.DATE,
                eligibility_reason="bounded deterministic Gregorian date role for the reviewed order event",
                surrogate_key=WarehouseKeySpec(key_name="date_key", namespace="dim_date"),
                alternate_key_columns=("full_date",),
                attributes=(
                    DimensionAttributeSpec(attribute_id="full_date", column_name="full_date", logical_type="DATE", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="year", column_name="year", logical_type="INTEGER", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="quarter", column_name="quarter", logical_type="INTEGER", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="month", column_name="month", logical_type="INTEGER", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="day", column_name="day", logical_type="INTEGER", nullable=False, lineage_refs=provenance),
                    DimensionAttributeSpec(attribute_id="weekday", column_name="weekday", logical_type="INTEGER", nullable=False, lineage_refs=provenance),
                ),
                scd_policy=type1,
                unknown_member_policy=unknown,
                conformed_dimension_id="dim_date",
                provenance_refs=provenance,
            ),
        )
