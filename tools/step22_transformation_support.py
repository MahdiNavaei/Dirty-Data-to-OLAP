"""Independent Step22 transformation fixtures.

This module is deliberately free of the QA oracle. It builds the representative
source fixture, runs the project-owned canonical finalizer, passes the finalized
model into the Step20 planner/compiler and materializer, and derives
stage-scoped accounting from those runtime objects. The oracle is loaded by a
separate QA module after this path completes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
from dirty_data_to_olap.application.canonical import (
    CanonicalFinalizationService,
    CanonicalHypothesisService,
    CanonicalIdentityProposalService,
)
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalCell,
    AnalyticalColumnBinding,
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalInputTable,
    AnalyticalRowBatch,
    AnalyticalPlanningRequest,
    DimensionSpec,
    FactSpec,
    GrainSpec,
    MeasureSpec,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalIdentityMembership,
    CanonicalModel,
    CanonicalRelationship,
    CanonicalSourceTable,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
    RecordDisposition,
    ReviewDecisionStatus,
)
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    DecisionExplanation,
    DecisionState,
    FusionScore,
    RelationshipDecision,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingArtifact,
    RecordAccountingEntry,
    RecordAccountingScope,
    record_accounting_id,
)
from tools.run_step20_reference import build_fixture, build_reference_plan
from tools.run_step20_generic_reference import build_request
from tools.step21_reference_support import Step20Context, _materialize


STAMP = datetime(2026, 9, 12, tzinfo=timezone.utc)
POLICY_VERSION = "step22-runtime-accounting-v2"

RETAIL_SNAPSHOT_ID = "step22-retail-source-snapshot-v2"
RETAIL_SCHEMA = {"crm": "schema-crm-step22", "erp": "schema-erp-step22", "sales": "schema-sales-step22"}
GENERIC_SNAPSHOT_ID = "step22-generic-source-snapshot-v2"
GENERIC_SCHEMA = {"telemetry": "schema-telemetry-step22"}


@dataclass(frozen=True)
class _RelationshipSpec:
    relationship_id: str
    from_type: str
    to_type: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str = "MANY_TO_ONE"


@dataclass(frozen=True)
class _SourceFixture:
    name: str
    snapshot_id: str
    schema_fingerprints: Mapping[str, str]
    metadata: Mapping[str, Mapping[str, str]]
    customer_refs: tuple[str, ...] = ()
    product_refs: tuple[str, ...] = ()
    branch_refs: tuple[str, ...] = ()
    order_refs: tuple[str, ...] = ()
    line_refs: tuple[str, ...] = ()
    device_refs: tuple[str, ...] = ()
    location_refs: tuple[str, ...] = ()
    reading_refs: tuple[str, ...] = ()


def _relationship_decisions(
    specs: Sequence[_RelationshipSpec],
) -> tuple[tuple[CanonicalRelationship, ...], tuple[RelationshipDecision, ...], tuple[Any, ...]]:
    fusion_policy = EvidenceFusionService.load_policy()
    review_policy = ReviewPolicyService()
    relationships: list[CanonicalRelationship] = []
    decisions: list[RelationshipDecision] = []
    reviews: list[Any] = []
    for spec in specs:
        decision_id = f"step22-reviewed-{spec.relationship_id}"
        decision = RelationshipDecision(
            decision_id=decision_id,
            candidate_id=f"candidate-{spec.relationship_id}",
            subject_id=spec.relationship_id,
            from_table=spec.from_table,
            from_columns=(spec.from_column,),
            to_table=spec.to_table,
            to_columns=(spec.to_column,),
            proposed_cardinality=spec.cardinality,
            score=FusionScore(
                value=0.7,
                eligible_weight=1.0,
                observed_weight=1.0,
                evidence_coverage=1.0,
                sufficient=True,
                contributions={"step22_domain_review": 0.7},
            ),
            confidence_band="HIGH",
            decision_state=DecisionState.REVIEW_REQUIRED,
            policy=fusion_policy,
            supporting_signal_refs=(f"signal-{spec.relationship_id}",),
            explanation=DecisionExplanation(
                supports=(f"step22:domain:{spec.relationship_id}",),
                limitations=("reviewed synthetic relationship evidence",),
            ),
            input_evidence_fingerprint=f"step22:evidence:{spec.relationship_id}",
            provenance="step22-independent-transformation-fixture",
        )
        domain_ref = f"step22:domain:{spec.relationship_id}"
        review = review_policy.create_decision(
            review_policy.evidence_context(decision, (domain_ref,)),
            decision=ReviewDecisionStatus.ACCEPTED,
            actor="step22-domain-review",
            actor_source="STEP22_REVIEWED_TRANSFORMATION_FIXTURE",
            rationale=f"Reviewed source-backed relationship {spec.relationship_id} for the representative V1 fixture.",
            reviewed_at=STAMP,
        )
        relationships.append(
            CanonicalRelationship(
                relationship_id=spec.relationship_id,
                from_entity_type_id=spec.from_type,
                to_entity_type_id=spec.to_type,
                cardinality=spec.cardinality,
                upstream_decision_ref=decision_id,
                review_decision_ref=review.review_decision_id,
                provenance_refs=("step22:canonical-finalization", domain_ref),
            )
        )
        decisions.append(decision)
        reviews.append(review)
    return tuple(relationships), tuple(decisions), tuple(reviews)


def _entity_type(
    *,
    entity_type_id: str,
    semantic_id: str,
    business_name: str,
    kind: CanonicalEntityKind,
    source_tables: tuple[CanonicalSourceTable, ...],
    relationship_refs: tuple[str, ...],
) -> CanonicalEntityType:
    return CanonicalEntityType(
        canonical_entity_type_id=entity_type_id,
        semantic_id=semantic_id,
        business_name=business_name,
        kind=kind,
        entity_resolution_family=semantic_id,
        identity_strategy=("human-reviewed-consolidation" if kind is CanonicalEntityKind.IDENTITY else "source-local-event-identity"),
        source_table_refs=source_tables,
        relationship_refs=relationship_refs,
        domain_assertion_refs=(f"step22:domain:{semantic_id}",),
        review_state="FINALIZED_BY_STEP19_REVIEW",
        provenance_refs=("step22:source-fixture", "step19:canonical-finalization"),
    )


def _finalize_retail(record_accounting_refs: tuple[str, ...]) -> tuple[CanonicalModel, _SourceFixture]:
    snapshot = RETAIL_SNAPSHOT_ID
    table = lambda source, table_id, schema: CanonicalSourceTable(
        source_id=source,
        snapshot_id=snapshot,
        table_id=table_id,
        schema_fingerprint=schema,
    )
    relationships, decisions, evidence_reviews = _relationship_decisions(
        (
            _RelationshipSpec("rel_order_customer", "cet_order", "cet_customer", "orders", "customer_ref", "customers", "customer_ref"),
            _RelationshipSpec("rel_order_branch", "cet_order", "cet_branch", "orders", "branch_ref", "branches", "branch_ref"),
            _RelationshipSpec("rel_orderline_order", "cet_order_line", "cet_order", "order_lines", "order_ref", "orders", "order_ref"),
            _RelationshipSpec("rel_orderline_product", "cet_order_line", "cet_product", "order_lines", "product_ref", "products", "product_ref"),
            _RelationshipSpec("rel_payment_order", "cet_payment", "cet_order", "payments", "order_ref", "orders", "order_ref"),
        )
    )
    entity_types = (
        _entity_type(entity_type_id="cet_customer", semantic_id="customer", business_name="Customer", kind=CanonicalEntityKind.IDENTITY, source_tables=(table("crm", "customers", RETAIL_SCHEMA["crm"]), table("erp", "customer_aliases", RETAIL_SCHEMA["erp"])), relationship_refs=("rel_order_customer",)),
        _entity_type(entity_type_id="cet_product", semantic_id="product", business_name="Product", kind=CanonicalEntityKind.IDENTITY, source_tables=(table("erp", "products", RETAIL_SCHEMA["erp"]),), relationship_refs=("rel_orderline_product",)),
        _entity_type(entity_type_id="cet_branch", semantic_id="branch", business_name="Branch", kind=CanonicalEntityKind.IDENTITY, source_tables=(table("erp", "branches", RETAIL_SCHEMA["erp"]),), relationship_refs=("rel_order_branch",)),
        _entity_type(entity_type_id="cet_order", semantic_id="order", business_name="Order", kind=CanonicalEntityKind.EVENT, source_tables=(table("sales", "orders", RETAIL_SCHEMA["sales"]),), relationship_refs=("rel_order_customer", "rel_order_branch", "rel_orderline_order")),
        _entity_type(entity_type_id="cet_order_line", semantic_id="orderline", business_name="OrderLine", kind=CanonicalEntityKind.EVENT, source_tables=(table("sales", "order_lines", RETAIL_SCHEMA["sales"]),), relationship_refs=("rel_orderline_order", "rel_orderline_product")),
        _entity_type(entity_type_id="cet_payment", semantic_id="payment", business_name="Payment", kind=CanonicalEntityKind.EVENT, source_tables=(table("sales", "payments", RETAIL_SCHEMA["sales"]),), relationship_refs=("rel_payment_order",)),
    )
    requirements = {item.semantic_id: EntityResolutionRequirement.ER_NOT_REQUIRED for item in entity_types}
    policy = ReviewPolicyService()
    hypothesis = CanonicalHypothesisService(policy).build(
        run_id="step22-retail-transformation-run",
        execution_context_id="step22-retail-transformation-context",
        model_version="step22-canonical-v2",
        evidence_reviews=evidence_reviews,
        relationship_decisions=decisions,
        evidence_domain_assertion_refs={
            decision.decision_id: (f"step22:domain:{relationship.relationship_id}",)
            for relationship, decision in zip(relationships, decisions)
        },
        entity_types=entity_types,
        source_ids=tuple(RETAIL_SCHEMA),
        domain_assertion_refs=("step22:domain:retail",),
        entity_resolution_requirements=requirements,
        relationships=relationships,
        snapshot_fingerprints={source: snapshot for source in RETAIL_SCHEMA},
        source_schema_fingerprints=RETAIL_SCHEMA,
        evidence_refs=tuple(item.decision_id for item in decisions) + ("step22:domain:retail",),
        provenance_refs=("step22:independent-transformation-fixture", "step19:canonical-finalization"),
        created_at=STAMP,
    )
    customer_members = (
        ("customer-alice", "cet_customer", ("crm-customer-1", "erp-customer-alias-1"), IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW),
        ("customer-bardia", "cet_customer", ("crm-customer-2",), IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW),
        ("product-1", "cet_product", ("erp-product-1",), IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW),
        ("product-2", "cet_product", ("erp-product-2",), IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW),
        ("branch-1", "cet_branch", ("erp-branch-1",), IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW),
        ("branch-2", "cet_branch", ("erp-branch-2",), IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW),
        ("order-1", "cet_order", ("sales-order-1",), IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY),
        ("order-2", "cet_order", ("sales-order-2",), IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY),
        ("line-1-1", "cet_order_line", ("sales-order-line-1-1",), IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY),
        ("line-1-2", "cet_order_line", ("sales-order-line-1-2",), IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY),
        ("line-2-1", "cet_order_line", ("sales-order-line-2-1",), IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY),
    )
    memberships = tuple(
        CanonicalIdentityMembership(
            membership_group_id=group,
            canonical_entity_type_id=type_id,
            entity_resolution_family=next(item.semantic_id for item in entity_types if item.canonical_entity_type_id == type_id),
            source_record_refs=refs,
            derivation_basis=basis,
            actor="step22-domain-review" if basis is IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW else None,
            actor_source="STEP22_REVIEWED_TRANSFORMATION_FIXTURE" if basis is IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW else None,
            domain_assertion_refs=(f"step22:domain:{group}",) if basis is IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW else (),
            evidence_refs=(f"step22:evidence:{group}",),
            policy_refs=("step22-human-identity-v1" if basis is IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW else "step22-source-local-event-v1",),
            rationale=("two independently maintained customer representations are explicitly reviewed as one identity" if group == "customer-alice" else "explicitly reviewed representative transformation membership"),
            provenance_refs=("step22:source-fixture", "step19:canonical-finalization"),
        )
        for group, type_id, refs, basis in customer_members
    )
    proposal = CanonicalIdentityProposalService().build(
        hypothesis=hypothesis,
        memberships=memberships,
        policy_refs=("step22-canonical-identity-v1",),
        provenance_refs=("step22:identity-review",),
        created_at=STAMP,
    )
    finalizer = CanonicalFinalizationService(policy)
    identity_review = policy.create_decision(
        finalizer.identity_context(hypothesis, proposal),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step22-canonical-review",
        actor_source="STEP22_REVIEWED_TRANSFORMATION_FIXTURE",
        rationale="Exact Step19 identity proposal, including the reviewed customer consolidation, is accepted.",
        reviewed_at=STAMP,
    )
    metadata = {
        "crm-customer-1": {"source_id": "crm", "snapshot_id": snapshot, "table_id": "customers"},
        "erp-customer-alias-1": {"source_id": "erp", "snapshot_id": snapshot, "table_id": "customer_aliases"},
        "crm-customer-2": {"source_id": "crm", "snapshot_id": snapshot, "table_id": "customers"},
        "erp-product-1": {"source_id": "erp", "snapshot_id": snapshot, "table_id": "products"},
        "erp-product-2": {"source_id": "erp", "snapshot_id": snapshot, "table_id": "products"},
        "erp-branch-1": {"source_id": "erp", "snapshot_id": snapshot, "table_id": "branches"},
        "erp-branch-2": {"source_id": "erp", "snapshot_id": snapshot, "table_id": "branches"},
        "sales-order-1": {"source_id": "sales", "snapshot_id": snapshot, "table_id": "orders"},
        "sales-order-2": {"source_id": "sales", "snapshot_id": snapshot, "table_id": "orders"},
        "sales-order-line-1-1": {"source_id": "sales", "snapshot_id": snapshot, "table_id": "order_lines"},
        "sales-order-line-1-2": {"source_id": "sales", "snapshot_id": snapshot, "table_id": "order_lines"},
        "sales-order-line-2-1": {"source_id": "sales", "snapshot_id": snapshot, "table_id": "order_lines"},
    }
    model = finalizer.finalize(
        hypothesis=hypothesis,
        identity_proposal=proposal,
        identity_review=identity_review,
        source_record_metadata=metadata,
        record_accounting_refs=record_accounting_refs,
        lineage_refs=("step22:canonical-source-membership", "step22:canonical-event-identity"),
        finalized_at=STAMP,
    )
    fixture = build_fixture_from_canonical(model, "retail")
    source = _SourceFixture(
        name="retail",
        snapshot_id=snapshot,
        schema_fingerprints=RETAIL_SCHEMA,
        metadata=metadata,
        customer_refs=("crm-customer-1", "erp-customer-alias-1", "crm-customer-2"),
        product_refs=("erp-product-1", "erp-product-2"),
        branch_refs=("erp-branch-1", "erp-branch-2"),
        order_refs=("sales-order-1", "sales-order-2"),
        line_refs=("sales-order-line-1-1", "sales-order-line-1-2", "sales-order-line-2-1"),
    )
    return model, source


def _finalize_generic(record_accounting_refs: tuple[str, ...]) -> tuple[CanonicalModel, _SourceFixture]:
    snapshot = GENERIC_SNAPSHOT_ID
    table = lambda table_id, schema: CanonicalSourceTable(source_id="telemetry", snapshot_id=snapshot, table_id=table_id, schema_fingerprint=schema)
    relationships, decisions, evidence_reviews = _relationship_decisions(
        (
            _RelationshipSpec("rel_reading_device", "cet_reading", "cet_device", "readings", "device_ref", "devices", "device_ref"),
            _RelationshipSpec("rel_reading_location", "cet_reading", "cet_location", "readings", "location_ref", "locations", "location_ref"),
        )
    )
    entity_types = (
        _entity_type(entity_type_id="cet_device", semantic_id="device", business_name="Device", kind=CanonicalEntityKind.IDENTITY, source_tables=(table("devices", GENERIC_SCHEMA["telemetry"]),), relationship_refs=("rel_reading_device",)),
        _entity_type(entity_type_id="cet_location", semantic_id="location", business_name="Location", kind=CanonicalEntityKind.IDENTITY, source_tables=(table("locations", GENERIC_SCHEMA["telemetry"]),), relationship_refs=("rel_reading_location",)),
        _entity_type(entity_type_id="cet_reading", semantic_id="reading", business_name="Reading", kind=CanonicalEntityKind.EVENT, source_tables=(table("readings", GENERIC_SCHEMA["telemetry"]),), relationship_refs=("rel_reading_device", "rel_reading_location")),
    )
    policy = ReviewPolicyService()
    hypothesis = CanonicalHypothesisService(policy).build(
        run_id="step22-generic-transformation-run",
        execution_context_id="step22-generic-transformation-context",
        model_version="step22-generic-canonical-v2",
        evidence_reviews=evidence_reviews,
        relationship_decisions=decisions,
        evidence_domain_assertion_refs={
            decision.decision_id: (f"step22:domain:{relationship.relationship_id}",)
            for relationship, decision in zip(relationships, decisions)
        },
        entity_types=entity_types,
        source_ids=("telemetry",),
        domain_assertion_refs=("step22:domain:generic",),
        entity_resolution_requirements={item.semantic_id: EntityResolutionRequirement.ER_NOT_REQUIRED for item in entity_types},
        relationships=relationships,
        snapshot_fingerprints={"telemetry": snapshot},
        source_schema_fingerprints=GENERIC_SCHEMA,
        evidence_refs=tuple(item.decision_id for item in decisions) + ("step22:domain:generic",),
        provenance_refs=("step22:independent-transformation-fixture", "step19:canonical-finalization"),
        created_at=STAMP,
    )
    member_specs = (
        ("device-1", "cet_device", ("telemetry-device-1",)),
        ("device-2", "cet_device", ("telemetry-device-2",)),
        ("location-1", "cet_location", ("telemetry-location-1",)),
        ("location-2", "cet_location", ("telemetry-location-2",)),
        ("reading-1", "cet_reading", ("telemetry-reading-1",)),
        ("reading-2", "cet_reading", ("telemetry-reading-2",)),
        ("reading-3", "cet_reading", ("telemetry-reading-3",)),
    )
    memberships = tuple(
        CanonicalIdentityMembership(
            membership_group_id=group,
            canonical_entity_type_id=type_id,
            entity_resolution_family=next(item.semantic_id for item in entity_types if item.canonical_entity_type_id == type_id),
            source_record_refs=refs,
            derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY,
            evidence_refs=(f"step22:evidence:{group}",),
            policy_refs=("step22-source-local-event-v1",),
            rationale="explicit source-local identity for the reviewed generic telemetry fixture",
            provenance_refs=("step22:source-fixture", "step19:canonical-finalization"),
        )
        for group, type_id, refs in member_specs
    )
    proposal = CanonicalIdentityProposalService().build(
        hypothesis=hypothesis,
        memberships=memberships,
        policy_refs=("step22-canonical-identity-v1",),
        provenance_refs=("step22:identity-review",),
        created_at=STAMP,
    )
    finalizer = CanonicalFinalizationService(policy)
    identity_review = policy.create_decision(
        finalizer.identity_context(hypothesis, proposal),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step22-generic-canonical-review",
        actor_source="STEP22_REVIEWED_TRANSFORMATION_FIXTURE",
        rationale="Exact Step19 generic identity proposal is accepted for the representative V1 flow.",
        reviewed_at=STAMP,
    )
    metadata = {
        "telemetry-device-1": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "devices"},
        "telemetry-device-2": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "devices"},
        "telemetry-location-1": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "locations"},
        "telemetry-location-2": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "locations"},
        "telemetry-reading-1": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "readings"},
        "telemetry-reading-2": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "readings"},
        "telemetry-reading-3": {"source_id": "telemetry", "snapshot_id": snapshot, "table_id": "readings"},
    }
    model = finalizer.finalize(
        hypothesis=hypothesis,
        identity_proposal=proposal,
        identity_review=identity_review,
        source_record_metadata=metadata,
        record_accounting_refs=record_accounting_refs,
        lineage_refs=("step22:canonical-source-membership", "step22:canonical-event-identity"),
        finalized_at=STAMP,
    )
    return model, _SourceFixture(
        name="generic",
        snapshot_id=snapshot,
        schema_fingerprints=GENERIC_SCHEMA,
        metadata=metadata,
        device_refs=("telemetry-device-1", "telemetry-device-2"),
        location_refs=("telemetry-location-1", "telemetry-location-2"),
        reading_refs=("telemetry-reading-1", "telemetry-reading-2", "telemetry-reading-3"),
    )


def _instance_by_ref(model: CanonicalModel) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for item in model.source_record_maps:
        result[item.record_ref] = item.canonical_entity_id
    return result


def build_fixture_from_canonical(model: CanonicalModel, name: str):
    """Create the downstream typed fixture only from finalized runtime IDs."""

    from dirty_data_to_olap.domain.contracts.analytical import (
        AnalyticalInputFixture,
        BranchFixtureRow,
        CustomerFixtureRow,
        OrderFixtureRow,
        OrderLineFixtureRow,
        ProductFixtureRow,
    )

    ids = _instance_by_ref(model)
    if name == "retail":
        customers = (
            CustomerFixtureRow(canonical_entity_id=ids["crm-customer-1"], customer_code="C-001", display_name="Alice", source_record_refs=("crm-customer-1", "erp-customer-alias-1")),
            CustomerFixtureRow(canonical_entity_id=ids["crm-customer-2"], customer_code="C-002", display_name="Bardia", source_record_refs=("crm-customer-2",)),
        )
        products = (
            ProductFixtureRow(canonical_entity_id=ids["erp-product-1"], product_code="P-001", product_name="Notebook", category="Stationery", source_record_refs=("erp-product-1",)),
            ProductFixtureRow(canonical_entity_id=ids["erp-product-2"], product_code="P-002", product_name="Pen", category="Stationery", source_record_refs=("erp-product-2",)),
        )
        branches = (
            BranchFixtureRow(canonical_entity_id=ids["erp-branch-1"], branch_code="B-001", branch_name="Central", source_record_refs=("erp-branch-1",)),
            BranchFixtureRow(canonical_entity_id=ids["erp-branch-2"], branch_code="B-002", branch_name="North", source_record_refs=("erp-branch-2",)),
        )
        orders = (
            OrderFixtureRow(canonical_entity_id=ids["sales-order-1"], order_event_id="order-event-1", customer_entity_id=ids["crm-customer-1"], branch_entity_id=ids["erp-branch-1"], order_date=date(2026, 1, 2), source_record_refs=("sales-order-1",)),
            OrderFixtureRow(canonical_entity_id=ids["sales-order-2"], order_event_id="order-event-2", customer_entity_id=ids["crm-customer-2"], branch_entity_id=ids["erp-branch-2"], order_date=date(2026, 1, 3), source_record_refs=("sales-order-2",)),
        )
        lines = (
            OrderLineFixtureRow(canonical_entity_id=ids["sales-order-line-1-1"], order_event_id="order-event-1", line_sequence=1, product_entity_id=ids["erp-product-1"], quantity=Decimal("2"), unit_price=Decimal("10.00"), discount_rate=Decimal("0.10"), source_record_refs=("sales-order-line-1-1",)),
            OrderLineFixtureRow(canonical_entity_id=ids["sales-order-line-1-2"], order_event_id="order-event-1", line_sequence=2, product_entity_id=ids["erp-product-2"], quantity=Decimal("1"), unit_price=Decimal("20.00"), discount_rate=Decimal("0.05"), source_record_refs=("sales-order-line-1-2",)),
            OrderLineFixtureRow(canonical_entity_id=ids["sales-order-line-2-1"], order_event_id="order-event-2", line_sequence=1, product_entity_id=ids["erp-product-1"], quantity=Decimal("3"), unit_price=Decimal("12.00"), discount_rate=Decimal("0"), source_record_refs=("sales-order-line-2-1",)),
        )
        return AnalyticalInputFixture(
            fixture_id="step22-retail-transformation-fixture-v2",
            canonical_model_id=model.model_id,
            canonical_model_content_hash=model.content_hash,
            customers=customers,
            products=products,
            branches=branches,
            orders=orders,
            order_lines=lines,
            provenance_refs=("step22:source-fixture", "step19:canonical-finalization", "no-qa-truth-input"),
        )
    raise ValueError("retail fixture requested for non-retail transformation")


def build_generic_dataset(model: CanonicalModel) -> AnalyticalInputDataset:
    ids = _instance_by_ref(model)

    def row(ref: str, values: tuple[tuple[str, Any], ...], source_ref: str) -> AnalyticalInputRow:
        return AnalyticalInputRow(
            row_ref=source_ref,
            canonical_reference=ref,
            values=tuple(AnalyticalCell(column_name=name, value=value) for name, value in values),
            source_record_refs=(source_ref,),
            lineage_refs=("step22:source-fixture", source_ref),
        )

    def table(table_id: str, concept: str, columns: tuple[tuple[str, str], ...], rows: tuple[AnalyticalInputRow, ...]) -> AnalyticalInputTable:
        bindings = tuple(AnalyticalColumnBinding(column_id=f"step22:{table_id}:{name}", column_name=name, logical_type=logical_type, lineage_refs=("step22:source-fixture",)) for name, logical_type in columns)
        batch = AnalyticalRowBatch(batch_id=f"step22-generic:{table_id}:0", table_id=table_id, rows=rows, source_batch_refs=(f"step22:{table_id}",), source_snapshot_fingerprints={"telemetry": GENERIC_SNAPSHOT_ID}, lineage_refs=("step22:source-fixture",))
        return AnalyticalInputTable(table_id=table_id, canonical_concept_ref=concept, columns=bindings, batches=(batch,), source_table_refs=(table_id,), lineage_refs=("step22:source-fixture",))

    devices = tuple(row(ids[ref], (("device_code", code), ("device_name", label)), ref) for ref, code, label in (("telemetry-device-1", "D-001", "Pump A"), ("telemetry-device-2", "D-002", "Pump B")))
    locations = tuple(row(ids[ref], (("location_code", code), ("region", region)), ref) for ref, code, region in (("telemetry-location-1", "L-001", "north"), ("telemetry-location-2", "L-002", "south")))
    readings = tuple(
        row(ids[ref], (("reading_id", reading_id), ("device_ref", ids[device_ref]), ("location_ref", ids[location_ref]), ("observed_on", observed_on), ("temperature", temperature)), ref)
        for ref, reading_id, device_ref, location_ref, observed_on, temperature in (
            ("telemetry-reading-1", "reading-1", "telemetry-device-1", "telemetry-location-1", date(2026, 2, 1), Decimal("10.5")),
            ("telemetry-reading-2", "reading-2", "telemetry-device-1", "telemetry-location-1", date(2026, 2, 2), Decimal("11.0")),
            ("telemetry-reading-3", "reading-3", "telemetry-device-2", "telemetry-location-2", date(2026, 2, 1), Decimal("9.5")),
        )
    )
    return AnalyticalInputDataset(
        dataset_id="step22-generic-transformation-dataset-v2",
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        tables=(
            table("devices", "device", (("device_code", "STRING"), ("device_name", "STRING")), devices),
            table("locations", "location", (("location_code", "STRING"), ("region", "STRING")), locations),
            table("readings", "reading", (("reading_id", "STRING"), ("device_ref", "STRING"), ("location_ref", "STRING"), ("observed_on", "DATE"), ("temperature", "DECIMAL")), readings),
        ),
        source_schema_fingerprints=GENERIC_SCHEMA,
        source_snapshot_fingerprints={"telemetry": GENERIC_SNAPSHOT_ID},
        allow_literal_sql=False,
        provenance_refs=("step22:source-fixture", "step19:canonical-finalization", "no-qa-truth-input"),
    )


def _source_fixture_metadata(name: str) -> _SourceFixture:
    if name == "retail":
        _, fixture = _finalize_retail(("step22:runtime-accounting-placeholder",))
        return fixture
    if name == "generic":
        _, fixture = _finalize_generic(("step22:runtime-accounting-placeholder",))
        return fixture
    raise ValueError(name)


def build_runtime_accounting(
    model: CanonicalModel,
    dataset: AnalyticalInputDataset,
    dimensions: Sequence[DimensionSpec],
    facts: Sequence[FactSpec],
    grains: Sequence[GrainSpec],
    run_id: str,
    source_snapshot_id: str,
) -> RecordAccountingArtifact:
    """Derive both accounting scopes exclusively from runtime artifacts."""

    source_entries = tuple(
        RecordAccountingEntry(
            input_record_ref=item.record_ref,
            disposition=item.terminal_disposition,
            output_or_group_ref=item.canonical_entity_id,
            transformation_or_policy_ref=item.identity_decision_ref,
            reason=item.disposition_reason,
            provenance_refs=("CanonicalFinalizationService", "step22:runtime-source-accounting"),
        )
        for item in sorted(model.source_record_maps, key=lambda current: current.record_ref)
    )
    source_scope = RecordAccountingScope(
        scope_id=f"{run_id}:source-to-canonical",
        boundary=AccountingBoundary.SOURCE_TO_CANONICAL,
        input_object_ref=source_snapshot_id,
        input_record_refs=tuple(item.input_record_ref for item in source_entries),
        entries=source_entries,
        policy_version=POLICY_VERSION,
        provenance_refs=("CanonicalFinalizationService", "step22:runtime-source-accounting"),
    )
    dimension_by_type = {item.canonical_entity_type_id: item for item in dimensions}
    fact_by_event_type = {item.canonical_event_type_id: item for item in facts}
    dataset_rows = {table.table_id: table.rows for table in dataset.tables}
    canonical_entries: list[RecordAccountingEntry] = []
    for instance in sorted(model.instances, key=lambda current: current.canonical_entity_id):
        dimension = dimension_by_type.get(instance.canonical_entity_type_id)
        fact = fact_by_event_type.get(instance.canonical_entity_type_id)
        if dimension is not None:
            disposition = RecordDisposition.EMITTED_DIRECT
            output = f"{dimension.table_name}:{instance.canonical_entity_id}"
            policy_ref = "AnalyticalInputDataset:dimension-materialization"
            reason = "canonical dimension instance is emitted by the reviewed analytical plan"
        elif fact is not None:
            rows = [row for row in dataset_rows.get(fact.input_table_id, ()) if row.canonical_reference == instance.canonical_entity_id]
            if not rows:
                raise ValueError(f"runtime fact input is missing canonical instance {instance.canonical_entity_id}")
            row = rows[0].value_map
            grain = next(item for item in grains if item.grain_id == fact.grain_spec_id)
            key = ":".join(str(row.get(column)) for column in grain.key_columns)
            disposition = RecordDisposition.EMITTED_DIRECT
            output = f"{fact.table_name}:{key}"
            policy_ref = "AnalyticalPlan:fact-materialization"
            reason = "canonical event instance is emitted by the reviewed analytical fact plan"
        else:
            disposition = RecordDisposition.FILTERED_EXPLICIT
            output = None
            policy_ref = "AnalyticalPlan:explicit-parent-scope"
            reason = "canonical event is retained in the analytical input context but is not materialized as a fact by the reviewed plan"
        canonical_entries.append(
            RecordAccountingEntry(
                input_record_ref=instance.canonical_entity_id,
                disposition=disposition,
                output_or_group_ref=output,
                transformation_or_policy_ref=policy_ref,
                reason=reason,
                provenance_refs=("AnalyticalInputDataset", "AnalyticalPlan", "step22:runtime-canonical-accounting"),
            )
        )
    canonical_scope = RecordAccountingScope(
        scope_id=f"{run_id}:canonical-to-analytical",
        boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL,
        input_object_ref=model.model_id,
        input_record_refs=tuple(item.input_record_ref for item in canonical_entries),
        entries=tuple(canonical_entries),
        policy_version=POLICY_VERSION,
        provenance_refs=("AnalyticalInputDataset", "AnalyticalPlan", "step22:runtime-canonical-accounting"),
    )
    payload = {"run_id": run_id, "policy_version": POLICY_VERSION, "scopes": [source_scope.content_hash, canonical_scope.content_hash]}
    return RecordAccountingArtifact(
        accounting_id=record_accounting_id(payload),
        run_id=run_id,
        scopes=(source_scope, canonical_scope),
        policy_version=POLICY_VERSION,
        provenance_refs=("CanonicalFinalizationService", "AnalyticalInputDataset", "AnalyticalPlan", "step22:runtime-accounting"),
    )


def _context_once(name: str, model: CanonicalModel, source: _SourceFixture) -> tuple[Step20Context, RecordAccountingArtifact]:
    if name == "retail":
        fixture = build_fixture_from_canonical(model, name)
        dataset = fixture.to_analytical_dataset().model_copy(update={
            "source_schema_fingerprints": dict(source.schema_fingerprints),
            "source_snapshot_fingerprints": {key: source.snapshot_id for key in source.schema_fingerprints},
            "allow_literal_sql": False,
            "provenance_refs": fixture.provenance_refs + ("step22:runtime-analytical-input",),
        })
        binding = AnalyticalInputBinding(
            binding_id=stable_id("abind", {"dataset": dataset.dataset_id, "dataset_hash": dataset.content_hash, "model": model.content_hash}),
            canonical_model_id=model.model_id,
            canonical_model_content_hash=model.content_hash,
            dataset_id=dataset.dataset_id,
            dataset_content_hash=dataset.content_hash,
            source_schema_fingerprints=dict(source.schema_fingerprints),
            source_snapshot_fingerprints={key: source.snapshot_id for key in source.schema_fingerprints},
            row_counts=dataset.row_counts,
            provenance_refs=("step22:runtime-analytical-input", "canonical-model-content-hash"),
        )
        plan, dimensions, facts, grains, measures = build_reference_plan(model, binding, fixture, input_dataset=dataset, created_at=STAMP)
        context = _materialize(
            canonical_model=model,
            dataset=dataset,
            binding=binding,
            plan=plan,
            dimensions=tuple(dimensions),
            facts=(facts,),
            grains=(grains,),
            measures=tuple(measures),
            run_directory=ROOT / "workspace" / "runs" / "step20-reference-run" / "olap",
            run_id="step22-retail-runtime-run",
            analytical_actor="step22-retail-analytical-review",
            analytical_source="STEP22_REVIEWED_TRANSFORMATION_FIXTURE",
            materialization_actor="step22-retail-materialization-review",
        )
        accounting = build_runtime_accounting(model, dataset, dimensions, (facts,), (grains,), "step22-retail-runtime-run", source.snapshot_id)
        return context, accounting
    if name == "generic":
        dataset = build_generic_dataset(model)
        binding = AnalyticalInputBinding(
            binding_id=stable_id("abind", {"dataset": dataset.dataset_id, "dataset_hash": dataset.content_hash, "model": model.content_hash}),
            canonical_model_id=model.model_id,
            canonical_model_content_hash=model.content_hash,
            dataset_id=dataset.dataset_id,
            dataset_content_hash=dataset.content_hash,
            source_schema_fingerprints=dict(source.schema_fingerprints),
            source_snapshot_fingerprints={"telemetry": source.snapshot_id},
            row_counts=dataset.row_counts,
            provenance_refs=("step22:runtime-analytical-input", "canonical-model-content-hash"),
        )
        request = build_request(model)
        plan, dimensions, facts, grains, measures = AnalyticalPlannerService().build_plan(model, binding, dataset, request, created_at=STAMP)
        context = _materialize(
            canonical_model=model,
            dataset=dataset,
            binding=binding,
            plan=plan,
            dimensions=tuple(dimensions),
            facts=tuple(facts),
            grains=tuple(grains),
            measures=tuple(measures),
            run_directory=ROOT / "workspace" / "runs" / "step20-generic-reference-run" / "olap",
            run_id="step22-generic-runtime-run",
            analytical_actor="step22-generic-analytical-review",
            analytical_source="STEP22_REVIEWED_TRANSFORMATION_FIXTURE",
            materialization_actor="step22-generic-materialization-review",
        )
        accounting = build_runtime_accounting(model, dataset, dimensions, tuple(facts), tuple(grains), "step22-generic-runtime-run", source.snapshot_id)
        return context, accounting
    raise ValueError(name)


def build_transformation_context(name: str) -> tuple[Step20Context, RecordAccountingArtifact, _SourceFixture]:
    """Run the actual Step19-to-Step21 transformation path for one family."""

    if name == "retail":
        placeholder_model, placeholder_source = _finalize_retail(("step22:runtime-accounting-placeholder",))
        _, placeholder_accounting = _context_once(name, placeholder_model, placeholder_source)
        model, source = _finalize_retail((placeholder_accounting.accounting_id,))
    elif name == "generic":
        placeholder_model, placeholder_source = _finalize_generic(("step22:runtime-accounting-placeholder",))
        _, placeholder_accounting = _context_once(name, placeholder_model, placeholder_source)
        model, source = _finalize_generic((placeholder_accounting.accounting_id,))
    else:
        raise ValueError(name)
    context, accounting = _context_once(name, model, source)
    if model.record_accounting_refs != (accounting.accounting_id,):
        raise ValueError("canonical model is not bound to the actual runtime accounting artifact")
    return context, accounting, source
