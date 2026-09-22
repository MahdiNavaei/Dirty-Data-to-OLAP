from datetime import datetime, timezone

import pytest

from dirty_data_to_olap.application.compiler import AnalyticalCompilationError, AnalyticalCompilerService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import AggregationClass, TargetConfig
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus

from tests.step20_support import planned_flow
from tests.product_acceptance.prompt02_control_evidence import record_control_observation


STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)


def test_exact_analytical_review_context_binds_plan_and_canonical_model():
    model, _, _, plan, *_ = planned_flow()
    context = ReviewPolicyService().analytical_plan_context(plan)
    assert context.subject_artifact_id == plan.plan_id
    assert context.subject_content_hash == plan.content_hash
    assert context.source_schema_fingerprints["canonical_model_id"] == model.model_id
    changed = plan.model_copy(update={"measure_spec_ids": (*plan.measure_spec_ids, "measure-new")})
    assert not ReviewPolicyService().create_decision(context, decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="test", reviewed_at=STAMP).is_compatible(ReviewPolicyService().analytical_plan_context(changed))


def test_compiler_rejects_stale_or_non_accepted_analytical_review():
    model, fixture, binding, plan, dimensions, fact, grain, measures = planned_flow()
    policy = ReviewPolicyService()
    context = policy.analytical_plan_context(plan)
    rejected = policy.create_decision(context, decision=ReviewDecisionStatus.REJECTED, actor="test", rationale="not approved", reviewed_at=STAMP)
    with pytest.raises(AnalyticalCompilationError, match="INCOMPATIBLE"):
        AnalyticalCompilerService().compile(plan, dimensions, fact, grain, measures, binding, fixture, TargetConfig(relative_path="target.duckdb"), rejected, reviewed_at=STAMP)


def test_compiler_rejects_missing_dimension_reference_and_measure_reclassification():
    model, fixture, binding, plan, dimensions, fact, grain, measures = planned_flow()
    policy = ReviewPolicyService()
    review = policy.create_decision(policy.analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="test", reviewed_at=STAMP)
    missing_customer = fixture.orders[0].model_copy(update={"customer_entity_id": "missing-customer"})
    bad_fixture = fixture.model_copy(update={"orders": (missing_customer, *fixture.orders[1:])})
    with pytest.raises(AnalyticalCompilationError, match="MISSING_DIMENSION_REFERENCE"):
        AnalyticalCompilerService._validate_references(bad_fixture)
    bad_quantity = measures[0].model_copy(update={"aggregation_class": AggregationClass.NON_ADDITIVE})
    with pytest.raises(AnalyticalCompilationError, match="quantity"):
        AnalyticalCompilerService().compile(plan, dimensions, fact, grain, (bad_quantity, *measures[1:]), binding, fixture, TargetConfig(relative_path="target.duckdb"), review, reviewed_at=STAMP)
    undeclared_revenue = measures[0].model_copy(update={
        "semantic_name": "revenue",
        "unit_semantics": "currency amount",
        "currency_semantics": "UNDECLARED",
    })
    with pytest.raises(AnalyticalCompilationError, match="SPEC_PACKAGE_STALE"):
        AnalyticalCompilerService().compile(plan, dimensions, fact, grain, (undeclared_revenue, *measures[1:]), binding, fixture, TargetConfig(relative_path="target.duckdb"), review, reviewed_at=STAMP)
    record_control_observation("NC09", "AnalyticalCompilationError:SPEC_PACKAGE_STALE", "tests/contract/test_step20_review_and_compile.py:52")
