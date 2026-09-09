from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.profiling import ProfilingService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.profiling import (
    NullMarkerPolicy,
    ProfileFailureKind,
    ProfileMode,
    ProfileRequest,
    ProfileCompleteness,
    ProfileObservationStatus,
    ProfileDiffStatus,
    diff_column_profiles,
)
from dirty_data_to_olap.domain.contracts.source import (
    ExtractionPolicy,
    SelectionScope,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
)


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def profiling_case() -> tuple[Path, object, object, object]:
    root = ROOT / "workspace" / "tests" / f"step08_{uuid4().hex}"
    root.mkdir(parents=True)
    path = root / "dirty.csv"
    path.write_text(
        "id,amount,note,email,phone\n"
        "1,10.5,hello,person1@example.com,+989121234567\n"
        "2,0,,NULL,not-a-phone\n"
        "3,-2.0,NULL,person3@example.com,+989121234568\n"
        "3,-2.0,NULL,person3@example.com,+989121234568\n"
        "4,3.5,2026-09-09,person4@example.com,+989121234569\n",
        encoding="utf-8",
    )
    registry = InMemorySourceRegistry()
    record = registry.register(SourceRegistryRecord(
        registry_id="profiling-source",
        display_name="Step08 profiling fixture",
        source_type=SourceType.CSV,
        file_locator=str(path),
        scope=SelectionScope(),
        adapter_name="file_source",
        adapter_version="1.0.0",
    ))
    adapter = FileSourceAdapter(SourceType.CSV, project_root=ROOT)
    selection = SourceSelection(
        registry_id=record.registry_id,
        scope=SelectionScope(),
        extraction=ExtractionPolicy(chunk_size=2),
        execution_context_id="step08-test",
    )
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    snapshot = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(
        catalog,
        selection,
        staging_root=root / "workspace" / "runs" / "step08-test" / "staging",
    )
    try:
        yield root, catalog, snapshot, record
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _request(catalog, snapshot, *, mode=ProfileMode.FULL, sample_limit=None, seed=None, markers=()):
    return ProfileRequest(
        profile_request_id=f"request-{uuid4().hex}",
        source_id=catalog.source_id,
        snapshot_id=snapshot.snapshot.snapshot_id,
        selected_table_ids=(catalog.tables[0].table_id,),
        mode=mode,
        sample_limit=sample_limit,
        seed=seed,
        null_marker_policy=NullMarkerPolicy(configured_markers=markers),
    )


def test_real_dataprofiler_full_profile_is_project_owned_and_privacy_safe(profiling_case):
    root, catalog, snapshot, _ = profiling_case
    request = _request(catalog, snapshot, markers=("NULL",))
    result = ProfilingService(DataProfilerAdapter(), project_root=ROOT).profile(
        request, catalog, snapshot, artifact_root=root / "workspace" / "runs" / "step08-test"
    )
    assert result.completeness is ProfileCompleteness.COMPLETE
    assert not result.failures
    assert result.tables[0].observation_scope.profiling_mode is ProfileMode.FULL
    assert result.tables[0].observation_scope.completeness is ProfileObservationStatus.FULLY_OBSERVED
    assert result.columns
    assert all(column.provenance.dataprofiler_version == "0.13.4" for column in result.columns)
    note_id = next(column.column_id for column in catalog.columns if column.physical_name == "note")
    note = next(column for column in result.columns if column.column_id == note_id)
    assert note.physical_null_count == 0
    assert note.configured_null_marker_count == 2
    assert result.patterns
    assert any(pattern.pattern_type.value == "EMAIL_LIKE" for pattern in result.patterns)
    assert result.artifacts
    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    assert "person1@example.com" not in serialized
    assert "+989121234567" not in serialized
    assert not list((root / "workspace" / "runs" / "step08-test").rglob("*.partial"))
    for path in (root / "workspace" / "runs" / "step08-test" / "profiles").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "person1@example.com" not in text
        assert "StructuredProfiler" not in text
    print("INSPECT_COLUMN", result.columns[0].model_dump(mode="json"))
    print("INSPECT_TABLE", result.tables[0].model_dump(mode="json"))
    print("INSPECT_PATTERN", result.patterns[0].model_dump(mode="json"))
    print("INSPECT_ARTIFACT_DIR", root / "workspace" / "runs" / "step08-test" / "profiles")
    print("INSPECT_DATAPROFILER_VERSION", result.columns[0].provenance.dataprofiler_version)
    print("INSPECT_PRIVACY_SCAN", True)


def test_sampling_is_deterministic_and_explicitly_not_full(profiling_case):
    _, catalog, snapshot, _ = profiling_case
    service = ProfilingService(DataProfilerAdapter(), project_root=ROOT)
    first = service.profile(_request(catalog, snapshot, mode=ProfileMode.SAMPLE, sample_limit=3, seed=19), catalog, snapshot)
    second = service.profile(first.profile_request, catalog, snapshot)
    assert first.completeness is ProfileCompleteness.COMPLETE
    assert first.tables[0].observation_scope.profiling_mode is ProfileMode.SAMPLE
    assert first.tables[0].observation_scope.sample_identity == second.tables[0].observation_scope.sample_identity
    assert [item.model_dump(exclude={"provenance"}) for item in first.columns] == [item.model_dump(exclude={"provenance"}) for item in second.columns]


def test_item_failure_and_batch_integrity_are_explicit(profiling_case, monkeypatch):
    root, catalog, snapshot, _ = profiling_case
    adapter = DataProfilerAdapter()
    original = adapter._build_column_profile
    failing_id = catalog.columns[0].column_id

    def fail_one(accumulator, **kwargs):
        if kwargs["column"].column_id == failing_id:
            raise RuntimeError("test item failure")
        return original(accumulator, **kwargs)

    monkeypatch.setattr(adapter, "_build_column_profile", fail_one)
    result = ProfilingService(adapter, project_root=ROOT).profile(_request(catalog, snapshot), catalog, snapshot)
    assert any(item.kind is ProfileFailureKind.COLUMN_FAILED for item in result.failures)
    assert len(result.columns) == len(catalog.columns) - 1
    batch_path = ROOT / snapshot.batches[0].artifact_location
    batch_path.write_bytes(batch_path.read_bytes() + b"tampered")
    failed = ProfilingService(DataProfilerAdapter(), project_root=ROOT).profile(_request(catalog, snapshot), catalog, snapshot)
    assert any(item.kind is ProfileFailureKind.BATCH_INTEGRITY_FAILED for item in failed.failures)
    assert failed.completeness is ProfileCompleteness.INCOMPLETE


def test_profile_diff_reports_limited_and_incompatible_comparability(profiling_case):
    _, catalog, snapshot, _ = profiling_case
    service = ProfilingService(DataProfilerAdapter(), project_root=ROOT)
    left = service.profile(_request(catalog, snapshot), catalog, snapshot).columns[0]
    right_request = _request(catalog, snapshot, mode=ProfileMode.SAMPLE, sample_limit=2, seed=7)
    right = service.profile(right_request, catalog, snapshot).columns[0]
    limited = diff_column_profiles(left, right)
    assert limited.status is ProfileDiffStatus.LIMITED_COMPARABILITY
    incompatible = right.model_copy(update={"column_id": "other-column"})
    assert diff_column_profiles(left, incompatible).status is ProfileDiffStatus.INCOMPATIBLE
