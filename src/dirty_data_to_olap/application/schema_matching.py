"""Application orchestration for Step13 schema matching."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.schema_matching import MatchingAuthorization, SchemaMatchMode, SchemaMatchRequest, SchemaMatchResult


class SchemaMatchingService:
    def __init__(self, adapter: Any, *, project_root: Path, privacy_policy: PrivacyPolicyService | None = None) -> None:
        self.project_root = project_root.resolve()
        self.adapter = adapter
        self.privacy_policy = privacy_policy or PrivacyPolicyService(project_root=self.project_root)

    def match(self, request: SchemaMatchRequest, catalogs: Mapping[str, Any], snapshots: Mapping[str, Any], *, profiles: Any | None = None, dependencies: Mapping[str, Any] | None = None, artifact_root: Path | None = None) -> SchemaMatchResult:
        authorization = None
        if request.mode is SchemaMatchMode.INSTANCE_AWARE:
            artifact_ids = tuple(batch.batch_id for snapshot in snapshots.values() for batch in snapshot.batches)
            decision = self.privacy_policy.authorize_schema_matching_analysis(request.privacy_context, source_ids=request.source_ids, snapshot_ids=request.snapshot_ids, table_ids_by_source=request.selected_table_ids_by_source, column_ids_by_table=request.selected_column_ids_by_table, artifact_ids=artifact_ids)
            if decision.allowed:
                authorization = self.privacy_policy.matching_authorization_for_decision(decision)
                if not isinstance(authorization, MatchingAuthorization):
                    authorization = None
        return self.adapter.discover(request, catalogs, snapshots, profiles=profiles, dependencies=dependencies, artifact_root=artifact_root, authorization=authorization)
