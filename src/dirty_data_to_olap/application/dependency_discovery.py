"""Step12 orchestration over the immutable Step07 snapshot boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.dependency import DependencyRequest, DependencyResult
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult


class DependencyDiscoveryService:
    """Coordinates discovery without reconnecting to or mutating the source."""

    def __init__(self, adapter: Any, *, project_root: Path, privacy_policy: PrivacyPolicyService | None = None) -> None:
        self.project_root = project_root.resolve()
        self.adapter = adapter
        self.privacy_policy = privacy_policy or PrivacyPolicyService(project_root=self.project_root)

    def discover(
        self,
        request: DependencyRequest,
        catalog: SourceCatalog,
        snapshot_result: SourceSnapshotResult,
        *,
        profiles: Any | None = None,
        artifact_root: Path | None = None,
    ) -> DependencyResult:
        decision = self.privacy_policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in snapshot_result.batches if batch.table_id in request.selected_table_ids))
        authorization = None
        if decision.allowed:
            from dirty_data_to_olap.domain.contracts.dependency import DependencyAuthorization
            authorization = self.privacy_policy.authorization_for_decision(decision)
            if not isinstance(authorization, DependencyAuthorization):
                authorization = None
        return self.adapter.discover(request, catalog, snapshot_result, profiles=profiles, artifact_root=artifact_root, authorization=authorization)
