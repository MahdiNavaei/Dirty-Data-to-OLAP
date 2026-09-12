"""Shared exact-Step20 inputs for Step21 tests."""

from __future__ import annotations

from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from tools.step21_reference_support import Step20Context, generic_context, retail_context


def semantic_model(context: Step20Context):
    return SemanticLayerService().build_model(
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


def retail_semantic_flow():
    context = retail_context()
    return context, semantic_model(context)


def generic_semantic_flow():
    context = generic_context()
    return context, semantic_model(context)

