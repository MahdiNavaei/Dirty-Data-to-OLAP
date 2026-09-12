from __future__ import annotations

import pytest
from pydantic import ValidationError

from dirty_data_to_olap.application.visualization import VisualizationInputError, VisualizationService
from dirty_data_to_olap.domain.contracts.visualization import (
    AnalyticalMeasureVisualInput,
    EvidenceVisualItem,
    LineageViewInput,
    ProfileDistributionInput,
    ProfilePoint,
    QualityHeatmapCellInput,
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
    VisualizationGraphInput,
    VisualizationGraphInputEdge,
    VisualizationGraphInputNode,
    VisualizationKind,
    VisualizationRequest,
    VisualizationScope,
)


def _scope(visualization_id: str = "viz-step26") -> VisualizationScope:
    return VisualizationScope(
        visualization_id=visualization_id,
        visualization_version="step26-visualization-v1",
        project_id="project-demo",
        run_id="run-demo-001",
        snapshot_id="snapshot-demo-001",
        stage_id="stage-demo-001",
        scope_id="scope-demo-001",
        scope_semantics="synthetic contract scenario",
        provenance_refs=("prov-demo-001",),
    )


def _label(value: str) -> VisualLabel:
    return VisualLabel(value=value, accessible_text=value, sensitivity=VisualSensitivityState.INTERNAL)


def _node(ref: str, node_type, *, state=VisualState.OBSERVED, review=VisualReviewState.NOT_REVIEWED):
    return VisualizationGraphInputNode(
        domain_ref=ref,
        node_type=node_type,
        label=_label(ref.replace(":", " ")),
        state=state,
        evidence_state=VisualEvidenceState.OBSERVED,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("prov-demo-001",),
        reviewability=VisualReviewability(review_state=review, consequence="informational only"),
    )


def _edge(ref: str, source: str, target: str, edge_type=VisualEdgeType.LINEAGE, *, declared=False, inferred=False):
    return VisualizationGraphInputEdge(
        domain_ref=ref,
        source_ref=source,
        target_ref=target,
        edge_type=edge_type,
        declared=declared,
        inferred=inferred,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("prov-demo-001",),
    )


def _graph() -> VisualizationGraphInput:
    nodes = (
        _node("source:crm", VisualNodeType.SOURCE),
        _node("table:orders", VisualNodeType.TABLE),
        _node("column:orders.customer_id", VisualNodeType.COLUMN),
        _node("dim:customer", VisualNodeType.DIMENSION),
    )
    return VisualizationGraphInput(
        scope=_scope(),
        nodes=nodes,
        edges=(
            _edge("lineage:source-orders", "source:crm", "table:orders"),
            _edge("lineage:orders-customer", "table:orders", "column:orders.customer_id", inferred=True),
            _edge("lineage:customer-dim", "column:orders.customer_id", "dim:customer"),
        ),
    )


def test_graph_is_bounded_deterministic_and_accessible() -> None:
    service = VisualizationService()
    request = VisualizationRequest(
        visualization_id="viz-step26",
        kind=VisualizationKind.SOURCE_SCHEMA,
        mode=VisualizationDisclosureMode.OVERVIEW,
        max_nodes=2,
        max_edges=1,
    )
    first = service.build_graph(request, _graph())
    second = service.build_graph(request, _graph())

    assert first.content_hash == second.content_hash
    assert [node.visual_node_id for node in first.nodes] == [node.visual_node_id for node in second.nodes]
    assert first.disclosure.hidden_node_count == 2
    assert first.disclosure.hidden_edge_count == 3
    assert first.disclosure.show_more_available is True
    assert len(first.accessible_rows) == len(first.nodes)
    assert any("hidden" in first.disclosure.accessible_summary for _ in [0])


def test_neighborhood_and_lineage_direction_are_explicit() -> None:
    service = VisualizationService()
    graph = _graph()
    neighborhood = service.build_graph(
        VisualizationRequest(
            visualization_id="viz-step26",
            kind=VisualizationKind.NEIGHBORHOOD,
            mode=VisualizationDisclosureMode.NEIGHBORHOOD,
            focus_ref="table:orders",
            max_hops=1,
        ),
        graph,
    )
    assert {node.domain_ref for node in neighborhood.nodes} == {"source:crm", "table:orders", "column:orders.customer_id"}

    lineage = service.build_lineage(LineageViewInput(graph=graph, direction=VisualDirection.UPSTREAM, focus_ref="dim:customer", max_depth=2))
    assert {node.domain_ref for node in lineage.nodes} == {"dim:customer", "column:orders.customer_id", "table:orders"}
    assert all("not causality" in edge.accessible_description for edge in lineage.edges)


