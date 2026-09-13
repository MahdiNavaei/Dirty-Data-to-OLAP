"""Application-owned phased preparation of durable execution plans."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application.jobs import ExecutionPlanSelectionError, load_authoritative_execution_plan
from dirty_data_to_olap.application.platform import (
    ArtifactConflictError,
    ArtifactStorePort,
    ConcurrencyConflictError,
    ControlStorePort,
    PlatformError,
)
from dirty_data_to_olap.application.planning_authority import ServerOwnedExecutionPlanSelectionResolver
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    ExecutionPlanIntent,
    ExecutionPlanPhase,
    ExecutionPlanPreparation,
    PlanPreparationStatus,
)
from dirty_data_to_olap.domain.contracts.platform import RunRecord
from dirty_data_to_olap.domain.contracts.source import stable_id


class ExecutionPlanService:
    """Prepare and durably advance a plan as runtime truth is produced."""

    BOOTSTRAP_STAGE_IDS = ("SOURCE_DISCOVERY",)
    SOURCE_PHASE_STAGE_IDS = (
        "SOURCE_DISCOVERY",
        "SOURCE_SNAPSHOT_STAGE",
        "PROFILING",
        "DEPENDENCY_DISCOVERY",
        "SCHEMA_MATCHING",
        "QUALITY_ANALYSIS",
        "OPTIONAL_SEMANTIC_EVIDENCE",
        "LEARNED_EVIDENCE",
        "EVIDENCE_FUSION",
        "REVIEW_EVIDENCE_DECISIONS",
        "CANONICAL_HYPOTHESES",
    )

    def __init__(self, project_root: Path, control_store: ControlStorePort, artifact_store: ArtifactStorePort, *, graph_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.graph_root = Path(graph_root or project_root).resolve()
        self.control_store = control_store
        self.selection_resolver = ServerOwnedExecutionPlanSelectionResolver(control_store, artifact_store, self.graph_root)

    def prepare(self, *, run: RunRecord, intent: ExecutionPlanIntent) -> ExecutionPlanPreparation:
        existing = self.control_store.get_execution_plan(run.run_id)
        if existing is not None:
            if existing.planning_intent is not None and existing.planning_intent.content_hash != intent.content_hash:
                return self._blocked(run.run_id, intent.content_hash, ("EXISTING_PLAN",), "run already has a durable plan bound to a different pre-execution intent")
            return self._preparation(existing, intent)

        full = self.selection_resolver.resolve(run=run, intent=intent)
        if full.selection is not None:
            plan = self._build(
                run=run,
                intent=intent,
                selection=full.selection,
                stage_ids=None,
                planning_phase=ExecutionPlanPhase.COMPLETE,
                pending_stage_ids=(),
                success_guard_required=True,
            )
            return self._register(plan, intent)
        if "PLAN_GRAPH" in full.unresolved_stage_ids:
            return self._blocked(run.run_id, intent.content_hash, full.unresolved_stage_ids, full.detail)

        source = self.selection_resolver.resolve(
            run=run,
            intent=intent,
            require_hypothesis=False,
            stage_ids=self.SOURCE_PHASE_STAGE_IDS,
        )
        if source.selection is not None:
            plan = self._build(
                run=run,
                intent=intent,
                selection=source.selection,
                stage_ids=self.SOURCE_PHASE_STAGE_IDS,
                planning_phase=ExecutionPlanPhase.SOURCE_RESOLVED,
                pending_stage_ids=("ENTITY_RESOLUTION",),
                success_guard_required=False,
            )
            return self._register(plan, intent)

        plan = self._build(
            run=run,
            intent=intent,
            selection=None,
            stage_ids=self.BOOTSTRAP_STAGE_IDS,
            planning_phase=ExecutionPlanPhase.BOOTSTRAP,
            pending_stage_ids=("SCHEMA_MATCHING", "ENTITY_RESOLUTION"),
            success_guard_required=False,
        )
        return self._register(plan, intent)

    def advance_after_stage(self, *, run_id: str, completed_stage_id: str) -> ExecutionPlan:
        """Advance only at the owner stage that produced the required truth."""

        current = self.control_store.get_execution_plan(run_id)
        run = self.control_store.get_run(run_id)
        if current is None or run is None:
            raise PlatformError("phased plan advancement requires a durable run plan")
        intent = current.planning_intent or ExecutionPlanIntent()

        if current.planning_phase is ExecutionPlanPhase.BOOTSTRAP and completed_stage_id == "SOURCE_DISCOVERY":
            resolution = self.selection_resolver.resolve(
                run=run,
                intent=intent,
                require_hypothesis=False,
                stage_ids=self.SOURCE_PHASE_STAGE_IDS,
            )
            if resolution.selection is None:
                pending = ("SCHEMA_MATCHING", "ENTITY_RESOLUTION")
                candidate = current.model_copy(update={"pending_stage_ids": pending, "revision": current.revision + 1})
            else:
                candidate = self._build(
                    run=run,
                    intent=intent,
                    selection=resolution.selection,
                    stage_ids=self.SOURCE_PHASE_STAGE_IDS,
                    planning_phase=ExecutionPlanPhase.SOURCE_RESOLVED,
                    pending_stage_ids=("ENTITY_RESOLUTION",),
                    success_guard_required=False,
                    plan_id=current.plan_id,
                    created_at=current.created_at,
                    revision=current.revision + 1,
                )
            return self._advance(current, candidate)

        if current.planning_phase is ExecutionPlanPhase.SOURCE_RESOLVED and completed_stage_id == "CANONICAL_HYPOTHESES":
            resolution = self.selection_resolver.resolve(run=run, intent=intent)
            if resolution.selection is None:
                candidate = current.model_copy(update={"pending_stage_ids": ("ENTITY_RESOLUTION",), "revision": current.revision + 1})
            else:
                candidate = self._build(
                    run=run,
                    intent=intent,
                    selection=resolution.selection,
                    stage_ids=None,
                    planning_phase=ExecutionPlanPhase.COMPLETE,
                    pending_stage_ids=(),
                    success_guard_required=True,
                    plan_id=current.plan_id,
                    created_at=current.created_at,
                    revision=current.revision + 1,
                )
            return self._advance(current, candidate)

        return current

    def _plan_id(self, run_id: str) -> str:
        return stable_id("execution-plan", {"run_id": run_id, "graph": "docs/architecture/specs/stage_graph.yml"})

    def _build(
        self,
        *,
        run: RunRecord,
        intent: ExecutionPlanIntent,
        selection,
        stage_ids,
        planning_phase: ExecutionPlanPhase,
        pending_stage_ids,
        success_guard_required: bool,
        plan_id: str | None = None,
        created_at=None,
        revision: int = 0,
    ) -> ExecutionPlan:
        plan = load_authoritative_execution_plan(
            self.graph_root,
            run_id=run.run_id,
            selection=selection,
            plan_id=plan_id or self._plan_id(run.run_id),
            stage_ids=stage_ids,
            planning_phase=planning_phase,
            planning_intent=intent,
            pending_stage_ids=pending_stage_ids,
            revision=revision,
            success_guard_required=success_guard_required,
        )
        return plan if created_at is None else plan.model_copy(update={"created_at": created_at})

    def _register(self, plan: ExecutionPlan, intent: ExecutionPlanIntent) -> ExecutionPlanPreparation:
        try:
            registered = self.control_store.register_execution_plan(plan)
        except ArtifactConflictError:
            existing = self.control_store.get_execution_plan(plan.run_id)
            if existing is None or existing.content_hash != plan.content_hash:
                return self._blocked(plan.run_id, intent.content_hash, ("EXISTING_PLAN",), "run already has a different immutable execution plan")
            registered = existing
        except PlatformError:
            return self._blocked(plan.run_id, intent.content_hash, ("PLAN_PERSISTENCE",), "execution plan could not be durably registered")
        return self._preparation(registered, intent)

    def _advance(self, current: ExecutionPlan, candidate: ExecutionPlan) -> ExecutionPlan:
        if current.content_hash == candidate.content_hash:
            return current
        try:
            return self.control_store.advance_execution_plan(candidate, expected_content_hash=current.content_hash)
        except ConcurrencyConflictError:
            latest = self.control_store.get_execution_plan(current.run_id)
            if latest is None:
                raise
            return latest

    @staticmethod
    def _preparation(plan: ExecutionPlan, intent: ExecutionPlanIntent) -> ExecutionPlanPreparation:
        return ExecutionPlanPreparation(
            run_id=plan.run_id,
            status=PlanPreparationStatus.READY,
            plan_id=plan.plan_id,
            selection_fingerprint=plan.selection.content_hash if plan.selection is not None else intent.content_hash,
            planning_phase=plan.planning_phase,
            detail="durable execution plan is ready; conditional truth may advance from runtime artifacts",
        )

    @staticmethod
    def _blocked(run_id: str, fingerprint: str, unresolved: tuple[str, ...], detail: str) -> ExecutionPlanPreparation:
        return ExecutionPlanPreparation(
            run_id=run_id,
            status=PlanPreparationStatus.BLOCKED,
            selection_fingerprint=fingerprint,
            unresolved_stage_ids=tuple(unresolved) or ("PLAN_PERSISTENCE",),
            detail=detail or "execution plan preparation is blocked",
        )


__all__ = ["ExecutionPlanService"]
