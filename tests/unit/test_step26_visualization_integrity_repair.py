from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest
from pydantic import ValidationError

from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.visualization import VisualizationInputError, VisualizationService
from dirty_data_to_olap.domain.contracts.analytical import AggregationClass
from dirty_data_to_olap.domain.contracts.canonical import RecordDisposition, ReviewCheckpoint, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.validation import (
    ValidationArtifactBindings,
    ValidationCheck,
    ValidationPolicy,
    ValidationReport,
    ValidationScope,
    ValidationSeverity,
    ValidationStatus,
    GateStatus,
)
from dirty_data_to_olap.domain.contracts.visualization import (
    DisclosureMetadata,
    VisualEdgeType,
    VisualEvidenceState,
    VisualLabel,
    VisualNodeType,
    VisualObservationScope,
    VisualReliabilityState,
    VisualReviewState,
    VisualReviewability,
    VisualState,
    VisualizationDisclosureMode,
    VisualizationGraph,
    VisualizationGraphInput,
    VisualizationGraphInputEdge,
    VisualizationGraphInputNode,
    VisualizationKind,
    VisualizationRequest,
    VisualizationScope,
    ValidationReportVisualizationBinding,
    ValidationDisplayState,
    ValidationVisualCheckInput,
    ValidationView,
)
from tests.step20_support import planned_flow


def _scope(visualization_id: str, run_id: str = "run-validation-001") -> VisualizationScope:
    return VisualizationScope(
        visualization_id=visualization_id,
        visualization_version="step26-visualization-v1",
        project_id="project-step26",
        run_id=run_id,
        snapshot_id="snapshot-step26",
        stage_id="stage-step26",
        scope_id="scope-step26",
        scope_semantics="bounded integrity repair test",
        provenance_refs=("prov:visualization",),
    )


def _bindings(policy: ValidationPolicy) -> ValidationArtifactBindings:
    return ValidationArtifactBindings(
        source_snapshot_id="snapshot-validation",
        source_snapshot_hash="a" * 64,
        source_truth_id="truth-validation",
        source_truth_content_hash="b" * 64,
        canonical_model_id="canonical-validation",
        canonical_model_content_hash="c" * 64,
        record_accounting_id="accounting-validation",
        record_accounting_content_hash="d" * 64,
        analytical_plan_id="aplan_" + "e" * 32,
        analytical_plan_content_hash="f" * 64,
        analytical_spec_package_hash="1" * 64,
        analytical_dataset_id="dataset-validation",
        analytical_dataset_content_hash="2" * 64,
        analytical_input_binding_id="binding-validation",
        analytical_input_binding_content_hash="3" * 64,
        analytical_input_source_snapshot_fingerprints={"source": "snapshot-fingerprint"},
        compiled_plan_id="compiled-validation",
        compiled_plan_content_hash="4" * 64,
        materialization_artifact_id="materialization-validation",
        materialization_artifact_content_hash="5" * 64,
        target_relative_path="target.duckdb",
        target_config_fingerprint="6" * 64,
        target_file_sha256="7" * 64,
        semantic_model_id="semantic-validation",
        semantic_model_content_hash="8" * 64,
        semantic_validation_id="semantic-check-validation",
        semantic_validation_content_hash="9" * 64,
        validation_policy_id=policy.policy_id,
        validation_policy_version=policy.policy_version,
    )


