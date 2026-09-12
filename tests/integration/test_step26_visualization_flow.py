from __future__ import annotations

from time import perf_counter

from dirty_data_to_olap.application.visualization import VisualizationService
from dirty_data_to_olap.domain.contracts.visualization import (
    VisualEdgeType,
    VisualEvidenceState,
    VisualLabel,
    VisualObservationScope,
    VisualReliabilityState,
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


def _large_graph() -> VisualizationGraphInput:
    scope = VisualizationScope(
        visualization_id="viz-large-step26",
        visualization_version="step26-visualization-v1",
        project_id="project-demo",
        run_id="run-large-demo",
        snapshot_id="snapshot-large-demo",
        stage_id="stage-large-demo",
        scope_id="scope-large-demo",
        scope_semantics="deterministic synthetic performance scope",
        provenance_refs=("prov-large-demo",),
    )
    nodes: list[VisualizationGraphInputNode] = [
        VisualizationGraphInputNode(
            domain_ref="source:synthetic",
            node_type=VisualNodeType.SOURCE,
            label=VisualLabel(value="Synthetic source", accessible_text="Synthetic source"),
            state=VisualState.OBSERVED,
            evidence_state=VisualEvidenceState.OBSERVED,
            reliability=VisualReliabilityState.FULL,
            observation_scope=VisualObservationScope.FULL_SNAPSHOT,
            provenance_refs=("prov-large-demo",),
        )
    ]
    edges: list[VisualizationGraphInputEdge] = []
    for table_index in range(220):
        table_ref = f"table:{table_index:03d}"
        nodes.append(
            VisualizationGraphInputNode(
                domain_ref=table_ref,
                node_type=VisualNodeType.TABLE,
                label=VisualLabel(value=table_ref, accessible_text=table_ref),
                state=VisualState.OBSERVED,
                evidence_state=VisualEvidenceState.OBSERVED,
                reliability=VisualReliabilityState.FULL,
                observation_scope=VisualObservationScope.FULL_SNAPSHOT,
                provenance_refs=("prov-large-demo",),
            )
        )
        edges.append(
            VisualizationGraphInputEdge(
                domain_ref=f"contains:source-{table_index:03d}",
                source_ref="source:synthetic",
                target_ref=table_ref,
                edge_type=VisualEdgeType.CONTAINS,
                reliability=VisualReliabilityState.FULL,
                observation_scope=VisualObservationScope.FULL_SNAPSHOT,
                provenance_refs=("prov-large-demo",),
            )
        )
        column_count = 600 if table_index == 0 else 13
        for column_index in range(column_count):
            column_ref = f"column:{table_index:03d}.{column_index:04d}"
            nodes.append(
                VisualizationGraphInputNode(
                    domain_ref=column_ref,
                    node_type=VisualNodeType.COLUMN,
                    label=VisualLabel(value=column_ref, accessible_text=column_ref),
                    state=VisualState.OBSERVED,
                    evidence_state=VisualEvidenceState.OBSERVED,
                    reliability=VisualReliabilityState.FULL,
                    observation_scope=VisualObservationScope.FULL_SNAPSHOT,
                    provenance_refs=("prov-large-demo",),
                )
            )
            edges.append(
                VisualizationGraphInputEdge(
                    domain_ref=f"contains:{table_index:03d}-{column_index:04d}",
                    source_ref=table_ref,
                    target_ref=column_ref,
                    edge_type=VisualEdgeType.CONTAINS,
                    reliability=VisualReliabilityState.FULL,
                    observation_scope=VisualObservationScope.FULL_SNAPSHOT,
                    provenance_refs=("prov-large-demo",),
                )
            )
    return VisualizationGraphInput(scope=scope, nodes=tuple(nodes), edges=tuple(edges))


def test_representative_large_graph_is_progressively_disclosed() -> None:
    graph = _large_graph()
    assert len(graph.nodes) >= 3000
    assert len(graph.edges) >= 3000
    service = VisualizationService()

    started = perf_counter()
    overview = service.build_graph(
        VisualizationRequest(
            visualization_id="viz-large-step26",
            kind=VisualizationKind.SOURCE_SCHEMA,
            mode=VisualizationDisclosureMode.OVERVIEW,
            max_nodes=250,
            max_edges=500,
        ),
        graph,
    )
    neighborhood = service.build_graph(
        VisualizationRequest(
            visualization_id="viz-large-step26",
            kind=VisualizationKind.NEIGHBORHOOD,
            mode=VisualizationDisclosureMode.NEIGHBORHOOD,
            focus_ref="table:000",
            max_nodes=120,
            max_edges=180,
            max_hops=1,
        ),
        graph,
    )
    elapsed = perf_counter() - started

    assert elapsed < 15.0
    assert len(overview.nodes) <= 250
    assert len(overview.edges) <= 500
    assert overview.disclosure.hidden_node_count > 0
    assert overview.disclosure.hidden_edge_count > 0
    assert overview.disclosure.show_more_available is True
    assert len(neighborhood.nodes) <= 120
    assert len(neighborhood.edges) <= 180
    assert neighborhood.disclosure.focus_ref == "table:000"
    assert neighborhood.disclosure.hop_depth == 1
    assert neighborhood.disclosure.hidden_node_count > 0
