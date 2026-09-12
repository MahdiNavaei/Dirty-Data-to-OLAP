from __future__ import annotations

import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_step26_machine_specs_preserve_visualization_boundaries() -> None:
    encoding = yaml.safe_load((ROOT / "docs/visualization/specs/visualization_encoding.yml").read_text(encoding="utf-8"))
    bounds = yaml.safe_load((ROOT / "docs/visualization/specs/graph_bounds.yml").read_text(encoding="utf-8"))
    assert encoding["raw_domain_objects_to_browser"] is False
    assert encoding["color_only_encoding"] is False
    assert encoding["graph"]["declared_vs_inferred_distinct"] is True
    assert encoding["graph"]["cluster_is_canonical_identity"] is False
    assert encoding["graph"]["raw_score_is_probability"] is False
    assert encoding["analytical"]["non_additive_default_sum"] is False
    assert bounds["disclosure"]["silent_truncation"] is False
    assert bounds["neighborhood"]["focus_required"] is True
    required_states = set(encoding["states"]["exact_step25_interaction_states_supported"])
    from dirty_data_to_olap.domain.contracts.visualization import VisualState
    assert required_states.issubset({state.value for state in VisualState})


def test_step26_service_has_no_renderer_or_later_surface_dependency() -> None:
    source = (ROOT / "src/dirty_data_to_olap/application/visualization.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import) and node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = {"plotly", "networkx", "cytoscape", "react", "fastapi", "flask", "sqlalchemy", "redis"}
    assert not any(item.split(".", 1)[0].lower() in forbidden for item in imports)
    assert "Frontend" not in source


def test_step26_docs_explicitly_defer_step27_plus() -> None:
    docs = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (ROOT / "docs/visualization").rglob("*")
        if path.is_file()
    )
    assert "step27" in docs
    assert "step28" in docs
    assert "step29" in docs
    assert "browser fps" in docs
