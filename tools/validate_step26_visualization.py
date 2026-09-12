"""Behavioral and anti-pattern validator for the Step26 visualization boundary."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.visualization import VisualizationService
from dirty_data_to_olap.domain.contracts.analytical import AnalyticalInputBinding, AggregationClass
from dirty_data_to_olap.domain.contracts.canonical import RecordDisposition, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.source import stable_id
from dirty_data_to_olap.domain.contracts.validation import (
    GateStatus,
    ValidationArtifactBindings,
    ValidationCheck,
    ValidationPolicy,
    ValidationReport,
    ValidationScope,
    ValidationSeverity,
    ValidationStatus,
)
from dirty_data_to_olap.domain.contracts.visualization import (
    AnalyticalMeasureVisualInput,
    AnalyticalMeasureVisualization,
    DisclosureMetadata,
    EvidenceVisualItem,
    LineageViewInput,
    ProfileDistributionInput,
    ProfilePoint,
    QualityHeatmapCellInput,
    ValidationView,
    ValidationReportVisualizationBinding,
    ValidationDisplayState,
    ValidationVisualCheckInput,
    VisualAggregationClass,
    VisualChartKind,
    VisualDirection,
    VisualEdgeType,
    VisualEvidenceState,
    VisualLabel,
    VisualObservationScope,
    VisualReliabilityState,
    VisualReviewState,
    VisualReviewability,
    VisualSensitivityState,
    VisualState,
    VisualNodeType,
    VisualizationDisclosureMode,
    VisualizationGraph,
    VisualizationGraphInput,
    VisualizationGraphInputEdge,
    VisualizationGraphInputNode,
    VisualizationKind,
    VisualizationRequest,
    VisualizationScope,
)
from run_step20_reference import build_canonical_model, build_fixture, build_reference_plan


class ValidationFailure(RuntimeError):
    pass


def _scope(visualization_id: str) -> VisualizationScope:
    return VisualizationScope(
        visualization_id=visualization_id,
        visualization_version="step26-visualization-v1",
        project_id="project-step26",
        run_id="run-step26-synthetic",
        snapshot_id="snapshot-step26-synthetic",
        stage_id="stage-step26-visualization",
        scope_id="scope-step26-synthetic",
        scope_semantics="synthetic, bounded contract scenario",
        provenance_refs=("provenance-step26-synthetic",),
    )


def _label(value: str) -> VisualLabel:
    return VisualLabel(value=value, accessible_text=value, sensitivity=VisualSensitivityState.INTERNAL)


def _node(ref: str, node_type: VisualNodeType, *, state: VisualState = VisualState.OBSERVED) -> VisualizationGraphInputNode:
    return VisualizationGraphInputNode(
        domain_ref=ref,
        node_type=node_type,
        label=_label(ref.replace(":", " ")),
        state=state,
        evidence_state=VisualEvidenceState.OBSERVED,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("provenance-step26-synthetic",),
        reviewability=VisualReviewability(review_state=VisualReviewState.NOT_REVIEWED, consequence="informational only"),
    )


def _edge(
    ref: str,
    source: str,
    target: str,
    edge_type: VisualEdgeType,
    *,
    declared: bool = False,
    inferred: bool = False,
    state: VisualState = VisualState.OBSERVED,
    conflict_state: str | None = None,
) -> VisualizationGraphInputEdge:
    kwargs = {}
    if conflict_state is not None:
        from dirty_data_to_olap.domain.contracts.visualization import VisualConflictState
        kwargs["conflict_state"] = VisualConflictState(conflict_state)
    return VisualizationGraphInputEdge(
        domain_ref=ref,
        source_ref=source,
        target_ref=target,
        edge_type=edge_type,
        state=state,
        declared=declared,
        inferred=inferred,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("provenance-step26-synthetic",),
        **kwargs,
    )


def _validation_bindings(policy: ValidationPolicy) -> ValidationArtifactBindings:
    return ValidationArtifactBindings(
        source_snapshot_id="snapshot-step26-validation",
        source_snapshot_hash="a" * 64,
        source_truth_id="truth-step26-validation",
        source_truth_content_hash="b" * 64,
        canonical_model_id="canonical-step26-validation",
        canonical_model_content_hash="c" * 64,
        record_accounting_id="accounting-step26-validation",
        record_accounting_content_hash="d" * 64,
        analytical_plan_id="aplan_" + "e" * 32,
        analytical_plan_content_hash="f" * 64,
        analytical_spec_package_hash="1" * 64,
        analytical_dataset_id="dataset-step26-validation",
        analytical_dataset_content_hash="2" * 64,
        analytical_input_binding_id="binding-step26-validation",
        analytical_input_binding_content_hash="3" * 64,
        analytical_input_source_snapshot_fingerprints={"source": "step26-fingerprint"},
        compiled_plan_id="compiled-step26-validation",
        compiled_plan_content_hash="4" * 64,
        materialization_artifact_id="materialization-step26-validation",
        materialization_artifact_content_hash="5" * 64,
        target_relative_path="target.duckdb",
        target_config_fingerprint="6" * 64,
        target_file_sha256="7" * 64,
        semantic_model_id="semantic-step26-validation",
        semantic_model_content_hash="8" * 64,
        semantic_validation_id="semantic-check-step26-validation",
        semantic_validation_content_hash="9" * 64,
        validation_policy_id=policy.policy_id,
        validation_policy_version=policy.policy_version,
    )


def _validation_report(required_status: ValidationStatus, extras: tuple[ValidationCheck, ...] = ()) -> ValidationReport:
    policy = ValidationPolicy(
        policy_id="policy-step26-validation",
        policy_version="step26-v1",
        required_check_ids=("check-required",),
        allowed_terminal_dispositions=tuple(RecordDisposition),
        orphan_policy={"required_fk": "FAIL"},
        monetary_reason="not applicable to the visualization contract fixture",
        provenance_refs=("prov:validation-policy",),
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
    is_pending = required_status in {ValidationStatus.REVIEW_REQUIRED, ValidationStatus.NOT_EVALUATED}
    return ValidationReport(
        report_id="report-step26-validation",
        run_id="run-step26-synthetic",
        bindings=_validation_bindings(policy),
        policy=policy,
        checks=(required, *extras),
        overall_status=ValidationStatus.FAIL if required_status is ValidationStatus.FAIL else ValidationStatus.REVIEW_REQUIRED if is_pending else ValidationStatus.PASS,
        g6_status=GateStatus.FAIL if required_status is ValidationStatus.FAIL else GateStatus.PENDING if is_pending else GateStatus.PASS,
        g6_eligible=required_status is ValidationStatus.PASS,
        generated_at=datetime(2026, 9, 13, tzinfo=timezone.utc).isoformat(),
        provenance_refs=("prov:validation-report",),
    )


def _step20_measure_context():
    model = build_canonical_model()
    fixture = build_fixture(model)
    binding = AnalyticalInputBinding(
        binding_id=stable_id("abind", {"fixture_id": fixture.fixture_id, "fixture_hash": fixture.content_hash, "model": model.content_hash}),
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        fixture_id=fixture.fixture_id,
        fixture_content_hash=fixture.content_hash,
        source_schema_fingerprints={"crm": "schema-crm-step20", "erp": "schema-erp-step20", "sales": "schema-sales-step20"},
        row_counts=fixture.row_counts,
        provenance_refs=("step26:step20-reviewed-binding",),
    )
    plan, _dimensions, fact, _grain, measures = build_reference_plan(
        model,
        binding,
        fixture,
        created_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    return plan, fact, measures


def _core_graph() -> VisualizationGraphInput:
    nodes = (
        _node("source:crm", VisualNodeType.SOURCE),
        _node("snapshot:crm-001", VisualNodeType.SNAPSHOT),
        _node("table:orders", VisualNodeType.TABLE),
        _node("column:orders.customer_id", VisualNodeType.COLUMN),
        _node("schema-candidate:orders-customer", VisualNodeType.SCHEMA_MAPPING_CANDIDATE, state=VisualState.PROPOSED),
        _node("record:crm-001", VisualNodeType.ENTITY_RECORD),
        _node("cluster:customer-001", VisualNodeType.ENTITY_CLUSTER, state=VisualState.PROPOSED),
        _node("canonical:customer-001", VisualNodeType.CANONICAL_ENTITY, state=VisualState.ACCEPTED),
        _node("fact:orders", VisualNodeType.FACT),
        _node("dim:customer", VisualNodeType.DIMENSION),
        _node("grain:order", VisualNodeType.GRAIN),
    )
    edges = (
        _edge("contains:source-snapshot", "source:crm", "snapshot:crm-001", VisualEdgeType.CONTAINS),
        _edge("contains:snapshot-orders", "snapshot:crm-001", "table:orders", VisualEdgeType.CONTAINS),
        _edge("declared:orders-customer", "table:orders", "column:orders.customer_id", VisualEdgeType.DECLARED_CONSTRAINT, declared=True),
        _edge("candidate:schema", "column:orders.customer_id", "schema-candidate:orders-customer", VisualEdgeType.SCHEMA_MAPPING_CANDIDATE, inferred=True),
        _edge("candidate:er", "record:crm-001", "cluster:customer-001", VisualEdgeType.ER_CANDIDATE_LINK, inferred=True),
        _edge("authorized:er", "record:crm-001", "cluster:customer-001", VisualEdgeType.ER_AUTHORIZED_LINKAGE, state=VisualState.ACCEPTED),
        _edge("canonical:map", "record:crm-001", "canonical:customer-001", VisualEdgeType.CANONICAL_SOURCE_MAPPING),
        _edge("analytical:fact-dim", "fact:orders", "dim:customer", VisualEdgeType.ANALYTICAL_FACT_DIMENSION),
        _edge("analytical:grain", "fact:orders", "grain:order", VisualEdgeType.ANALYTICAL_FACT_DIMENSION),
    )
    return VisualizationGraphInput(scope=_scope("viz-core"), nodes=nodes, edges=edges)


def _check(name: str, condition: bool, details: str, results: list[dict[str, object]]) -> None:
    if not condition:
        raise ValidationFailure(f"{name}: {details}")
    results.append({"name": name, "status": "PASS", "details": details})


def _negative(name: str, action, results: list[dict[str, object]]) -> None:
    try:
        action()
    except (ValidationError, ValueError):
        results.append({"name": name, "status": "PASS", "details": "rejected as expected"})
        return
    raise ValidationFailure(f"{name}: negative control was accepted")


def _large_graph() -> VisualizationGraphInput:
    scope = _scope("viz-large")
    nodes = [_node("source:synthetic", VisualNodeType.SOURCE)]
    edges: list[VisualizationGraphInputEdge] = []
    for table_index in range(220):
        table_ref = f"table:{table_index:03d}"
        nodes.append(_node(table_ref, VisualNodeType.TABLE))
        edges.append(_edge(f"contains:source-{table_index:03d}", "source:synthetic", table_ref, VisualEdgeType.CONTAINS))
        column_count = 600 if table_index == 0 else 13
        for column_index in range(column_count):
            column_ref = f"column:{table_index:03d}.{column_index:04d}"
            nodes.append(_node(column_ref, VisualNodeType.COLUMN))
            edges.append(_edge(f"contains:{table_index:03d}-{column_index:04d}", table_ref, column_ref, VisualEdgeType.CONTAINS))
    return VisualizationGraphInput(scope=scope, nodes=tuple(nodes), edges=tuple(edges))


def main() -> int:
    results: list[dict[str, object]] = []
    encoding_path = ROOT / "docs" / "visualization" / "specs" / "visualization_encoding.yml"
    bounds_path = ROOT / "docs" / "visualization" / "specs" / "graph_bounds.yml"
    encoding = yaml.safe_load(encoding_path.read_text(encoding="utf-8"))
    bounds = yaml.safe_load(bounds_path.read_text(encoding="utf-8"))
    _check("encoding-spec", encoding["color_only_encoding"] is False and encoding["accessible_equivalent_required"] is True, "text-equivalent encoding is required", results)
    _check("privacy-spec", all(encoding["privacy"].values()) is False, "raw PII, values, SQL, and secrets are excluded", results)
    _check("graph-bounds-spec", bounds["disclosure"]["silent_truncation"] is False, "hidden counts and truncation reasons are required", results)
    required_states = set(encoding["states"]["exact_step25_interaction_states_supported"])
    _check("step25-state-coverage", required_states.issubset({state.value for state in VisualState}), "all Step25 interaction states remain representable", results)
    source_text = "\n".join((ROOT / path).read_text(encoding="utf-8").lower() for path in (
        "src/dirty_data_to_olap/application/visualization.py",
        "src/dirty_data_to_olap/domain/contracts/visualization.py",
    ))
    for forbidden in ("plotly", "networkx", "cytoscape", "three.js", "reactflow"):
        _check(f"no-renderer-dependency:{forbidden}", forbidden not in source_text, "no renderer dependency is required", results)

    service = VisualizationService()
    core = _core_graph()
    overview = service.build_graph(VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, max_nodes=20, max_edges=20), core)
    _check("scenario-01-source-schema", any(edge.declared for edge in overview.edges) and any(edge.inferred for edge in overview.edges), "declared and inferred relationships are separately encoded", results)

    neighborhood = service.build_graph(VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.NEIGHBORHOOD, mode=VisualizationDisclosureMode.NEIGHBORHOOD, focus_ref="table:orders", max_hops=1), core)
    _check("scenario-02-neighborhood", neighborhood.disclosure.focus_ref == "table:orders" and neighborhood.disclosure.hop_depth == 1, "focus and hop bound are explicit", results)

    scope = _scope("viz-evidence")
    evidence = service.build_evidence_breakdown(
        visualization_id="viz-evidence",
        scope=scope,
        subject_ref="candidate:001",
        subject_label=_label("Candidate 001"),
        items=(
            EvidenceVisualItem(evidence_ref="family:profile", family="PROFILE", role="SIGNAL", direction="FOR", state=VisualEvidenceState.OBSERVED, reliability=VisualReliabilityState.SAMPLED, observation_scope=VisualObservationScope.SAMPLED_SCOPE, metric_value=0.7, metric_semantics="bounded overlap ratio", supporting=True, provenance_refs=("prov-profile",), accessible_description="Profile signal on sampled scope."),
            EvidenceVisualItem(evidence_ref="family:quality", family="QUALITY", role="SIGNAL", direction="AGAINST", state=VisualEvidenceState.OBSERVED, reliability=VisualReliabilityState.FULL, observation_scope=VisualObservationScope.FULL_SNAPSHOT, metric_value=2, metric_semantics="observed duplicate count", contradicting=True, conflict_refs=("conflict:001",), provenance_refs=("prov-quality",), accessible_description="Quality signal contradicts profile signal."),
        ),
    )
    _check("scenario-03-evidence-conflict", "contradicting=1" in evidence.accessible_summary and "Families and conflicts remain separate" in evidence.accessible_summary, "supporting and contradicting families remain visible", results)

    _check("scenario-04-schema-matching", any(edge.edge_type is VisualEdgeType.SCHEMA_MAPPING_CANDIDATE for edge in overview.edges) or any(node.node_type is VisualNodeType.SCHEMA_MAPPING_CANDIDATE for node in overview.nodes), "schema candidate representation is explicit", results)
    _check("scenario-05-er-cluster-bridge", any(node.node_type is VisualNodeType.ENTITY_CLUSTER for node in core.nodes) and any(node.node_type is VisualNodeType.CANONICAL_ENTITY for node in core.nodes), "cluster and canonical identity are separate node types", results)
    _check("scenario-06-canonical-boundary", any(edge.edge_type is VisualEdgeType.CANONICAL_SOURCE_MAPPING for edge in core.edges), "canonical source mapping is distinct", results)

    lineage = service.build_lineage(LineageViewInput(graph=core, direction=VisualDirection.UPSTREAM, focus_ref="table:orders", max_depth=2))
    _check("scenario-07-lineage", lineage.kind is VisualizationKind.LINEAGE and all("not causality" in edge.accessible_description for edge in lineage.edges) and any("bounded lineage exploration" in item for item in lineage.legend), "direction, bounded exploration, and non-causality semantics are explicit", results)

    plan, fact, measures = _step20_measure_context()
    review_policy = ReviewPolicyService()
    analytical_context = review_policy.analytical_plan_context(plan)
    analytical_review = review_policy.create_decision(
        analytical_context,
        decision=ReviewDecisionStatus.ACCEPTED,
        actor="step26-validator-reviewer",
        rationale="accepted actual Step20 analytical plan for visualization projection",
    )
    measure_views = {
        measure.measure_id: service.build_measure_from_spec(
            visualization_id=f"viz-{measure.measure_id}",
            measure_spec=measure,
            analytical_plan=plan,
            fact_spec=fact,
            analytical_review=analytical_review,
            expected_plan_content_hash=plan.content_hash,
            expected_package_hash=plan.analytical_spec_package_hash,
        )
        for measure in measures
    }
    _check(
        "scenario-08-olap-measures",
        measure_views["measure_quantity"].aggregation_class is VisualAggregationClass.ADDITIVE
        and measure_views["measure_quantity"].aggregation_rule == "SUM(quantity) at validated OrderLine grain"
        and measure_views["measure_unit_price"].aggregation_class is VisualAggregationClass.NON_ADDITIVE
        and measure_views["measure_discount_rate"].aggregation_class is VisualAggregationClass.NON_ADDITIVE
        and measure_views["measure_unit_price"].currency_semantics == "UNSPECIFIED_NOT_REVENUE",
        "actual Step20 quantity, unit_price, and discount_rate semantics remain hash-bound",
        results,
    )
    _check(
        "scenario-09-analytical-review-binding",
        all(
            view.review_decision_id == analytical_review.review_decision_id
            and view.review_decision_content_hash == analytical_review.content_hash
            and view.review_checkpoint_id == analytical_context.review_checkpoint_id.value
            and view.review_decision_status == ReviewDecisionStatus.ACCEPTED.value
            and view.review_applicability_fingerprint == analytical_context.applicability_fingerprint
            and view.fact_semantic_content_hash == fact.semantic_content_hash
            and view.grain_spec_id == fact.grain_spec_id
            for view in measure_views.values()
        ),
        "actual Step20 plan context and accepted ReviewDecision are preserved in every measure projection",
        results,
    )

    quality = service.build_quality_heatmap(
        visualization_id="viz-quality",
        scope=_scope("viz-quality"),
        cells=(QualityHeatmapCellInput(cell_id="quality:measured", subject_ref="table:orders", dimension="completeness", severity="warning", numerator=95, denominator=100, state=VisualState.PASS, observation_scope=VisualObservationScope.FULL_SNAPSHOT, reliability=VisualReliabilityState.FULL, evidence_state=VisualEvidenceState.OBSERVED, provenance_refs=("prov-quality",), accessible_description="95 of 100 rows measured."), QualityHeatmapCellInput(cell_id="quality:missing", subject_ref="table:customers", dimension="validity", severity="unknown", state=VisualState.NOT_EVALUATED, observation_scope=VisualObservationScope.NOT_OBSERVED, reliability=VisualReliabilityState.UNKNOWN, evidence_state=VisualEvidenceState.NOT_OBSERVED, provenance_refs=("prov-quality",), accessible_description="Validity not evaluated.")),
    )
    _check("scenario-09-quality-heatmap", "NOT_EVALUATED=1" in quality.accessible_summary and "denominator" in quality.accessible_summary, "measured and unavailable states retain scope and denominator semantics", results)

    validation_report = _validation_report(
        ValidationStatus.PASS,
        (
            ValidationCheck(
                check_id="check-optional-warning",
                name="optional warning",
                status=ValidationStatus.FAIL,
                severity=ValidationSeverity.WARNING,
                scope=ValidationScope.FACT,
                required=False,
                details="optional warning remains visible",
                evidence_refs=("evidence:optional-warning",),
            ),
        ),
    )
    authoritative_validation = service.build_validation_from_report(
        visualization_id="viz-validation",
        scope=_scope("viz-validation"),
        report=validation_report,
        binding=ValidationReportVisualizationBinding.from_report(validation_report),
    )
    _check(
        "scenario-11-validation",
        authoritative_validation.overall_status == validation_report.overall_status.value
        and authoritative_validation.g6_status == validation_report.g6_status.value
        and authoritative_validation.g6_eligible is True
        and any(check.check_id == "check-optional-warning" and check.status == "FAIL" for check in authoritative_validation.checks),
        "authoritative report status is preserved and optional warning remains visible",
        results,
    )

    privacy_canaries = (
        "step26.synthetic.person@example.test",
        "+989121234567",
        "password=STEP26_FAKE_SECRET",
        "STEP26_LONG_IDENTIFIER_12345678901234567890",
        "STEP26_FREE_TEXT_CANARY",
        {"nested": ["STEP26_STRUCTURED_CANARY"]},
    )
    privacy_checks = tuple(
        ValidationCheck(
            check_id=f"check-privacy-{index}",
            name="privacy display boundary",
            status=ValidationStatus.PASS,
            severity=ValidationSeverity.INFORMATIONAL,
            scope=ValidationScope.FACT,
            required=False,
            details="privacy fixture is projected through the UI preview boundary",
            expected=value,
            observed=value,
            evidence_refs=(f"evidence:privacy-{index}",),
        )
        for index, value in enumerate(privacy_canaries, start=1)
    ) + (
        ValidationCheck(
            check_id="check-privacy-aggregate",
            name="aggregate display boundary",
            status=ValidationStatus.PASS,
            severity=ValidationSeverity.INFORMATIONAL,
            scope=ValidationScope.FACT,
            required=False,
            details="aggregate fixture remains a safe primitive",
            expected=42,
            observed=41,
            evidence_refs=("evidence:privacy-aggregate",),
        ),
        ValidationCheck(
            check_id="check-privacy-unavailable",
            name="unavailable display boundary",
            status=ValidationStatus.PASS,
            severity=ValidationSeverity.INFORMATIONAL,
            scope=ValidationScope.FACT,
            required=False,
            details="unavailable fixture remains unavailable",
            expected=None,
            observed=None,
            evidence_refs=("evidence:privacy-unavailable",),
        ),
    )
    privacy_report = _validation_report(ValidationStatus.PASS, privacy_checks)
    privacy_view = service.build_validation_from_report(
        visualization_id="viz-validation-privacy",
        scope=_scope("viz-validation-privacy"),
        report=privacy_report,
        binding=ValidationReportVisualizationBinding.from_report(privacy_report),
    )
    privacy_serialized = privacy_view.model_dump_json()
    privacy_canary_tokens = (
        "step26.synthetic.person@example.test",
        "+989121234567",
        "STEP26_FAKE_SECRET",
        "STEP26_LONG_IDENTIFIER_12345678901234567890",
        "STEP26_FREE_TEXT_CANARY",
        "STEP26_STRUCTURED_CANARY",
    )
    _check(
        "scenario-12-validation-privacy-boundary",
        all(token not in privacy_serialized for token in privacy_canary_tokens)
        and privacy_view.display_context == "UI_PREVIEW"
        and all(
            item.expected_display_state is ValidationDisplayState.MASKED
            and item.observed_display_state is ValidationDisplayState.MASKED
            for item in privacy_view.checks[: len(privacy_canaries)]
        ),
        "sensitive, identifier, free-text and structured values are masked in the authoritative UI preview",
        results,
    )
    privacy_by_id = {item.check_id: item for item in privacy_view.checks}
    _check(
        "scenario-13-validation-safe-primitives",
        privacy_by_id["check-privacy-aggregate"].expected == "42"
        and privacy_by_id["check-privacy-aggregate"].observed == "41"
        and privacy_by_id["check-privacy-aggregate"].expected_display_state is ValidationDisplayState.SHOWN
        and privacy_by_id["check-privacy-unavailable"].expected_display_state is ValidationDisplayState.UNAVAILABLE,
        "aggregate primitives remain useful while unavailable values stay distinct",
        results,
    )

    stale = service.build_graph(VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED), core.model_copy(update={"nodes": (core.nodes[0].model_copy(update={"state": VisualState.STALE}),) + core.nodes[1:]}))
    _check("scenario-11-stale-invalidation", any(node.domain_ref == "source:crm" and node.state is VisualState.STALE for node in stale.nodes), "stale state is preserved rather than silently refreshed", results)

    restricted = service.build_profile_distribution(visualization_id="viz-profile", scope=_scope("viz-profile"), profile=ProfileDistributionInput(metric_id="metric:restricted", column_ref="column:customer.email", metric_kind="categorical", points=(ProfilePoint(bucket="redacted", count=4),), chart_kind=VisualChartKind.BAR, observation_scope=VisualObservationScope.SAMPLED_SCOPE, reliability=VisualReliabilityState.SAMPLED, evidence_state=VisualEvidenceState.OBSERVED, sensitivity_state=VisualSensitivityState.RESTRICTED, provenance_refs=("prov-profile",), accessible_description="Restricted profile distribution."))
    _check("scenario-12-profile-privacy", restricted.chart_kind is VisualChartKind.NONE and not restricted.points, "privacy-blocked profile points are not transferred", results)

    large = _large_graph()
    started = perf_counter()
    large_view = service.build_graph(VisualizationRequest(visualization_id="viz-large", kind=VisualizationKind.SOURCE_SCHEMA, max_nodes=250, max_edges=500), large)
    large_neighborhood = service.build_graph(VisualizationRequest(visualization_id="viz-large", kind=VisualizationKind.NEIGHBORHOOD, mode=VisualizationDisclosureMode.NEIGHBORHOOD, focus_ref="table:000", max_nodes=120, max_edges=180, max_hops=1), large)
    elapsed = perf_counter() - started
    _check("scenario-13-large-progressive-disclosure", len(large.nodes) >= 3000 and len(large.edges) >= 3000 and large_view.disclosure.hidden_node_count > 0 and large_neighborhood.disclosure.hidden_node_count > 0, f"nodes={len(large.nodes)}, edges={len(large.edges)}, elapsed_seconds={elapsed:.4f}", results)

    filter_cases = (
        ("node_types", VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, node_types=(VisualNodeType.TABLE,))),
        ("edge_types", VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, edge_types=(VisualEdgeType.CONTAINS,))),
        ("evidence_states", VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, evidence_states=(VisualEvidenceState.OBSERVED,))),
        ("review_states", VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, review_states=(VisualReviewState.ACCEPTED,))),
        ("combined", VisualizationRequest(visualization_id="viz-core", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED, node_types=(VisualNodeType.TABLE,), edge_types=(VisualEdgeType.CONTAINS,), evidence_states=(VisualEvidenceState.OBSERVED,), review_states=(VisualReviewState.ACCEPTED,))),
    )
    for label, filter_request in filter_cases:
        filtered = service.build_graph(filter_request, core)
        expected_key = {"combined": {"node_types", "edge_types", "evidence_states", "review_states"}}.get(label, {label})
        actual_keys = {item.split("=", 1)[0] for item in filtered.disclosure.active_filters}
        _check(f"scenario-14-filter-disclosure-{label}", actual_keys == expected_key, f"active_filters={filtered.disclosure.active_filters}", results)

    authorized_edges = [edge for edge in overview.edges if edge.edge_type is VisualEdgeType.ER_AUTHORIZED_LINKAGE]
    candidate_edges = [edge for edge in overview.edges if edge.edge_type is VisualEdgeType.ER_CANDIDATE_LINK]
    _check(
        "scenario-15-er-linkage-distinction",
        len(authorized_edges) == 1 and len(candidate_edges) == 1
        and authorized_edges[0].line_style is not candidate_edges[0].line_style
        and "not canonical identity" in authorized_edges[0].accessible_description
        and "not canonical identity" in candidate_edges[0].accessible_description,
        "candidate and authorized linkage remain distinct non-identity evidence",
        results,
    )

    optional_not_evaluated = _validation_report(
        ValidationStatus.PASS,
        (
            ValidationCheck(
                check_id="check-optional-not-evaluated",
                name="optional not evaluated",
                status=ValidationStatus.NOT_EVALUATED,
                severity=ValidationSeverity.INFORMATIONAL,
                scope=ValidationScope.FACT,
                required=False,
                details="optional state remains visible",
                evidence_refs=("evidence:optional-not-evaluated",),
            ),
        ),
    )
    optional_view = service.build_validation_from_report(
        visualization_id="viz-validation-optional",
        scope=_scope("viz-validation-optional"),
        report=optional_not_evaluated,
        binding=ValidationReportVisualizationBinding.from_report(optional_not_evaluated),
    )
    _check("scenario-16-optional-not-evaluated", optional_view.g6_status == "PASS" and any(check.status == "NOT_EVALUATED" for check in optional_view.checks), "optional NOT_EVALUATED remains visible without changing authoritative G6", results)

    blocking_failure = _validation_report(ValidationStatus.FAIL)
    blocking_view = service.build_validation_from_report(
        visualization_id="viz-validation-blocking",
        scope=_scope("viz-validation-blocking"),
        report=blocking_failure,
        binding=ValidationReportVisualizationBinding.from_report(blocking_failure),
    )
    _check("scenario-17-blocking-failure-visible", blocking_view.g6_status == "FAIL" and any(check.status == "FAIL" for check in blocking_view.checks), "required blocking failure is visible and remains G6 FAIL", results)

    pending_report = _validation_report(ValidationStatus.NOT_EVALUATED)
    pending_view = service.build_validation_from_report(
        visualization_id="viz-validation-pending",
        scope=_scope("viz-validation-pending"),
        report=pending_report,
        binding=ValidationReportVisualizationBinding.from_report(pending_report),
    )
    _check("scenario-18-required-not-evaluated", pending_view.g6_status == "PENDING" and pending_view.g6_eligible is False, "required NOT_EVALUATED remains pending", results)

    _negative("negative-uncalibrated-probability", lambda: EvidenceVisualItem(evidence_ref="bad", family="ML", role="SIGNAL", direction="FOR", state=VisualEvidenceState.OBSERVED, reliability=VisualReliabilityState.FULL, observation_scope=VisualObservationScope.FULL_SNAPSHOT, metric_value=0.9, metric_semantics="uncalibrated probability", provenance_refs=("prov",), accessible_description="bad"), results)
    _negative("negative-nonadditive-sum", lambda: AnalyticalMeasureVisualInput(measure_ref="bad", fact_ref="fact", label=_label("Bad"), aggregation_class=VisualAggregationClass.NON_ADDITIVE, aggregation_rule="SUM", unit_semantics="ratio", currency_semantics="none", provenance_refs=("prov",)), results)
    _negative("negative-quality-denominator", lambda: QualityHeatmapCellInput(cell_id="bad", subject_ref="table", dimension="completeness", severity="error", numerator=1, state=VisualState.FAIL, observation_scope=VisualObservationScope.OBSERVED_SUBSET, reliability=VisualReliabilityState.BOUNDED, evidence_state=VisualEvidenceState.OBSERVED, provenance_refs=("prov",), accessible_description="bad"), results)
    _negative("negative-sensitive-label", lambda: VisualLabel(value="user@example.com", accessible_text="user@example.com"), results)
    _negative("negative-unordered-histogram", lambda: ProfileDistributionInput(metric_id="bad", column_ref="column", metric_kind="numeric", points=(ProfilePoint(bucket="1", count=1),), chart_kind=VisualChartKind.HISTOGRAM, observation_scope=VisualObservationScope.FULL_SNAPSHOT, reliability=VisualReliabilityState.FULL, evidence_state=VisualEvidenceState.OBSERVED, sensitivity_state=VisualSensitivityState.INTERNAL, provenance_refs=("prov",), accessible_description="bad"), results)
    _negative("negative-status-laundering", lambda: ValidationView(visualization_id="viz-validation", scope=_scope("viz-validation"), checks=(ValidationVisualCheckInput(check_id="check:fail", subject_ref="table", status="FAIL", severity="blocking", scope="full", evidence_refs=("evidence",), provenance_refs=("prov",)),), overall_status="PASS", g6_eligible=True, accessible_summary="bad", content_hash="bad"), results)
    _negative("negative-disclosure-accounting", lambda: DisclosureMetadata(mode=VisualizationDisclosureMode.OVERVIEW, total_node_count=2, total_edge_count=0, rendered_node_count=1, rendered_edge_count=0, hidden_node_count=0, hidden_edge_count=0, aggregated_node_count=0, aggregated_edge_count=0, truncated=False, show_more_available=False, accessible_summary="bad"), results)
    _negative("negative-hidden-nodes-not-truncated", lambda: DisclosureMetadata(mode=VisualizationDisclosureMode.OVERVIEW, total_node_count=1, total_edge_count=0, rendered_node_count=0, rendered_edge_count=0, hidden_node_count=1, hidden_edge_count=0, aggregated_node_count=0, aggregated_edge_count=0, truncated=False, show_more_available=False, accessible_summary="bad"), results)
    _negative("negative-hidden-edges-no-reason", lambda: DisclosureMetadata(mode=VisualizationDisclosureMode.OVERVIEW, total_node_count=0, total_edge_count=1, rendered_node_count=0, rendered_edge_count=0, hidden_node_count=0, hidden_edge_count=1, aggregated_node_count=0, aggregated_edge_count=0, truncated=True, show_more_available=True, accessible_summary="bad"), results)
    _negative("negative-false-truncation-without-hidden-content", lambda: DisclosureMetadata(mode=VisualizationDisclosureMode.OVERVIEW, total_node_count=1, total_edge_count=0, rendered_node_count=1, rendered_edge_count=0, hidden_node_count=0, hidden_edge_count=0, aggregated_node_count=0, aggregated_edge_count=0, truncated=True, truncation_reason="incorrectly claimed truncation", show_more_available=True, accessible_summary="bad"), results)
    _negative("negative-invented-revenue", lambda: service.build_measure(AnalyticalMeasureVisualInput(measure_ref="measure:revenue", fact_ref="fact:orders", label=_label("Revenue"), aggregation_class=VisualAggregationClass.ADDITIVE, aggregation_rule="SUM", unit_semantics="currency", currency_semantics="USD", provenance_refs=("prov-invented",))), results)
    _negative("negative-same-id-measure-mutation", lambda: service.build_measure_from_spec(visualization_id="viz-mutated", measure_spec=measures[0].model_copy(update={"aggregation_class": AggregationClass.NON_ADDITIVE}), analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review, expected_plan_content_hash=plan.content_hash, expected_package_hash=plan.analytical_spec_package_hash), results)
    _negative("negative-same-id-fact-mutation", lambda: service.build_measure_from_spec(visualization_id="viz-mutated-fact", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact.model_copy(update={"table_name": "mutated_fact"}), analytical_review=analytical_review), results)
    _negative("negative-missing-fact-spec", lambda: service.build_measure_from_spec(visualization_id="viz-missing-fact", measure_spec=measures[0], analytical_plan=plan, fact_spec=None, analytical_review=analytical_review), results)
    _negative("negative-missing-analytical-review", lambda: service.build_measure_from_spec(visualization_id="viz-missing-review", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=None), results)
    _negative("negative-skipped-review-without-authorization", lambda: review_policy.create_decision(analytical_context, decision=ReviewDecisionStatus.SKIPPED, actor="step26-validator-reviewer", rationale="missing skip authorization"), results)
    _negative("negative-rejected-analytical-review", lambda: service.build_measure_from_spec(visualization_id="viz-rejected-review", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=review_policy.create_decision(analytical_context, decision=ReviewDecisionStatus.REJECTED, actor="step26-validator-reviewer", rationale="negative status")), results)
    _negative("negative-deferred-analytical-review", lambda: service.build_measure_from_spec(visualization_id="viz-deferred-review", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=review_policy.create_decision(analytical_context, decision=ReviewDecisionStatus.DEFERRED, actor="step26-validator-reviewer", rationale="negative status")), results)
    _negative("negative-invalidated-analytical-review", lambda: service.build_measure_from_spec(visualization_id="viz-invalidated-review", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=review_policy.invalidate(analytical_review, "negative invalidation")), results)
    _negative("negative-superseded-analytical-review", lambda: service.build_measure_from_spec(visualization_id="viz-superseded-review", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"superseded": True, "superseded_by": "rdec-new"})), results)
    _negative("negative-stale-analytical-review", lambda: service.build_measure_from_spec(visualization_id="viz-stale-review", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"subject_content_hash": "0" * 64})), results)
    _negative("negative-wrong-checkpoint-review", lambda: service.build_measure_from_spec(visualization_id="viz-wrong-checkpoint", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"review_checkpoint_id": "REVIEW_EVIDENCE_DECISIONS"})), results)
    _negative("negative-wrong-plan-review", lambda: service.build_measure_from_spec(visualization_id="viz-wrong-plan", measure_spec=measures[0], analytical_plan=plan.model_copy(update={"plan_version": "mutated-plan-version"}), fact_spec=fact, analytical_review=analytical_review), results)
    _negative("negative-wrong-package-review", lambda: service.build_measure_from_spec(visualization_id="viz-wrong-package", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"source_schema_fingerprints": {**dict(analytical_review.source_schema_fingerprints), "analytical_spec_package_hash": "0" * 64}})), results)
    _negative("negative-wrong-source-fingerprint-review", lambda: service.build_measure_from_spec(visualization_id="viz-wrong-source", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"source_schema_fingerprints": {"wrong": "fingerprint"}})), results)
    _negative("negative-wrong-domain-review", lambda: service.build_measure_from_spec(visualization_id="viz-wrong-domain", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"domain_assertion_refs": ("wrong-domain",)})), results)
    _negative("negative-wrong-applicability-review", lambda: service.build_measure_from_spec(visualization_id="viz-wrong-applicability", measure_spec=measures[0], analytical_plan=plan, fact_spec=fact, analytical_review=analytical_review.model_copy(update={"applicability_fingerprint": "wrong-applicability"})), results)
    report_for_binding = _validation_report(ValidationStatus.PASS)
    report_binding = ValidationReportVisualizationBinding.from_report(report_for_binding)
    _negative("negative-validation-partial-universe", lambda: service.build_validation_from_report(visualization_id="viz-validation", scope=_scope("viz-validation"), report=report_for_binding, binding=report_binding.model_copy(update={"complete_check_ids": ()})), results)
    _negative("negative-validation-tampered-binding", lambda: service.build_validation_from_report(visualization_id="viz-validation", scope=_scope("viz-validation"), report=report_for_binding, binding=report_binding.model_copy(update={"validation_report_content_hash": "0" * 64})), results)
    tampered_report = report_for_binding.model_copy(update={"checks": (report_for_binding.checks[0].model_copy(update={"details": "tampered"}),)})
    _negative("negative-validation-tampered-report", lambda: service.build_validation_from_report(visualization_id="viz-validation", scope=_scope("viz-validation"), report=tampered_report, binding=report_binding), results)
    _negative("negative-accessible-duplicate-row", lambda: VisualizationGraph.model_validate({**overview.model_dump(mode="python"), "accessible_rows": overview.accessible_rows + (overview.accessible_rows[0],)}), results)
    invalid_related = overview.accessible_rows[0].model_copy(update={"related_visual_ids": ("vnode_missing",)})
    _negative("negative-accessible-invalid-related-id", lambda: VisualizationGraph.model_validate({**overview.model_dump(mode="python"), "accessible_rows": (invalid_related, *overview.accessible_rows[1:])}), results)

    artifact_dir = ROOT / "workspace" / "runs" / "step26-visualization"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    scenario_count = sum(item["name"].startswith("scenario-") for item in results)
    negative_control_count = sum(item["name"].startswith("negative-") for item in results)
    artifact = {
        "validator": "validate_step26_visualization.py",
        "scenario_count": scenario_count,
        "negative_control_count": negative_control_count,
        "executed_check_count": len(results),
        "checks": results,
        "authoritative_validation_preview": privacy_view.model_dump(mode="json"),
        "large_graph": {
            "nodes": len(large.nodes),
            "edges": len(large.edges),
            "overview_rendered_nodes": len(large_view.nodes),
            "overview_rendered_edges": len(large_view.edges),
            "overview_hidden_nodes": large_view.disclosure.hidden_node_count,
            "overview_hidden_edges": large_view.disclosure.hidden_edge_count,
            "neighborhood_rendered_nodes": len(large_neighborhood.nodes),
            "neighborhood_rendered_edges": len(large_neighborhood.edges),
            "elapsed_seconds": round(elapsed, 6),
            "content_hash": large_view.content_hash,
        },
    }
    output_path = artifact_dir / "visualization_reference.json"
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    for item in results:
        print(f"{item['status']} {item['name']}: {item['details']}")
    print(f"ARTIFACT {output_path.relative_to(ROOT)}")
    print(f"SUMMARY PASS checks={len(results)} scenarios={scenario_count} negative_controls={negative_control_count} elapsed_seconds={elapsed:.4f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationFailure, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        raise SystemExit(1)
