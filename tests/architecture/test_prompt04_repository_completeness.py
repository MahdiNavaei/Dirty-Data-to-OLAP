"""Publication-integrity checks for the Prompt04 review runtime."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application import review_readiness


def test_prompt04_readiness_module_is_loaded_from_this_project() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    expected = repository_root / "src" / "dirty_data_to_olap" / "application" / "review_readiness.py"

    assert expected.is_file()
    assert Path(review_readiness.__file__).resolve() == expected.resolve()
