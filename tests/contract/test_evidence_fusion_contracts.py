from dirty_data_to_olap.domain.contracts.evidence_fusion import *


def test_policy_contract_blocks_automation_and_decisions_are_review_only():
    policy = FusionPolicyReference(policy_id="p", version="1", status=FusionPolicyStatus.UNCALIBRATED, content_hash="hash")
    assert not policy.automation_enabled and policy.requires_g5_for_automation
    score = FusionScore(value=None, eligible_weight=0, observed_weight=0, evidence_coverage=0, sufficient=False)
    assert score.confidence_kind is ConfidenceKind.UNCALIBRATED_SCORE


def test_missing_signal_cannot_have_numeric_value():
    try:
        NormalizedEvidenceSignal(signal_id="s", subject_id="rel:x", evidence_id="e", producer_id="p", family=EvidenceFamily.PROFILE, role=EvidenceRole.DIRECT_OBSERVATION, raw_metric_name="missing", raw_metric_value=1, raw_metric_semantics="x", normalization_method="x", normalization_version="1", normalized_value=0, direction=EvidenceDirection.SUPPORTS, presence=EvidencePresenceState.UNAVAILABLE, scope_id="scope", correlation_group="e")
    except ValueError:
        return
    raise AssertionError("unavailable evidence must not carry a numeric value")
