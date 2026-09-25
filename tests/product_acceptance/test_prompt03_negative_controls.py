from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.product_policy import ProductPolicyRegistry
from dirty_data_to_olap.application.product_runtime import LocalProductStageHandlers
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
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
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityMembership, IdentityDerivationBasis, ReviewCheckpoint, ReviewCompatibilityContext, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, ExecutionPlanIntent, StageExecutionRequest, StageSpec
from dirty_data_to_olap.domain.contracts.platform import RunRecord
from dirty_data_to_olap.domain.contracts.product import ProductPolicyBinding
from dirty_data_to_olap.domain.contracts.source import stable_id
from dirty_data_to_olap.domain.contracts.validation import AccountingBoundary, RecordAccountingEntry, RecordAccountingScope, RecordDisposition


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


def _stage_request() -> StageExecutionRequest:
    return StageExecutionRequest(
        request_id="request-negative-policy-binding",
        job_id="job-negative-policy-binding",
        run_id="run-negative-policy-binding",
        stage_id="CANONICAL_HYPOTHESES",
        attempt_id="attempt-negative-policy-binding",
        plan_id="plan-negative-policy-binding",
        configuration_fingerprint="config-negative-policy-binding",
        policy_config_fingerprint="policy-negative-policy-binding",
        cancellation_token_id="cancel-negative-policy-binding",
    )


def _bound_run(fingerprint: str) -> RunRecord:
    return RunRecord(
        run_id="run-negative-policy-binding",
        project_id="prompt03-negative-controls",
        configuration_fingerprint="config-negative-policy-binding",
        metadata={"product_policy_fingerprint": fingerprint},
    )


def test_nc_g02_changed_fingerprint_cannot_reinterpret_existing_run() -> None:
    telemetry = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
    handler = SimpleNamespace(
        platform=SimpleNamespace(control_store=SimpleNamespace(get_run=lambda _run_id: _bound_run(telemetry.content_fingerprint))),
        product_policy=SimpleNamespace(content_fingerprint="0" * 64),
    )
    with pytest.raises(ValueError, match="runtime product policy is incompatible"):
        LocalProductStageHandlers._assert_policy_binding(handler, _stage_request())


def test_nc_g03_order_policy_cannot_substitute_telemetry_run() -> None:
    telemetry = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
    order = ProductPolicyRegistry(ROOT).resolve(product_id="order", version="order-product-v1")
    handler = SimpleNamespace(
        platform=SimpleNamespace(control_store=SimpleNamespace(get_run=lambda _run_id: _bound_run(telemetry.content_fingerprint))),
        product_policy=order,
    )
    with pytest.raises(ValueError, match="runtime product policy is incompatible"):
        LocalProductStageHandlers._assert_policy_binding(handler, _stage_request())


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


def test_nc_g04_review_decision_cannot_cross_policy_context() -> None:
    service = ReviewPolicyService()
    base = dict(
        review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id="evidence-artifact",
        subject_content_hash="evidence-content",
        subject_schema_version="1.0",
        model_version="evidence-model-v1",
        source_schema_fingerprints={"input": "schema-input"},
        domain_assertion_refs=("assertion",),
        subject_semantic_id="relationship",
        applicability_fingerprint="applicability",
    )
    telemetry_context = ReviewCompatibilityContext(policy_version="telemetry-product-v1", **base)
    order_context = ReviewCompatibilityContext(policy_version="order-product-v1", **base)
    decision = service.create_decision(
        telemetry_context,
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="negative-control",
        rationale="telemetry review must remain policy-bound",
    )
    with pytest.raises(ReviewCompatibilityError, match="MISMATCH_POLICY_VERSION"):
        service.require_compatible(decision, order_context)


def test_nc_g05_same_name_device_merge_requires_authorization() -> None:
    policy = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
    membership = CanonicalIdentityMembership(
        membership_group_id="negative-injected-device-merge",
        canonical_entity_type_id="entity_device",
        entity_resolution_family="device",
        source_record_refs=("device-record-1", "device-record-2"),
        derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY,
        evidence_refs=("negative-fault-injection",),
        policy_refs=(policy.provenance,),
        rationale="same display name is not identity evidence",
        provenance_refs=("negative-fault-injection",),
    )
    with pytest.raises(ValueError, match="same-name device hard-negative"):
        policy.validate_identity_memberships((membership,))


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


def test_nc_g08_unsupported_concept_is_explicitly_deferred() -> None:
    policy = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
    validation = policy.validation_policy(canonical_model_id="canonical-negative-control", materialization_id="materialization-negative-control")
    assert validation.deferred_concepts == {"energy_consumption": "No energy counter is present in the reviewed readings source set"}


def test_nc_g09_oracle_is_not_a_runtime_input(monkeypatch: pytest.MonkeyPatch) -> None:
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path.name == "telemetry_v1_truth.yml":
            raise AssertionError("runtime attempted to read the independent oracle")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    policy = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
    assert policy.product_id == "telemetry"
    assert policy.provenance.endswith("config/product/telemetry_v1.json")


def test_nc_g10_incomplete_source_record_accounting_is_rejected() -> None:
    entry = RecordAccountingEntry(
        input_record_ref="record-1",
        disposition=RecordDisposition.EMITTED_DIRECT,
        output_or_group_ref="canonical-1",
        transformation_or_policy_ref="prompt03-negative-control",
        reason="emitted for the negative-control fixture",
        provenance_refs=("record-1",),
    )
    with pytest.raises(ValueError, match="exact input record universe"):
        RecordAccountingScope(
            scope_id="negative-accounting-scope",
            boundary=AccountingBoundary.SOURCE_TO_CANONICAL,
            input_object_ref="negative-input",
            input_record_refs=("record-1", "record-2"),
            entries=(entry,),
            policy_version="prompt03-negative-control",
            provenance_refs=("prompt03-negative-control",),
        )


def test_runtime_source_cannot_read_or_materialize_from_oracle_path() -> None:
    runtime_sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "dirty_data_to_olap" / "application").glob("*.py"))
    assert "product_acceptance/oracle" not in runtime_sources.replace("\\", "/")
    assert "duckdb.connect" not in (ROOT / "src" / "dirty_data_to_olap" / "application" / "telemetry_policy.py").read_text(encoding="utf-8")
