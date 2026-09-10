from __future__ import annotations

import shutil
import hashlib
from pathlib import Path

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter, ProviderFD, ProviderIND, ProviderUCC, _encode_cell, _ind_metrics
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.adapters.dependencies.staged import DependencyInputIntegrityError, DependencyStagedReader
from dirty_data_to_olap.domain.contracts.dependency import (
    DependencyKind,
    DependencyRequest,
    DependencySearchPolicy,
    DependencyStageStatus,
    NullPolicy,
    RelationshipCandidate,
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
    RecordLocatorKind,
)
from dirty_data_to_olap.adapters.dependencies.staged import StagedDependencyRow


class _Ucc:
    def __init__(self, indices):
        self.indices = indices


class _Fd:
    def __init__(self, lhs_indices, rhs_index):
        self.lhs_indices = lhs_indices
        self.rhs_index = rhs_index


class _Combination:
    def __init__(self, table_index, column_indices):
        self.table_index = table_index
        self.column_indices = column_indices


class _Ind:
    def __init__(self, lhs, rhs):
        self._lhs = lhs
        self._rhs = rhs

    def get_lhs(self):
        return self._lhs

    def get_rhs(self):
        return self._rhs


class _Engine:
    name = "fake-desbordante"
    version = "test"
    runtime_identity = "unit-test-provider-boundary"

    def discover_ucc(self, path, *, max_arity):
        return (ProviderUCC((0,)), ProviderUCC((1, 2)))

    def discover_fd(self, path, *, approximate, max_error, max_lhs):
        return (ProviderFD((0,), 1),)

    def discover_ind(self, paths, *, approximate, max_error, max_arity):
        return (ProviderIND(0, (1,), 1, (0,)),)


class _Reader:
    rows = {
        "orders": (
            {"order_id": "o1", "customer_id": "c1", "region": "north"},
            {"order_id": "o2", "customer_id": "c2", "region": "south"},
            {"order_id": "o3", "customer_id": "missing", "region": None},
        ),
        "customers": (
            {"customer_id": "c1", "name": "A", "region": "north"},
            {"customer_id": "c2", "name": "B", "region": "south"},
            {"customer_id": "c2", "name": "B2", "region": "south"},
        ),
    }

    def iter_table(self, snapshot_result, catalog, table, physical_columns, *, project_root):
        for index, row in enumerate(self.rows[table.table_id]):
            yield StagedDependencyRow(record_ref=f"{table.table_id}-record-{index}", extraction_ordinal=index, values={key: row.get(key) for key in physical_columns})


