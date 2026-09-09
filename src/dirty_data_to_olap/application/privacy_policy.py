"""Privacy policy service: classify, minimize and guard exposure."""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.domain.contracts.privacy import (
    ArtifactSensitivity,
    ClassificationState,
    ExternalProcessingDecision,
    ExposureContext,
    ExposureRequest,
    MaskingPolicy,
    PrivacyAction,
    PrivacyClassification,
    PrivacyDecision,
    PrivacyEvidence,
    PrivacyEvidenceType,
    PrivacyFailure,
    PrivacyFailureKind,
    PrivacyManifest,
    PrivacyPolicy,
    PrivacyRule,
    PrivacyScanResult,
    PseudonymizationPolicy,
    RetentionPolicy,
    SensitiveDataCategory,
    SensitivityLevel,
)


class PrivacyOperationError(ValueError):
    def __init__(self, failure: PrivacyFailure) -> None:
        super().__init__(failure.detail)
        self.failure = failure


class PrivacyPolicyService:
    """A deterministic cross-cutting guard; it is not an authorization system."""

    def __init__(self, policy: PrivacyPolicy | None = None) -> None:
        self.policy = policy or PrivacyPolicy(policy_id="privacy-v1", version="1.0")

    def classify_field(self, value: Any, *, source_id: str | None = None, table_id: str | None = None, column_id: str | None = None, rules: Sequence[PrivacyRule] = ()) -> PrivacyClassification:
        subject = ":".join(item for item in (source_id, table_id, column_id) if item) or "unbound-field"
        applicable = tuple(rules) + self.policy.rules
        explicit = next((rule for rule in applicable if self._rule_matches(rule, source_id, table_id, column_id)), None)
        if explicit is not None:
            evidence = PrivacyEvidence(evidence_id=f"privacy-evidence-{explicit.rule_id}", evidence_type=PrivacyEvidenceType.EXPLICIT_PRIVACY_RULE, subject_ref=subject, detail=explicit.reason, provenance=self.policy.policy_id)
            return self._classification(subject, source_id, table_id, column_id, explicit.category, explicit.state, explicit.sensitivity, evidence)
        category = self._pattern_category(value)
        if category is not None:
            evidence = PrivacyEvidence(evidence_id=f"privacy-pattern-{hashlib.sha256(subject.encode()).hexdigest()[:16]}", evidence_type=PrivacyEvidenceType.DETERMINISTIC_PATTERN, subject_ref=subject, detail="deterministic pattern match; semantic identity is not asserted", detector_version="privacy-patterns-v1", provenance=self.policy.policy_id)
            return self._classification(subject, source_id, table_id, column_id, category, ClassificationState.POTENTIALLY_SENSITIVE, SensitivityLevel.SENSITIVE, evidence)
        return self._classification(subject, source_id, table_id, column_id, None, ClassificationState.UNKNOWN, self.policy.unknown_sensitivity, None)

    def classify_artifact(self, artifact_id: str, artifact_type: str, *, evidence_refs: Sequence[str] = ()) -> ArtifactSensitivity:
        normalized = artifact_type.lower()
        if normalized in {"raw_staging", "raw", "source_snapshot"}:
            return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.RESTRICTED, classification_state=ClassificationState.POTENTIALLY_SENSITIVE, raw_value_allowed=self.policy.raw_staging_allowed, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="restricted_staging", evidence_refs=tuple(evidence_refs))
        if normalized in {"record_reference", "source_record_reference"}:
            return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.SENSITIVE, classification_state=ClassificationState.POTENTIALLY_SENSITIVE, raw_value_allowed=False, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="linkable_metadata", evidence_refs=tuple(evidence_refs))
        if normalized in {"profile", "quality", "profile_artifact", "quality_artifact"}:
            return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.SENSITIVE, classification_state=ClassificationState.POTENTIALLY_SENSITIVE, raw_value_allowed=False, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="derived_sensitive", evidence_refs=tuple(evidence_refs))
        return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.INTERNAL, classification_state=ClassificationState.UNKNOWN, raw_value_allowed=False, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="controlled_metadata", evidence_refs=tuple(evidence_refs))

    def decide_exposure(self, classification: PrivacyClassification, request: ExposureRequest) -> PrivacyDecision:
        if request.context is ExposureContext.RAW_STAGING and self.policy.raw_staging_allowed and request.requested_mode == "raw":
            return PrivacyDecision(allowed=True, action=PrivacyAction.RETAIN_RESTRICTED, reason="raw is allowed only in restricted source-faithful staging", classification_id=classification.classification_id)
        if request.context is ExposureContext.EXTERNAL_PROCESSING:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="external processing requires the dedicated aggregate-safe boundary", classification_id=classification.classification_id, failure_ref="privacy-external-raw-blocked")
        if request.context in {ExposureContext.LOG, ExposureContext.DEBUG, ExposureContext.EXPORT, ExposureContext.UI_PREVIEW}:
            return PrivacyDecision(allowed=True, action=PrivacyAction.ALLOW_MASKED, reason="raw values are excluded; a masked representation is required", classification_id=classification.classification_id, required_transformation="mask")
        if classification.state is ClassificationState.UNKNOWN:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="absence of a detector match is unknown, not public", classification_id=classification.classification_id, failure_ref="privacy-unknown-blocked")
        if classification.state in {ClassificationState.CONFIRMED_SENSITIVE, ClassificationState.POTENTIALLY_SENSITIVE}:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="sensitive values require minimization before exposure", classification_id=classification.classification_id, failure_ref="privacy-sensitive-blocked")
        return PrivacyDecision(allowed=True, action=PrivacyAction.ALLOW_AGGREGATE, reason="explicitly non-sensitive under the active policy", classification_id=classification.classification_id)

    def mask(self, value: Any, *, category: SensitiveDataCategory | None = None, policy: MaskingPolicy | None = None) -> str:
        masking = policy or MaskingPolicy()
        try:
            if not isinstance(value, str):
                raise TypeError("masking requires a string value")
            if category is SensitiveDataCategory.AUTHENTICATION_SECRET:
                return "<REDACTED_SECRET>"
            if category is SensitiveDataCategory.EMAIL and "@" in value:
                return "<REDACTED_EMAIL>"
            if category is SensitiveDataCategory.PHONE:
                return "<REDACTED_PHONE>"
            return masking.replacement
        except Exception as error:
            if masking.fail_closed:
                raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-mask-failed", kind=PrivacyFailureKind.MASKING_FAILED, detail=f"masking failed closed: {error.__class__.__name__}")) from None
            raise

    def pseudonymize(self, value: Any, *, key: bytes | None, policy: PseudonymizationPolicy) -> str:
        if not isinstance(value, str) or not key:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-pseudonym-key", kind=PrivacyFailureKind.PSEUDONYMIZATION_KEY_UNAVAILABLE, detail="HMAC key is unavailable; plain hashing or raw fallback is forbidden"))
        message = f"{policy.scope}:{policy.version}:{value}".encode("utf-8")
        digest = hmac.new(key, message, hashlib.sha256).hexdigest()
        return f"{policy.output_prefix}{digest}"

    def sanitize_log_value(self, value: Any, *, category: SensitiveDataCategory | None = None) -> Any:
        if isinstance(value, Mapping):
            return {str(key): ("<REDACTED_SECRET>" if self._secret_key(str(key)) else self.sanitize_log_value(item, category=category)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize_log_value(item, category=category) for item in value]
        if isinstance(value, str):
            sanitized = re.sub(r"[^@\s]+@[^@\s]+\.[^@\s]+", "<REDACTED_EMAIL>", value)
            sanitized = re.sub(r"\+?[0-9][0-9()\-\s]{6,}", "<REDACTED_PHONE>", sanitized)
            return re.sub(r"(?i)(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", "<REDACTED_SECRET>", sanitized)
        return value

    def build_debug_bundle(self, *, artifact: ArtifactSensitivity, metadata: Mapping[str, Any]) -> Mapping[str, Any]:
        if artifact.artifact_type.lower() in {"raw_staging", "raw", "source_snapshot"}:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-debug-raw", kind=PrivacyFailureKind.EXPOSURE_BLOCKED, detail="raw staging is excluded from debug bundles", context=ExposureContext.DEBUG))
        return {"artifact_id": artifact.artifact_id, "artifact_type": artifact.artifact_type, "sensitivity": artifact.sensitivity.value, "metadata": self.sanitize_log_value(metadata)}

    def prepare_external_payload(self, payload: Mapping[str, Any], *, aggregate_only: bool = False) -> ExternalProcessingDecision:
        if not aggregate_only:
            return ExternalProcessingDecision(allowed=False, action=PrivacyAction.BLOCK, reason="raw, sensitive and unknown external payloads are blocked by default", aggregate_only=False, blocked_fields=tuple(str(key) for key in payload), policy_id=self.policy.policy_id)
        blocked = tuple(str(key) for key, value in payload.items() if not isinstance(value, (int, float)) or isinstance(value, bool))
        if blocked:
            return ExternalProcessingDecision(allowed=False, action=PrivacyAction.BLOCK, reason="aggregate-safe payload contains a non-aggregate value", aggregate_only=True, blocked_fields=blocked, policy_id=self.policy.policy_id)
        return ExternalProcessingDecision(allowed=True, action=PrivacyAction.ALLOW_AGGREGATE, reason="only aggregate scalar values were supplied", aggregate_only=True, policy_id=self.policy.policy_id)

    def cleanup_ephemeral(self, path: Path, *, allowed_root: Path, retention: RetentionPolicy | None = None) -> PrivacyDecision:
        root = allowed_root.resolve()
        target = path.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-retention-path", kind=PrivacyFailureKind.UNSAFE_RETENTION_PATH, detail="retention cleanup path escapes the privacy-owned root")) from None
        if not target.exists():
            return PrivacyDecision(allowed=True, action=PrivacyAction.DELETE_EPHEMERAL, reason="ephemeral path was already absent")
        if target == root:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-retention-root", kind=PrivacyFailureKind.UNSAFE_RETENTION_PATH, detail="privacy cleanup cannot delete its root"))
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return PrivacyDecision(allowed=True, action=PrivacyAction.DELETE_EPHEMERAL, reason="privacy-owned ephemeral artifact deleted")

    def privacy_manifest(self, manifest_id: str, artifacts: Sequence[ArtifactSensitivity]) -> PrivacyManifest:
        return PrivacyManifest(manifest_id=manifest_id, policy_id=self.policy.policy_id, artifacts=tuple(artifacts), created_at=datetime.now(timezone.utc))

    def scan_values(self, scan_id: str, values: Sequence[Any], *, allow_raw_staging: bool = False) -> PrivacyScanResult:
        sensitive = unknown = 0
        violations: list[str] = []
        for value in values:
            classification = self.classify_field(value)
            if classification.state in {ClassificationState.CONFIRMED_SENSITIVE, ClassificationState.POTENTIALLY_SENSITIVE}:
                sensitive += 1
                if not allow_raw_staging:
                    violations.append("sensitive value exposed outside restricted staging")
            elif classification.state is ClassificationState.UNKNOWN:
                unknown += 1
        return PrivacyScanResult(scan_id=scan_id, policy_id=self.policy.policy_id, scanned_artifacts=1, scanned_values=len(values), raw_sensitive_matches=sensitive, raw_unknown_matches=unknown, restricted_staging_exceptions=int(allow_raw_staging), violations=tuple(violations), clean=not violations, created_at=datetime.now(timezone.utc))

    @staticmethod
    def _rule_matches(rule: PrivacyRule, source_id: str | None, table_id: str | None, column_id: str | None) -> bool:
        return all(expected is None or expected == actual for expected, actual in ((rule.source_id, source_id), (rule.table_id, table_id), (rule.column_id, column_id)))

    @staticmethod
    def _pattern_category(value: Any) -> SensitiveDataCategory | None:
        if not isinstance(value, str):
            return None
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            return SensitiveDataCategory.EMAIL
        if re.fullmatch(r"\+?[0-9][0-9()\-\s]{6,}", value):
            return SensitiveDataCategory.PHONE
        return None

    @staticmethod
    def _classification(subject: str, source_id: str | None, table_id: str | None, column_id: str | None, category: SensitiveDataCategory | None, state: ClassificationState, sensitivity: SensitivityLevel, evidence: PrivacyEvidence | None) -> PrivacyClassification:
        return PrivacyClassification(classification_id=f"classification-{hashlib.sha256(subject.encode()).hexdigest()[:24]}", source_id=source_id, table_id=table_id, column_id=column_id, category=category, state=state, sensitivity=sensitivity, evidence_refs=(evidence.evidence_id,) if evidence else (), raw_value_allowed=False, log_allowed=False, external_processing_allowed=False, pseudonymization_required=category is not None, retention_class="restricted_ephemeral" if category is not None else "unknown")

    @staticmethod
    def _secret_key(key: str) -> bool:
        return bool(re.search(r"(?i)(password|passwd|secret|token|api[_-]?key|raw[_-]?value|credential)", key))
