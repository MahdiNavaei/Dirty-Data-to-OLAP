"""Deterministic, project-owned projections for Step26 visualization views.

The service turns reviewed domain-shaped inputs into bounded view models.  It
does not render HTML, execute SQL, expose raw domain objects, or make a new
decision about evidence, identity, canonicalization, or analytical truth.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from dirty_data_to_olap.domain.contracts.analytical import AnalyticalPlan, FactSpec, MeasureSpec
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.domain.contracts.visualization import (
    AccessibleGraphRow,
    AnalyticalMeasureVisualInput,
    AnalyticalMeasureVisualization,
    DisclosureMetadata,
    EvidenceBreakdownView,
    EvidenceVisualItem,
    LineageViewInput,
    ProfileDistributionInput,
    ProfileDistributionView,
    QualityHeatmapCellInput,
    QualityHeatmapView,
    ValidationView,
    ValidationReportVisualization,
    ValidationReportVisualizationBinding,
    ValidationVisualCheckInput,
    VisualAggregationClass,
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


_FILTER_PREFIXES = {
    "node_types=": "node_types",
    "edge_types=": "edge_types",
    "evidence_states=": "evidence_states",
    "review_states=": "review_states",
}


def _effective_active_filters(request: VisualizationRequest) -> tuple[str, ...]:
    selectors = {
        "node_types": tuple(sorted({item.value for item in request.node_types})),
        "edge_types": tuple(sorted({item.value for item in request.edge_types})),
        "evidence_states": tuple(sorted({item.value for item in request.evidence_states})),
        "review_states": tuple(sorted({item.value for item in request.review_states})),
    }
    canonical = {
        key: f"{key}={','.join(values)}"
        for key, values in selectors.items()
        if values
    }
    free_form: list[str] = []
    for raw_filter in request.active_filters:
        value = raw_filter.strip()
        if not value:
            raise VisualizationInputError("active filter labels cannot be empty")
        matching_prefix = next((prefix for prefix in _FILTER_PREFIXES if value.startswith(prefix)), None)
        if matching_prefix is not None:
            key = _FILTER_PREFIXES[matching_prefix]
            if key not in canonical or value != canonical[key]:
                raise VisualizationInputError(f"active filter {value!r} contradicts the effective {key} selector")
        free_form.append(value)
    return tuple(sorted(set((*canonical.values(), *free_form))))


def _safe_validation_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return text if len(text) <= 160 else "[SCALAR_VALUE_REDACTED]"
    return "[STRUCTURED_VALUE_REDACTED]"


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
    if edge.edge_type is VisualEdgeType.ER_AUTHORIZED_LINKAGE:
        return VisualLineStyle.SOLID
    if edge.edge_type is VisualEdgeType.ER_CANDIDATE_LINK:
        return VisualLineStyle.DOTTED
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
    if edge.edge_type is VisualEdgeType.ER_CANDIDATE_LINK:
        qualifier = "candidate linkage evidence; not canonical identity"
    elif edge.edge_type is VisualEdgeType.ER_AUTHORIZED_LINKAGE:
        qualifier = "authorized linkage evidence; not canonical identity"
    else:
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
            active_filters=_effective_active_filters(request),
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
                "Solid edges are declared, accepted, or authorized linkage evidence; dashed edges are inferred or lineage; dotted edges are candidates. ER linkage is evidence, not canonical identity.",
                "State, evidence, reliability, observation scope, review state, conflicts, and hidden counts are explicit fields; color is never the sole signal.",
                "FOCUSED_PATH is focused bounded lineage exploration from a focus reference, not a unique source-to-target path.",
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
        elif review_required:
            overall = "REVIEW_REQUIRED"
        else:
            overall = "NOT_EVALUATED"
        g6_eligible = False
        summary = (
            f"overall={overall}; checks={len(ordered)}; failures={failures}; "
            f"review_required={review_required}; not_evaluated={not_evaluated}; "
            "exploratory subset only; authoritative G6 remains in ValidationReport."
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

    def build_validation_from_report(
        self,
        *,
        visualization_id: str,
        scope: VisualizationScope,
        report: ValidationReport,
        binding: ValidationReportVisualizationBinding,
    ) -> ValidationReportVisualization:
        """Project the complete authoritative report without re-deriving G6."""

        _check_scope(visualization_id, scope)
        try:
            report = ValidationReport.model_validate(report.model_dump(mode="python"))
        except ValidationError as exc:
            raise VisualizationInputError("validation report failed canonical revalidation") from exc
        if scope.run_id != report.run_id or binding.run_id != report.run_id:
            raise VisualizationInputError("validation visualization scope and binding must use the report run")
        if binding.validation_report_id != report.report_id:
            raise VisualizationInputError("validation report ID does not match its visualization binding")
        if binding.validation_report_content_hash != report.content_hash:
            raise VisualizationInputError("validation report content hash is stale or mismatched")
        if binding.validation_policy_id != report.policy.policy_id or binding.validation_policy_version != report.policy.policy_version:
            raise VisualizationInputError("validation policy does not match its visualization binding")
        if report.bindings.validation_policy_id != report.policy.policy_id or report.bindings.validation_policy_version != report.policy.policy_version:
            raise VisualizationInputError("validation report artifact bindings do not match its policy")

        report_check_ids = tuple(sorted(item.check_id for item in report.checks))
        required_check_ids = tuple(sorted(report.policy.required_check_ids))
        if binding.complete_check_ids != report_check_ids:
            raise VisualizationInputError("validation visualization must include the complete check universe")
        if binding.required_check_ids != required_check_ids:
            raise VisualizationInputError("validation visualization required check IDs do not match the policy")
        if tuple(sorted(binding.provenance_refs)) != tuple(sorted(report.provenance_refs)):
            raise VisualizationInputError("validation visualization provenance does not match the report")
        if len(report_check_ids) != len(report.checks):
            raise VisualizationInputError("validation report contains duplicate check IDs")

        checks = tuple(
            ValidationVisualCheckInput(
                check_id=check.check_id,
                subject_ref=f"validation:{report.report_id}:{check.check_id}",
                status=check.status.value,
                severity=check.severity.value,
                scope=check.scope.value,
                required=check.check_id in set(report.policy.required_check_ids),
                expected=_safe_validation_value(check.expected),
                observed=_safe_validation_value(check.observed),
                discrepancy_refs=tuple(sorted(check.discrepancy_ids)),
                evidence_refs=tuple(sorted(check.evidence_refs)),
                provenance_refs=tuple(sorted(report.provenance_refs)),
            )
            for check in sorted(report.checks, key=lambda item: item.check_id)
        )
        summary = (
            f"authoritative report={report.report_id}; policy={report.policy.policy_id}@{report.policy.policy_version}; "
            f"checks={len(checks)}; overall={report.overall_status.value}; g6={report.g6_status.value}; "
            f"g6_eligible={report.g6_eligible}. Complete report universe and individual evidence remain visible."
        )
        payload = {
            "visualization_id": visualization_id,
            "scope": scope.model_dump(mode="json"),
            "validation_report_id": report.report_id,
            "validation_report_content_hash": report.content_hash,
            "validation_policy_id": report.policy.policy_id,
            "validation_policy_version": report.policy.policy_version,
            "complete_check_ids": report_check_ids,
            "required_check_ids": required_check_ids,
            "checks": [check.model_dump(mode="json") for check in checks],
            "overall_status": report.overall_status.value,
            "g6_status": report.g6_status.value,
            "g6_eligible": report.g6_eligible,
            "provenance_refs": tuple(sorted(report.provenance_refs)),
            "authoritative": True,
        }
        return ValidationReportVisualization(
            visualization_id=visualization_id,
            scope=scope,
            validation_report_id=report.report_id,
            validation_report_content_hash=report.content_hash,
            validation_policy_id=report.policy.policy_id,
            validation_policy_version=report.policy.policy_version,
            complete_check_ids=report_check_ids,
            required_check_ids=required_check_ids,
            checks=checks,
            overall_status=report.overall_status.value,
            g6_status=report.g6_status.value,
            g6_eligible=report.g6_eligible,
            provenance_refs=tuple(sorted(report.provenance_refs)),
            accessible_summary=summary,
            content_hash=visualization_content_hash(payload),
        )

    @staticmethod
    def build_measure(measure: AnalyticalMeasureVisualInput) -> AnalyticalMeasureVisualInput:
        """Reject the legacy unbound path; it cannot establish OLAP truth."""

        raise VisualizationInputError(
            "unbound analytical measure input cannot be authoritative; use build_measure_from_spec"
        )

    @staticmethod
    def build_measure_from_spec(
        *,
        visualization_id: str,
        measure_spec: MeasureSpec,
        analytical_plan: AnalyticalPlan,
        fact_spec: FactSpec | None = None,
        expected_plan_content_hash: str | None = None,
        expected_package_hash: str | None = None,
    ) -> AnalyticalMeasureVisualization:
        """Project exact reviewed measure semantics from the analytical plan."""

        if expected_plan_content_hash is not None and analytical_plan.content_hash != expected_plan_content_hash:
            raise VisualizationInputError("analytical plan content hash is stale or mismatched")
        if expected_package_hash is not None and analytical_plan.analytical_spec_package_hash != expected_package_hash:
            raise VisualizationInputError("analytical specification package hash is stale or mismatched")
        if measure_spec.measure_id not in analytical_plan.measure_spec_ids:
            raise VisualizationInputError("measure is not part of the analytical plan")
        if analytical_plan.measure_spec_content_hashes.get(measure_spec.measure_id) != measure_spec.semantic_content_hash:
            raise VisualizationInputError("measure semantic content hash does not match the analytical plan")
        if measure_spec.fact_id not in analytical_plan.materialized_fact_ids:
            raise VisualizationInputError("measure fact is not materialized by the analytical plan")
        if fact_spec is not None:
            if fact_spec.fact_id != measure_spec.fact_id:
                raise VisualizationInputError("measure and fact references do not match")
            if measure_spec.measure_id not in fact_spec.measure_ids:
                raise VisualizationInputError("measure is not owned by the supplied fact specification")
            if analytical_plan.fact_spec_content_hashes.get(fact_spec.fact_id) != fact_spec.semantic_content_hash:
                raise VisualizationInputError("fact semantic content hash does not match the analytical plan")

        package_hash = analytical_plan.analytical_spec_package_hash
        output = {
            "visualization_id": visualization_id,
            "measure_id": measure_spec.measure_id,
            "fact_id": measure_spec.fact_id,
            "grain_spec_id": fact_spec.grain_spec_id if fact_spec is not None else None,
            "label": VisualLabel(value=measure_spec.semantic_name, accessible_text=measure_spec.semantic_name).model_dump(mode="json"),
            "measure_semantic_content_hash": measure_spec.semantic_content_hash,
            "analytical_plan_id": analytical_plan.plan_id,
            "analytical_plan_version": analytical_plan.plan_version,
            "analytical_plan_content_hash": analytical_plan.content_hash,
            "analytical_spec_package_hash": package_hash,
            "aggregation_class": VisualAggregationClass(measure_spec.aggregation_class.value),
            "aggregation_rule": measure_spec.aggregation_rule,
            "unit_semantics": measure_spec.unit_semantics,
            "currency_semantics": measure_spec.currency_semantics,
            "logical_type": measure_spec.logical_type,
            "domain_assertion_refs": tuple(sorted(measure_spec.domain_assertion_refs)),
            "provenance_refs": tuple(sorted(set(measure_spec.provenance_refs) | set(analytical_plan.provenance_refs))),
            "measure_review_state": measure_spec.review_state.value,
            "plan_review_state": analytical_plan.review_state.value,
            "fact_review_state": fact_spec.review_state.value if fact_spec is not None else None,
            "authoritative": True,
        }
        return AnalyticalMeasureVisualization(
            **output,
            accessible_summary=(
                f"reviewed measure={measure_spec.measure_id}; fact={measure_spec.fact_id}; "
                f"aggregation={measure_spec.aggregation_class.value}; rule={measure_spec.aggregation_rule}; "
                f"unit={measure_spec.unit_semantics}; currency={measure_spec.currency_semantics}; "
                f"measure_review={measure_spec.review_state.value}; plan_review={analytical_plan.review_state.value}."
            ),
            content_hash=visualization_content_hash(output),
        )


__all__ = ["VisualizationInputError", "VisualizationService"]
