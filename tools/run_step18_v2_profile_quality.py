"""Run the project-owned Profiling and Quality contracts for Step18 v2.

This runner deliberately emits aggregate project contracts only.  It is a
companion to the real Desbordante runner: the evaluator binds both normalized
artifacts to the same scenario manifest, protocol, truth, split, and content
commit before using them for Fusion.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.application.profiling import ProfilingService
from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.domain.contracts.profiling import NullMarkerPolicy, ProfileMode, ProfileRequest
from dirty_data_to_olap.domain.contracts.quality import (
    QualityRequest,
    QualityRule,
    QualityRuleScope,
    QualityRuleSet,
    QualityRuleType,
    QualitySeverity,
    Repairability,
    quality_profile_fingerprint,
)
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    BatchReference,
    ColumnDescriptor,
    ExtractionMetrics,
    ExtractionPolicy,
    ObservationMode,
    ObservationScope,
    PublicationState,
    RecordLocatorKind,
    RowAccounting,
    SelectionScope,
    SnapshotConsistency,
    SourceCatalog,
    SourceDescriptor,
    SourceRecordReference,
    SourceSnapshot,
    SourceSnapshotResult,
    SourceTableKind,
    SourceType,
    StabilityScope,
    TableDescriptor,
    TableObservationStatus,
    TableSnapshotObservation,
)

from run_step18_v2_dependency_provider import _rows


MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(scenario: dict, run_root: Path) -> tuple[SourceCatalog, SourceSnapshotResult]:
    group = str(scenario["scenario_group_id"])
    kind = str(scenario["kind"])
    source_id, snapshot_id = f"source-{group}", f"snapshot-{group}"
    from_table, to_table = f"orders-{group}", f"customers-{group}"
    source_rows, target_rows = _rows(kind, 200 if kind == "orphan" else 100)
    adapter_ref = AdapterReference(name="step18-v2-fixture", version="2", config_fingerprint=f"profile-quality-{group}")
    source = SourceDescriptor(source_id=source_id, display_name=group, source_type=SourceType.CSV, file_locator=f"{group}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{group}", adapter_reference=adapter_ref)
    tables = (
        TableDescriptor(table_id=from_table, source_id=source_id, physical_name=from_table, table_kind=SourceTableKind.FILE, row_count=len(source_rows)),
        TableDescriptor(table_id=to_table, source_id=source_id, physical_name=to_table, table_kind=SourceTableKind.FILE, row_count=len(target_rows)),
    )
    columns = tuple(
        ColumnDescriptor(
            column_id=f"{table.table_id}-{name}",
            table_id=table.table_id,
            physical_name=name,
            ordinal=index,
            native_physical_type="INTEGER" if kind == "type_mismatch" and name == "customer_id" and table.table_id == to_table else "TEXT",
            normalized_physical_type="integer" if kind == "type_mismatch" and name == "customer_id" and table.table_id == to_table else "text",
        )
        for table, rows in ((tables[0], source_rows), (tables[1], target_rows))
        for index, name in enumerate(sorted({str(key) for row in rows for key in row}))
    )
    catalog = SourceCatalog(source=source, tables=tables, columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(
        source_id=source_id,
        snapshot_id=snapshot_id,
        execution_context_id=f"step18-v2-profile-quality-{group}",
        schema_fingerprint=f"schema-{group}",
        source_fingerprint=f"fixture-{group}",
        observed_at="2026-09-11T00:00:00Z",
        selection_scope=SelectionScope(),
        observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=200, input_records_observed=len(source_rows) + len(target_rows)),
        extraction_policy=ExtractionPolicy(chunk_size=200),
        consistency=SnapshotConsistency.FILE_IMMUTABLE,
        adapter_reference=adapter_ref,
    )
    batches: list[BatchReference] = []
    refs: list[SourceRecordReference] = []
    for table, rows in ((tables[0], source_rows), (tables[1], target_rows)):
        path = run_root / "staging" / group / f"{table.table_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(list(rows)), path)
        batch_id = f"batch-{group}-{table.table_id}"
        batches.append(BatchReference(batch_id=batch_id, source_id=source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_index=0, row_count=len(rows), artifact_location=path.relative_to(run_root).as_posix(), content_hash=_sha(path), schema_fingerprint=f"schema-{group}", first_extraction_ordinal=0, last_extraction_ordinal=len(rows) - 1, adapter_reference=adapter_ref, publication_state=PublicationState.COMPLETE))
        refs.extend(SourceRecordReference(record_ref=f"{table.table_id}-record-{index}", source_id=source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_id=batch_id, extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for index in range(len(rows)))
    snapshot_result = SourceSnapshotResult(
        snapshot=snapshot,
        batches=tuple(batches),
        record_references=tuple(refs),
        accounting=RowAccounting(input_records_observed=len(refs), successfully_staged_records=len(refs), explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True),
        metrics=ExtractionMetrics(input_records_observed=len(refs), staged_records=len(refs), batch_count=2, configured_chunk_size=200, bytes_staged=sum((run_root / batch.artifact_location).stat().st_size for batch in batches)),
        table_observations=tuple(TableSnapshotObservation(table_id=table.table_id, rows_observed=table.row_count or 0, status=TableObservationStatus.FULLY_OBSERVED) for table in tables),
    )
    return catalog, snapshot_result


def _quality_request(catalog: SourceCatalog, snapshot: SourceSnapshotResult, profile) -> QualityRequest:
    rules = tuple(
        QualityRule(
            rule_id=f"step18.required.{table.table_id}.customer_id",
            rule_version="1",
            rule_type=QualityRuleType.REQUIRED_VALUE,
            scope=QualityRuleScope.USER_POLICY,
            entity_type="column",
            table_id=table.table_id,
            column_ids=(next(column.column_id for column in catalog.columns if column.table_id == table.table_id and column.physical_name == "customer_id"),),
            severity=QualitySeverity.MEDIUM,
            repairability=Repairability.MANUAL_BUSINESS_DECISION,
            detector_config={"missing_markers": ("NULL",)},
            provenance="step18-v2-provider-scenario-quality-policy",
        )
        for table in catalog.tables
    )
    return QualityRequest(
        quality_run_id=f"step18-v2-quality-{snapshot.snapshot.snapshot_id}",
        source_id=catalog.source_id,
        snapshot_id=snapshot.snapshot.snapshot_id,
        rule_set=QualityRuleSet(rule_set_id="step18-v2-quality-rules", version="1", rules=rules, provenance="step18-v2-provider-scenario-quality-policy"),
        profile_result_fingerprint=quality_profile_fingerprint(profile),
        profile_refs=tuple(item.profile_id for item in profile.tables),
        batch_ids=tuple(batch.batch_id for batch in snapshot.batches),
        batch_hashes=tuple(batch.content_hash for batch in snapshot.batches),
        provenance="step18-v2-real-quality-contract",
    )


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation" / "profiling_quality"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    profiles: list[dict] = []
    qualities: list[dict] = []
    profile_service = ProfilingService(DataProfilerAdapter(), project_root=run_root)
    quality_service = QualityAnalysisService(ParquetQualityStagedReader(), project_root=run_root)
    for scenario in manifest["scenarios"]:
        catalog, snapshot = _fixture(scenario, run_root)
        profile_request = ProfileRequest(
            profile_request_id=f"step18-v2-profile-{snapshot.snapshot.snapshot_id}",
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            selected_table_ids=tuple(table.table_id for table in catalog.tables),
            mode=ProfileMode.FULL,
            null_marker_policy=NullMarkerPolicy(configured_markers=("NULL",)),
        )
        profile = profile_service.profile(profile_request, catalog, snapshot, artifact_root=run_root / "contract-artifacts")
        quality = quality_service.analyze(_quality_request(catalog, snapshot, profile), catalog, snapshot, profile, artifact_root=run_root / "contract-artifacts")
        profiles.append({"scenario_group_id": scenario["scenario_group_id"], "result": profile.model_dump(mode="json")})
        qualities.append({"scenario_group_id": scenario["scenario_group_id"], "result": quality.model_dump(mode="json")})
    run_root.mkdir(parents=True, exist_ok=True)
    profile_data = (json.dumps({"schema_version": "1", "provider": "dataprofiler", "adapter": "DataProfilerAdapter", "version": profiles[0]["result"]["columns"][0]["provenance"]["dataprofiler_version"], "manifest_hash": _sha(MANIFEST), "results": profiles}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    quality_data = (json.dumps({"schema_version": "1", "provider": "project-quality", "adapter": "QualityAnalysisService", "version": "step09-contract-v1", "manifest_hash": _sha(MANIFEST), "results": qualities}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    profile_path = run_root / "normalized_profile_results.json"
    quality_path = run_root / "normalized_quality_results.json"
    profile_path.write_bytes(profile_data)
    quality_path.write_bytes(quality_data)
    for component, path, data in (("profiling", profile_path, profile_data), ("quality", quality_path, quality_data)):
        receipt = {"component": component, "adapter": "DataProfilerAdapter" if component == "profiling" else "QualityAnalysisService", "provider": "dataprofiler" if component == "profiling" else "project-owned", "version": json.loads(data.decode("utf-8"))["version"], "execution_result": "COMPLETE", "scenario_group_ids": [item["scenario_group_id"] for item in manifest["scenarios"]], "scenario_fixture_hash": _sha(MANIFEST), "output_artifact": path.relative_to(ROOT).as_posix(), "output_hash": _sha(path)}
        (run_root / f"{component}_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"profiling": str(profile_path), "quality": str(quality_path), "scenario_count": len(profiles), "profile_completeness": sorted({item["result"]["completeness"] for item in profiles}), "quality_completeness": sorted({item["result"]["completeness"] for item in qualities})}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
