from datetime import datetime, timezone

import duckdb
import pytest

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
from dirty_data_to_olap.application.compiler import AnalyticalCompilationError, AnalyticalCompilerService
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalInputTable,
    AggregationClass,
    DimensionSpec,
    FactSpec,
    GrainSpec,
    MeasureSpec,
    TargetConfig,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
)
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.source import stable_id
from tools.run_step20_generic_reference import build_dataset, build_model, build_request


STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)


def generic_flow(dataset: AnalyticalInputDataset | None = None, request=None):
    model = build_model()
    dataset = dataset or build_dataset(model)
    binding = AnalyticalInputBinding(
        binding_id=stable_id("abind", {"dataset": dataset.dataset_id, "hash": dataset.content_hash, "model": model.content_hash}),
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        dataset_id=dataset.dataset_id,
        dataset_content_hash=dataset.content_hash,
        source_schema_fingerprints=dict(dataset.source_schema_fingerprints),
        source_snapshot_fingerprints=dict(dataset.source_snapshot_fingerprints),
        row_counts=dataset.row_counts,
        provenance_refs=("test:generic-binding",),
    )
    request = request or build_request(model)
    plan, dimensions, facts, grains, measures = AnalyticalPlannerService().build_plan(model, binding, dataset, request, created_at=STAMP)
    review = ReviewPolicyService().create_decision(ReviewPolicyService().analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="exact generic analytical package reviewed", reviewed_at=STAMP)
    return model, dataset, binding, plan, dimensions, facts, grains, measures, review


def compile_flow(flow, target=None):
    _, dataset, binding, plan, dimensions, facts, grains, measures, review = flow
    return AnalyticalCompilerService().compile(plan, dimensions, facts, grains, measures, binding, dataset, target or TargetConfig(relative_path="target.duckdb"), review, reviewed_at=STAMP)


@pytest.mark.parametrize("mutation", [
    "measure_class",
    "measure_currency",
    "grain_columns",
    "dimension_namespace",
    "dimension_unknown_policy",
    "fact_fk",
    "fact_degenerate",
    "scd_policy",
])
def test_same_id_spec_mutations_fail_without_new_review(mutation):
    flow = generic_flow()
    _, _, _, plan, dimensions, facts, grains, measures, review = flow
    mutated_dimensions = dimensions
    mutated_facts = facts
    mutated_grains = grains
    mutated_measures = measures
    if mutation == "measure_class":
        mutated_measures = (measures[0].model_copy(update={"aggregation_class": AggregationClass.NON_ADDITIVE}),)
    elif mutation == "measure_currency":
        mutated_measures = (measures[0].model_copy(update={"currency_semantics": "USD"}),)
    elif mutation == "grain_columns":
        mutated_grains = (grains[0].model_copy(update={"key_columns": ("device_ref",)}),)
    elif mutation == "dimension_namespace":
        changed_key = dimensions[0].surrogate_key.model_copy(update={"namespace": "different_namespace"})
        mutated_dimensions = (dimensions[0].model_copy(update={"surrogate_key": changed_key}), *dimensions[1:])
    elif mutation == "dimension_unknown_policy":
        changed_policy = UnknownMemberPolicySpec(policy=UnknownMemberPolicy.NULLABLE_FK, rationale="changed after review")
        mutated_dimensions = (dimensions[0].model_copy(update={"unknown_member_policy": changed_policy}), *dimensions[1:])
    elif mutation == "fact_fk":
        changed_fk = facts[0].dimension_foreign_keys[0].model_copy(update={"dimension_id": "dimension_location"})
        mutated_facts = (facts[0].model_copy(update={"dimension_foreign_keys": (changed_fk, *facts[0].dimension_foreign_keys[1:])}),)
    elif mutation == "fact_degenerate":
        mutated_facts = (facts[0].model_copy(update={"degenerate_dimension_columns": ("device_ref",)}),)
    elif mutation == "scd_policy":
        changed_scd = dimensions[0].scd_policy.model_copy(update={"rationale": "changed SCD semantics"})
        mutated_dimensions = (dimensions[0].model_copy(update={"scd_policy": changed_scd}), *dimensions[1:])
    with pytest.raises(AnalyticalCompilationError, match="REVIEW_ANALYTICAL_PLAN_SPEC_PACKAGE"):
        AnalyticalCompilerService().compile(plan, mutated_dimensions, mutated_facts, mutated_grains, mutated_measures, flow[2], flow[1], TargetConfig(relative_path="target.duckdb"), review, reviewed_at=STAMP)


