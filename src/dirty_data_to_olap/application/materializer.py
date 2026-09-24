"""Review-gated materialization service and its target port."""

from __future__ import annotations

from typing import Protocol

from dirty_data_to_olap.application.compiler import AnalyticalCompilationError
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    CompiledPlan,
    GeneratedSQL,
    MaterializationArtifact,
    TargetConfig,
    as_analytical_dataset,
)


class MaterializerPort(Protocol):
    """Application-to-adapter boundary; no database connection crosses it."""

    def materialize(
        self,
        compiled_plan: CompiledPlan,
        generated_sql: GeneratedSQL,
        input_data: AnalyticalInputDataset,
        target_config: TargetConfig,
        *,
        run_id: str,
    ) -> MaterializationArtifact:
        ...


class MaterializationService:
    """Authorize one exact compiled target plan before invoking an adapter."""

    def __init__(self, materializer: MaterializerPort):
        self.materializer = materializer

    def materialize(
        self,
        compiled_plan: CompiledPlan,
        generated_sql: GeneratedSQL,
        review_decision,
        binding: AnalyticalInputBinding,
        input_data: AnalyticalInputDataset,
        target_config: TargetConfig,
        *,
        run_id: str,
    ) -> MaterializationArtifact:
        if compiled_plan.generated_sql_hash != generated_sql.sql_hash:
            raise AnalyticalCompilationError("generated SQL does not match compiled plan")
        if compiled_plan.target_config_fingerprint != target_config.config_fingerprint:
            raise AnalyticalCompilationError("target configuration does not match compiled plan")
        if binding.binding_id != compiled_plan.input_binding_id or binding.content_hash != compiled_plan.input_binding_content_hash:
            raise AnalyticalCompilationError("compiled plan input binding is stale")
        dataset = as_analytical_dataset(input_data)
        if binding.dataset_id is not None:
            if dataset.dataset_id != binding.dataset_id or dataset.content_hash != binding.dataset_content_hash:
                raise AnalyticalCompilationError("materialization dataset is stale")
        elif dataset.dataset_id != binding.fixture_id:
            raise AnalyticalCompilationError("materialization input does not match the binding")
        if dataset.canonical_model_id != compiled_plan.canonical_model_id or dataset.canonical_model_content_hash != compiled_plan.canonical_model_content_hash:
            raise AnalyticalCompilationError("materialization input is not bound to the compiled canonical model")
        try:
            ReviewPolicyService().require_compatible(
                review_decision,
                ReviewPolicyService().materialization_context(
                    compiled_plan,
                    generated_sql,
                    target_config,
                ).model_copy(update={"subject_content_hash": review_decision.subject_content_hash}),
            )
        except ReviewCompatibilityError as exc:
            raise AnalyticalCompilationError("REVIEW_MATERIALIZATION_PLAN_INCOMPATIBLE:" + ",".join(exc.errors)) from exc
        return self.materializer.materialize(compiled_plan, generated_sql, dataset, target_config, run_id=run_id)
