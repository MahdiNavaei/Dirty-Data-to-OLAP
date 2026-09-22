"""Independent typed source truth and scoped accounting for the order product."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

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


def build_multi_source_truth_and_accounting(
    *,
    run_id: str,
    source_records: Sequence[Mapping[str, Any]],
    snapshots: Mapping[str, SourceSnapshotResult],
    catalogs: Mapping[str, SourceCatalog],
    dataset,
    canonical: CanonicalModel,
    fact,
    measure,
    policy,
) -> tuple[SourceTruthManifest, RecordAccountingArtifact]:
    """Build independent truth for the accepted four-source product path.

    ``source_records`` is a runtime observation projection, not an oracle: it
    is assembled from the pinned snapshots and carries source-local logical
    values plus the exact record provenance used by the runtime.  The oracle
    remains in the acceptance test and is compared only after materialization.
    """

    maps = {item.record_ref: item for item in canonical.source_record_maps}
    entity_types = {item.canonical_entity_type_id: item for item in canonical.entity_types}
    semantic_by_type = {item.canonical_entity_type_id: item.semantic_id for item in canonical.entity_types}
    canonical_instances = {item.canonical_entity_id: item for item in canonical.instances}
    ordinals = {
        item.record_ref: item.extraction_ordinal
        for snapshot in snapshots.values()
        for item in snapshot.record_references
    }
    records: list[SourceTruthRecord] = []
    source_entries: list[RecordAccountingEntry] = []
    source_expectations: list[SourceTruthAccountingExpectation] = []
    facts: list[SourceTruthFact] = []
    relationships: list[SourceTruthRelationship] = []
    valid_fact_by_event: dict[str, str] = {}

    customer_rel = next((item for item in canonical.relationships if item.to_entity_type_id == "entity_customer"), None)
    if customer_rel is None:
        raise ValueError("multi-source truth requires a reviewed event-to-customer relationship")

    registry_key_to_canonical: dict[str, str] = {}
    for row in source_records:
        if row.get("role") != "registry":
            continue
        mapping = maps.get(str(row["record_ref"]))
        customer_id = row.get("customer_id")
        if mapping is not None and customer_id is not None:
            registry_key_to_canonical.setdefault(str(customer_id), mapping.canonical_entity_id)

    for row in sorted(source_records, key=lambda item: (str(item["source_id"]), int(item.get("extraction_ordinal", 0)), str(item["record_ref"]))):
        record_ref = str(row["record_ref"])
        mapping = maps.get(record_ref)
        if mapping is None:
            raise ValueError(f"canonical model does not map source record {record_ref}")
        role = str(row["role"])
        subject_type = "customer" if role == "registry" else "order"
        disposition = mapping.terminal_disposition
        output_reference = mapping.canonical_entity_id
        fact_ref = stable_id("truth-fact", {"run": run_id, "record": record_ref})
        values = dict(row.get("logical_values", {}))
        if role == "event":
            customer_ref = values.get("customer_id")
            customer_canonical_id = registry_key_to_canonical.get(str(customer_ref)) if customer_ref is not None else None
            values["canonical_customer_id"] = customer_canonical_id
            is_fact = all(values.get(name) is not None for name in ("order_id", "order_date", "quantity")) and customer_canonical_id is not None
            if is_fact:
                valid_fact_by_event[record_ref] = fact_ref
        records.append(SourceTruthRecord(
            record_ref=record_ref,
            source_id=str(row["source_id"]),
            snapshot_id=str(row["snapshot_id"]),
            table_id=str(row["table_id"]),
            extraction_ordinal=int(row.get("extraction_ordinal", ordinals.get(record_ref, 0))),
            subject_type=subject_type,
            canonical_entity_id=mapping.canonical_entity_id,
            terminal_disposition=disposition,
            output_reference=output_reference,
            values=values,
            provenance_refs=(str(row["snapshot_id"]), record_ref, policy.provenance),
        ))
        source_entries.append(RecordAccountingEntry(
            input_record_ref=record_ref,
            disposition=disposition,
            output_or_group_ref=mapping.canonical_entity_id if disposition in {RecordDisposition.EMITTED_DIRECT, RecordDisposition.CONSOLIDATED, RecordDisposition.AGGREGATED} else None,
            transformation_or_policy_ref=policy.provenance,
            reason=("customer registry record is consolidated into the reviewed canonical customer group" if disposition is RecordDisposition.CONSOLIDATED else "source-local event identity is emitted before analytical FK policy" if role == "event" else "customer registry record is emitted as a reviewed canonical identity"),
            provenance_refs=(record_ref, mapping.canonical_entity_id),
        ))
        source_expectations.append(SourceTruthAccountingExpectation(
            boundary=AccountingBoundary.SOURCE_TO_CANONICAL,
            input_record_ref=record_ref,
            expected_disposition=disposition,
            expected_output_or_group_ref=mapping.canonical_entity_id if disposition in {RecordDisposition.EMITTED_DIRECT, RecordDisposition.CONSOLIDATED, RecordDisposition.AGGREGATED} else None,
            reason_contains="consolidated" if disposition is RecordDisposition.CONSOLIDATED else "emitted",
            provenance_refs=(record_ref,),
        ))
        if role == "event" and record_ref in valid_fact_by_event:
            customer_canonical_id = values["canonical_customer_id"]
            facts.append(SourceTruthFact(
                fact_ref=fact_ref,
                fact_id=fact.fact_id,
                source_record_refs=(record_ref,),
                canonical_event_id=mapping.canonical_entity_id,
                grain_values={"source_id": str(row["source_id"]), "order_id": str(values["order_id"])},
                dimension_entity_ids={"dim_customer": customer_canonical_id, "dim_source": str(row["source_id"])},
                measure_values={measure.field_name: values["quantity"] if isinstance(values["quantity"], Decimal) else Decimal(str(values["quantity"]))},
                date_value=str(values["order_date"]),
                provenance_refs=(record_ref, fact.fact_id),
            ))
            relationships.append(SourceTruthRelationship(
                relationship_ref=customer_rel.relationship_id,
                from_record_ref=record_ref,
                to_canonical_entity_id=customer_canonical_id,
                required=True,
                provenance_refs=(record_ref, customer_rel.relationship_id),
            ))

    entities = tuple(
        SourceTruthEntity(
            entity_type=semantic_by_type[item.canonical_entity_type_id],
            canonical_entity_id=item.canonical_entity_id,
            source_record_refs=item.source_record_refs,
            expected_attributes={},
            provenance_refs=tuple(item.provenance_refs) or (item.canonical_entity_id,),
        )
        for item in sorted(canonical.instances, key=lambda value: value.canonical_entity_id)
    )
    canonical_entries: list[RecordAccountingEntry] = []
    canonical_expectations: list[SourceTruthAccountingExpectation] = []
    valid_fact_ids = {item.canonical_event_id: item.fact_ref for item in facts}
    for instance in sorted(canonical.instances, key=lambda value: value.canonical_entity_id):
        semantic = semantic_by_type[instance.canonical_entity_type_id]
        if semantic == "order":
            fact_ref = valid_fact_ids.get(instance.canonical_entity_id)
            disposition = RecordDisposition.EMITTED_DIRECT if fact_ref is not None else RecordDisposition.QUARANTINED
            output_reference = fact_ref
            reason = "canonical event is materialized at the reviewed fact grain" if fact_ref else "canonical event is quarantined because its customer reference is unresolved"
        else:
            disposition = RecordDisposition.EMITTED_DIRECT
            output_reference = f"dimension:{semantic}:{instance.canonical_entity_id}"
            reason = f"canonical {semantic} is materialized as a reviewed dimension member"
        canonical_entries.append(RecordAccountingEntry(input_record_ref=instance.canonical_entity_id, disposition=disposition, output_or_group_ref=output_reference, transformation_or_policy_ref=policy.provenance, reason=reason, provenance_refs=(instance.canonical_entity_id,)))
        canonical_expectations.append(SourceTruthAccountingExpectation(boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL, input_record_ref=instance.canonical_entity_id, expected_disposition=disposition, expected_output_or_group_ref=output_reference, reason_contains="materialized" if disposition is RecordDisposition.EMITTED_DIRECT else "quarantined", provenance_refs=(instance.canonical_entity_id,)))

    aggregate_rows = [(str(item.date_value), next(iter(item.measure_values.values()))) for item in facts if item.date_value is not None]
    total = sum((value if isinstance(value, Decimal) else Decimal(str(value)) for _key, value in aggregate_rows), Decimal("0"))
    by_date: dict[str, Decimal] = {}
    for key, value in aggregate_rows:
        by_date[key] = by_date.get(key, Decimal("0")) + (value if isinstance(value, Decimal) else Decimal(str(value)))
    aggregate_expectations = (
        SourceTruthAggregateExpectation(aggregate_id="quantity_global", fact_id=fact.fact_id, measure_field=measure.field_name, semantic_measure_ref=measure.measure_id, operation="SUM", expected=total, unit_semantics=measure.unit_semantics, provenance_refs=(fact.fact_id, measure.measure_id)),
        SourceTruthAggregateExpectation(aggregate_id="quantity_by_date", fact_id=fact.fact_id, measure_field=measure.field_name, semantic_measure_ref=measure.measure_id, operation="SUM", group_by=("dim_date",), expected=total, expected_by_key=by_date, unit_semantics=measure.unit_semantics, provenance_refs=(fact.fact_id, measure.measure_id)),
    )
    source_snapshot_ids = {source_id: snapshots[source_id].snapshot.snapshot_id for source_id in sorted(snapshots)}
    source_snapshot_id = stable_id("multi-source-snapshot-set", source_snapshot_ids)
    truth = SourceTruthManifest(
        truth_id=stable_id("truth", {"run": run_id, "source_snapshot_ids": source_snapshot_ids, "records": [item.record_ref for item in records]}),
        truth_version=policy.version,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_ids=source_snapshot_ids,
        source_schema_fingerprints={source_id: catalogs[source_id].source.schema_fingerprint for source_id in sorted(catalogs)},
        records=tuple(records),
        entities=entities,
        relationships=tuple(relationships),
        facts=tuple(facts),
        accounting_expectations=tuple((*source_expectations, *canonical_expectations)),
        aggregate_expectations=aggregate_expectations,
        expected_aggregates={"aggregates": [item.model_dump(mode="json") for item in aggregate_expectations]},
        provenance_refs=("application.product_truth", source_snapshot_id, policy.provenance, *source_snapshot_ids.values()),
    )
    accounting = RecordAccountingArtifact(
        accounting_id=stable_id("accounting", {"run": run_id, "source_snapshot_ids": source_snapshot_ids}),
        run_id=run_id,
        scopes=(
            RecordAccountingScope(scope_id=stable_id("accounting-scope", {"run": run_id, "boundary": AccountingBoundary.SOURCE_TO_CANONICAL.value}), boundary=AccountingBoundary.SOURCE_TO_CANONICAL, input_object_ref=source_snapshot_id, input_record_refs=tuple(item.record_ref for item in records), entries=tuple(source_entries), policy_version=policy.version, provenance_refs=(truth.truth_id, source_snapshot_id)),
            RecordAccountingScope(scope_id=stable_id("accounting-scope", {"run": run_id, "boundary": AccountingBoundary.CANONICAL_TO_ANALYTICAL.value}), boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL, input_object_ref=canonical.model_id, input_record_refs=tuple(item.canonical_entity_id for item in entities), entries=tuple(canonical_entries), policy_version=policy.version, provenance_refs=(truth.truth_id, canonical.model_id)),
        ),
        policy_version=policy.version,
        provenance_refs=(truth.truth_id, canonical.model_id, "application.product_truth"),
    )
    return truth, accounting