def test_generic_load_is_parameterized_and_alternate_target_is_materialized(tmp_path):
    flow = generic_flow()
    target = TargetConfig(relative_path="alternate/target.duckdb")
    compiled, generated = compile_flow(flow, target)
    assert "Pump A" not in generated.load_dimensions_sql
    assert "temperature" not in generated.load_facts_sql
    assert "?" in generated.load_dimensions_sql and "?" in generated.load_facts_sql
    policy = ReviewPolicyService()
    material_review = policy.create_decision(policy.materialization_context(compiled, generated, target), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="alternate controlled target", reviewed_at=STAMP)
    artifact = MaterializationService(DuckDBMaterializer(tmp_path / "run-a", repository_root=tmp_path)).materialize(compiled, generated, material_review, flow[2], flow[1], target, run_id="run-a")
    assert artifact.usable is True
    assert artifact.target_relative_path == "run-a/alternate/target.duckdb"
    connection = duckdb.connect(str(tmp_path / artifact.target_relative_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM fact_device_reading").fetchone()[0] == 3
    finally:
        connection.close()
    with pytest.raises(AnalyticalCompilationError, match="target configuration"):
        MaterializationService(object()).materialize(compiled, generated, material_review, flow[2], flow[1], TargetConfig(relative_path="other/target.duckdb"), run_id="run-b")


def _dataset_with_missing_device(flow):
    dataset = flow[1]
    table = dataset.table("readings")
    row = table.rows[0]
    values = tuple(cell.model_copy(update={"value": "missing-device"}) if cell.column_name == "device_ref" else cell for cell in row.values)
    changed_row = row.model_copy(update={"values": values})
    changed_batch = table.batches[0].model_copy(update={"rows": (changed_row, *table.rows[1:])})
    changed_table = table.model_copy(update={"batches": (changed_batch,)})
    return dataset.model_copy(update={"tables": tuple(changed_table if item.table_id == "readings" else item for item in dataset.tables)})


def _reviewed_missing_flow(policy, *, nullable=False):
    base = generic_flow()
    dataset = _dataset_with_missing_device(base)
    request = build_request(base[0])
    dimension = request.dimensions[0].model_copy(update={"unknown_member_policy": UnknownMemberPolicySpec(policy=policy, unknown_member_key=None if policy is not UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER else 1, rationale="behavioral policy test")})
    fact = request.facts[0]
    if nullable:
        fk = fact.dimension_foreign_keys[0].model_copy(update={"required": False})
        fact = fact.model_copy(update={"dimension_foreign_keys": (fk, *fact.dimension_foreign_keys[1:])})
    request = request.model_copy(update={"dimensions": (dimension, *request.dimensions[1:]), "facts": (fact,)})
    return generic_flow(dataset, request)


def test_unknown_member_and_orphan_policies_are_executable(tmp_path):
    explicit = _reviewed_missing_flow(UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER)
    compiled, generated = compile_flow(explicit)
    assert generated.quarantine_records == ()
    policy = ReviewPolicyService()
    target = TargetConfig(relative_path="explicit/target.duckdb")
    compiled, generated = compile_flow(explicit, target)
    review = policy.create_decision(policy.materialization_context(compiled, generated, target), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="explicit unknown member", reviewed_at=STAMP)
    artifact = MaterializationService(DuckDBMaterializer(tmp_path / "explicit", repository_root=tmp_path)).materialize(compiled, generated, review, explicit[2], explicit[1], target, run_id="explicit")
    assert artifact.usable and artifact.quarantined_record_count == 0
    connection = duckdb.connect(str(tmp_path / artifact.target_relative_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM dim_device").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM fact_device_reading WHERE device_key=1").fetchone()[0] == 1
        assert "policy:explicit-unknown-member:dimension_device" in connection.execute("SELECT source_record_refs FROM fact_device_reading WHERE device_key=1").fetchone()[0]
    finally:
        connection.close()

    quarantine = _reviewed_missing_flow(UnknownMemberPolicy.QUARANTINE_FACT)
    compiled, generated = compile_flow(quarantine)
    assert len(generated.quarantine_records) == 1
    target = TargetConfig(relative_path="quarantine/target.duckdb")
    compiled, generated = compile_flow(quarantine, target)
    review = policy.create_decision(policy.materialization_context(compiled, generated, target), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="quarantine missing references", reviewed_at=STAMP)
    artifact = MaterializationService(DuckDBMaterializer(tmp_path / "quarantine", repository_root=tmp_path)).materialize(compiled, generated, review, quarantine[2], quarantine[1], target, run_id="quarantine")
    assert artifact.quarantined_record_count == 1
    connection = duckdb.connect(str(tmp_path / artifact.target_relative_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM fact_device_reading").fetchone()[0] == 2
    finally:
        connection.close()

    nullable = _reviewed_missing_flow(UnknownMemberPolicy.NULLABLE_FK, nullable=True)
    compiled, generated = compile_flow(nullable)
    target = TargetConfig(relative_path="nullable/target.duckdb")
    compiled, generated = compile_flow(nullable, target)
    review = policy.create_decision(policy.materialization_context(compiled, generated, target), decision=ReviewDecisionStatus.ACCEPTED, actor="test", rationale="nullable reviewed FK", reviewed_at=STAMP)
    artifact = MaterializationService(DuckDBMaterializer(tmp_path / "nullable", repository_root=tmp_path)).materialize(compiled, generated, review, nullable[2], nullable[1], target, run_id="nullable")
    assert artifact.quarantined_record_count == 0
    connection = duckdb.connect(str(tmp_path / artifact.target_relative_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM fact_device_reading WHERE device_key IS NULL").fetchone()[0] == 1
    finally:
        connection.close()
