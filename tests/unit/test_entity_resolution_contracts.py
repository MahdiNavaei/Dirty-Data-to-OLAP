from __future__ import annotations

import pytest

from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule,
    ERThresholdPolicy,
    EntityResolutionMode,
    EntityResolutionNormalizationRule,
)


def test_er_blocks_cartesian_and_unsafe_normalization():
    with pytest.raises(ValueError):
        ERBlockingRule(rule_id="cartesian", version="1", field_ids=("email",), sql_expression="1=1")
    with pytest.raises(ValueError):
        EntityResolutionNormalizationRule(rule_id="bad", version="1", applies_to=("name",), operations=("NFKC", "TRANSLITERATE"))


def test_threshold_policy_is_review_below_match():
    with pytest.raises(ValueError):
        ERThresholdPolicy(match_probability_threshold=0.8, review_probability_threshold=0.9)
    assert EntityResolutionMode.LINK_ONLY.value == "LINK_ONLY"
