from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.profiling import (
    ColumnProfile,
    ProfileCompleteness,
    ProfileMode,
    ProfileObservationScope,
    ProfileObservationStatus,
    ProfileProvenance,
    ProfileRequest,
    ProfileResult,
    TableProfile,
    UniquenessSemantics,
)
from dirty_data_to_olap.domain.contracts.quality import (
    QualityRequest,
    QualityRule,
    QualityRuleScope,
    QualityRuleSet,
    QualityRuleType,
    QualitySeverity,
    Repairability,
    QualityFailureKind,
    quality_profile_fingerprint,
)
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    ExtractionPolicy,
    SelectionScope,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
    TableObservationStatus,
)


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def quality_case():
    root = ROOT / "workspace" / "tests" / f"step09_{uuid4().hex}"
    root.mkdir(parents=True)
    path = root / "dirty.csv"
    path.write_text(
        "id,email,status\n"
        "1,good@example.com,A\n"
        "2,bad,Z\n"
        "2,bad,Z\n"
        "3,NULL,B\n",
        encoding="utf-8",
    )
    registry = InMemorySourceRegistry()
    record = registry.register(SourceRegistryRecord(
        registry_id="step09-source",
        display_name="Step09 quality fixture",
        source_type=SourceType.CSV,
        file_locator=str(path),
        scope=SelectionScope(),
        adapter_name="file_source",
        adapter_version="1.0.0",
    ))
    adapter = FileSourceAdapter(SourceType.CSV, project_root=ROOT)
    selection = SourceSelection(registry_id=record.registry_id, scope=SelectionScope(), extraction=ExtractionPolicy(chunk_size=2), execution_context_id="step09-test")
    catalog = SourceDiscoveryService(registry, {record.adapter_name: adapter}).discover(selection)
    snapshot = SourceSnapshotService(registry, {record.adapter_name: adapter}).extract(catalog, selection, staging_root=root / "workspace" / "runs" / "step09" / "staging")
    table = catalog.tables[0]
    observation = next(item for item in snapshot.table_observations if item.table_id == table.table_id)
    scope = ProfileObservationScope(
        source_snapshot_mode=snapshot.snapshot.observation_scope.mode.value,
        source_snapshot_was_bounded=False,
        source_table_observation_status=observation.status,
        source_rows_observed=observation.rows_observed,
        rows_available_in_snapshot=observation.rows_observed,
        rows_profiled=observation.rows_observed,
        profiling_mode=ProfileMode.FULL,
        sample_method="none",
        all_available_staged_rows_covered=True,
        completeness=ProfileObservationStatus.FULLY_OBSERVED,
    )
    profile_request = ProfileRequest(profile_request_id="step09-profile", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=(table.table_id,), mode=ProfileMode.FULL)
    profiler_ref = AdapterReference(name="fixture-profiler", version="1", config_fingerprint="fixture")
    provenance = ProfileProvenance(execution_context_id=snapshot.snapshot.execution_context_id, source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_id=table.table_id, schema_fingerprint=catalog.source.schema_fingerprint, input_batch_ids=tuple(item.batch_id for item in snapshot.batches), input_batch_hashes=tuple(item.content_hash for item in snapshot.batches), profiling_adapter=profiler_ref, dataprofiler_version="fixture", profile_config_hash="fixture-profile", created_at="2026-09-10T00:00:00Z")
    columns = tuple(ColumnProfile(profile_id=f"profile-{column.column_id}", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_id=table.table_id, column_id=column.column_id, physical_type="TEXT", primitive_type_observations={"string": 4}, physical_null_count=0, configured_null_marker_count=1 if column.physical_name == "email" else 0, unknown_missing_count=0, rows_observed=4, non_missing_observed_count=3 if column.physical_name == "email" else 4, observed_distinct_count=3, observed_distinct_ratio=1.0, uniqueness_semantics=UniquenessSemantics.EXACT_ON_FULL_SCOPE, observation_scope=scope, provenance=provenance.model_copy(update={"column_id": column.column_id}), status=ProfileCompleteness.COMPLETE) for column in catalog.columns)
    profile = ProfileResult(profile_request=profile_request, tables=(TableProfile(profile_id="table-profile", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_id=table.table_id, table_kind=table.table_kind, observation_scope=scope, rows_available_in_snapshot=4, rows_profiled=4, column_profile_refs=tuple(item.profile_id for item in columns), duplicate_row_count=1, duplicate_observation_complete=True, provenance=provenance, status=ProfileCompleteness.COMPLETE),), columns=columns, patterns=(), failures=(), completeness=ProfileCompleteness.COMPLETE)
    try:
        yield root, catalog, snapshot, profile
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _request(catalog, snapshot, profile, rules):
    return QualityRequest(quality_run_id="step09-quality", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, rule_set=QualityRuleSet(rule_set_id="step09-rules", version="1", rules=tuple(rules), provenance="integration-test"), profile_result_fingerprint=quality_profile_fingerprint(profile), provenance="integration-test")