def _report(required_status: ValidationStatus, optional: tuple[ValidationCheck, ...] = ()) -> ValidationReport:
    policy = ValidationPolicy(
        policy_id="policy-step26-validation",
        policy_version="step26-v1",
        required_check_ids=("check-required",),
        allowed_terminal_dispositions=tuple(RecordDisposition),
        orphan_policy={"required_fk": "FAIL"},
        monetary_reason="not applicable to this validation contract fixture",
        provenance_refs=("prov:policy",),
    )
    required = ValidationCheck(
        check_id="check-required",
        name="required blocking check",
        status=required_status,
        severity=ValidationSeverity.G6_BLOCKING,
        scope=ValidationScope.FACT,
        required=True,
        details="required check fixture",
        expected="PASS",
        observed=required_status.value,
        evidence_refs=("evidence:required",),
    )
    return ValidationReport(
        report_id="report-step26-validation",
        run_id="run-validation-001",
        bindings=_bindings(policy),
        policy=policy,
        checks=(required, *optional),
        overall_status=(
            ValidationStatus.FAIL if required_status is ValidationStatus.FAIL
            else ValidationStatus.REVIEW_REQUIRED if required_status in {ValidationStatus.REVIEW_REQUIRED, ValidationStatus.NOT_EVALUATED}
            else ValidationStatus.PASS
        ),
        g6_status=(
            GateStatus.FAIL if required_status is ValidationStatus.FAIL
            else GateStatus.PENDING if required_status in {ValidationStatus.REVIEW_REQUIRED, ValidationStatus.NOT_EVALUATED}
            else GateStatus.PASS
        ),
        g6_eligible=required_status is ValidationStatus.PASS,
        generated_at=datetime(2026, 9, 13, tzinfo=timezone.utc).isoformat(),
        provenance_refs=("prov:validation-report",),
    )


def _node(ref: str, node_type: VisualNodeType, *, review: VisualReviewState = VisualReviewState.NOT_REVIEWED) -> VisualizationGraphInputNode:
    return VisualizationGraphInputNode(
        domain_ref=ref,
        node_type=node_type,
        label=VisualLabel(value=ref, accessible_text=ref),
        state=VisualState.OBSERVED,
        evidence_state=VisualEvidenceState.OBSERVED,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("prov:graph",),
        reviewability=VisualReviewability(review_state=review, consequence="visual only"),
    )


def _edge(ref: str, source: str, target: str, edge_type: VisualEdgeType, *, inferred: bool = False, state: VisualState = VisualState.OBSERVED) -> VisualizationGraphInputEdge:
    return VisualizationGraphInputEdge(
        domain_ref=ref,
        source_ref=source,
        target_ref=target,
        edge_type=edge_type,
        state=state,
        inferred=inferred,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("prov:graph",),
    )


def _graph(visualization_id: str = "viz-repair") -> VisualizationGraphInput:
    return VisualizationGraphInput(
        scope=_scope(visualization_id, run_id="run-graph-001"),
        nodes=(
            _node("source:crm", VisualNodeType.SOURCE),
            _node("table:orders", VisualNodeType.TABLE, review=VisualReviewState.ACCEPTED),
            _node("column:orders.customer_id", VisualNodeType.COLUMN),
        ),
        edges=(
            _edge("contains:source-table", "source:crm", "table:orders", VisualEdgeType.CONTAINS),
            _edge("contains:table-column", "table:orders", "column:orders.customer_id", VisualEdgeType.CONTAINS),
        ),
    )


def test_authoritative_validation_projection_preserves_report_gate_and_optional_states() -> None:
    optional_warning = ValidationCheck(
        check_id="check-optional-warning",
        name="optional warning",
        status=ValidationStatus.FAIL,
        severity=ValidationSeverity.WARNING,
        scope=ValidationScope.FACT,
        required=False,
        details="optional warning failed",
        evidence_refs=("evidence:warning",),
    )
    report = _report(ValidationStatus.PASS, (optional_warning,))
    binding = ValidationReportVisualizationBinding.from_report(report)
    view = VisualizationService().build_validation_from_report(
        visualization_id="viz-validation-authoritative",
        scope=_scope("viz-validation-authoritative"),
        report=report,
        binding=binding,
    )
    assert view.authoritative is True
    assert view.g6_status == "PASS"
    assert view.g6_eligible is True
    assert view.overall_status == "PASS"
    assert {check.check_id for check in view.checks} == {"check-required", "check-optional-warning"}
    assert next(check for check in view.checks if check.check_id == "check-optional-warning").status == "FAIL"


