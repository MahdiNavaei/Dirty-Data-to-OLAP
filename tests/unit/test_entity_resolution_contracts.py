from __future__ import annotations

import pytest

from dirty_data_to_olap.adapters.entity_resolution.splink import SplinkEntityResolutionAdapter
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule,
    ERThresholdPolicy,
    EntityMatchPredictionBand,
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


def test_match_weight_gate_requires_independent_evidence():
    policy = ERThresholdPolicy(match_probability_threshold=0.95, review_probability_threshold=0.80, match_weight_threshold=-5.0)

    band, risk = SplinkEntityResolutionAdapter._prediction_band(probability=0.04, weight=-4.69, evidence_count=2, policy=policy)
    assert band is EntityMatchPredictionBand.STRONG_LINK_EVIDENCE
    assert risk == []

    band, risk = SplinkEntityResolutionAdapter._prediction_band(probability=0.04, weight=-4.69, evidence_count=1, policy=policy)
    assert band is EntityMatchPredictionBand.REVIEW_LINK_EVIDENCE
    assert risk == ["insufficient_independent_evidence"]

    band, risk = SplinkEntityResolutionAdapter._prediction_band(probability=1e-300, weight=-996.5, evidence_count=1, policy=policy)
    assert band is EntityMatchPredictionBand.BELOW_EVIDENCE_THRESHOLD
    assert risk == []