def test_real_staged_parquet_quality_scan_is_scoped_and_privacy_safe(quality_case):
    root, catalog, snapshot, profile = quality_case
    email = next(item for item in catalog.columns if item.physical_name == "email")
    status = next(item for item in catalog.columns if item.physical_name == "status")
    rules = (
        QualityRule(rule_id="required.email", rule_version="1", rule_type=QualityRuleType.REQUIRED_VALUE, scope=QualityRuleScope.USER_POLICY, entity_type="column", table_id=catalog.tables[0].table_id, column_ids=(email.column_id,), severity=QualitySeverity.HIGH, repairability=Repairability.MANUAL_BUSINESS_DECISION, detector_config={"missing_markers": ("NULL",)}, provenance="integration-test"),
        QualityRule(rule_id="domain.status", rule_version="1", rule_type=QualityRuleType.ALLOWED_DOMAIN, scope=QualityRuleScope.USER_POLICY, entity_type="column", table_id=catalog.tables[0].table_id, column_ids=(status.column_id,), severity=QualitySeverity.MEDIUM, repairability=Repairability.MANUAL_BUSINESS_DECISION, allowed_values=("A", "B"), provenance="integration-test"),
    )
    result = QualityAnalysisService(ParquetQualityStagedReader(), project_root=ROOT).analyze(_request(catalog, snapshot, profile, rules), catalog, snapshot, profile, artifact_root=root / "workspace" / "runs" / "step09")
    assert {item.issue_type for item in result.issues} == {"REQUIRED_VALUE_MISSING", "ALLOWED_DOMAIN_VIOLATION"}
    assert all(item.measurement_semantics.value == "EXACT_ON_FULL_SNAPSHOT_SCOPE" for item in result.issues)
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in (root / "workspace" / "runs" / "step09" / "quality").rglob("*.json"))
    assert "good@example.com" not in serialized
    assert "overall" not in serialized.lower()
    print("INSPECT_QUALITY_ISSUES", [item.model_dump(mode="json") for item in result.issues])
    print("INSPECT_QUALITY_VECTOR", [item.model_dump(mode="json") for item in result.dimension_summaries])
    print("INSPECT_QUALITY_PRIVACY_SCAN", True)


def test_staged_hash_mismatch_is_not_a_clean_quality_result(quality_case):
    root, catalog, snapshot, profile = quality_case
    batch_path = ROOT / snapshot.batches[0].artifact_location
    batch_path.write_bytes(batch_path.read_bytes() + b"tampered")
    email = next(item for item in catalog.columns if item.physical_name == "email")
    rule = QualityRule(rule_id="required.email", rule_version="1", rule_type=QualityRuleType.REQUIRED_VALUE, scope=QualityRuleScope.USER_POLICY, entity_type="column", table_id=catalog.tables[0].table_id, column_ids=(email.column_id,), severity=QualitySeverity.HIGH, repairability=Repairability.MANUAL_BUSINESS_DECISION, provenance="integration-test")
    result = QualityAnalysisService(ParquetQualityStagedReader(), project_root=ROOT).analyze(_request(catalog, snapshot, profile, (rule,)), catalog, snapshot, profile)
    assert any(item.kind is QualityFailureKind.INPUT_INTEGRITY_FAILED for item in result.failures)
    assert not result.issues
