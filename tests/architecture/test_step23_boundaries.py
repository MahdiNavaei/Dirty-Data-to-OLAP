from __future__ import annotations

import re
from pathlib import Path


def test_step23_platform_does_not_import_distributed_runtime_or_qa_oracle() -> None:
    root = Path(__file__).parents[2]
    paths = [root / "src/dirty_data_to_olap/domain/contracts/platform.py", root / "src/dirty_data_to_olap/application/platform.py"]
    paths.extend((root / "src/dirty_data_to_olap/adapters/platform").rglob("*.py"))
    sources = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    lowered = sources.lower()
    assert "benchmarks/validation" not in lowered
    assert not re.search(r"\b(celery|ray|spark|kubernetes)\b", lowered)
    assert "pickle" not in lowered


def test_step23_is_reflected_as_infrastructure_not_a_business_stage() -> None:
    root = Path(__file__).parents[2]
    stage_graph = (root / "docs/architecture/specs/stage_graph.yml").read_text(encoding="utf-8")
    assert "PLATFORM_FOUNDATION" not in stage_graph
