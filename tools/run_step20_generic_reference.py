"""Run a second, non-retail domain through the generic Step20 V1 runtime."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
from dirty_data_to_olap.application.compiler import AnalyticalCompilerService
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalCell,
    AnalyticalColumnBinding,
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalInputTable,
    AnalyticalPlanningRequest,
    AnalyticalRowBatch,
    DimensionAttributeSpec,
    DimensionRole,
    DimensionSpec,
    FactForeignKeySpec,
    FactSpec,
    FactType,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    SCDMode,
    SCDPolicySpec,
    TargetConfig,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
    WarehouseKeySpec,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalModel,
    CanonicalRelationship,
    CanonicalSourceTable,
    ReviewDecisionStatus,
)
from dirty_data_to_olap.domain.contracts.source import stable_id


RUN = ROOT / "workspace" / "runs" / "step20-generic-reference-run" / "olap"
STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)
RUN_ID = "step20-generic-reference-run"


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def build_model() -> CanonicalModel:
    entities = (
        CanonicalEntityType(canonical_entity_type_id="cet_device", semantic_id="device", business_name="Device", kind=CanonicalEntityKind.IDENTITY, identity_strategy="domain-reviewed-device-membership", source_table_refs=(CanonicalSourceTable(source_id="telemetry", snapshot_id="snapshot-step20-generic", table_id="devices", schema_fingerprint="schema-device"),), relationship_refs=("rel_reading_device",), review_state="FINALIZED", provenance_refs=("synthetic:generic-reference",)),
        CanonicalEntityType(canonical_entity_type_id="cet_location", semantic_id="location", business_name="Location", kind=CanonicalEntityKind.IDENTITY, identity_strategy="domain-reviewed-location-membership", source_table_refs=(CanonicalSourceTable(source_id="telemetry", snapshot_id="snapshot-step20-generic", table_id="locations", schema_fingerprint="schema-location"),), relationship_refs=("rel_reading_location",), review_state="FINALIZED", provenance_refs=("synthetic:generic-reference",)),
        CanonicalEntityType(canonical_entity_type_id="cet_reading", semantic_id="reading", business_name="Reading", kind=CanonicalEntityKind.EVENT, identity_strategy="source-local-reading-identity", source_table_refs=(CanonicalSourceTable(source_id="telemetry", snapshot_id="snapshot-step20-generic", table_id="readings", schema_fingerprint="schema-reading"),), relationship_refs=("rel_reading_device", "rel_reading_location"), review_state="FINALIZED", provenance_refs=("synthetic:generic-reference",)),
    )
    relationships = (
        CanonicalRelationship(relationship_id="rel_reading_device", from_entity_type_id="cet_reading", to_entity_type_id="cet_device", cardinality="MANY_TO_ONE", upstream_decision_ref="generic-domain-review", review_decision_ref="generic-step20-review", provenance_refs=("synthetic:generic-reference",)),
        CanonicalRelationship(relationship_id="rel_reading_location", from_entity_type_id="cet_reading", to_entity_type_id="cet_location", cardinality="MANY_TO_ONE", upstream_decision_ref="generic-domain-review", review_decision_ref="generic-step20-review", provenance_refs=("synthetic:generic-reference",)),
    )
    payload = {"version": "canonical-v1", "entities": [item.model_dump(mode="json") for item in entities], "relationships": [item.model_dump(mode="json") for item in relationships], "scope": "step20-generic-reference"}
    return CanonicalModel(
        model_id=stable_id("cmodel", payload),
        model_version="canonical-v1",
        hypothesis_artifact_id="step20-generic-reference-hypothesis",
        finalized_at=STAMP,
        review_decision_refs=("generic-step20-review",),
        entity_types=entities,
        relationships=relationships,
        lineage_refs=("synthetic:generic-reference",),
        record_accounting_refs=("step20-generic-reference-accounting",),
        provenance_refs=("step19-finalized-canonical-boundary", "step20-generic-reference"),
    )


def _table(dataset_id: str, table_id: str, concept: str, columns: tuple[tuple[str, str], ...], rows: tuple[AnalyticalInputRow, ...]) -> AnalyticalInputTable:
    bindings = tuple(AnalyticalColumnBinding(column_id=f"{table_id}:{name}", column_name=name, logical_type=logical_type, lineage_refs=("synthetic:generic-reference",)) for name, logical_type in columns)
    batch = AnalyticalRowBatch(batch_id=f"{dataset_id}:{table_id}:0", table_id=table_id, rows=rows, source_batch_refs=(f"synthetic:{table_id}",), lineage_refs=("synthetic:generic-reference",))
    return AnalyticalInputTable(table_id=table_id, canonical_concept_ref=concept, columns=bindings, batches=(batch,), source_table_refs=(table_id,), lineage_refs=("synthetic:generic-reference",))


def build_dataset(model: CanonicalModel) -> AnalyticalInputDataset:
    def row(ref: str, values: tuple[tuple[str, object], ...], source_ref: str) -> AnalyticalInputRow:
        return AnalyticalInputRow(row_ref=source_ref, canonical_reference=ref, values=tuple(AnalyticalCell(column_name=name, value=value) for name, value in values), source_record_refs=(source_ref,), lineage_refs=("synthetic:generic-reference", source_ref))

    devices = (
        row("device-1", (("device_code", "D-001"), ("device_name", "Pump A")), "telemetry-device-1"),
        row("device-2", (("device_code", "D-002"), ("device_name", "Pump B")), "telemetry-device-2"),
    )
    locations = (
        row("location-1", (("location_code", "L-001"), ("region", "north")), "telemetry-location-1"),
        row("location-2", (("location_code", "L-002"), ("region", "south")), "telemetry-location-2"),
    )
    readings = (
        row("reading-1", (("reading_id", "reading-1"), ("device_ref", "device-1"), ("location_ref", "location-1"), ("observed_on", date(2026, 2, 1)), ("temperature", Decimal("10.5"))), "telemetry-reading-1"),
        row("reading-2", (("reading_id", "reading-2"), ("device_ref", "device-1"), ("location_ref", "location-1"), ("observed_on", date(2026, 2, 2)), ("temperature", Decimal("11.0"))), "telemetry-reading-2"),
        row("reading-3", (("reading_id", "reading-3"), ("device_ref", "device-2"), ("location_ref", "location-2"), ("observed_on", date(2026, 2, 1)), ("temperature", Decimal("9.5"))), "telemetry-reading-3"),
    )
    return AnalyticalInputDataset(
        dataset_id="step20-generic-dataset-v1",
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        tables=(
            _table("step20-generic-dataset-v1", "devices", "device", (("device_code", "STRING"), ("device_name", "STRING")), devices),
            _table("step20-generic-dataset-v1", "locations", "location", (("location_code", "STRING"), ("region", "STRING")), locations),
            _table("step20-generic-dataset-v1", "readings", "reading", (("reading_id", "STRING"), ("device_ref", "STRING"), ("location_ref", "STRING"), ("observed_on", "DATE"), ("temperature", "DECIMAL")), readings),
        ),
        source_schema_fingerprints={"telemetry": "schema-generic-step20"},
        source_snapshot_fingerprints={"telemetry": "snapshot-generic-step20"},
        provenance_refs=("synthetic:generic-reference", "no-raw-source-values"),
    )


def build_request(model: CanonicalModel) -> AnalyticalPlanningRequest:
    provenance = ("synthetic:generic-reference", "canonical:finalized-model")
    type1 = SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="bounded V1 current snapshot")
    unknown = UnknownMemberPolicySpec(policy=UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER, unknown_member_key=1, rationale="reviewed reserved member for telemetry lookup misses")
    dimensions = (
        DimensionSpec(dimension_id="dimension_device", table_name="dim_device", input_table_id="devices", canonical_entity_type_id="cet_device", canonical_entity_refs=("cet_device",), role=DimensionRole.CONFORMED, eligibility_reason="reviewed device descriptor", surrogate_key=WarehouseKeySpec(key_name="device_key", namespace="dimension_device"), alternate_key_columns=("device_code",), attributes=(DimensionAttributeSpec(attribute_id="device_code", column_name="device_code", logical_type="STRING", lineage_refs=provenance), DimensionAttributeSpec(attribute_id="device_name", column_name="device_name", logical_type="STRING", lineage_refs=provenance)), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dimension_device", provenance_refs=provenance),
        DimensionSpec(dimension_id="dimension_location", table_name="dim_location", input_table_id="locations", canonical_entity_type_id="cet_location", canonical_entity_refs=("cet_location",), role=DimensionRole.CONFORMED, eligibility_reason="reviewed location descriptor", surrogate_key=WarehouseKeySpec(key_name="location_key", namespace="dimension_location"), alternate_key_columns=("location_code",), attributes=(DimensionAttributeSpec(attribute_id="location_code", column_name="location_code", logical_type="STRING", lineage_refs=provenance), DimensionAttributeSpec(attribute_id="region", column_name="region", logical_type="STRING", lineage_refs=provenance)), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dimension_location", provenance_refs=provenance),
        DimensionSpec(dimension_id="dimension_observed_date", table_name="dim_observed_date", canonical_entity_type_id="synthetic_observed_date", canonical_entity_refs=("observed_on",), role=DimensionRole.DATE, eligibility_reason="reviewed bounded Gregorian observation date", surrogate_key=WarehouseKeySpec(key_name="observed_date_key", namespace="dimension_observed_date"), alternate_key_columns=("full_date",), attributes=(DimensionAttributeSpec(attribute_id="full_date", column_name="full_date", logical_type="DATE", derivation="FULL_DATE", lineage_refs=provenance), DimensionAttributeSpec(attribute_id="year", column_name="year", logical_type="INTEGER", derivation="YEAR", lineage_refs=provenance), DimensionAttributeSpec(attribute_id="month", column_name="month", logical_type="INTEGER", derivation="MONTH", lineage_refs=provenance)), scd_policy=type1, unknown_member_policy=unknown, conformed_dimension_id="dimension_observed_date", provenance_refs=provenance),
    )
    fact_id = "fact_device_reading"
    grain = GrainSpec(grain_id="grain_reading_event_v1", fact_id=fact_id, human_readable_grain="one telemetry reading event identified by reading_id", key_columns=("reading_id",), null_policy=GrainNullPolicy.REJECT_NULLS, observed_row_count=0, duplicate_key_count=0, evidence_refs=("generic-domain-reviewed:reading-grain",), provenance_refs=provenance)
    measure = MeasureSpec(measure_id="measure_temperature", fact_id=fact_id, field_name="temperature", semantic_name="observed temperature", aggregation_class=AggregationClass.SEMI_ADDITIVE, aggregation_rule="MAX(temperature) within a reviewed observation slice; no cross-time total", unit_semantics="degrees Celsius", currency_semantics="NOT_APPLICABLE", logical_type="DECIMAL", domain_assertion_refs=("generic-domain-reviewed:temperature-semi-additive",), provenance_refs=provenance)
    fact = FactSpec(fact_id=fact_id, table_name="fact_device_reading", input_table_id="readings", fact_type=FactType.TRANSACTION, canonical_event_type_id="cet_reading", canonical_event_refs=("cet_reading",), grain_spec_id=grain.grain_id, dimension_foreign_keys=(FactForeignKeySpec(relationship_ref="rel_reading_device", dimension_id="dimension_device", fact_column="device_key", dimension_key_column="device_key", canonical_entity_type_id="cet_device", input_reference_column="device_ref"), FactForeignKeySpec(relationship_ref="rel_reading_location", dimension_id="dimension_location", fact_column="location_key", dimension_key_column="location_key", canonical_entity_type_id="cet_location", input_reference_column="location_ref"), FactForeignKeySpec(relationship_ref="rel_reading_date", dimension_id="dimension_observed_date", fact_column="observed_date_key", dimension_key_column="observed_date_key", canonical_entity_type_id="synthetic_observed_date", input_reference_column="observed_on")), degenerate_dimension_columns=("reading_id",), measure_ids=(measure.measure_id,), date_role_columns=("observed_on",), relationship_refs=("rel_reading_device", "rel_reading_location"), provenance_refs=provenance)
    return AnalyticalPlanningRequest(request_id="step20-generic-modeling-request-v1", dimensions=dimensions, facts=(fact,), grains=(grain,), measures=(measure,), accepted_relationship_refs=("rel_reading_device", "rel_reading_location"), domain_assertion_refs=("generic-domain-reviewed:device-reading-model", "generic-domain-reviewed:temperature-semi-additive"), provenance_refs=provenance)


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    model = build_model()
    dataset = build_dataset(model)
    binding = AnalyticalInputBinding(binding_id=stable_id("abind", {"dataset": dataset.dataset_id, "dataset_hash": dataset.content_hash, "model": model.content_hash}), canonical_model_id=model.model_id, canonical_model_content_hash=model.content_hash, dataset_id=dataset.dataset_id, dataset_content_hash=dataset.content_hash, source_schema_fingerprints=dict(dataset.source_schema_fingerprints), source_snapshot_fingerprints=dict(dataset.source_snapshot_fingerprints), row_counts=dataset.row_counts, provenance_refs=("generic-dataset-binding", "canonical-model-content-hash"))
    request = build_request(model)
    plan, dimensions, facts, grains, measures = AnalyticalPlannerService().build_plan(model, binding, dataset, request, created_at=STAMP)
    policy = ReviewPolicyService()
    analytical_review = policy.create_decision(policy.analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="step20-generic-domain-review", actor_source="SYNTHETIC_GENERIC_DOMAIN_REVIEW", rationale="Device, Location and Reading semantics are explicit and exact spec content is reviewed.", reviewed_at=STAMP)
    target = TargetConfig(relative_path="target.duckdb")
    compiled, generated = AnalyticalCompilerService().compile(plan, dimensions, facts, grains, measures, binding, dataset, target, analytical_review, reviewed_at=STAMP)
    material_review = policy.create_decision(policy.materialization_context(compiled, generated, target), decision=ReviewDecisionStatus.ACCEPTED, actor="step20-generic-materialization-review", actor_source="CONTROLLED_TARGET_REVIEW", rationale="Exact generic DuckDB compilation and injected controlled target are approved for the synthetic run.", reviewed_at=STAMP)
    artifact = MaterializationService(DuckDBMaterializer(ROOT / "workspace" / "runs" / "step20-generic-reference-run" / "olap", repository_root=ROOT)).materialize(compiled, generated, material_review, binding, dataset, target, run_id=RUN_ID)
    if not artifact.usable:
        raise SystemExit(artifact.failure_reason or "generic materialization failed")
    target_path = ROOT / artifact.target_relative_path
    connection = duckdb.connect(str(target_path), read_only=True)
    try:
        inspection = {"table_names": [row[0] for row in connection.execute("SHOW TABLES").fetchall()], "reading_count": int(connection.execute("SELECT COUNT(*) FROM fact_device_reading").fetchone()[0]), "temperature_max": str(connection.execute("SELECT MAX(temperature) FROM fact_device_reading").fetchone()[0]), "unresolved_fk_count": int(connection.execute("SELECT COUNT(*) FROM fact_device_reading f LEFT JOIN dim_device d ON f.device_key=d.device_key LEFT JOIN dim_location l ON f.location_key=l.location_key LEFT JOIN dim_observed_date dt ON f.observed_date_key=dt.observed_date_key WHERE d.device_key IS NULL OR l.location_key IS NULL OR dt.observed_date_key IS NULL").fetchone()[0])}
    finally:
        connection.close()
    if inspection["reading_count"] != 3 or inspection["unresolved_fk_count"] != 0:
        raise SystemExit("generic target inspection failed: " + json.dumps(inspection, sort_keys=True))
    dump(RUN / "input_binding.json", binding.model_dump(mode="json"))
    dump(RUN / "analytical_plan.json", plan.model_dump(mode="json"))
    dump(RUN / "dimension_specs.json", [item.model_dump(mode="json") for item in dimensions])
    dump(RUN / "fact_specs.json", [item.model_dump(mode="json") for item in facts])
    dump(RUN / "grain_specs.json", [item.model_dump(mode="json") for item in grains])
    dump(RUN / "measure_specs.json", [item.model_dump(mode="json") for item in measures])
    dump(RUN / "review_analytical_plan.json", analytical_review.model_dump(mode="json"))
    dump(RUN / "compiled_plan.json", compiled.model_dump(mode="json"))
    dump(RUN / "generated_sql_manifest.json", {"generated_sql_id": generated.generated_sql_id, "plan_id": generated.plan_id, "plan_content_hash": generated.plan_content_hash, "compiler_version": generated.compiler_version, "sql_hash": generated.sql_hash, "statement_counts": generated.statement_counts})
    (RUN / "create_schema.sql").write_text(generated.create_schema_sql, encoding="utf-8")
    (RUN / "load_date.sql").write_text(generated.load_date_sql, encoding="utf-8")
    (RUN / "load_dimensions.sql").write_text(generated.load_dimensions_sql, encoding="utf-8")
    (RUN / "load_facts.sql").write_text(generated.load_facts_sql, encoding="utf-8")
    dump(RUN / "review_materialization_plan.json", material_review.model_dump(mode="json"))
    dump(RUN / "materialization_artifact.json", artifact.model_dump(mode="json"))
    dump(RUN / "target_inspection.json", inspection)
    dump(RUN / "run_manifest.json", {"run_id": RUN_ID, "flow": ["CANONICAL_FINALIZATION_REF", "ANALYTICAL_PLANNING", "REVIEW_ANALYTICAL_PLAN", "COMPILATION", "REVIEW_MATERIALIZATION_PLAN", "MATERIALIZATION"], "canonical_model_id": model.model_id, "analytical_plan_id": plan.plan_id, "analytical_plan_content_hash": plan.content_hash, "compiled_plan_id": compiled.compiled_plan_id, "compiled_plan_content_hash": compiled.content_hash, "generated_sql_hash": generated.sql_hash, "materialization_artifact_id": artifact.artifact_id, "target_relative_path": artifact.target_relative_path, "target_inspection": inspection, "step21_semantic_layer": "NOT_IMPLEMENTED", "g6_data_correctness": "PENDING_STEP22", "g5_inference_validity": "PASS"})
    print(json.dumps({"run": str(RUN), "plan_id": plan.plan_id, "plan_hash": plan.content_hash, "compiled_plan_id": compiled.compiled_plan_id, "sql_hash": generated.sql_hash, "artifact_id": artifact.artifact_id, "inspection": inspection}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
