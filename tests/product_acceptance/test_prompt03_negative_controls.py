from __future__ import annotations

from pathlib import Path

import pytest

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.product_policy import ProductPolicyRegistry
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalPlanningRequest,
    AnalyticalReviewState,
    FactForeignKeySpec,
    FactRelationshipScope,
    FactSpec,
    FactType,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
)
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    DomainAssertion,
    EvidenceFusionInputs,
    EvidenceFusionRequest,
    FusionSubjectKind,
)
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, ExecutionPlanIntent, StageSpec
from dirty_data_to_olap.domain.contracts.product import ProductPolicyBinding
from dirty_data_to_olap.domain.contracts.source import stable_id


ROOT = Path(__file__).resolve().parents[2]


def _fact() -> FactSpec:
    return FactSpec(
        fact_id="fact_reading",
        table_name="fact_reading",
        input_table_id="reading_input",
        fact_type=FactType.SNAPSHOT,
        canonical_event_type_id="entity_reading",
        canonical_event_refs=("reading-1",),
        grain_spec_id="grain_reading",
        dimension_foreign_keys=(
            FactForeignKeySpec(
                relationship_ref="relationship_device",
                relationship_scope=FactRelationshipScope.CANONICAL_ACCEPTED,
                dimension_id="dim_device",
                fact_column="device_key",
                dimension_key_column="device_key",
                canonical_entity_type_id="entity_device",
                input_reference_column="device_id",
            ),
        ),
        measure_ids=("measure_temperature",),
        date_role_columns=("observed_on",),
        relationship_refs=("relationship_device",),
        provenance_refs=("negative-control",),
    )


def _grain() -> GrainSpec:
    return GrainSpec(
        grain_id="grain_reading",
        fact_id="fact_reading",
        human_readable_grain="one row per reading_id",
        key_columns=("reading_id",),
        null_policy=GrainNullPolicy.REJECT_NULLS,
        observed_row_count=1,
        duplicate_key_count=0,
        evidence_refs=("negative-control",),
        provenance_refs=("negative-control",),
    )


def _measure() -> MeasureSpec:
    return MeasureSpec(
        measure_id="measure_temperature",
        fact_id="fact_reading",
        field_name="temperature",
        semantic_name="temperature",
        aggregation_class=AggregationClass.SEMI_ADDITIVE,
        aggregation_rule="MAX within reviewed observation slice; no cross-time total",
        unit_semantics="degrees Celsius",
        currency_semantics="NOT_APPLICABLE",
        domain_assertion_refs=("telemetry:temperature-semantics",),
        provenance_refs=("negative-control",),
    )


def test_nc_g01_unknown_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown product policy"):
        ProductPolicyRegistry(ROOT).resolve(product_id="unregistered", version="x")


def test_nc_g02_policy_version_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="version is not registered"):
        ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v999")


def test_nc_g03_policy_fingerprint_tamper_is_rejected() -> None:
    with pytest.raises(ValueError, match="fingerprint is incompatible"):
        ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1", fingerprint="0" * 64)


def test_nc_g04_durable_plan_cannot_cross_policy_binding() -> None:
    stage = StageSpec(stage_id="SOURCE", handler_key="source")
    with pytest.raises(ValueError, match="does not match its planning intent"):
        ExecutionPlan(
            plan_id="plan-negative-control",
            run_id="run-negative-control",
            stages=(stage,),
            planning_intent=ExecutionPlanIntent(product_policy_id="order", product_policy_version="order-product-v1"),
            product_policy=ProductPolicyBinding(product_id="telemetry", version="telemetry-product-v1", content_fingerprint="a" * 64, provenance_ref="config:telemetry"),
        )


def test_nc_g05_missing_telemetry_role_is_rejected() -> None:
    policy = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
    catalog = type("Catalog", (), {"source_id": "bad-source", "tables": (type("Table", (), {"table_id": "bad", "physical_name": "bad"})(),), "columns": ()})()
    with pytest.raises(ValueError, match="does not match a registered telemetry role"):
        policy.source_role(catalog)


def test_nc_g06_fact_cannot_use_measure_as_grain() -> None:
    with pytest.raises(ValueError, match="grain spec reference cannot be a measure reference"):
        FactSpec.model_validate({**_fact().model_dump(), "grain_spec_id": "measure_temperature"})


def test_nc_g07_measure_requires_explicit_domain_assertion() -> None:
    with pytest.raises(ValueError):
        MeasureSpec.model_validate({**_measure().model_dump(), "domain_assertion_refs": ()})


def test_nc_g08_accepted_planning_request_cannot_cross_review_boundary() -> None:
    with pytest.raises(ValueError, match="require a separate review decision"):
        AnalyticalPlanningRequest(
            request_id="planning-negative-control",
            facts=(_fact(),),
            grains=(_grain(),),
            measures=(_measure(),),
            domain_assertion_refs=("telemetry:temperature-semantics",),
            provenance_refs=("negative-control",),
            review_state=AnalyticalReviewState.ACCEPTED,
        )


def test_nc_g09_unbound_domain_evidence_does_not_complete_fusion() -> None:
    policy = EvidenceFusionService.load_policy("relationship", policy_root=ROOT / "policies" / "evidence-fusion")
    request = EvidenceFusionRequest(
        request_id=stable_id("negative-fusion", "unbound"),
        execution_context_id="negative-control",
        relationship_candidate_ids=(),
        subject_kind=FusionSubjectKind.RELATIONSHIP,
        policy=policy,
    )
    result = EvidenceFusionService(policy_root=ROOT / "policies" / "evidence-fusion").fuse(
        request,
        inputs=EvidenceFusionInputs(
            domain_assertions=(DomainAssertion(assertion_id="negative:unbound", subject_id="rel:not-selected", statement="fault injection", status="ACTIVE", source_ids=("source",), snapshot_ids=("snapshot",), scope_id="negative-scope", asserted_by="negative-control", evidence_refs=("fault-injection",)),),
        ),
    )
    assert result.completeness.value != "COMPLETE_REVIEW_READY"
    assert result.failures


def test_nc_g10_runtime_cannot_read_or_materialize_from_oracle_path() -> None:
    runtime_sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "dirty_data_to_olap" / "application").glob("*.py"))
    assert "product_acceptance/oracle" not in runtime_sources.replace("\\", "/")
    assert "duckdb.connect" not in (ROOT / "src" / "dirty_data_to_olap" / "application" / "telemetry_policy.py").read_text(encoding="utf-8")
