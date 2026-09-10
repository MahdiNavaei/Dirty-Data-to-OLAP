from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

RUNTIME = Path(__file__).resolve().parents[3] / "workspace" / "test-temp" / "entity-resolution" / "splink-runtime"
if RUNTIME.is_dir():
    sys.path.insert(0, str(RUNTIME))
else:
    pytest.skip("official Splink optional runtime is not installed in this clean environment", allow_module_level=True)

from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule, ERClusteringPolicy, ERComparisonSpecification, EntityResolutionMode,
    ERThresholdPolicy, ERTrainingPolicy, EntityResolutionSpec, IdentityFieldSpecification,
    EntityResolutionStatus, EntityResolutionNormalizationRule,
)
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference, BatchReference, ColumnDescriptor, ExtractionMetrics, ExtractionPolicy,
    ObservationMode, ObservationScope, PublicationState, RecordLocatorKind, RowAccounting,
    SelectionScope, SnapshotConsistency, SourceCatalog, SourceDescriptor, SourceRecordReference,
    SourceSnapshot, SourceSnapshotResult, SourceTableKind, SourceType, StabilityScope,
    TableDescriptor, TableObservationStatus, TableSnapshotObservation,
)


def _fixture(source_id: str, table_id: str, root: Path, rows: tuple[dict[str, object], ...]):
    ref = AdapterReference(name="fixture", version="1", config_fingerprint="step14-fixture")
    source = SourceDescriptor(source_id=source_id, display_name=source_id, source_type=SourceType.CSV, file_locator=f"{source_id}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{source_id}", adapter_reference=ref)
    table = TableDescriptor(table_id=table_id, source_id=source_id, physical_name=table_id, table_kind=SourceTableKind.FILE, row_count=len(rows))
    columns = tuple(ColumnDescriptor(column_id=f"{table_id}-{name}", table_id=table_id, physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text") for index, name in enumerate(("name", "email", "phone")))
    catalog = SourceCatalog(source=source, tables=(table,), columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(source_id=source_id, snapshot_id=f"snapshot-{source_id}", execution_context_id="step14-real-splink", schema_fingerprint=source.schema_fingerprint, source_fingerprint=f"fixture-{source_id}", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=100, input_records_observed=len(rows)), extraction_policy=ExtractionPolicy(chunk_size=100), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=ref)
    path = root / "staging" / f"{table_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(rows)), path)
    batch = BatchReference(batch_id=f"batch-{table_id}", source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_index=0, row_count=len(rows), artifact_location=path.relative_to(root).as_posix(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), schema_fingerprint=source.schema_fingerprint, first_extraction_ordinal=0, last_extraction_ordinal=len(rows)-1, adapter_reference=ref, publication_state=PublicationState.COMPLETE)
    refs = tuple(SourceRecordReference(record_ref=row["record_ref"], source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_id=batch.batch_id, extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for index, row in enumerate(rows))
    result = SourceSnapshotResult(snapshot=snapshot, batches=(batch,), record_references=refs, accounting=RowAccounting(input_records_observed=len(rows), successfully_staged_records=len(rows), explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=len(rows), staged_records=len(rows), batch_count=1, configured_chunk_size=100, bytes_staged=path.stat().st_size), table_observations=(TableSnapshotObservation(table_id=table_id, rows_observed=len(rows), status=TableObservationStatus.FULLY_OBSERVED),))
    return catalog, result


def test_step14_real_splink_is_bounded_local_and_candidate_only():
    root = Path(__file__).resolve().parents[3] / "workspace" / "test-temp" / f"step14-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        crm_rows = tuple({"record_ref": f"crm-r{i}", "name": name, "email": email, "phone": phone} for i, (name, email, phone) in enumerate((("Alice Smith", "alice@example.com", "+12025550101"), ("Bob Jones", "BOB@example.com", "+12025550102"), ("Common Name", "common-crm@example.com", "0000000000"), ("Household A", "house-a@example.com", "+12025550103")), 1))
        erp_rows = tuple({"record_ref": f"erp-r{i}", "name": name, "email": email, "phone": phone} for i, (name, email, phone) in enumerate((("Alice Smyth", "alice@example.com", "+12025550101"), ("Bob Jones", "bob@example.com", "+12025550102"), ("Common Name", "common-erp@example.com", "0000000000"), ("Household B", "house-b@example.com", "+12025550103")), 1))
        crm_catalog, crm_snapshot = _fixture("crm", "crm_customers", root, crm_rows)
        erp_catalog, erp_snapshot = _fixture("erp", "erp_customers", root, erp_rows)
        fields = tuple(IdentityFieldSpecification(field_id=field, source_id=source, snapshot_id=f"snapshot-{source}", table_id=table, column_id=f"{table}-{field}", physical_name=field, semantic_role=field, normalization_rule_id=f"norm-{field}") for source, table in (("crm", "crm_customers"), ("erp", "erp_customers")) for field in ("name", "email", "phone"))
        spec = EntityResolutionSpec(spec_id="step14-real-splink", entity_family="person", mode=EntityResolutionMode.LINK_ONLY, source_ids=("crm", "erp"), snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"}, table_ids_by_source={"crm": ("crm_customers",), "erp": ("erp_customers",)}, identity_fields=fields, normalization_rules=tuple(EntityResolutionNormalizationRule(rule_id=f"norm-{field}", version="1", applies_to=(field,)) for field in ("name", "email", "phone")), blocking_rules=(ERBlockingRule(rule_id="block-email", version="1", field_ids=("email",), sql_expression="l.email = r.email"), ERBlockingRule(rule_id="block-phone", version="1", field_ids=("phone",), sql_expression="l.phone = r.phone")), comparisons=(ERComparisonSpecification(comparison_id="cmp-name", field_id="name", method="exact"), ERComparisonSpecification(comparison_id="cmp-email", field_id="email")), training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-phone",), max_u_pairs=100), threshold_policy=ERThresholdPolicy(match_probability_threshold=0.8, review_probability_threshold=0.5), clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1"))
        policy = PrivacyPolicyService(project_root=root)
        batch_ids = ("batch-crm_customers", "batch-erp_customers")
        decision = policy.authorize_entity_resolution_analysis(spec.privacy_context, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=tuple(field.column_id for field in fields), batch_ids=batch_ids)
        assert decision.allowed
        auth = policy.entity_resolution_authorization_for_decision(decision)
        result = SplinkEntityResolutionAdapter(project_root=root, privacy_policy=policy).run(spec, {"crm": crm_catalog, "erp": erp_catalog}, {"crm": crm_snapshot, "erp": erp_snapshot}, authorization=auth, artifact_root=root / "artifacts")
        assert result.status in {EntityResolutionStatus.COMPLETE, EntityResolutionStatus.INCOMPLETE}, result.failures
        assert result.capabilities[0].engine == "splink"
        assert result.observation_scope.records_read == 8
        assert result.metrics.all_pairs == 16
        assert all("alice@example.com" not in json.dumps(item.model_dump(mode="json"), ensure_ascii=False) for item in result.edges)
        assert all("SourceRecordCanonicalMap" not in json.dumps(result.model_dump(mode="json")) for _ in (0,))
        artifact = root / "artifacts" / "entity_resolution_result.json"
        assert artifact.is_file()
        assert "alice@example.com" not in artifact.read_text(encoding="utf-8")
        private = root / "workspace" / "test-temp" / "entity-resolution"
        assert not list(private.rglob("private.duckdb")) if private.exists() else True
    finally:
        shutil.rmtree(root, ignore_errors=True)
