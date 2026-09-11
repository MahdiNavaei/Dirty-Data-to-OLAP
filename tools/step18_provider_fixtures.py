"""Synthetic Step18 provider fixtures without a pytest/test-module dependency."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule,
    ERClusteringPolicy,
    ERComparisonSpecification,
    ERThresholdPolicy,
    ERTrainingPolicy,
    EntityResolutionMode,
    EntityResolutionNormalizationRule,
    EntityResolutionSpec,
    IdentityFieldSpecification,
)
from dirty_data_to_olap.domain.contracts.privacy import PrivacyPolicy, SensitivityLevel, ClassificationState
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


def step18_privacy_policy() -> PrivacyPolicy:
    """Represent the checked-in safe policy without requiring a YAML parser in provider venvs."""
    return PrivacyPolicy(
        policy_id="privacy-v1",
        version="1.0",
        unknown_state=ClassificationState.UNKNOWN,
        unknown_sensitivity=SensitivityLevel.SENSITIVE,
        raw_staging_sensitivity=SensitivityLevel.RESTRICTED,
        raw_staging_allowed=True,
        logs_allow_raw=False,
        debug_allow_raw_staging=False,
        export_allow_raw=False,
        external_allow_raw_sensitive=False,
        external_allow_unknown=False,
        future_llm_requires_guard=True,
    )


def schema_source(
    source_id: str,
    table_id: str,
    columns: tuple[tuple[str, str], ...],
    root: Path,
    rows_override: tuple[dict[str, object], ...] | None = None,
) -> tuple[SourceCatalog, SourceSnapshotResult]:
    ref = AdapterReference(name="fixture", version="1", config_fingerprint="step18-provider-fixture")
    source = SourceDescriptor(source_id=source_id, display_name=source_id, source_type=SourceType.CSV, file_locator=f"{source_id}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{source_id}", adapter_reference=ref)
    row_count = len(rows_override) if rows_override is not None else 4
    table = TableDescriptor(table_id=table_id, source_id=source_id, physical_name=table_id, table_kind=SourceTableKind.FILE, row_count=row_count)
    descriptors = tuple(ColumnDescriptor(column_id=f"{table_id}-{name}", table_id=table_id, physical_name=name, ordinal=index, native_physical_type=kind.upper(), normalized_physical_type=kind) for index, (name, kind) in enumerate(columns))
    catalog = SourceCatalog(source=source, tables=(table,), columns=descriptors, declared_constraints=())
    snapshot = SourceSnapshot(source_id=source_id, snapshot_id=f"snapshot-{source_id}", execution_context_id="step18-provider-fixture", schema_fingerprint=source.schema_fingerprint, source_fingerprint=f"fixture-{source_id}", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=100, input_records_observed=row_count), extraction_policy=ExtractionPolicy(chunk_size=100), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=ref)
    rows = rows_override or tuple({name: ("active" if i % 2 else "inactive") if name == "status" else float(i * 10) if kind == "numeric" else f"{name}-{i}" for name, kind in columns} for i in range(1, 5))
    path = root / "staging" / f"{table_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(rows)), path)
    batch = BatchReference(batch_id=f"batch-{table_id}", source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_index=0, row_count=len(rows), artifact_location=path.relative_to(root).as_posix(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), schema_fingerprint=source.schema_fingerprint, first_extraction_ordinal=0 if rows else None, last_extraction_ordinal=len(rows) - 1 if rows else None, adapter_reference=ref, publication_state=PublicationState.COMPLETE)
    refs = tuple(SourceRecordReference(record_ref=f"{table_id}-r{index}", source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_id=batch.batch_id, extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for index in range(len(rows)))
    result = SourceSnapshotResult(snapshot=snapshot, batches=(batch,), record_references=refs, accounting=RowAccounting(input_records_observed=len(rows), successfully_staged_records=len(rows), explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=len(rows), staged_records=len(rows), batch_count=1, configured_chunk_size=100, bytes_staged=path.stat().st_size), table_observations=(TableSnapshotObservation(table_id=table_id, rows_observed=len(rows), status=TableObservationStatus.FULLY_OBSERVED),))
    return catalog, result


def entity_source(source_id: str, table_id: str, root: Path, rows: tuple[dict[str, object], ...]) -> tuple[SourceCatalog, SourceSnapshotResult]:
    ref = AdapterReference(name="fixture", version="1", config_fingerprint="step18-provider-fixture")
    source = SourceDescriptor(source_id=source_id, display_name=source_id, source_type=SourceType.CSV, file_locator=f"{source_id}.csv", selection_scope=SelectionScope(), schema_fingerprint=f"schema-{source_id}", adapter_reference=ref)
    table = TableDescriptor(table_id=table_id, source_id=source_id, physical_name=table_id, table_kind=SourceTableKind.FILE, row_count=len(rows))
    columns = tuple(ColumnDescriptor(column_id=f"{table_id}-{name}", table_id=table_id, physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text") for index, name in enumerate(("name", "email", "phone")))
    catalog = SourceCatalog(source=source, tables=(table,), columns=columns, declared_constraints=())
    snapshot = SourceSnapshot(source_id=source_id, snapshot_id=f"snapshot-{source_id}", execution_context_id="step18-provider-fixture", schema_fingerprint=source.schema_fingerprint, source_fingerprint=f"fixture-{source_id}", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=ObservationScope(mode=ObservationMode.FULL, chunk_size=100, input_records_observed=len(rows)), extraction_policy=ExtractionPolicy(chunk_size=100), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=ref)
    path = root / "staging" / f"{table_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(rows)), path)
    batch = BatchReference(batch_id=f"batch-{table_id}", source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_index=0, row_count=len(rows), artifact_location=path.relative_to(root).as_posix(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), schema_fingerprint=source.schema_fingerprint, first_extraction_ordinal=0, last_extraction_ordinal=len(rows) - 1, adapter_reference=ref, publication_state=PublicationState.COMPLETE)
    refs = tuple(SourceRecordReference(record_ref=row["record_ref"], source_id=source_id, snapshot_id=snapshot.snapshot_id, table_id=table_id, batch_id=batch.batch_id, extraction_ordinal=index, locator_kind=RecordLocatorKind.SNAPSHOT_ORDINAL, stability_scope=StabilityScope.SNAPSHOT_ONLY) for index, row in enumerate(rows))
    result = SourceSnapshotResult(snapshot=snapshot, batches=(batch,), record_references=refs, accounting=RowAccounting(input_records_observed=len(rows), successfully_staged_records=len(rows), explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=len(rows), staged_records=len(rows), batch_count=1, configured_chunk_size=100, bytes_staged=path.stat().st_size), table_observations=(TableSnapshotObservation(table_id=table_id, rows_observed=len(rows), status=TableObservationStatus.FULLY_OBSERVED),))
    return catalog, result


def entity_spec(table_suffix: str = "v4") -> EntityResolutionSpec:
    fields = tuple(IdentityFieldSpecification(field_id=field, source_id=source, snapshot_id=f"snapshot-{source}", table_id=f"{source}_customers_{table_suffix}", column_id=f"{source}_customers_{table_suffix}-{field}", physical_name=field, semantic_role=field, normalization_rule_id=f"norm-{field}") for source in ("crm", "erp") for field in ("name", "email", "phone"))
    return EntityResolutionSpec(
        spec_id=f"step18-{table_suffix}-splink",
        entity_family="person",
        mode=EntityResolutionMode.LINK_AND_DEDUPE,
        source_ids=("crm", "erp"),
        snapshot_ids={"crm": "snapshot-crm", "erp": "snapshot-erp"},
        table_ids_by_source={"crm": (f"crm_customers_{table_suffix}",), "erp": (f"erp_customers_{table_suffix}",)},
        identity_fields=fields,
        normalization_rules=tuple(EntityResolutionNormalizationRule(rule_id=f"norm-{field}", version="1", applies_to=(field,)) for field in ("name", "email", "phone")),
        blocking_rules=(ERBlockingRule(rule_id="block-email", version="1", field_ids=("email",), sql_expression="l.email = r.email"), ERBlockingRule(rule_id="block-phone", version="1", field_ids=("phone",), sql_expression="l.phone = r.phone")),
        comparisons=(ERComparisonSpecification(comparison_id="cmp-name", field_id="name", method="exact"), ERComparisonSpecification(comparison_id="cmp-email", field_id="email", method="exact")),
        training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-email",), max_u_pairs=500),
        threshold_policy=ERThresholdPolicy(match_probability_threshold=0.8, review_probability_threshold=0.5),
        clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1"),
    )
