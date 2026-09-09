"""Profiling orchestration over pinned Step07 staged artifacts."""

from __future__ import annotations

from pathlib import Path
from .profiling_adapter import ProfilingAdapter
from dirty_data_to_olap.domain.contracts.profiling import (
    ProfileCompleteness,
    ProfileFailure,
    ProfileFailureKind,
    ProfileRequest,
    ProfileResult,
    profile_config_hash,
)
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult


class ProfilingService:
    def __init__(self, adapter: ProfilingAdapter, *, project_root: Path) -> None:
        self._adapter = adapter
        self._project_root = project_root.resolve()

    def profile(
        self,
        request: ProfileRequest,
        catalog: SourceCatalog,
        snapshot_result: SourceSnapshotResult,
        *,
        artifact_root: Path | None = None,
    ) -> ProfileResult:
        if request.source_id != catalog.source_id or request.snapshot_id != snapshot_result.snapshot.snapshot_id:
            raise ValueError("profile request is not bound to the supplied source snapshot")
        if set(request.selected_table_ids) - {table.table_id for table in catalog.tables}:
            raise ValueError("profile request selects an unknown table")
        batches_by_table: dict[str, list] = {}
        for batch in snapshot_result.batches:
            batches_by_table.setdefault(batch.table_id, []).append(batch)
        observations = {item.table_id: item for item in snapshot_result.table_observations}
        columns = {table_id: tuple(item for item in catalog.columns if item.table_id == table_id) for table_id in request.selected_table_ids}
        selected_column_ids = set(request.selected_column_ids)
        known_selected_columns = {item.column_id for items in columns.values() for item in items}
        if selected_column_ids - known_selected_columns:
            raise ValueError("profile request selects an unknown column")
        tables = []
        all_columns = []
        all_patterns = []
        failures: list[ProfileFailure] = []
        for table in catalog.tables:
            if table.table_id not in request.selected_table_ids:
                continue
            observation = observations.get(table.table_id)
            if observation is None:
                raise ValueError("source snapshot is missing per-table observation provenance")
            try:
                output = self._adapter.profile_table(
                    request,
                    catalog,
                    snapshot_result,
                    table,
                    columns[table.table_id],
                    tuple(batches_by_table.get(table.table_id, ())),
                    observation,
                    project_root=self._project_root,
                )
                all_columns.extend(output.columns)
                all_patterns.extend(output.patterns)
                failures.extend(output.failures)
                table_profile = _table_profile_from_output(table, output, request)
                tables.append(table_profile)
            except Exception:
                failure = ProfileFailure(
                    failure_id=f"failure_{table.table_id}_{request.profile_request_id}",
                    profile_request_id=request.profile_request_id,
                    source_id=request.source_id,
                    snapshot_id=request.snapshot_id,
                    table_id=table.table_id,
                    kind=ProfileFailureKind.ENGINE_FAILED,
                    detail="table profiling failed before a project-owned profile could be completed",
                    engine=self._adapter.name,
                    config_hash=profile_config_hash(request),
                    retryable=False,
                )
                failures.append(failure)
                tables.append(None)
        tables = [table for table in tables if table is not None]
        completeness = ProfileCompleteness.INCOMPLETE if failures else ProfileCompleteness.COMPLETE
        result = ProfileResult(
            profile_request=request,
            tables=tuple(tables),
            columns=tuple(all_columns),
            patterns=tuple(all_patterns),
            failures=tuple(failures),
            completeness=completeness,
        )
        if artifact_root is not None:
            from dirty_data_to_olap.adapters.profiling.artifacts import ProfileArtifactStore

            result = ProfileArtifactStore(self._project_root).publish(result, run_root=artifact_root)
        return result


def _table_profile_from_output(table, output, request):
    from dirty_data_to_olap.domain.contracts.profiling import TableProfile, table_profile_id_for

    profile = output.observation_scope
    provenance = output.provenance
    failure_refs = tuple(item.failure_id for item in output.failures)
    return TableProfile(
        profile_id=table_profile_id_for(request.source_id, request.snapshot_id, table.table_id, request),
        source_id=request.source_id,
        snapshot_id=request.snapshot_id,
        table_id=table.table_id,
        table_kind=table.table_kind,
        observation_scope=profile,
        rows_available_in_snapshot=profile.rows_available_in_snapshot,
        rows_profiled=profile.rows_profiled,
        column_profile_refs=tuple(item.profile_id for item in output.columns),
        duplicate_row_count=output.duplicate_row_count,
        duplicate_observation_complete=output.duplicate_observation_complete,
        failure_refs=failure_refs,
        provenance=provenance,
        status=ProfileCompleteness.INCOMPLETE if output.failures else ProfileCompleteness.COMPLETE,
    )
