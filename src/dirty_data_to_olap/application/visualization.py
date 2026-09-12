"""Deterministic, project-owned projections for Step26 visualization views.

The service turns reviewed domain-shaped inputs into bounded view models.  It
does not render HTML, execute SQL, expose raw domain objects, or make a new
decision about evidence, identity, canonicalization, or analytical truth.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Mapping

from dirty_data_to_olap.domain.contracts.visualization import (
    AccessibleGraphRow,
    AnalyticalMeasureVisualInput,
    DisclosureMetadata,
    EvidenceBreakdownView,
    EvidenceVisualItem,
    LineageViewInput,
    ProfileDistributionInput,
    ProfileDistributionView,
    QualityHeatmapCellInput,
    QualityHeatmapView,
    ValidationView,
    ValidationVisualCheckInput,
    VisualChartKind,
    VisualDirection,
    VisualEdgeType,
    VisualEdge,
    VisualEvidenceState,
    VisualLineStyle,
    VisualLabel,
    VisualNode,
    VisualNodeShape,
    VisualNodeType,
    VisualReviewState,
    VisualState,
    VisualizationGraph,
    VisualizationGraphInput,
    VisualizationGraphInputEdge,
    VisualizationGraphInputNode,
    VisualizationDisclosureMode,
    VisualizationKind,
    VisualizationRequest,
    VisualizationScope,
    visualization_content_hash,
    visual_edge_id,
    visual_node_id,
)


class VisualizationInputError(ValueError):
    """The supplied visualization input is incomplete or contradictory."""


_TOP_LEVEL_TYPES = {
    VisualNodeType.SOURCE,
    VisualNodeType.SNAPSHOT,
    VisualNodeType.TABLE,
    VisualNodeType.VIEW,
    VisualNodeType.FACT,
    VisualNodeType.DIMENSION,
    VisualNodeType.CANONICAL_ENTITY,
    VisualNodeType.CANONICAL_EVENT,
}


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _node_shape(node_type: VisualNodeType) -> VisualNodeShape:
    if node_type in {VisualNodeType.SOURCE, VisualNodeType.SNAPSHOT}:
        return VisualNodeShape.HEXAGON
    if node_type in {VisualNodeType.CONSTRAINT, VisualNodeType.GRAIN, VisualNodeType.VALIDATION_CHECK}:
        return VisualNodeShape.DIAMOND
    if node_type in {VisualNodeType.MEASURE, VisualNodeType.QUALITY_SUBJECT}:
        return VisualNodeShape.PILL
    if node_type in {VisualNodeType.ENTITY_CLUSTER, VisualNodeType.CANONICAL_ENTITY}:
        return VisualNodeShape.CIRCLE
    return VisualNodeShape.RECTANGLE


def _line_style(edge: VisualizationGraphInputEdge) -> VisualLineStyle:
    if edge.declared or edge.edge_type in {
        VisualEdgeType.DECLARED_CONSTRAINT,
        VisualEdgeType.ACCEPTED_RELATIONSHIP,
        VisualEdgeType.ACCEPTED_SCHEMA_MAPPING,
        VisualEdgeType.CANONICAL_RELATIONSHIP,
        VisualEdgeType.CANONICAL_SOURCE_MAPPING,
        VisualEdgeType.ANALYTICAL_FACT_DIMENSION,
    }:
        return VisualLineStyle.SOLID
    if edge.inferred or edge.edge_type in {
        VisualEdgeType.INFERRED_RELATIONSHIP,
        VisualEdgeType.LINEAGE,
    }:
        return VisualLineStyle.DASHED
    return VisualLineStyle.DOTTED


def _node_description(node: VisualizationGraphInputNode) -> str:
    return (
        f"{node.label.accessible_text}; type={node.node_type.value}; "
        f"state={node.state.value}; evidence={node.evidence_state.value}; "
        f"reliability={node.reliability.value}; scope={node.observation_scope.value}"
    )


def _edge_description(edge: VisualizationGraphInputEdge, *, lineage_context: bool = False) -> str:
    qualifier = "declared" if edge.declared else "inferred" if edge.inferred else "candidate-or-reviewed"
    lineage_note = " Direction is source to target; this is lineage, not causality." if lineage_context or edge.edge_type is VisualEdgeType.LINEAGE else ""
    return (
        f"{edge.edge_type.value} ({qualifier}); state={edge.state.value}; "
        f"evidence={edge.evidence_state.value}; reliability={edge.reliability.value}; "
        f"scope={edge.observation_scope.value}.{lineage_note}"
    )


def _check_scope(request_id: str, scope: VisualizationScope) -> None:
    if request_id != scope.visualization_id:
        raise VisualizationInputError("visualization request and input scope IDs must match")


class VisualizationService:
    """Build bounded graph and chart projections with stable ordering and IDs."""

    visualization_version = "step26-visualization-v1"

    def build_graph(
        self,
        request: VisualizationRequest,
        graph_input: VisualizationGraphInput,
        *,
        direction: VisualDirection = VisualDirection.BOTH,
    ) -> VisualizationGraph:
        _check_scope(request.visualization_id, graph_input.scope)
        nodes = tuple(sorted(graph_input.nodes, key=lambda item: item.domain_ref))
        edges = tuple(sorted(graph_input.edges, key=lambda item: (item.domain_ref, item.source_ref, item.target_ref)))
        node_by_ref = {node.domain_ref: node for node in nodes}
        if len(node_by_ref) != len(nodes):
            raise VisualizationInputError("domain node references must be unique")
        edge_refs = {edge.domain_ref for edge in edges}
        if len(edge_refs) != len(edges):
            raise VisualizationInputError("domain edge references must be unique")
        if any(edge.source_ref not in node_by_ref or edge.target_ref not in node_by_ref for edge in edges):
            raise VisualizationInputError("visualization edges must reference known domain nodes")

        selected_node_types = set(request.node_types)
        selected_edge_types = set(request.edge_types)
        filtered_nodes = tuple(
            node for node in nodes
            if (not selected_node_types or node.node_type in selected_node_types)
            and (not request.evidence_states or node.evidence_state in request.evidence_states)
            and (not request.review_states or node.reviewability.review_state in request.review_states)
        )
        filtered_refs = {node.domain_ref for node in filtered_nodes}
        filtered_edges = tuple(
            edge for edge in edges
            if edge.source_ref in filtered_refs
            and edge.target_ref in filtered_refs
            and (not selected_edge_types or edge.edge_type in selected_edge_types)
        )

        focus = request.focus_ref
        if request.mode in {VisualizationDisclosureMode.NEIGHBORHOOD, VisualizationDisclosureMode.FOCUSED_PATH}:
            if not focus:
                raise VisualizationInputError(f"{request.mode.value} views require focus_ref")
            if focus not in filtered_refs:
                raise VisualizationInputError("focus_ref is absent after visualization filters")
            selected_refs = self._bounded_refs(
                focus,
                filtered_refs,
                filtered_edges,
                max_hops=request.max_hops,
                direction=direction,
            )
        else:
            selected_refs = set(filtered_refs)
            if request.mode is VisualizationDisclosureMode.OVERVIEW:
                top_level = {node.domain_ref for node in filtered_nodes if node.node_type in _TOP_LEVEL_TYPES}
                selected_refs = (selected_refs & top_level) or selected_refs

        ordered_refs = sorted(selected_refs, key=lambda value: (value != focus, value))
        selected_refs = set(ordered_refs[: request.max_nodes])
        rendered_nodes_input = tuple(node for node in filtered_nodes if node.domain_ref in selected_refs)
        rendered_edges_input = tuple(
            edge for edge in filtered_edges
            if edge.source_ref in selected_refs and edge.target_ref in selected_refs
        )
        rendered_edges_input = tuple(
            sorted(rendered_edges_input, key=lambda item: (item.domain_ref, item.source_ref, item.target_ref))[: request.max_edges]
        )
        rendered_node_refs = {node.domain_ref for node in rendered_nodes_input}

        visual_nodes = tuple(self._visual_node(request.visualization_id, node) for node in rendered_nodes_input)
        visual_node_by_ref = {node.domain_ref: node for node in visual_nodes}
        visual_edges = tuple(
            self._visual_edge(request.visualization_id, edge, visual_node_by_ref, lineage_context=request.kind is VisualizationKind.LINEAGE)
            for edge in rendered_edges_input
        )
        accessible_rows = tuple(
            AccessibleGraphRow(
                subject_visual_id=node.visual_node_id,
                subject_label=node.label.value,
                subject_description=node.accessible_description,
                related_visual_ids=tuple(sorted({
                    edge.target_visual_node_id if edge.source_visual_node_id == node.visual_node_id else edge.source_visual_node_id
                    for edge in visual_edges
                    if node.visual_node_id in {edge.source_visual_node_id, edge.target_visual_node_id}
                })),
            )
            for node in visual_nodes
        )
        hidden_nodes = len(nodes) - len(rendered_nodes_input)
        hidden_edges = len(edges) - len(rendered_edges_input)
        truncated = hidden_nodes > 0 or hidden_edges > 0
        reason = None
        if truncated:
            reason = "FILTERED_OR_BOUNDED; hidden counts are explicit and show-more is available"
        disclosure = DisclosureMetadata(
            mode=request.mode,
            total_node_count=len(nodes),
            total_edge_count=len(edges),
            rendered_node_count=len(rendered_nodes_input),
            rendered_edge_count=len(rendered_edges_input),
            hidden_node_count=hidden_nodes,
            hidden_edge_count=hidden_edges,
            aggregated_node_count=0,
            aggregated_edge_count=0,
            truncated=truncated,
            truncation_reason=reason,
            show_more_available=truncated,
            active_filters=tuple(sorted(request.active_filters)),
            focus_ref=focus,
            hop_depth=request.max_hops if request.mode in {VisualizationDisclosureMode.NEIGHBORHOOD, VisualizationDisclosureMode.FOCUSED_PATH} else None,
            accessible_summary=(
                f"{len(rendered_nodes_input)} of {len(nodes)} nodes and "
                f"{len(rendered_edges_input)} of {len(edges)} edges rendered; "
                f"{hidden_nodes} nodes and {hidden_edges} edges hidden. "
                "Hidden content is not treated as absent."
            ),
        )
        legend = (
            "Node shape identifies the project-owned node type; text and accessible descriptions carry the same meaning.",
            "Solid edges are declared or accepted; dashed edges are inferred or lineage; dotted edges are candidates or other provisional links.",
            "State, evidence, reliability, observation scope, review state, conflicts, and hidden counts are explicit fields; color is never the sole signal.",
        )
        payload = {
            "visualization_id": request.visualization_id,
            "kind": request.kind.value,
            "scope": graph_input.scope.model_dump(mode="json"),
            "nodes": [node.model_dump(mode="json") for node in visual_nodes],
            "edges": [edge.model_dump(mode="json") for edge in visual_edges],
            "disclosure": disclosure.model_dump(mode="json"),
            "legend": legend,
            "accessible_rows": [row.model_dump(mode="json") for row in accessible_rows],
        }
        return VisualizationGraph(
            visualization_id=request.visualization_id,
            kind=request.kind,
            scope=graph_input.scope,
            nodes=visual_nodes,
            edges=visual_edges,
            disclosure=disclosure,
            legend=legend,
            accessible_rows=accessible_rows,
            content_hash=visualization_content_hash(payload),
        )

    @staticmethod
    def _bounded_refs(
        focus: str,
        node_refs: set[str],
        edges: tuple[VisualizationGraphInputEdge, ...],
        *,
        max_hops: int,
        direction: VisualDirection,
    ) -> set[str]:
        forward: dict[str, list[str]] = defaultdict(list)
        reverse: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            forward[edge.source_ref].append(edge.target_ref)
            reverse[edge.target_ref].append(edge.source_ref)
        queue: deque[tuple[str, int]] = deque([(focus, 0)])
        selected = {focus}
        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            neighbors: list[str] = []
            if direction in {VisualDirection.DOWNSTREAM, VisualDirection.BOTH}:
                neighbors.extend(forward[current])
            if direction in {VisualDirection.UPSTREAM, VisualDirection.BOTH}:
                neighbors.extend(reverse[current])
            for neighbor in sorted(set(neighbors)):
                if neighbor in node_refs and neighbor not in selected:
                    selected.add(neighbor)
                    queue.append((neighbor, depth + 1))
        return selected

    @staticmethod
    def _visual_node(visualization_id: str, node: VisualizationGraphInputNode) -> VisualNode:
        return VisualNode(
            visual_node_id=visual_node_id(visualization_id, node.domain_ref),
            domain_ref=node.domain_ref,
            node_type=node.node_type,
            label=node.label,
            state=node.state,
            evidence_state=node.evidence_state,
            reliability=node.reliability,
            observation_scope=node.observation_scope,
            conflict_state=node.conflict_state,
            sensitivity_state=node.sensitivity_state,
            lineage_refs=tuple(sorted(node.lineage_refs)),
            provenance_refs=tuple(sorted(node.provenance_refs)),
            reviewability=node.reviewability,
            layout_hint=_node_shape(node.node_type),
            accessible_description=_node_description(node),
        )

    @staticmethod
    def _visual_edge(
        visualization_id: str,
        edge: VisualizationGraphInputEdge,
        visual_node_by_ref: Mapping[str, VisualNode],
        *,
        lineage_context: bool = False,
    ) -> VisualEdge:
        return VisualEdge(
            visual_edge_id=visual_edge_id(visualization_id, edge.domain_ref, edge.source_ref, edge.target_ref),
            source_visual_node_id=visual_node_by_ref[edge.source_ref].visual_node_id,
            target_visual_node_id=visual_node_by_ref[edge.target_ref].visual_node_id,
            domain_ref=edge.domain_ref,
            edge_type=edge.edge_type,
            state=edge.state,
            evidence_state=edge.evidence_state,
            reliability=edge.reliability,
            observation_scope=edge.observation_scope,
            conflict_state=edge.conflict_state,
            lineage_refs=tuple(sorted(edge.lineage_refs)),
            provenance_refs=tuple(sorted(edge.provenance_refs)),
            reviewability=edge.reviewability,
            line_style=_line_style(edge),
            accessible_description=_edge_description(edge, lineage_context=lineage_context),
            declared=edge.declared,
            inferred=edge.inferred,
        )

    def build_lineage(self, input_view: LineageViewInput) -> VisualizationGraph:
        if not input_view.focus_ref:
            raise VisualizationInputError("lineage views require an explicit focus_ref")
        request = VisualizationRequest(
            visualization_id=input_view.graph.scope.visualization_id,
            kind=VisualizationKind.LINEAGE,
            mode=VisualizationDisclosureMode.FOCUSED_PATH,
            max_nodes=250,
            max_edges=500,
            max_hops=input_view.max_depth,
            focus_ref=input_view.focus_ref,
        )
        return self.build_graph(request, input_view.graph, direction=input_view.direction)

    def build_evidence_breakdown(
        self,
        *,
        visualization_id: str,
        scope: VisualizationScope,
        subject_ref: str,
        subject_label: VisualLabel,
        items: Iterable[EvidenceVisualItem],
        max_items: int = 200,
    ) -> EvidenceBreakdownView:
        _check_scope(visualization_id, scope)
        if max_items < 1:
            raise VisualizationInputError("evidence breakdown max_items must be positive")
        ordered = tuple(sorted(items, key=lambda item: item.evidence_ref))
        visible = ordered[:max_items]
        hidden = len(ordered) - len(visible)
        supporting = sum(item.supporting for item in ordered)
        contradicting = sum(item.contradicting for item in ordered)
        summary = (
            f"{len(visible)} of {len(ordered)} evidence items shown; "
            f"supporting={supporting}, contradicting={contradicting}, "
            f"unclassified={len(ordered) - supporting - contradicting}. "
            "Families and conflicts remain separate; no score is converted to probability."
        )
        payload = {
            "visualization_id": visualization_id,
            "scope": scope.model_dump(mode="json"),
            "subject_ref": subject_ref,
            "subject_label": subject_label.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in visible],
            "total_item_count": len(ordered),
            "hidden_item_count": hidden,
        }
        return EvidenceBreakdownView(
            visualization_id=visualization_id,
            scope=scope,
            subject_ref=subject_ref,
            subject_label=subject_label,
            items=visible,
            total_item_count=len(ordered),
            hidden_item_count=hidden,
            truncated=hidden > 0,
            accessible_summary=summary,
            legend=(
                "Supporting and contradicting evidence have separate labels and rows; conflict references are not averaged away.",
                "Numeric values retain their declared metric semantics and observation scope.",
                "Human assertions are explicitly labeled and are not presented as model evidence.",
            ),
            content_hash=visualization_content_hash(payload),
        )

    def build_quality_heatmap(
        self,
        *,
        visualization_id: str,
        scope: VisualizationScope,
        cells: Iterable[QualityHeatmapCellInput],
    ) -> QualityHeatmapView:
        _check_scope(visualization_id, scope)
        ordered = tuple(sorted(cells, key=lambda item: (item.subject_ref, item.dimension, item.cell_id)))
        dimensions = tuple(sorted({cell.dimension for cell in ordered}))
        state_counts: dict[str, int] = defaultdict(int)
        for cell in ordered:
            state_counts[cell.state.value] += 1
        summary = "; ".join(f"{state}={state_counts[state]}" for state in sorted(state_counts))
        summary = (
            f"{len(ordered)} quality cells across {len(dimensions)} dimensions; {summary}. "
            "Each measurable cell carries numerator, denominator, scope, reliability, and provenance; unavailable states are explicit."
        )
        payload = {
            "visualization_id": visualization_id,
            "scope": scope.model_dump(mode="json"),
            "cells": [cell.model_dump(mode="json") for cell in ordered],
            "dimensions": dimensions,
        }
        return QualityHeatmapView(
            visualization_id=visualization_id,
            scope=scope,
            cells=ordered,
            dimensions=dimensions or ("UNMEASURED",),
            accessible_summary=summary,
            legend=(
                "Severity and state are text fields, not color-only signals.",
                "A denominator and observation scope distinguish measured, sampled, incomplete, and unavailable cells.",
            ),
            content_hash=visualization_content_hash(payload),
        )

    def build_profile_distribution(
        self,
        *,
        visualization_id: str,
        scope: VisualizationScope,
        profile: ProfileDistributionInput,
    ) -> ProfileDistributionView:
        _check_scope(visualization_id, scope)
        points = profile.points
        chart_kind = profile.chart_kind
        if profile.sensitivity_state.value in {"REDACTED", "RESTRICTED"}:
            points = ()
            chart_kind = VisualChartKind.NONE
        if profile.evidence_state is not VisualEvidenceState.OBSERVED:
            points = ()
            chart_kind = VisualChartKind.NONE
        if chart_kind is VisualChartKind.HISTOGRAM and not profile.ordered:
            raise VisualizationInputError("histograms require an ordered numeric profile contract")
        summary = (
            f"metric={profile.metric_kind}; chart={chart_kind.value}; points={len(points)}; "
            f"observation_scope={profile.observation_scope.value}; reliability={profile.reliability.value}; "
            f"evidence={profile.evidence_state.value}; sensitivity={profile.sensitivity_state.value}. "
            "No chart is emitted when profile evidence is unavailable or privacy-blocked."
        )
        payload = {
            "visualization_id": visualization_id,
            "scope": scope.model_dump(mode="json"),
            "metric_id": profile.metric_id,
            "column_ref": profile.column_ref,
            "metric_kind": profile.metric_kind,
            "points": [point.model_dump(mode="json") for point in points],
            "chart_kind": chart_kind.value,
            "observation_scope": profile.observation_scope.value,
            "reliability": profile.reliability.value,
            "evidence_state": profile.evidence_state.value,
            "sensitivity_state": profile.sensitivity_state.value,
        }
        return ProfileDistributionView(
            visualization_id=visualization_id,
            scope=scope,
            metric_id=profile.metric_id,
            column_ref=profile.column_ref,
            chart_kind=chart_kind,
            points=points,
            observation_scope=profile.observation_scope,
            reliability=profile.reliability,
            evidence_state=profile.evidence_state,
            sensitivity_state=profile.sensitivity_state,
            accessible_summary=summary,
            content_hash=visualization_content_hash(payload),
        )

    def build_validation(
        self,
        *,
        visualization_id: str,
        scope: VisualizationScope,
        checks: Iterable[ValidationVisualCheckInput],
    ) -> ValidationView:
        _check_scope(visualization_id, scope)
        ordered = tuple(sorted(checks, key=lambda item: item.check_id))
        if not ordered:
            raise VisualizationInputError("validation visualization requires checks")
        failures = sum(check.status == "FAIL" for check in ordered)
        review_required = sum(check.status == "REVIEW_REQUIRED" for check in ordered)
        not_evaluated = sum(check.status == "NOT_EVALUATED" for check in ordered)
        if failures:
            overall = "FAIL"
        elif not_evaluated:
            overall = "NOT_EVALUATED"
        elif review_required:
            overall = "REVIEW_REQUIRED"
        else:
            overall = "PASS"
        required = tuple(check for check in ordered if check.required)
        g6_eligible = overall == "PASS" and all(check.status == "PASS" for check in required)
        summary = (
            f"overall={overall}; checks={len(ordered)}; failures={failures}; "
            f"review_required={review_required}; not_evaluated={not_evaluated}; "
            f"g6_eligible={g6_eligible}. Individual statuses remain visible."
        )
        payload = {
            "visualization_id": visualization_id,
            "scope": scope.model_dump(mode="json"),
            "checks": [check.model_dump(mode="json") for check in ordered],
            "overall_status": overall,
            "g6_eligible": g6_eligible,
        }
        return ValidationView(
            visualization_id=visualization_id,
            scope=scope,
            checks=ordered,
            overall_status=overall,
            g6_eligible=g6_eligible,
            accessible_summary=summary,
            content_hash=visualization_content_hash(payload),
        )

    @staticmethod
    def build_measure(measure: AnalyticalMeasureVisualInput) -> AnalyticalMeasureVisualInput:
        """Validate and return the already reviewed analytical measure view."""

        return measure


__all__ = ["VisualizationInputError", "VisualizationService"]
