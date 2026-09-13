"""Application-owned preparation of run-specific durable execution plans."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application.jobs import ExecutionPlanSelectionError, load_authoritative_execution_plan
from dirty_data_to_olap.application.platform import ArtifactConflictError, ArtifactStorePort, ControlStorePort, PlatformError
from dirty_data_to_olap.application.planning_authority import ServerOwnedExecutionPlanSelectionResolver
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanIntent, ExecutionPlanPreparation, PlanPreparationStatus
from dirty_data_to_olap.domain.contracts.platform import RunRecord


class ExecutionPlanService:
    """Compile and durably register the authoritative graph for one run."""

    def __init__(self, project_root: Path, control_store: ControlStorePort, artifact_store: ArtifactStorePort, *, graph_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.graph_root = Path(graph_root or project_root).resolve()
        self.control_store = control_store
        self.selection_resolver = ServerOwnedExecutionPlanSelectionResolver(control_store, artifact_store, self.graph_root)

    def prepare(self, *, run: RunRecord, intent: ExecutionPlanIntent) -> ExecutionPlanPreparation:
        resolution = self.selection_resolver.resolve(run=run, intent=intent)
        if resolution.selection is None:
            return ExecutionPlanPreparation(
                run_id=run.run_id,
                status=PlanPreparationStatus.BLOCKED,
                selection_fingerprint=intent.content_hash,
                unresolved_stage_ids=resolution.unresolved_stage_ids,
                detail=resolution.detail,
            )
        selection = resolution.selection
        selection_fingerprint = selection.content_hash
        try:
            plan = load_authoritative_execution_plan(self.graph_root, run_id=run.run_id, selection=selection)
        except ExecutionPlanSelectionError as exc:
            return ExecutionPlanPreparation(
                run_id=run.run_id,
                status=PlanPreparationStatus.BLOCKED,
                selection_fingerprint=selection_fingerprint,
                unresolved_stage_ids=exc.unresolved_stage_ids,
                detail=str(exc),
            )
        except (FileNotFoundError, OSError, ValueError):
            return ExecutionPlanPreparation(
                run_id=run.run_id,
                status=PlanPreparationStatus.BLOCKED,
                selection_fingerprint=selection_fingerprint,
                unresolved_stage_ids=("PLAN_GRAPH",),
                detail="authoritative execution graph could not be loaded or validated",
            )
        try:
            registered = self.control_store.register_execution_plan(plan)
        except ArtifactConflictError as exc:
            existing = self.control_store.get_execution_plan(run.run_id)
            if existing is None or existing.selection is None or existing.selection.content_hash != selection_fingerprint:
                return ExecutionPlanPreparation(
                    run_id=run.run_id,
                    status=PlanPreparationStatus.BLOCKED,
                    selection_fingerprint=selection_fingerprint,
                    unresolved_stage_ids=("EXISTING_PLAN",),
                    detail="run already has a different immutable execution plan",
                )
            registered = existing
        except PlatformError as exc:
            return ExecutionPlanPreparation(
                run_id=run.run_id,
                status=PlanPreparationStatus.BLOCKED,
                selection_fingerprint=selection_fingerprint,
                unresolved_stage_ids=("PLAN_PERSISTENCE",),
                detail="execution plan could not be durably registered",
            )
        return ExecutionPlanPreparation(
            run_id=run.run_id,
            status=PlanPreparationStatus.READY,
            plan_id=registered.plan_id,
            selection_fingerprint=selection_fingerprint,
            detail="authoritative run-specific execution plan is durably registered",
        )


__all__ = ["ExecutionPlanService"]
