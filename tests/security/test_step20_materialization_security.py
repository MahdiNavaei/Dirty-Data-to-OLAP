from datetime import datetime, timezone

import pytest

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.domain.contracts.analytical import TargetConfig

from tests.step20_support import planned_flow


def test_materializer_rejects_target_outside_controlled_reference_path(tmp_path):
    with pytest.raises(ValueError):
        TargetConfig(relative_path="../../private.duckdb")
    materializer = DuckDBMaterializer(tmp_path, repository_root=tmp_path)
    _, _, _, _, _, _, _, _ = planned_flow()
    with pytest.raises(ValueError, match="controlled target"):
        materializer._target_path(TargetConfig(relative_path="other.duckdb"))


def test_materialization_service_requires_exact_binding_and_review():
    model, fixture, binding, plan, dimensions, fact, grain, measures = planned_flow()
    from dirty_data_to_olap.application.compiler import AnalyticalCompilerService
    from dirty_data_to_olap.application.review_policy import ReviewPolicyService
    from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus

    policy = ReviewPolicyService()
    analytical_review = policy.create_decision(policy.analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="test", reviewed_at=datetime(2026, 9, 11, tzinfo=timezone.utc))
    compiled, sql = AnalyticalCompilerService().compile(plan, dimensions, fact, grain, measures, binding, fixture, TargetConfig(relative_path="target.duckdb"), analytical_review)
    bad_binding = binding.model_copy(update={"fixture_content_hash": "stale"})
    with pytest.raises(ValueError, match="stale"):
        MaterializationService(object()).materialize(compiled, sql, analytical_review, bad_binding, fixture, TargetConfig(relative_path="target.duckdb"), run_id="test")


def test_compiler_fails_closed_on_surrogate_key_collision(monkeypatch):
    _, fixture, binding, plan, dimensions, fact, grain, measures = planned_flow()
    from dirty_data_to_olap.application.compiler import AnalyticalCompilationError, AnalyticalCompilerService
    from dirty_data_to_olap.application.review_policy import ReviewPolicyService
    from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus

    policy = ReviewPolicyService()
    review = policy.create_decision(policy.analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="test")
    monkeypatch.setattr("dirty_data_to_olap.application.compiler.deterministic_warehouse_key", lambda namespace, reference: 7)
    with pytest.raises(AnalyticalCompilationError, match="WAREHOUSE_KEY_COLLISION"):
        AnalyticalCompilerService().compile(plan, dimensions, fact, grain, measures, binding, fixture, TargetConfig(relative_path="target.duckdb"), review)
