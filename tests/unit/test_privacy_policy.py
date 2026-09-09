from __future__ import annotations

from pathlib import Path

import pytest

from dirty_data_to_olap.application.privacy_policy import PrivacyOperationError, PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.privacy import (
    ClassificationState,
    ExposureContext,
    ExposureRequest,
    MaskingPolicy,
    PseudonymizationPolicy,
    PrivacyRule,
    SensitiveDataCategory,
)


def test_unknown_is_not_public_and_explicit_and_pattern_evidence_are_distinct():
    service = PrivacyPolicyService()
    unknown = service.classify_field("ordinary-value", column_id="column-1")
    assert unknown.state is ClassificationState.UNKNOWN
    assert service.decide_exposure(unknown, ExposureRequest(context=ExposureContext.EXPORT, purpose="test")).action.value == "ALLOW_MASKED"
    potential = service.classify_field("synthetic.person@example.test", column_id="email")
    assert potential.state is ClassificationState.POTENTIALLY_SENSITIVE
    explicit = service.classify_field("synthetic-id", column_id="national_id", rules=(PrivacyRule(rule_id="national-id", version="1", column_id="national_id", category=SensitiveDataCategory.GOVERNMENT_IDENTIFIER, reason="explicit fixture policy"),))
    assert explicit.state is ClassificationState.CONFIRMED_SENSITIVE


def test_artifact_sensitivity_is_conservative_and_record_refs_are_linkable():
    service = PrivacyPolicyService()
    assert service.classify_artifact("batch-1", "raw_staging").sensitivity.value == "RESTRICTED"
    assert service.classify_artifact("ref-1", "record_reference").retention_class == "linkable_metadata"
    assert service.classify_artifact("profile-1", "profile").raw_value_allowed is False
    assert service.classify_artifact("quality-1", "quality").external_processing_allowed is False


def test_masking_and_nested_log_sanitization_never_return_canaries():
    service = PrivacyPolicyService()
    email = "synthetic.person@example.test"
    secret = "STEP10_FAKE_SECRET_123"
    assert email not in service.mask(email, category=SensitiveDataCategory.EMAIL)
    assert secret not in service.mask(secret, category=SensitiveDataCategory.AUTHENTICATION_SECRET)
    safe = service.sanitize_log_value({"nested": [{"email": email}, {"token": secret}]})
    assert email not in str(safe)
    assert secret not in str(safe)
    with pytest.raises(PrivacyOperationError):
        service.mask(None, policy=MaskingPolicy(fail_closed=True))


def test_pseudonymization_is_keyed_scoped_and_key_is_not_serialized():
    service = PrivacyPolicyService()
    policy = PseudonymizationPolicy(key_reference="runtime-ref", scope="column.email", version="1")
    first = service.pseudonymize("synthetic.person@example.test", key=b"test-only-key", policy=policy)
    second = service.pseudonymize("synthetic.person@example.test", key=b"test-only-key", policy=policy)
    assert first == second and "synthetic.person@example.test" not in first
    assert "test-only-key" not in policy.model_dump_json()
    with pytest.raises(PrivacyOperationError):
        service.pseudonymize("synthetic.person@example.test", key=None, policy=policy)


def test_external_processing_and_debug_are_fail_closed():
    service = PrivacyPolicyService()
    assert service.prepare_external_payload({"count": 4, "ratio": 0.5}, aggregate_only=True).allowed
    assert not service.prepare_external_payload({"email": "synthetic.person@example.test"}, aggregate_only=True).allowed
    assert not service.prepare_external_payload({"count": 4}, aggregate_only=False).allowed
    with pytest.raises(PrivacyOperationError):
        service.build_debug_bundle(artifact=service.classify_artifact("raw-1", "raw_staging"), metadata={"email": "synthetic.person@example.test"})


def test_retention_cleanup_rejects_path_escape_and_deletes_only_owned_ephemeral_file(tmp_path: Path):
    service = PrivacyPolicyService()
    allowed = tmp_path / "privacy_ephemeral"
    allowed.mkdir()
    child = allowed / "run.tmp"
    child.write_text("ephemeral", encoding="utf-8")
    result = service.cleanup_ephemeral(child, allowed_root=allowed)
    assert result.allowed and not child.exists()
    outside = tmp_path / "outside.tmp"
    outside.write_text("do not delete", encoding="utf-8")
    with pytest.raises(PrivacyOperationError):
        service.cleanup_ephemeral(outside, allowed_root=allowed)


def test_privacy_canary_is_allowed_only_in_raw_source_faithful_staging():
    service = PrivacyPolicyService()
    values = ("synthetic.person@example.test", "+989121234567", "ordinary-value")
    restricted = service.scan_values("scan-staging", values, allow_raw_staging=True)
    assert restricted.clean and restricted.raw_sensitive_matches == 2
    exposed = service.scan_values("scan-export", values, allow_raw_staging=False)
    assert not exposed.clean and exposed.raw_sensitive_matches == 2