def test_evidence_conflict_quality_profile_and_validation_views_preserve_states() -> None:
    service = VisualizationService()
    scope = _scope("viz-evidence")
    evidence = service.build_evidence_breakdown(
        visualization_id="viz-evidence",
        scope=scope,
        subject_ref="candidate:001",
        subject_label=_label("Candidate 001"),
        items=(
            EvidenceVisualItem(
                evidence_ref="profile:001",
                family="PROFILE",
                role="SUPPORTING_SIGNAL",
                direction="FOR",
                state=VisualEvidenceState.OBSERVED,
                reliability=VisualReliabilityState.SAMPLED,
                observation_scope=VisualObservationScope.SAMPLED_SCOPE,
                metric_name="overlap_ratio",
                metric_value=0.8,
                metric_semantics="bounded overlap ratio",
                supporting=True,
                provenance_refs=("prov-profile",),
                accessible_description="Profile overlap ratio 0.8 on sampled scope.",
            ),
            EvidenceVisualItem(
                evidence_ref="quality:001",
                family="QUALITY",
                role="CONTRADICTING_SIGNAL",
                direction="AGAINST",
                state=VisualEvidenceState.OBSERVED,
                reliability=VisualReliabilityState.FULL,
                observation_scope=VisualObservationScope.FULL_SNAPSHOT,
                metric_name="duplicate_count",
                metric_value=3,
                metric_semantics="observed duplicate count",
                contradicting=True,
                conflict_refs=("conflict:001",),
                provenance_refs=("prov-quality",),
                accessible_description="Quality evidence contradicts the profile signal.",
            ),
        ),
    )
    assert "contradicting=1" in evidence.accessible_summary
    assert "no score is converted to probability" in evidence.accessible_summary

    quality_scope = _scope("viz-quality")
    heatmap = service.build_quality_heatmap(
        visualization_id="viz-quality",
        scope=quality_scope,
        cells=(
            QualityHeatmapCellInput(
                cell_id="quality:001",
                subject_ref="table:orders",
                dimension="completeness",
                severity="warning",
                numerator=95,
                denominator=100,
                state=VisualState.PASS,
                observation_scope=VisualObservationScope.FULL_SNAPSHOT,
                reliability=VisualReliabilityState.FULL,
                evidence_state=VisualEvidenceState.OBSERVED,
                provenance_refs=("prov-quality",),
                accessible_description="95 of 100 rows measured for completeness.",
            ),
            QualityHeatmapCellInput(
                cell_id="quality:002",
                subject_ref="table:customers",
                dimension="validity",
                severity="unknown",
                state=VisualState.NOT_EVALUATED,
                observation_scope=VisualObservationScope.NOT_OBSERVED,
                reliability=VisualReliabilityState.UNKNOWN,
                evidence_state=VisualEvidenceState.NOT_OBSERVED,
                provenance_refs=("prov-quality",),
                accessible_description="Validity was not evaluated.",
            ),
        ),
    )
    assert "NOT_EVALUATED=1" in heatmap.accessible_summary

    profile_scope = _scope("viz-profile")
    profile = service.build_profile_distribution(
        visualization_id="viz-profile",
        scope=profile_scope,
        profile=ProfileDistributionInput(
            metric_id="profile:status",
            column_ref="column:orders.status",
            metric_kind="categorical",
            points=(ProfilePoint(bucket="paid", count=8), ProfilePoint(bucket="pending", count=2)),
            chart_kind=VisualChartKind.BAR,
            ordered=False,
            observation_scope=VisualObservationScope.SAMPLED_SCOPE,
            reliability=VisualReliabilityState.SAMPLED,
            evidence_state=VisualEvidenceState.OBSERVED,
            sensitivity_state=VisualSensitivityState.INTERNAL,
            provenance_refs=("prov-profile",),
            accessible_description="Categorical status distribution on a sample.",
        ),
    )
    assert profile.chart_kind is VisualChartKind.BAR
    assert len(profile.points) == 2

    validation_scope = _scope("viz-validation")
    validation = service.build_validation(
        visualization_id="viz-validation",
        scope=validation_scope,
        checks=(
            ValidationVisualCheckInput(
                check_id="check:row-count",
                subject_ref="table:orders",
                status="PASS",
                severity="blocking",
                scope="full snapshot",
                evidence_refs=("evidence:row-count",),
                provenance_refs=("prov-validation",),
            ),
            ValidationVisualCheckInput(
                check_id="check:unresolved",
                subject_ref="table:orders",
                status="NOT_EVALUATED",
                severity="blocking",
                scope="not observed",
                evidence_refs=("evidence:not-evaluated",),
                provenance_refs=("prov-validation",),
            ),
        ),
    )
    assert validation.overall_status == "NOT_EVALUATED"
    assert validation.g6_eligible is False


