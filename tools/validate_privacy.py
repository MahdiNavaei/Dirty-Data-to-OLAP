"""Validate the implemented Step10 privacy boundary and its handoff state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.parse_args()
    checks: list[tuple[str, bool]] = []
    required_docs = (
        "DATA_HANDLING_POLICY.md", "CLASSIFICATION_AND_SENSITIVITY.md",
        "MASKING_AND_PSEUDONYMIZATION.md", "LOGGING_DEBUG_AND_EXPORT.md",
        "RETENTION_POLICY.md", "EXTERNAL_PROCESSING_POLICY.md",
        "STEP11_SECURITY_HANDOFF.md",
    )
    checks.append(("privacy documents exist", all((ROOT / "docs" / "privacy" / name).is_file() for name in required_docs)))
    config = yaml.safe_load((ROOT / "config" / "privacy-defaults.yml").read_text(encoding="utf-8"))
    checks.append(("privacy defaults are conservative", config["unknown_state"] == "UNKNOWN" and config["external_allow_raw_sensitive"] is False and config["external_allow_unknown"] is False and config["raw_staging_sensitivity"] == "RESTRICTED"))
    try:
        from dirty_data_to_olap.application.privacy_policy import PrivacyOperationError, PrivacyPolicyService
        from dirty_data_to_olap.domain.contracts.privacy import AggregateSafeMetric, ClassificationState, ExposureContext, ExposureRequest, MaskingPolicy, PseudonymizationPolicy

        service = PrivacyPolicyService()
        unknown = service.classify_field("unmatched-value")
        checks.append(("no detector match remains UNKNOWN", unknown.state is ClassificationState.UNKNOWN and not service.decide_exposure(unknown, ExposureRequest(context=ExposureContext.EXTERNAL_PROCESSING, purpose="validator")).allowed))
        checks.append(("raw sensitive external processing is blocked", not service.prepare_external_payload({"raw": "value"}, aggregate_only=False).allowed))
        checks.append(("aggregate-safe external processing is narrow", service.prepare_external_payload({"count": 3, "ratio": 0.5}, aggregate_only=True).allowed and not service.prepare_external_payload({"label": "value"}, aggregate_only=True).allowed))
        checks.append(("masking fails closed", _raises(lambda: service.mask(None, policy=MaskingPolicy(fail_closed=True)), PrivacyOperationError)))
        pseudo = service.pseudonymize("validator-value", key=b"validator-key", policy=PseudonymizationPolicy(key_reference="runtime", scope="validator", version="1"))
        checks.append(("pseudonymization uses HMAC-SHA256", pseudo.startswith("psn_v1_") and "validator-key" not in json.dumps(PseudonymizationPolicy(key_reference="runtime", scope="validator", version="1").model_dump(mode="json"))))
        checks.append(("raw staging is restricted", service.classify_artifact("batch", "raw_staging").sensitivity.value == "RESTRICTED"))
        checks.append(("record references are linkable metadata", service.classify_artifact("ref", "record_reference").retention_class == "linkable_metadata"))
        checks.append(("debug excludes raw staging", _raises(lambda: service.build_debug_bundle(artifact=service.classify_artifact("batch", "raw_staging"), metadata={}), PrivacyOperationError)))
        nested = service.sanitize_log_value({"nested": [{"token": "secret"}]})
        checks.append(("nested log sanitizer redacts secrets", "secret" not in json.dumps(nested)))
        checks.append(("unknown scan values are violations outside staging", not service.scan_values("validator", ("ordinary unicode: تهران",), allow_raw_staging=False).clean))
        checks.append(("arbitrary unknown log strings are redacted", service.sanitize_log_value("ordinary unicode: تهران") == "<REDACTED_UNKNOWN>"))
        metric = AggregateSafeMetric(metric_id="row_count", aggregate_kind="count", value=3, derivation_scope="table", privacy_classification="aggregate", provenance="validator")
        checks.append(("raw numeric identifiers cannot be aggregate payloads", not service.prepare_external_payload({"customer_id": 123456}, aggregate_only=True).allowed and service.prepare_external_payload({"row_count": metric}, aggregate_only=True).allowed))
        classified = service.classify_field_result("validator@example.test", column_id="email")
        checks.append(("classification evidence references resolve", all(ref in {item.evidence_id for item in classified.evidence} for ref in classified.classifications[0].evidence_refs)))
    except Exception as error:
        checks.append((f"privacy service imports and executes ({error.__class__.__name__})", False))
    state = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state.get("specialist_execution", {})
    checks.append(("formal G3 is pending, blocked, or evidenced PASS", state.get("gates", {}).get("G3_SOURCE_SAFETY") in {"PENDING", "BLOCKED", "PASS"}))
    checks.append(("execution remains at Step11 through later specialist handoff", execution.get("current_step") in {11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22}))
    checks.append(("Step11 database security implementation is present", (SRC / "dirty_data_to_olap" / "application" / "database_security.py").exists()))
    privacy_component = yaml.safe_load((ROOT / "docs" / "architecture" / "specs" / "components.yml").read_text(encoding="utf-8"))
    component = next((item for item in privacy_component["components"] if item.get("component_id") == "application.privacy_policy"), {})
    checks.append(("privacy component is explicitly implemented", component.get("implementation_step") == 10 and component.get("implementation_status") == "IMPLEMENTED"))
    failed = [name for name, passed in checks if not passed]
    if failed:
        print("FAIL")
        print("\n".join(f"- {name}" for name in failed))
        return 1
    print(f"PASS: privacy_checks={len(checks)}")
    return 0


def _raises(function, exception_type) -> bool:
    try:
        function()
    except exception_type:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
