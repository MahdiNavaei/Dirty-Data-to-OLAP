from __future__ import annotations

import json

from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.privacy import ExposureContext, ExposureRequest


def test_step10_canary_and_output_inspection():
    service = PrivacyPolicyService()
    email = "synthetic.person@example.test"
    phone = "+989121234567"
    secret = "STEP10_FAKE_SECRET_123"
    raw_staging = {"email": email, "phone": phone, "secret": secret}
    profile_artifact = {"pattern_summary": "EMAIL_LIKE", "profile_refs": ["profile-1"]}
    quality_artifact = {"issue_type": "EXPECTED_PATTERN_MISMATCH", "affected_record_refs": ["record-ref-1"]}
    log_output = service.sanitize_log_value({"payload": raw_staging, "token": secret})
    debug_output = service.build_debug_bundle(artifact=service.classify_artifact("quality-1", "quality"), metadata=quality_artifact)
    external_decision = service.prepare_external_payload({"row_count": 4, "null_ratio": 0.25}, aggregate_only=True)
    assert service.scan_values("step10-raw-staging", tuple(raw_staging.values()), allow_raw_staging=True).clean
    assert not service.scan_values("step10-export", tuple(raw_staging.values()), allow_raw_staging=False).clean
    assert email not in json.dumps(profile_artifact)
    assert email not in json.dumps(quality_artifact)
    assert email not in json.dumps(log_output)
    assert phone not in json.dumps(log_output)
    assert secret not in json.dumps(log_output)
    assert email not in json.dumps(debug_output)
    assert external_decision.allowed
    assert not service.decide_exposure(service.classify_field(email), ExposureRequest(context=ExposureContext.EXTERNAL_PROCESSING, purpose="step10-test")).allowed
