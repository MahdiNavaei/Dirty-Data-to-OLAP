"""Application-owned preparation of run-specific durable execution plans."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application.jobs import ExecutionPlanSelectionError, load_authoritative_execution_plan
from dirty_data_to_olap.application.platform import ArtifactConflictError, ControlStorePort, PlatformError
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanPreparation, ExecutionPlanSelection, PlanPreparationStatus
from dirty_data_to_olap.domain.contracts.platform import RunRecord


class ExecutionPlanService:
    """Compile and durably register the authoritative graph for one run."""

    def __init__(self, project_root: Path, control_store: ControlStorePort, *, graph_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.graph_root = Path(graph_root or project_root).resolve()
        self.control_store = control_store

    def prepare(self, *, run: RunRecord, selection: ExecutionPlanSelection) -> ExecutionPlanPreparation:
        selection_fingerprint = selection.content_hash
        if selection.run_id != run.run_id:
            return ExecutionPlanPreparation(
                run_id=run.run_id,
                status=PlanPreparationStatus.BLOCKED,
                selection_fingerprint=selection_fingerprint,
                unresolved_stage_ids=("RUN_SCOPE",),
                detail="execution selection is bound to a different run",
            )
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
