"""Review-gated materialization service and its target port."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dirty_data_to_olap.application.compiler import AnalyticalCompilationError
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalInputFixture,
    CompiledPlan,
    GeneratedSQL,
    MaterializationArtifact,
    TargetConfig,
)


class MaterializerPort(Protocol):
    """Application-to-adapter boundary; no database connection crosses it."""

    def materialize(
        self,
        compiled_plan: CompiledPlan,
        generated_sql: GeneratedSQL,
        fixture: AnalyticalInputFixture,
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
        fixture: AnalyticalInputFixture,
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
        if fixture.fixture_id != binding.fixture_id or fixture.content_hash != binding.fixture_content_hash:
            raise AnalyticalCompilationError("materialization fixture is stale")
        if fixture.canonical_model_id != compiled_plan.canonical_model_id or fixture.canonical_model_content_hash != compiled_plan.canonical_model_content_hash:
            raise AnalyticalCompilationError("materialization fixture is not bound to the compiled canonical model")
        try:
            ReviewPolicyService().require_compatible(
                review_decision,
                ReviewPolicyService().materialization_context(compiled_plan, generated_sql, target_config),
            )
        except ReviewCompatibilityError as exc:
            raise AnalyticalCompilationError("REVIEW_MATERIALIZATION_PLAN_INCOMPATIBLE:" + ",".join(exc.errors)) from exc
        return self.materializer.materialize(compiled_plan, generated_sql, fixture, target_config, run_id=run_id)