def _fixture():
    adapter = AdapterReference(name="fixture", version="1", config_fingerprint="fixture")
    source = SourceDescriptor(source_id="source-1", display_name="fixture", source_type=SourceType.CSV, file_locator="fixture.csv", selection_scope=SelectionScope(), schema_fingerprint="schema-1", adapter_reference=adapter)
    tables = (
        TableDescriptor(table_id="orders", source_id="source-1", physical_name="orders", table_kind=SourceTableKind.FILE, row_count=3),
        TableDescriptor(table_id="customers", source_id="source-1", physical_name="customers", table_kind=SourceTableKind.FILE, row_count=3),
    )
    columns = tuple(
        ColumnDescriptor(column_id=f"{table.table_id}-{name}", table_id=table.table_id, physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text")
        for table, names in ((tables[0], ("order_id", "customer_id", "region")), (tables[1], ("customer_id", "name", "region")))
        for index, name in enumerate(names)
    )
    catalog = SourceCatalog(source=source, tables=tables, columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(source_id="source-1", snapshot_id="snapshot-1", execution_context_id="run-1", schema_fingerprint="schema-1", source_fingerprint="fixture", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=10, input_records_observed=6), extraction_policy=ExtractionPolicy(chunk_size=10), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=adapter)
    batches = tuple(BatchReference(batch_id=f"batch-{table.table_id}", source_id="source-1", snapshot_id="snapshot-1", table_id=table.table_id, batch_index=0, row_count=3, artifact_location=f"staging/{table.table_id}.parquet", content_hash=f"hash-{table.table_id}", schema_fingerprint="schema-1", first_extraction_ordinal=0, last_extraction_ordinal=2, adapter_reference=adapter, publication_state=PublicationState.COMPLETE) for table in tables)
    refs = tuple(SourceRecordReference(record_ref=f"{table.table_id}-record-{index}", source_id="source-1", snapshot_id="snapshot-1", table_id=table.table_id, batch_id=f"batch-{table.table_id}", extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for table in tables for index in range(3))
    snapshot_result = SourceSnapshotResult(snapshot=snapshot, batches=batches, record_references=refs, accounting=RowAccounting(input_records_observed=6, successfully_staged_records=6, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=6, staged_records=6, batch_count=2, configured_chunk_size=10, bytes_staged=1), table_observations=tuple(TableSnapshotObservation(table_id=table.table_id, rows_observed=3, status=TableObservationStatus.FULLY_OBSERVED) for table in tables))
    return catalog, snapshot_result


def test_dependency_adapter_emits_measured_candidates_and_cleans_ephemeral_input():
    catalog, snapshot_result = _fixture()
    request = DependencyRequest(request_id="dependency-run", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND), null_policy=NullPolicy.EXCLUDE_PHYSICAL_NULL)
    project_root = Path.cwd() / "tests" / "dependency_unit_artifacts"
    shutil.rmtree(project_root, ignore_errors=True)
    project_root.mkdir(parents=True)
    try:
        policy = PrivacyPolicyService(project_root=project_root)
        decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in snapshot_result.batches))
        result = DesbordanteDependencyAdapter(project_root=project_root, engine=_Engine(), reader=_Reader(), privacy_policy=policy).discover(request, catalog, snapshot_result, artifact_root=project_root / "artifacts", authorization=policy.authorization_for_decision(decision))
        assert result.status is DependencyStageStatus.COMPLETE
        assert result.key_candidates
        assert result.ucc_evidence
        assert result.key_candidates[0].ucc_evidence_id == result.ucc_evidence[0].evidence_id
        assert all(column.startswith(("orders-", "customers-")) for column in result.key_candidates[0].columns)
        assert result.functional_dependencies[0].support_ratio == 1.0
        assert result.inclusion_dependencies[0].orphan_count == 1
        assert result.inclusion_dependencies[0].coverage_ratio == 2 / 3
        assert not result.relationship_candidates
        assert result.inclusion_dependencies[0].low_cardinality_risk is True
        assert not (project_root / "privacy_ephemeral" / "dependency_discovery" / request.request_id).exists()
        assert all((project_root / ref.artifact_location).is_file() for ref in result.artifacts)
    finally:
        shutil.rmtree(project_root, ignore_errors=True)


def test_relationship_candidate_contract_cannot_be_promoted():
    catalog, snapshot_result = _fixture()
    try:
        RelationshipCandidate(candidate_id="r", source_id="source-1", snapshot_id="snapshot-1", from_table="orders", from_columns=("customer_id",), to_table="customers", to_columns=("customer_id",), type_compatible=True, low_cardinality_risk=False, evidence_refs=("ind-1",), state="ACCEPTED")
    except ValueError as error:
        assert "candidate" in str(error)
    else:
        raise AssertionError("accepted relationships must not cross the Step12 boundary")


def test_column_pair_budget_fails_closed_before_ind_engine_call():
    catalog, snapshot_result = _fixture()
    request = DependencyRequest(request_id="bounded-run", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.IND,), search_policy=DependencySearchPolicy(max_column_pairs=1))
    policy = PrivacyPolicyService(project_root=Path.cwd())
    decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in snapshot_result.batches))
    result = DesbordanteDependencyAdapter(project_root=Path.cwd(), engine=_Engine(), reader=_Reader(), privacy_policy=policy).discover(request, catalog, snapshot_result, authorization=policy.authorization_for_decision(decision))
    assert result.status is DependencyStageStatus.FAILED
    assert result.search_stats.pruned_column_pairs > 0
    assert any(failure.kind.value == "BUDGET_EXCEEDED" for failure in result.failures)


