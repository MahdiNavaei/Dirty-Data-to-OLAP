"""Execute the real Docker dependency provider and persist its normalized result."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.dependencies.staged import DependencyStagedReader
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest
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


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    run_root = ROOT / "workspace" / "runs" / "step18-inference-baseline-v1" / "evaluation" / "dependency_discovery"
    run_root.mkdir(parents=True, exist_ok=True)
    rows = {
        "orders": tuple({"order_id": f"order-{index}", "customer_id": f"customer-{index}", "region": f"region-{index}"} for index in range(10)),
        "customers": tuple({"customer_id": f"customer-{index}", "name": f"name-{index}", "region": f"region-{index}"} for index in range(10)),
    }
    adapter_ref = AdapterReference(name="fixture", version="1", config_fingerprint="step18-dependency-fixture")
    source = SourceDescriptor(source_id="source-1", display_name="step18-fixture", source_type=SourceType.CSV, file_locator="fixture.csv", selection_scope=SelectionScope(), schema_fingerprint="schema-step18", adapter_reference=adapter_ref)
    tables = (
        TableDescriptor(table_id="orders", source_id="source-1", physical_name="orders", table_kind=SourceTableKind.FILE, row_count=10),
        TableDescriptor(table_id="customers", source_id="source-1", physical_name="customers", table_kind=SourceTableKind.FILE, row_count=10),
    )
    columns = tuple(ColumnDescriptor(column_id=f"{table.table_id}-{name}", table_id=table.table_id, physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text") for table, names in ((tables[0], ("order_id", "customer_id", "region")), (tables[1], ("customer_id", "name", "region"))) for index, name in enumerate(names))
    catalog = SourceCatalog(source=source, tables=tables, columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(source_id="source-1", snapshot_id="snapshot-step18", execution_context_id="step18-real-desbordante", schema_fingerprint="schema-step18", source_fingerprint="step18-fixture", observed_at="2026-09-11T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=10, input_records_observed=20), extraction_policy=ExtractionPolicy(chunk_size=10), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=adapter_ref)
    batches = []
    for table in tables:
        path = run_root / "staging" / f"{table.table_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(list(rows[table.table_id])), path)
        batches.append(BatchReference(batch_id=f"batch-{table.table_id}", source_id="source-1", snapshot_id="snapshot-step18", table_id=table.table_id, batch_index=0, row_count=10, artifact_location=path.relative_to(run_root).as_posix(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), schema_fingerprint="schema-step18", first_extraction_ordinal=0, last_extraction_ordinal=9, adapter_reference=adapter_ref, publication_state=PublicationState.COMPLETE))
    refs = tuple(SourceRecordReference(record_ref=f"{table.table_id}-record-{index}", source_id="source-1", snapshot_id="snapshot-step18", table_id=table.table_id, batch_id=f"batch-{table.table_id}", extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for table in tables for index in range(10))
    snapshot_result = SourceSnapshotResult(snapshot=snapshot, batches=tuple(batches), record_references=refs, accounting=RowAccounting(input_records_observed=20, successfully_staged_records=20, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=20, staged_records=20, batch_count=2, configured_chunk_size=10, bytes_staged=sum((run_root / "staging" / f"{table.table_id}.parquet").stat().st_size for table in tables)), table_observations=tuple(TableSnapshotObservation(table_id=table.table_id, rows_observed=10, status=TableObservationStatus.FULLY_OBSERVED) for table in tables))
    request = DependencyRequest(request_id="step18-real-desbordante", source_id="source-1", snapshot_id="snapshot-step18", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND))
    policy = PrivacyPolicyService(project_root=run_root)
    decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in batches))
    if not decision.allowed:
        raise RuntimeError(f"dependency authorization denied: {decision.reason}")
    result = DependencyDiscoveryService(DesbordanteDependencyAdapter(project_root=run_root, reader=DependencyStagedReader(), privacy_policy=policy), project_root=run_root, privacy_policy=policy).discover(request, catalog, snapshot_result, artifact_root=run_root / "provider-artifacts")
    payload = result.model_dump(mode="json")
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output = run_root / "real_provider_result.json"
    output.write_bytes(data)
    receipt = {
        "component": "dependency_discovery",
        "adapter": "DesbordanteDependencyAdapter",
        "provider": "desbordante-docker",
        "version": "step12-pinned-container",
        "dataset_id": "step18-inference-quality-v1",
        "scenario_scope": "declared-relationship/hidden-relationship/orphan-and-low-cardinality-controls",
        "output_artifact": output.relative_to(ROOT).as_posix(),
        "output_hash": hashlib.sha256(data).hexdigest(),
        "execution_result": result.status.value,
        "relationship_candidate_count": len(result.relationship_candidates),
        "ucc_count": len(result.ucc_evidence),
        "ind_count": len(result.inclusion_dependencies),
    }
    (run_root / "real_provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
