"""Loader for project-owned versioned quality rules."""

from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.domain.contracts.quality import QualityRuleSet


def load_quality_rule_set(path: Path) -> QualityRuleSet:
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return QualityRuleSet.model_validate(payload)