def test_authoritative_validation_projection_masks_unknown_and_structured_values() -> None:
    canaries = (
        "step26.synthetic.person@example.test",
        "+989121234567",
        "password=STEP26_FAKE_SECRET",
        "STEP26_LONG_IDENTIFIER_12345678901234567890",
        "STEP26_FREE_TEXT_CANARY",
        {"nested": ["STEP26_STRUCTURED_CANARY"]},
    )
    checks = tuple(
        ValidationCheck(
            check_id=f"check-privacy-{index}",
            name="privacy display boundary",
            status=ValidationStatus.PASS,
            severity=ValidationSeverity.INFORMATIONAL,
            scope=ValidationScope.FACT,
            required=False,
            details="privacy fixture",
            expected=value,
            observed=value,
            evidence_refs=(f"evidence:privacy-{index}",),
        )
        for index, value in enumerate(canaries, start=1)
    ) + (
        ValidationCheck(
            check_id="check-aggregate",
            name="aggregate display boundary",
            status=ValidationStatus.PASS,
            severity=ValidationSeverity.INFORMATIONAL,
            scope=ValidationScope.FACT,
            required=False,
            details="safe aggregate fixture",
            expected=42,
            observed=41,
            evidence_refs=("evidence:aggregate",),
        ),
    )
    report = _report(ValidationStatus.PASS, checks)
    view = VisualizationService().build_validation_from_report(
        visualization_id="viz-validation-privacy",
        scope=_scope("viz-validation-privacy"),
        report=report,
        binding=ValidationReportVisualizationBinding.from_report(report),
    )
    serialized = view.model_dump_json()
    for token in (
        "step26.synthetic.person@example.test",
        "+989121234567",
        "STEP26_FAKE_SECRET",
        "STEP26_LONG_IDENTIFIER_12345678901234567890",
        "STEP26_FREE_TEXT_CANARY",
        "STEP26_STRUCTURED_CANARY",
    ):
        assert token not in serialized
    assert view.display_context == "UI_PREVIEW"
    privacy_items = {item.check_id: item for item in view.checks if item.check_id.startswith("check-privacy-")}
    assert all(
        item.expected_display_state is ValidationDisplayState.MASKED
        and item.observed_display_state is ValidationDisplayState.MASKED
        for item in privacy_items.values()
    )
    aggregate = next(item for item in view.checks if item.check_id == "check-aggregate")
    assert aggregate.expected == "42"
    assert aggregate.expected_display_state is ValidationDisplayState.SHOWN


@pytest.mark.parametrize(
    ("status", "expected_gate", "expected_overall"),
    ((ValidationStatus.FAIL, "FAIL", "FAIL"), (ValidationStatus.NOT_EVALUATED, "PENDING", "REVIEW_REQUIRED")),
)
def test_authoritative_validation_projection_preserves_blocking_outcomes(
    status: ValidationStatus, expected_gate: str, expected_overall: str
) -> None:
    report = _report(status)
    view = VisualizationService().build_validation_from_report(
        visualization_id="viz-validation-authoritative",
        scope=_scope("viz-validation-authoritative"),
        report=report,
        binding=ValidationReportVisualizationBinding.from_report(report),
    )
    assert view.g6_status == expected_gate
    assert view.overall_status == expected_overall
    assert view.g6_eligible is False


def test_authoritative_validation_projection_rejects_omission_tampering_and_scope_mismatch() -> None:
    report = _report(ValidationStatus.PASS)
    binding = ValidationReportVisualizationBinding.from_report(report)
    service = VisualizationService()
    with pytest.raises(VisualizationInputError, match="complete check universe"):
        service.build_validation_from_report(
            visualization_id="viz-validation-authoritative",
            scope=_scope("viz-validation-authoritative"),
            report=report,
            binding=binding.model_copy(update={"complete_check_ids": ()}),
        )
    tampered = report.model_copy(update={"checks": (report.checks[0].model_copy(update={"details": "tampered"}),)})
    with pytest.raises(VisualizationInputError, match="hash"):
        service.build_validation_from_report(
            visualization_id="viz-validation-authoritative",
            scope=_scope("viz-validation-authoritative"),
            report=tampered,
            binding=binding,
        )
    tampered_status = report.model_copy(
        update={"overall_status": ValidationStatus.FAIL, "g6_status": GateStatus.FAIL, "g6_eligible": False}
    )
    with pytest.raises(VisualizationInputError, match="canonical revalidation"):
        service.build_validation_from_report(
            visualization_id="viz-validation-authoritative",
            scope=_scope("viz-validation-authoritative"),
            report=tampered_status,
            binding=ValidationReportVisualizationBinding.from_report(tampered_status),
        )
    with pytest.raises(VisualizationInputError, match="run"):
        service.build_validation_from_report(
            visualization_id="viz-validation-authoritative",
            scope=_scope("viz-validation-authoritative", run_id="other-run"),
            report=report,
            binding=binding,
        )
    assert "overall_status" not in inspect.signature(service.build_validation_from_report).parameters
    assert "g6_eligible" not in inspect.signature(service.build_validation_from_report).parameters


