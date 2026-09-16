"""Explicit local composition for the Step27 backend reference runtime."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application.backend import BackendService, ExecutionSubmissionPort
from dirty_data_to_olap.application.execution_plan import ExecutionPlanService
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission
from dirty_data_to_olap.domain.contracts.platform import ResourceBudget
from dirty_data_to_olap.platform import LocalPlatform
from dirty_data_to_olap.observability import TelemetryClient


def build_local_backend(
    project_root: Path,
    *,
    resource_budget: ResourceBudget | None = None,
    execution: ExecutionSubmissionPort | None = None,
    telemetry: TelemetryClient | None = None,
) -> tuple[LocalPlatform, BackendService]:
    """Wire concrete Step23 stores to the Step27 application service."""

    platform = LocalPlatform.from_project_root(project_root, resource_budget=resource_budget)
    execution_port = execution or DurableExecutionSubmission(platform.control_store)
    architecture_root = Path(__file__).resolve().parents[3]
    execution_plan_service = ExecutionPlanService(project_root, platform.control_store, platform.artifact_store, graph_root=architecture_root)
    backend = BackendService(
        control_store=platform.control_store,
        artifact_store=platform.artifact_store,
        execution=execution_port,
        configuration_fingerprint=platform.config.configuration_fingerprint,
        execution_plan_service=execution_plan_service,
        telemetry=telemetry,
    )
    return platform, backend


__all__ = ["build_local_backend"]
