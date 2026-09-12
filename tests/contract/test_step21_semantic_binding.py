from datetime import datetime, timezone

import pytest

from dirty_data_to_olap.application.semantic_layer import SemanticLayerError, SemanticLayerService, SemanticStaleError
from dirty_data_to_olap.domain.contracts.analytical import MaterializationStatus
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus
from tests.step21_support import retail_semantic_flow


def test_missing_or_rejected_analytical_review_blocks_projection():
    context, _ = retail_semantic_flow()
    with pytest.raises(SemanticLayerError, match="REVIEW_REQUIRED"):
        SemanticLayerService().build_model(
            context.plan,
            context.dimensions,
            context.facts,
            context.grains,
            context.measures,
            context.compiled_plan,
            context.materialization,
            context.canonical_model,
        )
    rejected = context.analytical_review.model_copy(update={"decision": ReviewDecisionStatus.REJECTED})
    with pytest.raises(SemanticLayerError, match="REVIEW_REQUIRED"):
        SemanticLayerService().build_model(
            context.plan,
            context.dimensions,
            context.facts,
            context.grains,
            context.measures,
            context.compiled_plan,
            context.materialization,
            context.canonical_model,
            analytical_review=rejected,
        )


def test_stale_plan_compiled_plan_and_non_consumable_target_are_rejected():
    context, _ = retail_semantic_flow()
    service = SemanticLayerService()
    stale_plan = context.plan.model_copy(update={"plan_id": "stale-plan"})
    with pytest.raises(SemanticLayerError, match="STALE_ANALYTICAL_PLAN"):
        service.build_model(stale_plan, context.dimensions, context.facts, context.grains, context.measures, context.compiled_plan, context.materialization, context.canonical_model, analytical_review=context.analytical_review)
    stale_compiled = context.compiled_plan.model_copy(update={"plan_id": "stale-plan"})
    with pytest.raises(SemanticLayerError, match="STALE_ANALYTICAL_PLAN"):
        service.build_model(context.plan, context.dimensions, context.facts, context.grains, context.measures, stale_compiled, context.materialization, context.canonical_model, analytical_review=context.analytical_review)
    unusable = context.materialization.model_copy(update={"status": MaterializationStatus.FAILED, "usable": False})
    with pytest.raises(SemanticLayerError, match="NON_CONSUMABLE_TARGET"):
        service.build_model(context.plan, context.dimensions, context.facts, context.grains, context.measures, context.compiled_plan, unusable, context.canonical_model, analytical_review=context.analytical_review)


def test_semantic_model_rejects_changed_inputs_at_query_resolution():
    context, model = retail_semantic_flow()
    changed_plan = context.plan.model_copy(update={"plan_id": "changed-plan"})
    with pytest.raises(SemanticStaleError, match="STALE_ANALYTICAL_PLAN"):
        SemanticLayerService().resolve_query(model, __import__("dirty_data_to_olap.domain.contracts.semantic", fromlist=["SemanticQueryRequest"]).SemanticQueryRequest(request_id="q", metric_ids=(model.metrics[0].metric_id,)), analytical_plan=changed_plan)