def test_exploratory_validation_view_cannot_claim_global_pass() -> None:
    with pytest.raises(ValidationError, match="global PASS"):
        ValidationView(
            visualization_id="viz-validation",
            scope=_scope("viz-validation"),
            checks=(
                ValidationVisualCheckInput(
                    check_id="check-fail",
                    subject_ref="table:orders",
                    status="FAIL",
                    severity="G6_BLOCKING",
                    scope="FACT",
                    evidence_refs=("evidence:fail",),
                    provenance_refs=("prov:validation",),
                ),
            ),
            overall_status="PASS",
            g6_eligible=True,
            accessible_summary="invalid",
            content_hash="invalid",
        )


def test_measure_projection_uses_actual_step20_semantics_and_rejects_mutation() -> None:
    _, _, _, plan, _, fact, _, measures = planned_flow()
    service = VisualizationService()
    review = ReviewPolicyService().create_decision(
        ReviewPolicyService().analytical_plan_context(plan),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step26-test-reviewer",
        rationale="accepted Step20 analytical plan for visualization projection",
    )
    views = {
        measure.measure_id: service.build_measure_from_spec(
            visualization_id=f"viz-{measure.measure_id}",
            measure_spec=measure,
            analytical_plan=plan,
            fact_spec=fact,
            analytical_review=review,
            expected_plan_content_hash=plan.content_hash,
            expected_package_hash=plan.analytical_spec_package_hash,
        )
        for measure in measures
    }
    assert views["measure_quantity"].aggregation_class.value == AggregationClass.ADDITIVE.value
    assert views["measure_quantity"].aggregation_rule == "SUM(quantity) at validated OrderLine grain"
    assert views["measure_unit_price"].aggregation_class.value == AggregationClass.NON_ADDITIVE.value
    assert views["measure_discount_rate"].aggregation_class.value == AggregationClass.NON_ADDITIVE.value
    assert views["measure_unit_price"].currency_semantics == "UNSPECIFIED_NOT_REVENUE"
    mutated = measures[0].model_copy(update={"aggregation_class": AggregationClass.NON_ADDITIVE})
    with pytest.raises(VisualizationInputError, match="semantic content hash"):
        service.build_measure_from_spec(
            visualization_id="viz-mutated",
            measure_spec=mutated,
            analytical_plan=plan,
            fact_spec=fact,
            analytical_review=review,
            expected_plan_content_hash=plan.content_hash,
            expected_package_hash=plan.analytical_spec_package_hash,
        )
    with pytest.raises(VisualizationInputError, match="fact semantic content hash"):
        service.build_measure_from_spec(
            visualization_id="viz-mutated-fact",
            measure_spec=measures[0],
            analytical_plan=plan,
            fact_spec=fact.model_copy(update={"table_name": "mutated_fact"}),
            analytical_review=review,
        )


