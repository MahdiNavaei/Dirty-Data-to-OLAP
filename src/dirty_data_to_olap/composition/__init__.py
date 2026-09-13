"""Explicit local composition for the Step27 backend reference runtime."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application.backend import BackendService, ExecutionSubmissionPort
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission
from dirty_data_to_olap.domain.contracts.platform import ResourceBudget
from dirty_data_to_olap.platform import LocalPlatform


def build_local_backend(
    project_root: Path,
    *,
    resource_budget: ResourceBudget | None = None,
    execution: ExecutionSubmissionPort | None = None,
) -> tuple[LocalPlatform, BackendService]:
    """Wire concrete Step23 stores to the Step27 application service."""

    platform = LocalPlatform.from_project_root(project_root, resource_budget=resource_budget)
    execution_port = execution or DurableExecutionSubmission(platform.control_store)
    backend = BackendService(
        control_store=platform.control_store,
        artifact_store=platform.artifact_store,
        execution=execution_port,
        configuration_fingerprint=platform.config.configuration_fingerprint,
    )
    return platform, backend


__all__ = ["build_local_backend"]
