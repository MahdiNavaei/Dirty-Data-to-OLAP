"""Shared exact-Step20 setup used by the Step21 reference runs.

The helper deliberately delegates domain fixtures and analytical specification
construction to the existing Step20 reference modules.  Step21 only projects
those reviewed objects and never creates a parallel planner or materializer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
from dirty_data_to_olap.application.compiler import AnalyticalCompilerService
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalPlan,
    CompiledPlan,
    DimensionSpec,
    FactSpec,
    GeneratedSQL,
    GrainSpec,
    MeasureSpec,
    MaterializationArtifact,
    TargetConfig,
)
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel, ReviewDecision, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.source import stable_id


STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Step20Context:
    """The exact reviewed Step20 package consumed by the semantic layer."""

    canonical_model: CanonicalModel
    dataset: object
    binding: AnalyticalInputBinding
    plan: AnalyticalPlan
    dimensions: tuple[DimensionSpec, ...]
    facts: tuple[FactSpec, ...]
    grains: tuple[GrainSpec, ...]
    measures: tuple[MeasureSpec, ...]
    analytical_review: ReviewDecision
    compiled_plan: CompiledPlan
    generated_sql: GeneratedSQL
    materialization_review: ReviewDecision
    materialization: MaterializationArtifact


def _materialize(
    *,
    canonical_model: CanonicalModel,
    dataset: object,
    binding: AnalyticalInputBinding,
    plan: AnalyticalPlan,
    dimensions: tuple[DimensionSpec, ...],
    facts: tuple[FactSpec, ...],
    grains: tuple[GrainSpec, ...],
    measures: tuple[MeasureSpec, ...],
    run_directory: Path,
    run_id: str,
    analytical_actor: str,
    analytical_source: str,
    materialization_actor: str,
) -> Step20Context:
    policy = ReviewPolicyService()
    analytical_review = policy.create_decision(
        policy.analytical_plan_context(plan),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor=analytical_actor,
        actor_source=analytical_source,
        rationale="The exact Step20 analytical package is reviewed before semantic projection.",
        reviewed_at=STAMP,
    )
    target = TargetConfig(relative_path="target.duckdb")
    compiled_plan, generated_sql = AnalyticalCompilerService().compile(
        plan,
        dimensions,
        facts,
        grains,
        measures,
        binding,
        dataset,
        target,
        analytical_review,
        reviewed_at=STAMP,
    )
    materialization_review = policy.create_decision(
        policy.materialization_context(compiled_plan, generated_sql, target),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor=materialization_actor,
        actor_source="CONTROLLED_TARGET_REVIEW",
        rationale="The exact Step20 compiled SQL and controlled DuckDB target are approved for this reference run.",
        reviewed_at=STAMP,
    )
    materialization = MaterializationService(
        DuckDBMaterializer(run_directory, repository_root=ROOT)
    ).materialize(
        compiled_plan,
        generated_sql,
        materialization_review,
        binding,
        dataset,
        target,
        run_id=run_id,
    )
    if not materialization.usable:
        raise RuntimeError(materialization.failure_reason or "Step20 target is not usable")
    return Step20Context(
        canonical_model=canonical_model,
        dataset=dataset,
        binding=binding,
        plan=plan,
        dimensions=dimensions,
        facts=facts,
        grains=grains,
        measures=measures,
        analytical_review=analytical_review,
        compiled_plan=compiled_plan,
        generated_sql=generated_sql,
        materialization_review=materialization_review,
        materialization=materialization,
    )


def retail_context() -> Step20Context:
    """Build the current domain-reviewed retail Step20 package exactly."""

    from tools.run_step20_reference import build_canonical_model, build_fixture, build_reference_plan

    canonical_model = build_canonical_model()
    dataset = build_fixture(canonical_model)
    binding = AnalyticalInputBinding(
        binding_id=stable_id(
            "abind",
            {
                "fixture_id": dataset.fixture_id,
                "fixture_hash": dataset.content_hash,
                "model": canonical_model.content_hash,
            },
        ),
        canonical_model_id=canonical_model.model_id,
        canonical_model_content_hash=canonical_model.content_hash,
        fixture_id=dataset.fixture_id,
        fixture_content_hash=dataset.content_hash,
        source_schema_fingerprints={
            "crm": "schema-crm-step20",
            "erp": "schema-erp-step20",
            "sales": "schema-sales-step20",
        },
        row_counts=dataset.row_counts,
        provenance_refs=("typed-fixture-binding", "canonical-model-content-hash"),
    )
    plan, dimensions, fact, grain, measures = build_reference_plan(
        canonical_model, binding, dataset, created_at=STAMP
    )
    return _materialize(
        canonical_model=canonical_model,
        dataset=dataset,
        binding=binding,
        plan=plan,
        dimensions=tuple(dimensions),
        facts=(fact,),
        grains=(grain,),
        measures=tuple(measures),
        run_directory=ROOT / "workspace" / "runs" / "step20-reference-run" / "olap",
        run_id="step20-reference-run",
        analytical_actor="step21-retail-domain-review",
        analytical_source="DOMAIN_REVIEWED_BENCHMARK",
        materialization_actor="step21-retail-materialization-review",
    )


def generic_context() -> Step20Context:
    """Build the non-retail Device/Location/Reading Step20 package exactly."""

    from tools.run_step20_generic_reference import build_dataset, build_model, build_request

    canonical_model = build_model()
    dataset = build_dataset(canonical_model)
    binding = AnalyticalInputBinding(
        binding_id=stable_id(
            "abind",
            {
                "dataset": dataset.dataset_id,
                "dataset_hash": dataset.content_hash,
                "model": canonical_model.content_hash,
            },
        ),
        canonical_model_id=canonical_model.model_id,
        canonical_model_content_hash=canonical_model.content_hash,
        dataset_id=dataset.dataset_id,
        dataset_content_hash=dataset.content_hash,
        source_schema_fingerprints=dict(dataset.source_schema_fingerprints),
        source_snapshot_fingerprints=dict(dataset.source_snapshot_fingerprints),
        row_counts=dataset.row_counts,
        provenance_refs=("generic-dataset-binding", "canonical-model-content-hash"),
    )
    request = build_request(canonical_model)
    plan, dimensions, facts, grains, measures = AnalyticalPlannerService().build_plan(
        canonical_model, binding, dataset, request, created_at=STAMP
    )
    return _materialize(
        canonical_model=canonical_model,
        dataset=dataset,
        binding=binding,
        plan=plan,
        dimensions=tuple(dimensions),
        facts=tuple(facts),
        grains=tuple(grains),
        measures=tuple(measures),
        run_directory=ROOT / "workspace" / "runs" / "step20-generic-reference-run" / "olap",
        run_id="step20-generic-reference-run",
        analytical_actor="step21-generic-domain-review",
        analytical_source="SYNTHETIC_GENERIC_DOMAIN_REVIEW",
        materialization_actor="step21-generic-materialization-review",
    )
