from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
import json

import pyarrow as pa
import pyarrow.parquet as pq

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.dependencies.staged import DependencyStagedReader
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest, DependencyStageStatus
from dirty_data_to_olap.adapters.dependencies.staged import StagedDependencyRow
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference, BatchReference, ColumnDescriptor, ExtractionMetrics, ExtractionPolicy,
    ObservationMode, ObservationScope, PublicationState, RowAccounting, SelectionScope,
    SnapshotConsistency, SourceCatalog, SourceDescriptor, SourceRecordReference, SourceSnapshot, SourceSnapshotResult,
    SourceTableKind, SourceType, StabilityScope, TableDescriptor, TableObservationStatus,
    TableSnapshotObservation, RecordLocatorKind,
)


def test_step12_real_provider_executes_ucc_fd_ind():
    rows = {
        "orders": tuple({"order_id": f"order-{index}", "customer_id": f"customer-{index}", "region": f"region-{index}"} for index in range(10)),
        "customers": tuple({"customer_id": f"customer-{index}", "name": f"name-{index}", "region": f"region-{index}"} for index in range(10)),
    }
    adapter_ref = AdapterReference(name="fixture", version="1", config_fingerprint="fixture")
    source = SourceDescriptor(source_id="source-1", display_name="fixture", source_type=SourceType.CSV, file_locator="fixture.csv", selection_scope=SelectionScope(), schema_fingerprint="schema-1", adapter_reference=adapter_ref)
    tables = (TableDescriptor(table_id="orders", source_id="source-1", physical_name="orders", table_kind=SourceTableKind.FILE, row_count=10), TableDescriptor(table_id="customers", source_id="source-1", physical_name="customers", table_kind=SourceTableKind.FILE, row_count=10))
    columns = tuple(ColumnDescriptor(column_id=f"{table.table_id}-{name}", table_id=table.table_id, physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text") for table, names in ((tables[0], ("order_id", "customer_id", "region")), (tables[1], ("customer_id", "name", "region"))) for index, name in enumerate(names))
    catalog = SourceCatalog(source=source, tables=tables, columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(source_id="source-1", snapshot_id="snapshot-1", execution_context_id="run-1", schema_fingerprint="schema-1", source_fingerprint="fixture", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=10, input_records_observed=20), extraction_policy=ExtractionPolicy(chunk_size=10), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=adapter_ref)
    batches = tuple(BatchReference(batch_id=f"batch-{table.table_id}", source_id="source-1", snapshot_id="snapshot-1", table_id=table.table_id, batch_index=0, row_count=10, artifact_location=f"staging/{table.table_id}.parquet", content_hash=f"hash-{table.table_id}", schema_fingerprint="schema-1", first_extraction_ordinal=0, last_extraction_ordinal=9, adapter_reference=adapter_ref, publication_state=PublicationState.COMPLETE) for table in tables)
    refs = tuple(SourceRecordReference(record_ref=f"{table.table_id}-record-{index}", source_id="source-1", snapshot_id="snapshot-1", table_id=table.table_id, batch_id=f"batch-{table.table_id}", extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for table in tables for index in range(10))
    snapshot_result = SourceSnapshotResult(snapshot=snapshot, batches=batches, record_references=refs, accounting=RowAccounting(input_records_observed=20, successfully_staged_records=20, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=20, staged_records=20, batch_count=2, configured_chunk_size=10, bytes_staged=1), table_observations=tuple(TableSnapshotObservation(table_id=table.table_id, rows_observed=10, status=TableObservationStatus.FULLY_OBSERVED) for table in tables))
    repo_root = Path(__file__).resolve().parents[3]
    root = repo_root / "workspace" / "test-temp" / "dependencies" / f"real-provider-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        batches = []
        for table in catalog.tables:
            path = root / "staging" / f"{table.table_id}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(list(rows[table.table_id])), path)
            batches.append(next(item for item in snapshot_result.batches if item.table_id == table.table_id).model_copy(update={
                "artifact_location": path.relative_to(root).as_posix(),
                "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            }))
        bound = snapshot_result.model_copy(update={"batches": tuple(batches)})
        request = DependencyRequest(
            request_id="real-provider-integration",
            source_id="source-1",
            snapshot_id="snapshot-1",
            selected_table_ids=("orders", "customers"),
            requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND),
        )
        policy = PrivacyPolicyService(project_root=root)
        adapter = DesbordanteDependencyAdapter(project_root=root, reader=DependencyStagedReader(), privacy_policy=policy)
        result = DependencyDiscoveryService(adapter, project_root=root, privacy_policy=policy).discover(
            request, catalog, bound, artifact_root=root / "artifacts"
        )

        assert result.capabilities[0].status.value == "AVAILABLE"
        assert result.capabilities[0].engine == "desbordante-docker"
        assert result.status is DependencyStageStatus.COMPLETE
        assert result.ucc_evidence and result.key_candidates
        assert result.functional_dependencies and result.inclusion_dependencies
        assert result.relationship_candidates
        assert all(item.columns[0].startswith(("orders-", "customers-")) for item in result.key_candidates)
        assert all(item.ucc_evidence_id in {evidence.evidence_id for evidence in result.ucc_evidence} for item in result.key_candidates)
        assert result.observation_scope.staged_rows_by_table == {"orders": 10, "customers": 10}
        assert result.observation_scope.provider_rows_by_table == {"orders": 10, "customers": 10}
        assert not (root / "privacy_ephemeral" / "dependency_discovery" / request.request_id).exists()
        published = [path.read_text(encoding="utf-8") for path in (root / "artifacts" / "dependencies").rglob("*.json")]
        serialized = json.dumps(published)
        assert all(value not in serialized for value in ("order-1", "region-1", "name-2"))
        assert any("provider_runtime" in item and "image:" in item for item in published)
    finally:
        shutil.rmtree(root, ignore_errors=True)
