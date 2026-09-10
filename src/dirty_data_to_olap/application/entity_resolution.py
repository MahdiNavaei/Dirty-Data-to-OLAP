"""Application orchestration for the bounded entity-resolution stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionAuthorization, EntityResolutionResult, EntityResolutionSpec
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult


class EntityResolutionService:
    def __init__(self, *, project_root: Path, privacy_policy: Any) -> None:
        self.adapter = SplinkEntityResolutionAdapter(project_root=project_root, privacy_policy=privacy_policy)

    def run(self, spec: EntityResolutionSpec, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult], *, authorization: EntityResolutionAuthorization | None, artifact_root: Path | None = None) -> EntityResolutionResult:
        return self.adapter.run(spec, catalogs, snapshots, authorization=authorization, artifact_root=artifact_root)