def test_measure_projection_requires_current_review_and_preserves_exact_review_binding() -> None:
    _, _, _, plan, _, fact, _, measures = planned_flow()
    review_service = ReviewPolicyService()
    context = review_service.analytical_plan_context(plan)
    accepted = review_service.create_decision(
        context,
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step26-test-reviewer",
        rationale="accepted Step20 analytical plan for visualization projection",
    )
    service = VisualizationService()
    view = service.build_measure_from_spec(
        visualization_id="viz-reviewed-measure",
        measure_spec=measures[0],
        analytical_plan=plan,
        fact_spec=fact,
        analytical_review=accepted,
    )
    assert view.review_decision_id == accepted.review_decision_id
    assert view.review_decision_content_hash == accepted.content_hash
    assert view.review_checkpoint_id == ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN.value
    assert view.review_decision_status == ReviewDecisionStatus.ACCEPTED.value
    assert view.review_applicability_fingerprint == context.applicability_fingerprint
    assert view.fact_semantic_content_hash == fact.semantic_content_hash
    assert view.grain_spec_id == fact.grain_spec_id

    with pytest.raises(VisualizationInputError, match="actual analytical ReviewDecision"):
        service.build_measure_from_spec(
            visualization_id="viz-missing-review",
            measure_spec=measures[0],
            analytical_plan=plan,
            fact_spec=fact,
            analytical_review=None,
        )
    with pytest.raises(VisualizationInputError, match="actual FactSpec"):
        service.build_measure_from_spec(
            visualization_id="viz-missing-fact",
            measure_spec=measures[0],
            analytical_plan=plan,
            fact_spec=None,
            analytical_review=accepted,
        )


def test_measure_projection_allows_only_a_policy_authorized_skip() -> None:
    from dirty_data_to_olap.domain.contracts.canonical import ReviewSkipAuthorization

    _, _, _, plan, _, fact, _, measures = planned_flow()
    review_service = ReviewPolicyService()
    context = review_service.analytical_plan_context(plan)
    authorization = ReviewSkipAuthorization(
        policy_id="step26-test-policy",
        policy_version=context.policy_version,
        checkpoint=context.review_checkpoint_id,
        scope="step26-test-scope",
        applicability_fingerprint=context.applicability_fingerprint,
        reason="bounded test-only skip authorization",
    )
    skip_context = context.model_copy(update={"skip_authorization": authorization})
    skipped = review_service.create_decision(
        skip_context,
        decision=ReviewDecisionStatus.SKIPPED,
        actor="step26-test-reviewer",
        rationale="authorized test skip",
        skip_authorization=authorization,
    )
    view = VisualizationService().build_measure_from_spec(
        visualization_id="viz-skipped-review",
        measure_spec=measures[0],
        analytical_plan=plan,
        fact_spec=fact,
        analytical_review=skipped,
    )
    assert view.review_decision_status == ReviewDecisionStatus.SKIPPED.value


@pytest.mark.parametrize("status", (ReviewDecisionStatus.REJECTED, ReviewDecisionStatus.DEFERRED))
def test_measure_projection_rejects_non_authorizing_review_status(status: ReviewDecisionStatus) -> None:
    _, _, _, plan, _, fact, _, measures = planned_flow()
    review_service = ReviewPolicyService()
    review = review_service.create_decision(
        review_service.analytical_plan_context(plan),
        decision=status,
        actor="step26-test-reviewer",
        rationale="negative review status control",
    )
    with pytest.raises(VisualizationInputError, match="stale, incompatible, or non-authorizing"):
        VisualizationService().build_measure_from_spec(
            visualization_id="viz-invalid-review",
            measure_spec=measures[0],
            analytical_plan=plan,
            fact_spec=fact,
            analytical_review=review,
        )


def test_measure_projection_rejects_invalidated_superseded_and_stale_review() -> None:
    _, _, _, plan, _, fact, _, measures = planned_flow()
    review_service = ReviewPolicyService()
    accepted = review_service.create_decision(
        review_service.analytical_plan_context(plan),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step26-test-reviewer",
        rationale="negative lifecycle control base",
    )
    invalidated = review_service.invalidate(accepted, "Step26 test invalidation")
    superseded = accepted.model_copy(update={"superseded": True, "superseded_by": "rdec-new"})
    stale = accepted.model_copy(update={"subject_content_hash": "0" * 64})
    for review in (invalidated, superseded, stale):
        with pytest.raises(VisualizationInputError, match="stale, incompatible, or non-authorizing"):
            VisualizationService().build_measure_from_spec(
                visualization_id="viz-invalid-lifecycle-review",
                measure_spec=measures[0],
                analytical_plan=plan,
                fact_spec=fact,
                analytical_review=review,
            )


