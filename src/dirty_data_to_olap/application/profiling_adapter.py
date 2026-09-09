"""Project-owned profiling port."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import Protocol, Sequence

from dirty_data_to_olap.domain.contracts.profiling import (
    ColumnProfile,
    ProfileFailure,
    ProfileRequest,
    ProfileObservationScope,
    ProfileProvenance,
    TableProfile,
    ValuePatternSummary,
)
from dirty_data_to_olap.domain.contracts.source import (
    BatchReference,
    ColumnDescriptor,
    SourceCatalog,
    SourceSnapshotResult,
    TableDescriptor,
    TableSnapshotObservation,
)


@dataclass(frozen=True)
class AdapterTableProfileResult:
    columns: tuple[ColumnProfile, ...]
    patterns: tuple[ValuePatternSummary, ...]
    failures: tuple[ProfileFailure, ...]
    observation_scope: ProfileObservationScope
    duplicate_row_count: int | None
    duplicate_observation_complete: bool
    provenance: ProfileProvenance


class ProfilingAdapter(Protocol):
    name: str
    version: str

    def profile_table(
        self,
        request: ProfileRequest,
        catalog: SourceCatalog,
        snapshot_result: SourceSnapshotResult,
        table: TableDescriptor,
        columns: Sequence[ColumnDescriptor],
        batches: Sequence[BatchReference],
        table_observation: TableSnapshotObservation,
        *,
        project_root: Path,
    ) -> AdapterTableProfileResult:
        ...