def test_staged_reader_requires_hash_bound_complete_parquet():
    catalog, snapshot_result = _fixture()
    root = Path.cwd() / "tests" / "dependency_reader_artifacts"
    shutil.rmtree(root, ignore_errors=True)
    (root / "staging").mkdir(parents=True)
    try:
        batches = []
        for table in catalog.tables:
            values = list(_Reader.rows[table.table_id])
            path = root / "staging" / f"{table.table_id}.parquet"
            pq.write_table(pa.Table.from_pylist(values), path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            batch = next(item for item in snapshot_result.batches if item.table_id == table.table_id).model_copy(update={"content_hash": digest, "artifact_location": path.relative_to(root).as_posix()})
            batches.append(batch)
        bound = snapshot_result.model_copy(update={"batches": tuple(batches)})
        reader = DependencyStagedReader()
        rows = list(reader.iter_table(bound, catalog, catalog.tables[0], ("order_id", "customer_id", "region"), project_root=root))
        assert len(rows) == 3 and rows[0].record_ref == "orders-record-0"
        (root / "staging" / "orders.parquet").write_bytes(b"tampered")
        with pytest.raises(DependencyInputIntegrityError, match="hash mismatch"):
            list(reader.iter_table(bound, catalog, catalog.tables[0], ("order_id",), project_root=root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_context_alone_cannot_authorize_dependency_execution():
    catalog, snapshot_result = _fixture()
    request = DependencyRequest(request_id="unauthorized", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.UCC,))
    result = DesbordanteDependencyAdapter(project_root=Path.cwd(), engine=_Engine(), reader=_Reader()).discover(request, catalog, snapshot_result)
    assert result.status is DependencyStageStatus.FAILED
    assert result.failures[0].kind.value == "PRIVACY_BLOCKED"


def test_config_fingerprint_excludes_run_identity_and_changes_for_material_policy():
    first = DependencyRequest(request_id="run-a", source_id="source-1", snapshot_id="snap-a", selected_table_ids=("orders",))
    second = first.model_copy(update={"request_id": "run-b", "snapshot_id": "snap-b"})
    assert first.request_id != second.request_id
    from dirty_data_to_olap.domain.contracts.dependency import dependency_config_hash
    assert dependency_config_hash(first) == dependency_config_hash(second)
    assert dependency_config_hash(first.model_copy(update={"null_policy": NullPolicy.NULLS_EQUAL})) != dependency_config_hash(first)


def test_null_encoding_keeps_physical_null_literal_and_empty_string_distinct():
    null_value = _encode_cell(None, NullPolicy.NULLS_EQUAL, 0, "column-1")
    literal = _encode_cell("__DDO_PHYSICAL_NULL__", NullPolicy.NULLS_EQUAL, 0, "column-1")
    empty = _encode_cell("", NullPolicy.NULLS_EQUAL, 0, "column-1")
    assert len({null_value, literal, empty}) == 3
    assert _encode_cell(None, NullPolicy.NULLS_BREAK_DEPENDENCY, 0, "column-1") != _encode_cell(None, NullPolicy.NULLS_BREAK_DEPENDENCY, 1, "column-1")


def test_orphan_reference_stays_with_original_row_after_null_filtering():
    columns = tuple(ColumnDescriptor(column_id=f"c-{name}", table_id="orders", physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text") for index, name in enumerate(("customer_id",)))
    metrics = _ind_metrics(({"customer_id": None}, {"customer_id": "orphan"}, {"customer_id": "matched"}), ("row0", "row1", "row2"), ({"customer_id": "matched"},), columns, columns, ("customer_id",), ("customer_id",), NullPolicy.EXCLUDE_PHYSICAL_NULL)
    assert metrics["refs"] == ["row1"]


def test_arity_or_column_bounds_make_result_incomplete_not_complete():
    catalog, snapshot_result = _fixture()
    request = DependencyRequest(request_id="bounded-columns", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.UCC,), search_policy=DependencySearchPolicy(max_columns_per_table=1))
    policy = PrivacyPolicyService(project_root=Path.cwd())
    decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in snapshot_result.batches))
    result = DesbordanteDependencyAdapter(project_root=Path.cwd(), engine=_Engine(), reader=_Reader(), privacy_policy=policy).discover(request, catalog, snapshot_result, authorization=policy.authorization_for_decision(decision))
    assert result.status is DependencyStageStatus.INCOMPLETE
    assert result.search_stats.columns_excluded_by_bound > 0
    assert result.search_stats.completeness == "INCOMPLETE"


def test_incompatible_types_retain_ind_evidence_without_relationship_candidate():
    catalog, snapshot_result = _fixture()
    changed = catalog.model_copy(update={"columns": tuple(column.model_copy(update={"normalized_physical_type": "integer"}) if column.column_id == "customers-customer_id" else column for column in catalog.columns)})
    request = DependencyRequest(request_id="incompatible-types", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.IND,))
    policy = PrivacyPolicyService(project_root=Path.cwd())
    decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in snapshot_result.batches))
    result = DesbordanteDependencyAdapter(project_root=Path.cwd(), engine=_Engine(), reader=_Reader(), privacy_policy=policy).discover(request, changed, snapshot_result, authorization=policy.authorization_for_decision(decision))
    assert result.inclusion_dependencies and not result.relationship_candidates