@pytest.mark.parametrize(
    "mutation",
    (
        {"review_checkpoint_id": ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS},
        {"subject_content_hash": "0" * 64},
        {"policy_version": "wrong-policy"},
        {"source_schema_fingerprints": {"wrong": "fingerprint"}},
        {"domain_assertion_refs": ("wrong-domain-ref",)},
        {"applicability_fingerprint": "wrong-applicability"},
    ),
)
def test_measure_projection_delegates_exact_review_compatibility_to_policy(mutation: dict[str, object]) -> None:
    _, _, _, plan, _, fact, _, measures = planned_flow()
    review_service = ReviewPolicyService()
    accepted = review_service.create_decision(
        review_service.analytical_plan_context(plan),
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step26-test-reviewer",
        rationale="negative compatibility control base",
    )
    with pytest.raises(VisualizationInputError, match="stale, incompatible, or non-authorizing"):
        VisualizationService().build_measure_from_spec(
            visualization_id="viz-incompatible-review",
            measure_spec=measures[0],
            analytical_plan=plan,
            fact_spec=fact,
            analytical_review=accepted.model_copy(update=mutation),
        )


def test_unbound_measure_path_is_rejected() -> None:
    from dirty_data_to_olap.domain.contracts.visualization import AnalyticalMeasureVisualInput, VisualAggregationClass

    candidate = AnalyticalMeasureVisualInput(
        measure_ref="measure:revenue",
        fact_ref="fact:orders",
        label=VisualLabel(value="Revenue", accessible_text="Revenue"),
        aggregation_class=VisualAggregationClass.ADDITIVE,
        aggregation_rule="SUM",
        unit_semantics="currency",
        currency_semantics="USD",
        provenance_refs=("prov:invented",),
    )
    with pytest.raises(VisualizationInputError, match="build_measure_from_spec"):
        VisualizationService.build_measure(candidate)


@pytest.mark.parametrize(
    "viz_request",
    (
        VisualizationRequest(visualization_id="viz-repair", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, node_types=(VisualNodeType.TABLE,)),
        VisualizationRequest(visualization_id="viz-repair", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, edge_types=(VisualEdgeType.CONTAINS,)),
        VisualizationRequest(visualization_id="viz-repair", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, evidence_states=(VisualEvidenceState.OBSERVED,)),
        VisualizationRequest(visualization_id="viz-repair", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, review_states=(VisualReviewState.ACCEPTED,)),
        VisualizationRequest(visualization_id="viz-repair", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, node_types=(VisualNodeType.TABLE,), edge_types=(VisualEdgeType.CONTAINS,), evidence_states=(VisualEvidenceState.OBSERVED,), review_states=(VisualReviewState.ACCEPTED,)),
    ),
)
def test_graph_disclosure_derives_effective_filters(viz_request: VisualizationRequest) -> None:
    view = VisualizationService().build_graph(viz_request, _graph())
    keys = {
        item.split("=", 1)[0]
        for item in view.disclosure.active_filters
        if "=" in item
    }
    expected = {
        "node_types" if viz_request.node_types else None,
        "edge_types" if viz_request.edge_types else None,
        "evidence_states" if viz_request.evidence_states else None,
        "review_states" if viz_request.review_states else None,
    } - {None}
    assert keys == expected


def test_filter_disclosure_rejects_contradictory_free_form_selector() -> None:
    request = VisualizationRequest(
        visualization_id="viz-repair",
        kind=VisualizationKind.SOURCE_SCHEMA,
        mode=VisualizationDisclosureMode.FULL_BOUNDED,
        node_types=(VisualNodeType.TABLE,),
        active_filters=("node_types=SOURCE",),
    )
    with pytest.raises(VisualizationInputError, match="contradicts"):
        VisualizationService().build_graph(request, _graph())


