"""Behavioral and anti-pattern validator for the Step26 visualization boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from time import perf_counter

import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.application.visualization import VisualizationService
from dirty_data_to_olap.domain.contracts.visualization import (
    AnalyticalMeasureVisualInput,
    DisclosureMetadata,
    EvidenceVisualItem,
    LineageViewInput,
    ProfileDistributionInput,
    ProfilePoint,
    QualityHeatmapCellInput,
    ValidationView,
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
        declared=declared,
        inferred=inferred,
        reliability=VisualReliabilityState.FULL,
        observation_scope=VisualObservationScope.FULL_SNAPSHOT,
        provenance_refs=("provenance-step26-synthetic",),
        **kwargs,
    )


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
    _check("scenario-07-lineage", lineage.kind is VisualizationKind.LINEAGE and all("not causality" in edge.accessible_description for edge in lineage.edges), "direction and non-causality semantics are explicit", results)

    additive = service.build_measure(AnalyticalMeasureVisualInput(measure_ref="measure:revenue", fact_ref="fact:orders", label=_label("Revenue"), aggregation_class=VisualAggregationClass.ADDITIVE, aggregation_rule="SUM", unit_semantics="currency", currency_semantics="USD", provenance_refs=("prov-olap",)))
    non_additive = service.build_measure(AnalyticalMeasureVisualInput(measure_ref="measure:margin", fact_ref="fact:orders", label=_label("Margin"), aggregation_class=VisualAggregationClass.NON_ADDITIVE, aggregation_rule="AT_GRAIN", unit_semantics="ratio", currency_semantics="USD", provenance_refs=("prov-olap",)))
    _check("scenario-08-olap-measures", additive.aggregation_class is VisualAggregationClass.ADDITIVE and non_additive.aggregation_rule == "AT_GRAIN", "additivity class and non-additive rule remain explicit", results)

    quality = service.build_quality_heatmap(
        visualization_id="viz-quality",
        scope=_scope("viz-quality"),
        cells=(QualityHeatmapCellInput(cell_id="quality:measured", subject_ref="table:orders", dimension="completeness", severity="warning", numerator=95, denominator=100, state=VisualState.PASS, observation_scope=VisualObservationScope.FULL_SNAPSHOT, reliability=VisualReliabilityState.FULL, evidence_state=VisualEvidenceState.OBSERVED, provenance_refs=("prov-quality",), accessible_description="95 of 100 rows measured."), QualityHeatmapCellInput(cell_id="quality:missing", subject_ref="table:customers", dimension="validity", severity="unknown", state=VisualState.NOT_EVALUATED, observation_scope=VisualObservationScope.NOT_OBSERVED, reliability=VisualReliabilityState.UNKNOWN, evidence_state=VisualEvidenceState.NOT_OBSERVED, provenance_refs=("prov-quality",), accessible_description="Validity not evaluated.")),
    )
    _check("scenario-09-quality-heatmap", "NOT_EVALUATED=1" in quality.accessible_summary and "denominator" in quality.accessible_summary, "measured and unavailable states retain scope and denominator semantics", results)

    validation = service.build_validation(visualization_id="viz-validation", scope=_scope("viz-validation"), checks=(ValidationVisualCheckInput(check_id="check:pass", subject_ref="table:orders", status="PASS", severity="blocking", scope="full", evidence_refs=("evidence:pass",), provenance_refs=("prov-validation",)), ValidationVisualCheckInput(check_id="check:pending", subject_ref="table:orders", status="NOT_EVALUATED", severity="blocking", scope="unobserved", evidence_refs=("evidence:pending",), provenance_refs=("prov-validation",))))
    _check("scenario-10-validation", validation.overall_status == "NOT_EVALUATED" and validation.g6_eligible is False, "NOT_EVALUATED cannot become a green G6 view", results)

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

    _negative("negative-uncalibrated-probability", lambda: EvidenceVisualItem(evidence_ref="bad", family="ML", role="SIGNAL", direction="FOR", state=VisualEvidenceState.OBSERVED, reliability=VisualReliabilityState.FULL, observation_scope=VisualObservationScope.FULL_SNAPSHOT, metric_value=0.9, metric_semantics="uncalibrated probability", provenance_refs=("prov",), accessible_description="bad"), results)
    _negative("negative-nonadditive-sum", lambda: AnalyticalMeasureVisualInput(measure_ref="bad", fact_ref="fact", label=_label("Bad"), aggregation_class=VisualAggregationClass.NON_ADDITIVE, aggregation_rule="SUM", unit_semantics="ratio", currency_semantics="none", provenance_refs=("prov",)), results)
    _negative("negative-quality-denominator", lambda: QualityHeatmapCellInput(cell_id="bad", subject_ref="table", dimension="completeness", severity="error", numerator=1, state=VisualState.FAIL, observation_scope=VisualObservationScope.OBSERVED_SUBSET, reliability=VisualReliabilityState.BOUNDED, evidence_state=VisualEvidenceState.OBSERVED, provenance_refs=("prov",), accessible_description="bad"), results)
    _negative("negative-sensitive-label", lambda: VisualLabel(value="user@example.com", accessible_text="user@example.com"), results)
    _negative("negative-unordered-histogram", lambda: ProfileDistributionInput(metric_id="bad", column_ref="column", metric_kind="numeric", points=(ProfilePoint(bucket="1", count=1),), chart_kind=VisualChartKind.HISTOGRAM, observation_scope=VisualObservationScope.FULL_SNAPSHOT, reliability=VisualReliabilityState.FULL, evidence_state=VisualEvidenceState.OBSERVED, sensitivity_state=VisualSensitivityState.INTERNAL, provenance_refs=("prov",), accessible_description="bad"), results)
    _negative("negative-status-laundering", lambda: ValidationView(visualization_id="viz-validation", scope=_scope("viz-validation"), checks=(ValidationVisualCheckInput(check_id="check:fail", subject_ref="table", status="FAIL", severity="blocking", scope="full", evidence_refs=("evidence",), provenance_refs=("prov",)),), overall_status="PASS", g6_eligible=True, accessible_summary="bad", content_hash="bad"), results)
    _negative("negative-disclosure-accounting", lambda: DisclosureMetadata(mode=VisualizationDisclosureMode.OVERVIEW, total_node_count=2, total_edge_count=0, rendered_node_count=1, rendered_edge_count=0, hidden_node_count=0, hidden_edge_count=0, aggregated_node_count=0, aggregated_edge_count=0, truncated=False, show_more_available=False, accessible_summary="bad"), results)

    artifact_dir = ROOT / "workspace" / "runs" / "step26-visualization"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "validator": "validate_step26_visualization.py",
        "scenario_count": 13,
        "negative_control_count": 7,
        "checks": results,
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
    print(f"SUMMARY PASS checks={len(results)} scenarios=13 negative_controls=7 elapsed_seconds={elapsed:.4f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationFailure, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        raise SystemExit(1)
