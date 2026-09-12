from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from tools.validate_step25_ux import (
    CHECKPOINTS,
    REQUIRED_STATES,
    _evaluate,
    validate,
)


ROOT = Path(__file__).resolve().parents[2]


def _fixtures() -> tuple[dict, dict, dict, dict]:
    spec = yaml.safe_load((ROOT / "docs/ux/specs/review_experience.yml").read_text(encoding="utf-8"))
    states = yaml.safe_load((ROOT / "docs/ux/specs/interaction_states.yml").read_text(encoding="utf-8"))
    sources = {
        "canonical": (ROOT / "src/dirty_data_to_olap/domain/contracts/canonical.py").read_text(encoding="utf-8"),
        "review_policy": (ROOT / "src/dirty_data_to_olap/application/review_policy.py").read_text(encoding="utf-8"),
        "evidence": (ROOT / "src/dirty_data_to_olap/domain/contracts/evidence_fusion.py").read_text(encoding="utf-8"),
        "quality": (ROOT / "src/dirty_data_to_olap/domain/contracts/quality.py").read_text(encoding="utf-8"),
        "analytical": (ROOT / "src/dirty_data_to_olap/domain/contracts/analytical.py").read_text(encoding="utf-8"),
        "validation": (ROOT / "src/dirty_data_to_olap/domain/contracts/validation.py").read_text(encoding="utf-8"),
        "privacy": (ROOT / "src/dirty_data_to_olap/domain/contracts/privacy.py").read_text(encoding="utf-8"),
        "architecture": (ROOT / "docs/architecture/SOFTWARE_ARCHITECTURE_CONTRACT.md").read_text(encoding="utf-8") + (ROOT / "docs/architecture/specs/review_checkpoints.yml").read_text(encoding="utf-8"),
    }
    docs = {path.name: path.read_text(encoding="utf-8") for path in (ROOT / "docs/ux").glob("*.md")}
    return spec, states, sources, docs


def _failed(checks: list[dict]) -> set[str]:
    return {item["name"] for item in checks if item["status"] != "PASS"}


def test_step25_validator_passes_against_current_contracts() -> None:
    failures = _failed(validate(ROOT))
    assert not failures, sorted(failures)


def test_step25_has_exact_checkpoint_and_state_registry() -> None:
    spec, states, _, _ = _fixtures()
    assert [item["checkpoint_id"] for item in spec["exact_checkpoints"]] == list(CHECKPOINTS)
    assert [item["state_id"] for item in states["states"]] == list(REQUIRED_STATES)


def test_negative_fixture_rejects_raw_score_as_probability() -> None:
    spec, states, sources, docs = _fixtures()
    mutated = deepcopy(spec)
    mutated["evidence_semantics"]["raw_score_is_probability"] = True
    failures = _failed(_evaluate(mutated, states, sources, docs))
    assert "score is not probability" in failures


def test_negative_fixture_rejects_unbounded_mass_merge() -> None:
    spec, states, sources, docs = _fixtures()
    mutated = deepcopy(spec)
    mutated["bulk_policy"]["arbitrary_mass_entity_merge_allowed"] = True
    failures = _failed(_evaluate(mutated, states, sources, docs))
    assert "mass high-impact action is blocked" in failures


def test_negative_fixture_rejects_color_only_accessibility() -> None:
    spec, states, sources, docs = _fixtures()
    mutated = deepcopy(spec)
    mutated["accessibility"]["color_alone_sufficient"] = True
    failures = _failed(_evaluate(mutated, states, sources, docs))
    assert "accessibility is nonvisual" in failures


def test_step25_does_not_add_frontend_or_step26_implementation() -> None:
    assert not (ROOT / "src/dirty_data_to_olap/application/ux.py").exists()
    assert not (ROOT / "src/dirty_data_to_olap/application/stage_orchestrator.py").exists()
    assert not (ROOT / "src/dirty_data_to_olap/application/job_control.py").exists()