def test_disclosure_contract_and_accessible_graph_fail_closed() -> None:
    with pytest.raises(ValidationError, match="truncated"):
        DisclosureMetadata(
            mode=VisualizationDisclosureMode.OVERVIEW,
            total_node_count=1,
            total_edge_count=0,
            rendered_node_count=0,
            rendered_edge_count=0,
            hidden_node_count=1,
            hidden_edge_count=0,
            aggregated_node_count=0,
            aggregated_edge_count=0,
            truncated=False,
            show_more_available=False,
            accessible_summary="invalid",
        )
    with pytest.raises(ValidationError, match="reason"):
        DisclosureMetadata(
            mode=VisualizationDisclosureMode.OVERVIEW,
            total_node_count=0,
            total_edge_count=1,
            rendered_node_count=0,
            rendered_edge_count=0,
            hidden_node_count=0,
            hidden_edge_count=1,
            aggregated_node_count=0,
            aggregated_edge_count=0,
            truncated=True,
            show_more_available=True,
            accessible_summary="invalid",
        )
    with pytest.raises(ValidationError, match="truncated"):
        DisclosureMetadata(
            mode=VisualizationDisclosureMode.OVERVIEW,
            total_node_count=1,
            total_edge_count=0,
            rendered_node_count=1,
            rendered_edge_count=0,
            hidden_node_count=0,
            hidden_edge_count=0,
            aggregated_node_count=0,
            aggregated_edge_count=0,
            truncated=True,
            truncation_reason="incorrectly claimed truncation",
            show_more_available=True,
            accessible_summary="invalid",
        )
    graph = VisualizationService().build_graph(
        VisualizationRequest(visualization_id="viz-repair", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED),
        _graph(),
    )
    duplicate_rows = graph.accessible_rows + (graph.accessible_rows[0],)
    with pytest.raises(ValidationError, match="accessible"):
        VisualizationGraph.model_validate({**graph.model_dump(mode="python"), "accessible_rows": duplicate_rows})
    invalid_related = graph.accessible_rows[0].model_copy(update={"related_visual_ids": ("vnode_missing",)})
    rows = (invalid_related, *graph.accessible_rows[1:])
    with pytest.raises(ValidationError, match="related"):
        VisualizationGraph.model_validate({**graph.model_dump(mode="python"), "accessible_rows": rows})


def test_er_candidate_and_authorized_linkage_are_distinct_non_identity_evidence() -> None:
    graph_input = VisualizationGraphInput(
        scope=_scope("viz-er", run_id="run-graph-001"),
        nodes=(_node("record:one", VisualNodeType.ENTITY_RECORD), _node("cluster:one", VisualNodeType.ENTITY_CLUSTER)),
        edges=(
            _edge("er:candidate", "record:one", "cluster:one", VisualEdgeType.ER_CANDIDATE_LINK, inferred=True, state=VisualState.PROPOSED),
            _edge("er:authorized", "record:one", "cluster:one", VisualEdgeType.ER_AUTHORIZED_LINKAGE, state=VisualState.ACCEPTED),
        ),
    )
    view = VisualizationService().build_graph(
        VisualizationRequest(visualization_id="viz-er", kind=VisualizationKind.ENTITY_RESOLUTION, mode=VisualizationDisclosureMode.FULL_BOUNDED),
        graph_input,
    )
    candidate = next(edge for edge in view.edges if edge.edge_type is VisualEdgeType.ER_CANDIDATE_LINK)
    authorized = next(edge for edge in view.edges if edge.edge_type is VisualEdgeType.ER_AUTHORIZED_LINKAGE)
    assert candidate.line_style != authorized.line_style
    assert "candidate linkage evidence" in candidate.accessible_description
    assert "authorized linkage evidence" in authorized.accessible_description
    assert "not canonical identity" in candidate.accessible_description
    assert "not canonical identity" in authorized.accessible_description
    assert any("bounded lineage exploration" in item for item in view.legend)
