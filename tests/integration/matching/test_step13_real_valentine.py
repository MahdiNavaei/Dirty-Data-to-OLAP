from __future__ import annotations

import hashlib
import shutil
import sys
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

RUNTIME = Path(__file__).resolve().parents[3] / "workspace" / "test-temp" / "valentine-runtime"
if RUNTIME.is_dir():
    sys.path.insert(0, str(RUNTIME))
else:
    pytest.skip("official Valentine optional runtime is not installed in this clean environment", allow_module_level=True)

from dirty_data_to_olap.adapters.matching.valentine import SchemaMatchingArtifactStore, ValentineSchemaMatchingAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.schema_matching import SchemaMatchingService
from tools.evaluate_schema_matching import evaluate_candidates
from dirty_data_to_olap.domain.contracts.schema_matching import (
    SchemaMatchMode,
    SchemaMatchRequest,
    SchemaMatcherReference,
    SchemaMatchStatus,
    schema_match_candidate_id,
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


def _source(source_id: str, table_id: str, columns: tuple[tuple[str, str], ...], root: Path) -> tuple[SourceCatalog, SourceSnapshotResult]:
    ref = AdapterReference(name="fixture", version="1", config_fingerprint="step13-fixture")
    source = SourceDescriptor(source_id=source_id, display_name=source_id, source_type=SourceType.CSV, file_locator=f"{source_id}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{source_id}", adapter_reference=ref)
    table = TableDescriptor(table_id=table_id, source_id=source_id, physical_name=table_id, table_kind=SourceTableKind.FILE, row_count=4)
    descriptors = tuple(ColumnDescriptor(column_id=f"{table_id}-{name}", table_id=table_id, physical_name=name, ordinal=index, native_physical_type=kind.upper(), normalized_physical_type=kind) for index, (name, kind) in enumerate(columns))
    catalog = SourceCatalog(source=source, tables=(table,), columns=descriptors, declared_constraints=())
    snapshot = SourceSnapshot(source_id=source_id, snapshot_id=f"snapshot-{source_id}", execution_context_id="step13-real-valentine", schema_fingerprint=source.schema_fingerprint, source_fingerprint=f"fixture-{source_id}", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=10, input_records_observed=4), extraction_policy=ExtractionPolicy(chunk_size=10), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=ref)
    rows = {
        "crm_customers": ({"customer_code": "C1", "status": "active", "order_total": 10.0}, {"customer_code": "C2", "status": "inactive", "order_total": 20.0}, {"customer_code": "C3", "status": "active", "order_total": 30.0}, {"customer_code": "C4", "status": "active", "order_total": 40.0}),
        "erp_customers": ({"client_no": "C1", "status": "active", "order_total": 10.0}, {"client_no": "C2", "status": "inactive", "order_total": 20.0}, {"client_no": "C3", "status": "active", "order_total": 30.0}, {"client_no": "C4", "status": "active", "order_total": 40.0}),
    }[table_id]
    path = root / "staging" / f"{table_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(rows)), path)
    batch = BatchReference(batch_id=f"batch-{table_id}", source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_index=0, row_count=4, artifact_location=path.relative_to(root).as_posix(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), schema_fingerprint=source.schema_fingerprint, first_extraction_ordinal=0, last_extraction_ordinal=3, adapter_reference=ref, publication_state=PublicationState.COMPLETE)
    refs = tuple(SourceRecordReference(record_ref=f"{table_id}-r{index}", source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_id=batch.batch_id, extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for index in range(4))
    result = SourceSnapshotResult(snapshot=snapshot, batches=(batch,), record_references=refs, accounting=RowAccounting(input_records_observed=4, successfully_staged_records=4, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=4, staged_records=4, batch_count=1, configured_chunk_size=10, bytes_staged=path.stat().st_size), table_observations=(TableSnapshotObservation(table_id=table_id, rows_observed=4, status=TableObservationStatus.FULLY_OBSERVED),))
    return catalog, result


def test_step13_real_valentine_instance_and_schema_modes_are_bounded_and_aggregate_only():
    repo_root = Path(__file__).resolve().parents[3]
    root = repo_root / "workspace" / "test-temp" / f"step13-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        crm_catalog, crm_snapshot = _source("crm", "crm_customers", (("customer_code", "text"), ("status", "text"), ("order_total", "numeric")), root)
        erp_catalog, erp_snapshot = _source("erp", "erp_customers", (("client_no", "text"), ("status", "text"), ("order_total", "numeric")), root)
        catalogs = {"crm": crm_catalog, "erp": erp_catalog}
        snapshots = {"crm": crm_snapshot, "erp": erp_snapshot}
        refs = (
            SchemaMatcherReference(matcher_id="coma-schema", name="Coma", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 3, "threshold": 0.0}),
            SchemaMatcherReference(matcher_id="distribution-instance", name="DistributionBased", version="1.0.0", configuration={"threshold1": 0.15, "threshold2": 0.15, "quantiles": 16, "process_num": 1}),
        )
        request = SchemaMatchRequest(request_id="step13-real-valentine", source_ids=("crm", "erp"), snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"}, selected_table_ids_by_source={"crm": ("crm_customers",), "erp": ("erp_customers",)}, mode=SchemaMatchMode.INSTANCE_AWARE, matcher_references=refs)
        policy = PrivacyPolicyService(project_root=root)
        service = SchemaMatchingService(ValentineSchemaMatchingAdapter(project_root=root, privacy_policy=policy), project_root=root, privacy_policy=policy)
        result = service.match(request, catalogs, snapshots, artifact_root=root / "workspace" / "runs" / "step13-real-valentine")
        assert result.status is SchemaMatchStatus.COMPLETE, result.failures
        assert result.capabilities[0].engine == "valentine"
        assert result.candidates
        assert result.observation_scope.sampled_rows_by_table == {"crm_customers": 4, "erp_customers": 4}
        assert result.pruning.matcher_calls == 2
        assert any(candidate.source_column_id == "crm_customers-customer_code" and candidate.target_column_id == "erp_customers-client_no" for candidate in result.candidates)
        assert all(candidate.state == "CANDIDATE" and not candidate.final_acceptance_allowed for candidate in result.candidates)
        assert schema_match_candidate_id(("crm", "crm_customers", "crm_customers-customer_code"), ("erp", "erp_customers", "erp_customers-client_no")) == schema_match_candidate_id(("erp", "erp_customers", "erp_customers-client_no"), ("crm", "crm_customers", "crm_customers-customer_code"))
        serialized = "\n".join(path.read_text(encoding="utf-8") for path in (root / "workspace" / "runs").rglob("*.json"))
        assert all(value not in serialized for value in ("C1", "C2", "active", "inactive"))
        assert "raw_native_score" in serialized
        assert all(key not in serialized.lower() for key in ('"probability"', '"confidence"'))
        ground_truth = (("crm_customers-customer_code", "erp_customers-client_no"), ("crm_customers-status", "erp_customers-status"))
        evaluations = tuple(evaluate_candidates(candidates=result.candidates, scores=result.scores, ground_truth=ground_truth, matcher_id=matcher_id, fixture_id="step13-cross-source-hard-negatives-v1", sample_identity=result.observation_scope.sample_identity) for matcher_id in ("coma-schema", "distribution-instance"))
        assert all(set(evaluation.recall_at_k) == {"1", "3", "5"} and evaluation.labeled_positive_count == 2 for evaluation in evaluations)
        print("STEP13_EVALUATION", [(item.matcher_id, dict(item.recall_at_k), item.mean_reciprocal_rank) for item in evaluations])
        SchemaMatchingArtifactStore(root).publish(result.model_copy(update={"evaluations": evaluations}), run_root=root / "workspace" / "runs" / "step13-real-valentine")
        assert list((root / "workspace" / "runs").rglob("*evaluation*.json"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_schema_only_is_allowed_without_instance_authorization_and_sampling_is_seed_bound():
    repo_root = Path(__file__).resolve().parents[3]
    root = repo_root / "workspace" / "test-temp" / f"step13-schema-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        crm_catalog, crm_snapshot = _source("crm", "crm_customers", (("customer_code", "text"), ("status", "text"), ("order_total", "numeric")), root)
        erp_catalog, erp_snapshot = _source("erp", "erp_customers", (("client_no", "text"), ("status", "text"), ("order_total", "numeric")), root)
        catalogs = {"crm": crm_catalog, "erp": erp_catalog}
        snapshots = {"crm": crm_snapshot, "erp": erp_snapshot}
        matcher = SchemaMatcherReference(matcher_id="coma-schema", name="Coma", version="1.0.0", configuration={"use_schema": True, "use_instances": False, "max_n": 3})
        base = SchemaMatchRequest(request_id="schema-only", source_ids=("crm", "erp"), snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"}, selected_table_ids_by_source={"crm": ("crm_customers",), "erp": ("erp_customers",)}, mode=SchemaMatchMode.SCHEMA_ONLY, matcher_references=(matcher,))
        policy = PrivacyPolicyService(project_root=root)
        service = SchemaMatchingService(ValentineSchemaMatchingAdapter(project_root=root, privacy_policy=policy), project_root=root, privacy_policy=policy)
        first = service.match(base.model_copy(update={"sample_seed": 11, "search_policy": base.search_policy.model_copy(update={"max_instance_rows_per_table": 2})}), catalogs, snapshots)
        second = service.match(base.model_copy(update={"sample_seed": 12, "search_policy": base.search_policy.model_copy(update={"max_instance_rows_per_table": 2})}), catalogs, snapshots)
        assert first.status is SchemaMatchStatus.COMPLETE
        assert first.observation_scope.reduced_scope is True
        assert first.observation_scope.sample_identity != second.observation_scope.sample_identity
        assert not first.failures
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_instance_matching_refuses_corrupt_hash_before_valentine():
    repo_root = Path(__file__).resolve().parents[3]
    root = repo_root / "workspace" / "test-temp" / f"step13-corrupt-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        crm_catalog, crm_snapshot = _source("crm", "crm_customers", (("customer_code", "text"),), root)
        erp_catalog, erp_snapshot = _source("erp", "erp_customers", (("client_no", "text"),), root)
        corrupt = erp_snapshot.model_copy(update={"batches": (erp_snapshot.batches[0].model_copy(update={"content_hash": "not-the-file-hash"}),)})
        request = SchemaMatchRequest(request_id="corrupt-instance", source_ids=("crm", "erp"), snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"}, selected_table_ids_by_source={"crm": ("crm_customers",), "erp": ("erp_customers",)}, mode=SchemaMatchMode.INSTANCE_AWARE, matcher_references=(SchemaMatcherReference(matcher_id="coma-schema", name="Coma", version="1.0.0", configuration={"use_schema": True, "use_instances": False}),))
        policy = PrivacyPolicyService(project_root=root)
        result = SchemaMatchingService(ValentineSchemaMatchingAdapter(project_root=root, privacy_policy=policy), project_root=root, privacy_policy=policy).match(request, {"crm": crm_catalog, "erp": erp_catalog}, {"crm": crm_snapshot, "erp": corrupt})
        assert result.status is SchemaMatchStatus.FAILED
        assert result.failures[0].kind.value == "STAGED_INPUT_INTEGRITY_FAILED"
        assert not result.candidates
    finally:
        shutil.rmtree(root, ignore_errors=True)
