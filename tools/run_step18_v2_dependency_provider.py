"""Run Desbordante over the frozen Step18 v2 relationship scenarios.

The scenario manifest contains only inference inputs.  Truth is joined later by
the evaluator from a separate topology manifest.
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

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.dependencies.staged import DependencyStagedReader
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference, BatchReference, ColumnDescriptor, ExtractionMetrics,
    ExtractionPolicy, ObservationMode, ObservationScope, PublicationState,
    RecordLocatorKind, RowAccounting, SelectionScope, SnapshotConsistency,
    SourceCatalog, SourceDescriptor, SourceRecordReference, SourceSnapshot,
    SourceSnapshotResult, SourceTableKind, SourceType, StabilityScope,
    TableDescriptor, TableObservationStatus, TableSnapshotObservation,
)

MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios.json"


def _rows(kind: str, count: int = 100) -> tuple[dict[str, object], dict[str, object]]:
    source: list[dict[str, object]] = []
    target: list[dict[str, object]] = []
    orphan_rate = {"orphan": 0.02}.get(kind, 0.0)
    if kind == "orphan":
        orphan_rate = 0.005 if count == 100 else orphan_rate
    for index in range(count):
        key = f"customer-{index}"
        if kind == "orphan" and index >= int(count * (1 - orphan_rate)):
            key = f"orphan-{index}"
        row = {"row_id": f"order-{index}", "customer_id": key, "region_code": f"r-{index % 5}", "tenant_id": f"t-{index % 3}"}
        source.append(row)
        target.append({"customer_id": f"customer-{index}", "region_code": f"r-{index % 5}", "tenant_id": f"t-{index % 3}", "label": f"label-{index}"})
    if kind == "duplicate_target":
        target.append(dict(target[0]))
    if kind == "null_heavy":
        for row in source[:85]:
            row["customer_id"] = None
    if kind == "type_mismatch":
        for row in target:
            row["customer_id"] = int(str(row["customer_id"]).split("-")[-1])
    if kind == "low_cardinality":
        for row in source:
            row["customer_id"] = row["region_code"]
    if kind == "same_domain":
        for row in target:
            row["customer_id"] = f"order-{str(row['customer_id']).split('-')[-1]}"
    if kind == "composite":
        source = [{"row_id": r["row_id"], "tenant_id": r["tenant_id"], "customer_id": r["customer_id"]} for r in source]
        target = [{"tenant_id": r["tenant_id"], "customer_id": r["customer_id"], "label": r["label"]} for r in target]
    if kind == "partial_composite":
        for row in source:
            row["tenant_id"] = "t-constant"
    if kind == "missing_candidate":
        for row in source:
            row["customer_id"] = f"hidden-{row['row_id']}"
    if kind == "multiple_target":
        target.extend({"customer_id": r["customer_id"], "region_code": r["region_code"], "tenant_id": r["tenant_id"], "label": "alternate"} for r in target[:10])
    return tuple(source), tuple(target)


def _run_one(scenario: dict, run_root: Path) -> dict:
    group = str(scenario["scenario_group_id"])
    kind = str(scenario["kind"])
    source_id, snapshot_id = f"source-{group}", f"snapshot-{group}"
    from_table, to_table = f"orders-{group}", f"customers-{group}"
    source_rows, target_rows = _rows(kind, 200 if kind == "orphan" else 100)
    adapter_ref = AdapterReference(name="step18-v2-fixture", version="2", config_fingerprint=f"dependency-{group}")
    source = SourceDescriptor(source_id=source_id, display_name=group, source_type=SourceType.CSV, file_locator=f"{group}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{group}", adapter_reference=adapter_ref)
    tables = (TableDescriptor(table_id=from_table, source_id=source_id, physical_name=from_table, table_kind=SourceTableKind.FILE, row_count=len(source_rows)), TableDescriptor(table_id=to_table, source_id=source_id, physical_name=to_table, table_kind=SourceTableKind.FILE, row_count=len(target_rows)))
    all_names = {name for row in source_rows + target_rows for name in row}
    columns = tuple(ColumnDescriptor(column_id=f"{table.table_id}-{name}", table_id=table.table_id, physical_name=name, ordinal=index, native_physical_type="INTEGER" if kind == "type_mismatch" and name == "customer_id" and table.table_id == to_table else "TEXT", normalized_physical_type="integer" if kind == "type_mismatch" and name == "customer_id" and table.table_id == to_table else "text") for table, rows in ((tables[0], source_rows), (tables[1], target_rows)) for index, name in enumerate(sorted({str(k) for row in rows for k in row})))
    catalog = SourceCatalog(source=source, tables=tables, columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(source_id=source_id, snapshot_id=snapshot_id, execution_context_id=f"step18-v2-{group}", schema_fingerprint=f"schema-{group}", source_fingerprint=f"fixture-{group}", observed_at="2026-09-11T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=200, input_records_observed=len(source_rows) + len(target_rows)), extraction_policy=ExtractionPolicy(chunk_size=200), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=adapter_ref)
    batches = []
    for table, rows in ((tables[0], source_rows), (tables[1], target_rows)):
        path = run_root / "staging" / group / f"{table.table_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(list(rows)), path)
        batches.append(BatchReference(batch_id=f"batch-{group}-{table.table_id}", source_id=source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_index=0, row_count=len(rows), artifact_location=path.relative_to(run_root).as_posix(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), schema_fingerprint=f"schema-{group}", first_extraction_ordinal=0, last_extraction_ordinal=len(rows) - 1, adapter_reference=adapter_ref, publication_state=PublicationState.COMPLETE))
    refs = tuple(SourceRecordReference(record_ref=f"{table.table_id}-record-{index}", source_id=source_id, snapshot_id=snapshot_id, table_id=table.table_id, batch_id=f"batch-{group}-{table.table_id}", extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for table, rows in ((tables[0], source_rows), (tables[1], target_rows)) for index in range(len(rows)))
    snapshot_result = SourceSnapshotResult(snapshot=snapshot, batches=tuple(batches), record_references=refs, accounting=RowAccounting(input_records_observed=len(refs), successfully_staged_records=len(refs), explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=len(refs), staged_records=len(refs), batch_count=2, configured_chunk_size=200, bytes_staged=sum((run_root / batch.artifact_location).stat().st_size for batch in batches)), table_observations=tuple(TableSnapshotObservation(table_id=table.table_id, rows_observed=table.row_count or 0, status=TableObservationStatus.FULLY_OBSERVED) for table in tables))
    request = DependencyRequest(request_id=f"step18-v2-{group}", source_id=source_id, snapshot_id=snapshot_id, selected_table_ids=(from_table, to_table), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND))
    policy = PrivacyPolicyService(project_root=run_root)
    decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=source_id, snapshot_id=snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in batches))
    if not decision.allowed:
        raise RuntimeError(f"dependency authorization denied: {decision.reason}")
    result = DependencyDiscoveryService(DesbordanteDependencyAdapter(project_root=run_root, reader=DependencyStagedReader(), privacy_policy=policy), project_root=run_root, privacy_policy=policy).discover(request, catalog, snapshot_result, artifact_root=run_root / "provider-artifacts")
    return {"scenario_group_id": group, "task_id": scenario["task_id"], "kind": kind, "result": result.model_dump(mode="json")}


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v2" / "evaluation" / "dependency_discovery"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload = {"schema_version":"1", "provider":"desbordante", "adapter":"DesbordanteDependencyAdapter", "version":"2.4.1", "manifest_hash":hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), "results":[_run_one(item, run_root) for item in manifest["scenarios"]]}
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    run_root.mkdir(parents=True, exist_ok=True)
    output = run_root / "normalized_provider_results.json"
    output.write_bytes(data)
    receipt = {"component":"dependency_discovery", "adapter":"DesbordanteDependencyAdapter", "provider":"desbordante", "version":"2.4.1", "execution_result":"COMPLETE", "scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]], "scenario_fixture_hash":payload["manifest_hash"], "output_artifact":output.relative_to(ROOT).as_posix(), "output_hash":hashlib.sha256(data).hexdigest()}
    (run_root / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
