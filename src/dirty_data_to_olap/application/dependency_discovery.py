"""Step12 orchestration over the immutable Step07 snapshot boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dirty_data_to_olap.domain.contracts.dependency import DependencyRequest, DependencyResult
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult


class DependencyDiscoveryService:
    """Coordinates discovery without reconnecting to or mutating the source."""

    def __init__(self, adapter: Any, *, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.adapter = adapter

    def discover(
        self,
        request: DependencyRequest,
        catalog: SourceCatalog,
        snapshot_result: SourceSnapshotResult,
        *,
        profiles: Any | None = None,
        artifact_root: Path | None = None,
    ) -> DependencyResult:
        return self.adapter.discover(request, catalog, snapshot_result, profiles=profiles, artifact_root=artifact_root)
