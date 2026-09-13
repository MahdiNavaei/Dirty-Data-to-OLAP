"""Independent typed source truth and scoped accounting for the order product."""

from __future__ import annotations

from decimal import Decimal

from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel, RecordDisposition
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, stable_id
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingArtifact,
    RecordAccountingEntry,
    RecordAccountingScope,
    SourceTruthAccountingExpectation,
    SourceTruthAggregateExpectation,
    SourceTruthEntity,
    SourceTruthFact,
    SourceTruthManifest,
    SourceTruthRecord,
    SourceTruthRelationship,
)


def build_order_truth_and_accounting(*, run_id: str, catalog: SourceCatalog, snapshot: SourceSnapshotResult, dataset, canonical: CanonicalModel, fact, measure, policy) -> tuple[SourceTruthManifest, RecordAccountingArtifact]:
    table = policy.source_table(catalog)
    source_rows = list(dataset.table(table.table_id).rows)
    maps = {item.record_ref: item for item in canonical.source_record_maps}
    records: list[SourceTruthRecord] = []
    entities: list[SourceTruthEntity] = []
    facts: list[SourceTruthFact] = []
    relationships: list[SourceTruthRelationship] = []
    source_entries: list[RecordAccountingEntry] = []
    canonical_entries: list[RecordAccountingEntry] = []
    accounting_expectations: list[SourceTruthAccountingExpectation] = []
    aggregate_rows: list[tuple[str, Decimal]] = []
    for row in source_rows:
        record_ref = row.source_record_refs[0]
        canonical_id = maps[record_ref].canonical_entity_id
        order_id = str(row.value_for("order_id"))
        current_date = row.value_for("order_date")
        quantity = row.value_for(measure.field_name)
        quantity_decimal = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
        fact_ref = stable_id("truth-fact", {"run": run_id, "record": record_ref})
        ordinal = next(item.extraction_ordinal for item in snapshot.record_references if item.record_ref == record_ref)
        records.append(SourceTruthRecord(record_ref=record_ref, source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_id=table.table_id, extraction_ordinal=ordinal, subject_type="order", canonical_entity_id=canonical_id, terminal_disposition=RecordDisposition.EMITTED_DIRECT, output_reference=fact_ref, values=row.value_map, provenance_refs=(snapshot.snapshot.snapshot_id, record_ref, policy.provenance)))
        entities.append(SourceTruthEntity(entity_type="order", canonical_entity_id=canonical_id, source_record_refs=(record_ref,), expected_attributes={"order_id": order_id}, provenance_refs=(record_ref, canonical_id)))
        facts.append(SourceTruthFact(fact_ref=fact_ref, fact_id=fact.fact_id, source_record_refs=(record_ref,), canonical_event_id=canonical_id, grain_values={"order_id": order_id}, dimension_entity_ids={"dim_order": canonical_id}, measure_values={measure.field_name: quantity_decimal}, date_value=current_date, provenance_refs=(record_ref, fact.fact_id)))
        relationships.append(SourceTruthRelationship(relationship_ref=fact.relationship_refs[0], from_record_ref=record_ref, to_canonical_entity_id=canonical_id, required=True, provenance_refs=(record_ref, fact.relationship_refs[0])))
        source_entries.append(RecordAccountingEntry(input_record_ref=record_ref, disposition=RecordDisposition.EMITTED_DIRECT, output_or_group_ref=canonical_id, transformation_or_policy_ref=policy.provenance, reason="source-local order event is emitted directly", provenance_refs=(record_ref, canonical_id)))
        canonical_entries.append(RecordAccountingEntry(input_record_ref=canonical_id, disposition=RecordDisposition.EMITTED_DIRECT, output_or_group_ref=fact_ref, transformation_or_policy_ref=policy.provenance, reason="finalized order event is materialized at the reviewed fact grain", provenance_refs=(canonical_id, fact_ref)))
        accounting_expectations.append(SourceTruthAccountingExpectation(boundary=AccountingBoundary.SOURCE_TO_CANONICAL, input_record_ref=record_ref, expected_disposition=RecordDisposition.EMITTED_DIRECT, expected_output_or_group_ref=canonical_id, reason_contains="emitted directly", provenance_refs=(record_ref,)))
        accounting_expectations.append(SourceTruthAccountingExpectation(boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL, input_record_ref=canonical_id, expected_disposition=RecordDisposition.EMITTED_DIRECT, expected_output_or_group_ref=fact_ref, reason_contains="materialized", provenance_refs=(canonical_id,)))
        aggregate_rows.append((str(current_date), quantity_decimal))
    total = sum((item[1] for item in aggregate_rows), Decimal("0"))
    by_date: dict[str, Decimal] = {}
    for key, value in aggregate_rows:
        by_date[key] = by_date.get(key, Decimal("0")) + value
    aggregate_expectations = (
        SourceTruthAggregateExpectation(aggregate_id="quantity_global", fact_id=fact.fact_id, measure_field=measure.field_name, semantic_measure_ref=measure.measure_id, operation="SUM", expected=total, unit_semantics=measure.unit_semantics, provenance_refs=(fact.fact_id, measure.measure_id)),
        SourceTruthAggregateExpectation(aggregate_id="quantity_by_date", fact_id=fact.fact_id, measure_field=measure.field_name, semantic_measure_ref=measure.measure_id, operation="SUM", group_by=("dim_date",), expected=total, expected_by_key=by_date, unit_semantics=measure.unit_semantics, provenance_refs=(fact.fact_id, measure.measure_id)),
    )
    truth = SourceTruthManifest(truth_id=stable_id("truth", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "records": [item.record_ref for item in records]}), truth_version=policy.version, source_snapshot_id=snapshot.snapshot.snapshot_id, source_schema_fingerprints={catalog.source_id: catalog.source.schema_fingerprint}, records=tuple(records), entities=tuple(entities), relationships=tuple(relationships), facts=tuple(facts), accounting_expectations=tuple(accounting_expectations), aggregate_expectations=aggregate_expectations, expected_aggregates={"aggregates": [item.model_dump(mode="json") for item in aggregate_expectations]}, provenance_refs=("application.product_truth", snapshot.snapshot.snapshot_id, catalog.source.schema_fingerprint, policy.provenance))
    accounting = RecordAccountingArtifact(accounting_id=stable_id("accounting", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id}), run_id=run_id, scopes=(
        RecordAccountingScope(scope_id=stable_id("accounting-scope", {"run": run_id, "boundary": AccountingBoundary.SOURCE_TO_CANONICAL.value}), boundary=AccountingBoundary.SOURCE_TO_CANONICAL, input_object_ref=snapshot.snapshot.snapshot_id, input_record_refs=tuple(item.record_ref for item in records), entries=tuple(source_entries), policy_version=policy.version, provenance_refs=(truth.truth_id, snapshot.snapshot.snapshot_id)),
        RecordAccountingScope(scope_id=stable_id("accounting-scope", {"run": run_id, "boundary": AccountingBoundary.CANONICAL_TO_ANALYTICAL.value}), boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL, input_object_ref=canonical.model_id, input_record_refs=tuple(item.canonical_entity_id for item in entities), entries=tuple(canonical_entries), policy_version=policy.version, provenance_refs=(truth.truth_id, canonical.model_id)),
    ), policy_version=policy.version, provenance_refs=(truth.truth_id, canonical.model_id, "application.product_truth"))
    return truth, accounting