def test_negative_controls_block_semantic_laundering() -> None:
    with pytest.raises(ValidationError, match="probability semantics"):
        EvidenceVisualItem(
            evidence_ref="evidence:bad",
            family="PROFILE",
            role="SIGNAL",
            direction="FOR",
            state=VisualEvidenceState.OBSERVED,
            reliability=VisualReliabilityState.SAMPLED,
            observation_scope=VisualObservationScope.SAMPLED_SCOPE,
            metric_value=0.9,
            metric_semantics="uncalibrated probability",
            provenance_refs=("prov",),
            accessible_description="bad",
        )

    with pytest.raises(ValidationError, match="non-additive"):
        AnalyticalMeasureVisualInput(
            measure_ref="measure:margin",
            fact_ref="fact:orders",
            label=_label("Margin"),
            aggregation_class=VisualAggregationClass.NON_ADDITIVE,
            aggregation_rule="SUM",
            unit_semantics="ratio",
            currency_semantics="USD",
            provenance_refs=("prov",),
        )

    with pytest.raises(ValidationError, match="numerator and denominator"):
        QualityHeatmapCellInput(
            cell_id="quality:missing-denominator",
            subject_ref="table:orders",
            dimension="completeness",
            severity="error",
            numerator=9,
            state=VisualState.FAIL,
            observation_scope=VisualObservationScope.OBSERVED_SUBSET,
            reliability=VisualReliabilityState.BOUNDED,
            evidence_state=VisualEvidenceState.OBSERVED,
            provenance_refs=("prov",),
            accessible_description="missing denominator",
        )

    with pytest.raises(ValidationError, match="sensitive values"):
        VisualLabel(value="person@example.com", accessible_text="person@example.com")


def test_stale_and_invalidated_states_are_not_rewritten() -> None:
    service = VisualizationService()
    graph = _graph()
    stale = graph.nodes[1].model_copy(update={"state": VisualState.STALE})
    invalidated = graph.nodes[2].model_copy(update={"state": VisualState.INVALIDATED})
    replaced = graph.model_copy(update={"nodes": (graph.nodes[0], stale, invalidated, graph.nodes[3])})
    view = service.build_graph(
        VisualizationRequest(visualization_id="viz-step26", kind=VisualizationKind.SOURCE_SCHEMA, mode=VisualizationDisclosureMode.FULL_BOUNDED),
        replaced,
    )
    states = {node.domain_ref: node.state for node in view.nodes}
    assert states["table:orders"] is VisualState.STALE
    assert states["column:orders.customer_id"] is VisualState.INVALIDATED


def test_invalid_graph_edges_and_missing_lineage_focus_fail_closed() -> None:
    graph = _graph()
    with pytest.raises(VisualizationInputError, match="known domain nodes"):
        VisualizationService().build_graph(
            VisualizationRequest(visualization_id="viz-step26", kind=VisualizationKind.SOURCE_SCHEMA),
            graph.model_copy(update={"edges": (graph.edges[0].model_copy(update={"target_ref": "missing"}),)}),
        )
    with pytest.raises(VisualizationInputError, match="focus_ref"):
        VisualizationService().build_lineage(LineageViewInput(graph=graph, direction=VisualDirection.BOTH))
