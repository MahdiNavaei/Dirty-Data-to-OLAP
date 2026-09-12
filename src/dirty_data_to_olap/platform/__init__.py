"""Local-first platform composition boundary for a single developer machine."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, LocalCapabilityRegistry, LocalStagingStore, SQLiteControlStore
from dirty_data_to_olap.domain.contracts.platform import LocalPlatformConfig, ResourceBudget, RunRecord, WorkspaceAllocation


class LocalPlatform:
    """Explicitly wired local platform; no cloud or distributed service is required."""

    def __init__(self, config: LocalPlatformConfig) -> None:
        self.config = config
        self.control_store = SQLiteControlStore(Path(config.control_store_path), project_root=Path(config.project_root))
        self.artifact_store = LocalArtifactStore(Path(config.artifact_root), project_root=Path(config.project_root), disk_budget_bytes=config.resource_budget.disk_budget_bytes)
        self.staging_artifact_store = LocalArtifactStore(Path(config.staging_root), project_root=Path(config.project_root), disk_budget_bytes=config.resource_budget.max_staged_bytes)
        self.staging_store = LocalStagingStore(self.staging_artifact_store, control_store=self.control_store)
        self.capabilities = LocalCapabilityRegistry()

    @classmethod
    def from_project_root(cls, project_root: Path, *, resource_budget: ResourceBudget | None = None) -> "LocalPlatform":
        root = Path(project_root).resolve()
        workspace = root / "workspace" / "platform"
        budget = resource_budget or ResourceBudget()
        config = LocalPlatformConfig(
            project_root=str(root),
            workspace_root=str(workspace),
            artifact_root=str(workspace / "artifacts"),
            staging_root=str(workspace / "staging"),
            control_store_path=str(workspace / "control.sqlite"),
            cache_root=str(workspace / "cache"),
            resource_budget=budget,
        )
        return cls(config)

    def allocation_for(self, run_id: str) -> WorkspaceAllocation:
        return WorkspaceAllocation(
            run_id=run_id,
            workspace_key=f"runs/{run_id}",
            artifact_key_prefix=f"runs/{run_id}/artifacts",
            staging_key_prefix=f"runs/{run_id}/staging",
            resource_budget=self.config.resource_budget,
        )

    def create_run(self, run: RunRecord) -> RunRecord:
        if run.configuration_fingerprint != self.config.configuration_fingerprint:
            raise ValueError("run configuration fingerprint does not match the local platform configuration")
        return self.control_store.create_run(run)

    def close(self) -> None:
        self.control_store.close()


__all__ = ["LocalPlatform"]
