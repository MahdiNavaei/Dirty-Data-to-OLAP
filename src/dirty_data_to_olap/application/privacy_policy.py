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
    AggregateSafeMetric,
    ClassificationState,
    ExternalProcessingDecision,
    ExposureContext,
    ExposureRequest,
    MaskingPolicy,
    PrivacyAction,
    PrivacyClassification,
    PrivacyClassificationResult,
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
from dirty_data_to_olap.domain.contracts.profiling import PatternType, ProfileResult


class PrivacyOperationError(ValueError):
    def __init__(self, failure: PrivacyFailure) -> None:
        super().__init__(failure.detail)
        self.failure = failure


class PrivacyPolicyService:
    """A deterministic cross-cutting policy and authorization boundary."""

    def __init__(self, policy: PrivacyPolicy | None = None, *, config_path: Path | None = None, project_root: Path | None = None) -> None:
        self.policy = policy or self.load_policy(config_path)
        self.project_root = project_root.resolve() if project_root else None
        self._dependency_authorizations: dict[str, object] = {}
        self._matching_authorizations: dict[str, object] = {}
        self._entity_resolution_authorizations: dict[str, object] = {}
        self._semantic_authorizations: dict[str, object] = {}

    def authorize_semantic_ai_analysis(self, context, *, request, context_manifest, provider_ref, prompt_ref) -> PrivacyDecision:
        """Authorize one exact, aggregate-only, loopback semantic request."""
        from dirty_data_to_olap.domain.contracts.semantic_ai import SemanticPrivacyContext, SemanticAuthorization
        try:
            validated = SemanticPrivacyContext.model_validate(context)
        except Exception:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="semantic AI context failed the fixed local-only contract", classification_id="semantic-ai-local-analysis", failure_ref="privacy-semantic-context-invalid")
        if validated.purpose != "SEMANTIC_AI_LOCAL_ANALYSIS" or not provider_ref.local_loopback_verified or provider_ref.endpoint not in {"http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"}:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="semantic AI provider is not verified loopback local", classification_id="semantic-ai-local-analysis", failure_ref="privacy-semantic-provider-not-local")
        if not context_manifest.subject_refs or any(item not in context_manifest.evidence_refs for item in request.evidence_refs if item):
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="semantic AI request and context manifest are not bound", classification_id="semantic-ai-local-analysis", failure_ref="privacy-semantic-scope-unbound")
        authorization = SemanticAuthorization(
            authorization_id="semantic-auth-" + hashlib.sha256(f"{self.policy.policy_id}:{self.policy.version}:{request.request_id}:{request.task.value}:{context_manifest.input_fingerprint}:{provider_ref.model}:{provider_ref.model_digest}:{prompt_ref.prompt_version}".encode()).hexdigest()[:32],
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            request_id=request.request_id,
            task=request.task,
            context_manifest_fingerprint=context_manifest.input_fingerprint,
            subject_refs=request.subject_refs,
            evidence_refs=request.evidence_refs,
            model=provider_ref.model,
            model_digest=provider_ref.model_digest,
            prompt_version=prompt_ref.prompt_version,
        )
        self._semantic_authorizations[authorization.authorization_id] = authorization
        return PrivacyDecision(allowed=True, action=PrivacyAction.RETAIN_RESTRICTED, reason="semantic AI is authorized for aggregate-only loopback analysis", classification_id="semantic-ai-local-analysis", required_transformation="aggregate_project_owned_evidence", authorization_id=authorization.authorization_id)

    def verify_semantic_ai_authorization(self, authorization, *, request, context_manifest, provider_ref, prompt_ref) -> bool:
        from dirty_data_to_olap.domain.contracts.semantic_ai import SemanticAuthorization
        return isinstance(authorization, SemanticAuthorization) and self._semantic_authorizations.get(authorization.authorization_id) == authorization and authorization.request_id == request.request_id and authorization.task == request.task and authorization.context_manifest_fingerprint == context_manifest.input_fingerprint and authorization.model == provider_ref.model and authorization.model_digest == provider_ref.model_digest and authorization.prompt_version == prompt_ref.prompt_version

    def semantic_authorization_for_decision(self, decision: PrivacyDecision):
        return self._semantic_authorizations.get(decision.authorization_id)

    @staticmethod
    def load_policy(config_path: Path | None = None) -> PrivacyPolicy:
        path = config_path or Path(__file__).resolve().parents[3] / "config" / "privacy-defaults.yml"
        try:
            import yaml
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, Mapping):
                raise ValueError("privacy policy must be a mapping")
            if raw.get("external_allow_raw_sensitive") or raw.get("external_allow_unknown") or raw.get("logs_allow_raw"):
                raise ValueError("unsafe privacy exposure defaults are rejected")
            allowed = {field for field in PrivacyPolicy.model_fields}
            return PrivacyPolicy.model_validate({key: value for key, value in raw.items() if key in allowed})
        except Exception as error:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-policy-config", kind=PrivacyFailureKind.CLASSIFICATION_UNAVAILABLE, detail=f"privacy policy could not be loaded: {error.__class__.__name__}")) from None

    def classify_field(self, value: Any, *, source_id: str | None = None, table_id: str | None = None, column_id: str | None = None, rules: Sequence[PrivacyRule] = ()) -> PrivacyClassification:
        return self.classify_field_result(value, source_id=source_id, table_id=table_id, column_id=column_id, rules=rules).classifications[0]

    def classify_field_result(self, value: Any, *, source_id: str | None = None, table_id: str | None = None, column_id: str | None = None, rules: Sequence[PrivacyRule] = ()) -> PrivacyClassificationResult:
        subject = ":".join(item for item in (source_id, table_id, column_id) if item) or "unbound-field"
        applicable = tuple(rules) + self.policy.rules
        explicit = next((rule for rule in applicable if self._rule_matches(rule, source_id, table_id, column_id)), None)
        if explicit is not None:
            evidence = PrivacyEvidence(evidence_id=f"privacy-evidence-{explicit.rule_id}", evidence_type=PrivacyEvidenceType.EXPLICIT_PRIVACY_RULE, subject_ref=subject, detail=explicit.reason, provenance=self.policy.policy_id)
            return PrivacyClassificationResult(classifications=(self._classification(subject, source_id, table_id, column_id, explicit.category, explicit.state, explicit.sensitivity, evidence),), evidence=(evidence,))
        category = self._pattern_category(value)
        if category is not None:
            evidence = PrivacyEvidence(evidence_id=f"privacy-pattern-{hashlib.sha256(subject.encode()).hexdigest()[:16]}", evidence_type=PrivacyEvidenceType.DETERMINISTIC_PATTERN, subject_ref=subject, detail="deterministic pattern match; semantic identity is not asserted", detector_version="privacy-patterns-v1", provenance=self.policy.policy_id)
            return PrivacyClassificationResult(classifications=(self._classification(subject, source_id, table_id, column_id, category, ClassificationState.POTENTIALLY_SENSITIVE, SensitivityLevel.SENSITIVE, evidence),), evidence=(evidence,))
        return PrivacyClassificationResult(classifications=(self._classification(subject, source_id, table_id, column_id, None, ClassificationState.UNKNOWN, self.policy.unknown_sensitivity, None),))

    def classify_profile(self, profile: ProfileResult) -> PrivacyClassificationResult:
        """Consume Step08 pattern summaries directly; no staged-row reread occurs."""
        classifications: list[PrivacyClassification] = []
        evidence: list[PrivacyEvidence] = []
        for pattern in profile.patterns:
            category = SensitiveDataCategory.EMAIL if pattern.pattern_type is PatternType.EMAIL_LIKE else SensitiveDataCategory.PHONE if pattern.pattern_type is PatternType.PHONE_LIKE else SensitiveDataCategory.FREE_TEXT_POTENTIALLY_SENSITIVE
            evidence_item = PrivacyEvidence(evidence_id=f"privacy-profile-{pattern.pattern_id}", evidence_type=PrivacyEvidenceType.DETERMINISTIC_PATTERN, subject_ref=pattern.column_id, detail=f"Step08 profile observed {pattern.pattern_type.value}", detector_version=pattern.method_version, provenance=pattern.provenance.profiling_adapter.name)
            classifications.append(self._classification(pattern.column_id, pattern.source_id, pattern.table_id, pattern.column_id, category, ClassificationState.POTENTIALLY_SENSITIVE, SensitivityLevel.SENSITIVE, evidence_item))
            evidence.append(evidence_item)
        return PrivacyClassificationResult(classifications=tuple(classifications), evidence=tuple(evidence))

    def classify_artifact(self, artifact_id: str, artifact_type: str, *, evidence_refs: Sequence[str] = ()) -> ArtifactSensitivity:
        normalized = artifact_type.lower()
        if normalized in {"raw_staging", "raw", "source_snapshot"}:
            return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.RESTRICTED, classification_state=ClassificationState.POTENTIALLY_SENSITIVE, raw_value_allowed=self.policy.raw_staging_allowed, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="restricted_staging", evidence_refs=tuple(evidence_refs))
        if normalized in {"record_reference", "source_record_reference"}:
            return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.SENSITIVE, classification_state=ClassificationState.POTENTIALLY_SENSITIVE, raw_value_allowed=False, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="linkable_metadata", evidence_refs=tuple(evidence_refs))
        if normalized in {"profile", "quality", "profile_artifact", "quality_artifact", "dependency", "dependency_evidence", "dependency_artifact"}:
            return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.SENSITIVE, classification_state=ClassificationState.POTENTIALLY_SENSITIVE, raw_value_allowed=False, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="derived_sensitive", evidence_refs=tuple(evidence_refs))
        return ArtifactSensitivity(artifact_id=artifact_id, artifact_type=artifact_type, sensitivity=SensitivityLevel.INTERNAL, classification_state=ClassificationState.UNKNOWN, raw_value_allowed=False, log_allowed=False, debug_allowed=False, export_allowed=False, external_processing_allowed=False, retention_class="controlled_metadata", evidence_refs=tuple(evidence_refs))

    def authorize_dependency_analysis(self, context, *, source_id: str | None = None, snapshot_id: str | None = None, table_ids: Sequence[str] = (), artifact_ids: Sequence[str] = ()) -> PrivacyDecision:
        """Issue a decision bound to the exact dependency input scope.

        A context is only a request for policy evaluation.  It is never an
        authorization token accepted by the dependency adapter.
        """
        from dirty_data_to_olap.domain.contracts.dependency import DependencyAuthorization, DependencyPrivacyContext

        try:
            validated = DependencyPrivacyContext.model_validate(context)
        except Exception:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="dependency context failed the local-only privacy contract", classification_id="dependency-local-analysis", failure_ref="privacy-dependency-context-invalid")
        if not source_id or not snapshot_id or not tuple(table_ids):
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="dependency authorization requires source, snapshot and table bindings", classification_id="dependency-local-analysis", failure_ref="privacy-dependency-scope-unbound")
        if self.project_root is not None:
            temp_root = (self.project_root / validated.project_temp_root).resolve()
            try:
                temp_root.relative_to(self.project_root)
            except ValueError:
                return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="dependency ephemeral input root escapes the project", classification_id="dependency-local-analysis", failure_ref="privacy-dependency-temp-root-invalid")
        authorization = DependencyAuthorization(
            authorization_id=f"dependency-auth-{hashlib.sha256(f'{self.policy.policy_id}:{self.policy.version}:{source_id}:{snapshot_id}:{tuple(table_ids)}'.encode()).hexdigest()[:32]}",
            purpose=validated.purpose,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            source_id=source_id,
            snapshot_id=snapshot_id,
            table_ids=tuple(table_ids),
            artifact_ids=tuple(artifact_ids),
            local_only=validated.local_only,
            network_allowed=validated.network_allowed,
            external_processing_allowed=validated.external_processing_allowed,
            issued_at=datetime.now(timezone.utc),
        )
        self._dependency_authorizations[authorization.authorization_id] = authorization
        return PrivacyDecision(allowed=True, action=PrivacyAction.RETAIN_RESTRICTED, reason="dependency analysis is authorized for local ephemeral staging only", classification_id="dependency-local-analysis", required_transformation=authorization.required_transformation, authorization_id=authorization.authorization_id)

    def verify_dependency_authorization(self, authorization, *, source_id: str, snapshot_id: str, table_ids: Sequence[str], artifact_ids: Sequence[str] = ()) -> bool:
        from dirty_data_to_olap.domain.contracts.dependency import DependencyAuthorization

        if not isinstance(authorization, DependencyAuthorization):
            return False
        issued = self._dependency_authorizations.get(authorization.authorization_id)
        if issued is None or issued != authorization:
            return False
        return issued.source_id == source_id and issued.snapshot_id == snapshot_id and issued.table_ids == tuple(table_ids) and (not artifact_ids or issued.artifact_ids == tuple(artifact_ids)) and issued.policy_id == self.policy.policy_id and issued.policy_version == self.policy.version

    def authorization_for_decision(self, decision: PrivacyDecision):
        return self._dependency_authorizations.get(decision.authorization_id)

    def authorize_schema_matching_analysis(self, context, *, source_ids: Sequence[str], snapshot_ids: Mapping[str, str], table_ids_by_source: Mapping[str, Sequence[str]], column_ids_by_table: Mapping[str, Sequence[str]] = (), artifact_ids: Sequence[str] = ()) -> PrivacyDecision:
        """Issue exact-scope local authorization for instance-aware matching."""
        from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchPrivacyContext, MatchingAuthorization

        try:
            validated = SchemaMatchPrivacyContext.model_validate(context)
        except Exception:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="schema matching privacy context failed the local-only contract", classification_id="schema-matching-local-analysis", failure_ref="privacy-schema-matching-context-invalid")
        ids = tuple(source_ids)
        if len(ids) < 2 or set(snapshot_ids) != set(ids) or set(table_ids_by_source) != set(ids):
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="matching authorization requires exact multi-source snapshot and table bindings", classification_id="schema-matching-local-analysis", failure_ref="privacy-schema-matching-scope-unbound")
        if self.project_root is not None:
            temp_root = (self.project_root / validated.project_temp_root).resolve()
            try:
                temp_root.relative_to(self.project_root)
            except ValueError:
                return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="matching ephemeral input root escapes the project", classification_id="schema-matching-local-analysis", failure_ref="privacy-schema-matching-temp-root-invalid")
        normalized_tables = {key: tuple(value) for key, value in table_ids_by_source.items()}
        normalized_columns = {key: tuple(value) for key, value in dict(column_ids_by_table or {}).items()}
        auth = MatchingAuthorization(
            authorization_id=f"schema-matching-auth-{hashlib.sha256(f'{self.policy.policy_id}:{self.policy.version}:{ids}:{dict(snapshot_ids)}:{normalized_tables}:{normalized_columns}'.encode()).hexdigest()[:32]}",
            purpose=validated.purpose,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            source_ids=ids,
            snapshot_ids=dict(snapshot_ids),
            table_ids_by_source=normalized_tables,
            column_ids_by_table=normalized_columns,
            artifact_ids=tuple(artifact_ids),
            issued_at=datetime.now(timezone.utc),
        )
        self._matching_authorizations[auth.authorization_id] = auth
        return PrivacyDecision(allowed=True, action=PrivacyAction.RETAIN_RESTRICTED, reason="instance-aware matching is authorized for local ephemeral staged rows only", classification_id="schema-matching-local-analysis", required_transformation="aggregate_project_owned_evidence", authorization_id=auth.authorization_id)

    def verify_schema_matching_authorization(self, authorization, *, source_ids: Sequence[str], snapshot_ids: Mapping[str, str], table_ids_by_source: Mapping[str, Sequence[str]], column_ids_by_table: Mapping[str, Sequence[str]] = (), artifact_ids: Sequence[str] = ()) -> bool:
        from dirty_data_to_olap.domain.contracts.schema_matching import MatchingAuthorization

        if not isinstance(authorization, MatchingAuthorization):
            return False
        issued = self._matching_authorizations.get(authorization.authorization_id)
        if issued is None or issued != authorization:
            return False
        return issued.source_ids == tuple(source_ids) and dict(issued.snapshot_ids) == dict(snapshot_ids) and issued.table_ids_by_source == {key: tuple(value) for key, value in table_ids_by_source.items()} and issued.column_ids_by_table == {key: tuple(value) for key, value in dict(column_ids_by_table or {}).items()} and (not artifact_ids or issued.artifact_ids == tuple(artifact_ids)) and issued.policy_id == self.policy.policy_id and issued.policy_version == self.policy.version

    def matching_authorization_for_decision(self, decision: PrivacyDecision):
        return self._matching_authorizations.get(decision.authorization_id)

    def authorize_entity_resolution_analysis(self, context, *, spec, source_ids: Sequence[str], snapshot_ids: Mapping[str, str], table_ids_by_source: Mapping[str, Sequence[str]], identity_column_ids: Sequence[str], batch_ids: Sequence[str] = (), artifact_ids: Sequence[str] = ()) -> PrivacyDecision:
        """Issue exact-scope authorization for local staged identity analysis."""
        from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionAuthorization, EntityResolutionPrivacyContext

        try:
            validated = EntityResolutionPrivacyContext.model_validate(context)
        except Exception:
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="entity resolution context failed the local-only privacy contract", classification_id="entity-resolution-local-analysis", failure_ref="privacy-entity-resolution-context-invalid")
        ids = tuple(source_ids)
        if not ids or set(snapshot_ids) != set(ids) or set(table_ids_by_source) != set(ids) or not tuple(identity_column_ids):
            return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="entity resolution authorization requires exact source, snapshot, table and identity-column bindings", classification_id="entity-resolution-local-analysis", failure_ref="privacy-entity-resolution-scope-unbound")
        if self.project_root is not None:
            temp_root = (self.project_root / validated.project_temp_root).resolve()
            try:
                temp_root.relative_to(self.project_root)
            except ValueError:
                return PrivacyDecision(allowed=False, action=PrivacyAction.BLOCK, reason="entity resolution ephemeral root escapes the project", classification_id="entity-resolution-local-analysis", failure_ref="privacy-entity-resolution-temp-root-invalid")
        normalized_tables = {key: tuple(value) for key, value in table_ids_by_source.items()}
        normalized_columns = tuple(identity_column_ids)
        authorization = EntityResolutionAuthorization(
            authorization_id=f"entity-resolution-auth-{hashlib.sha256(f'{self.policy.policy_id}:{self.policy.version}:{spec.spec_id}:{spec.fingerprint}:{ids}:{dict(snapshot_ids)}:{normalized_tables}:{normalized_columns}:{tuple(batch_ids)}'.encode()).hexdigest()[:32]}",
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            entity_family=spec.entity_family,
            spec_id=spec.spec_id,
            spec_fingerprint=spec.fingerprint,
            source_ids=ids,
            snapshot_ids=dict(snapshot_ids),
            table_ids_by_source=normalized_tables,
            identity_column_ids=normalized_columns,
            batch_ids=tuple(batch_ids),
            artifact_ids=tuple(artifact_ids),
            issued_at=datetime.now(timezone.utc),
        )
        self._entity_resolution_authorizations[authorization.authorization_id] = authorization
        return PrivacyDecision(allowed=True, action=PrivacyAction.RETAIN_RESTRICTED, reason="entity resolution is authorized for exact local ephemeral staged scope only", classification_id="entity-resolution-local-analysis", required_transformation="aggregate_project_owned_evidence", authorization_id=authorization.authorization_id)

    def verify_entity_resolution_authorization(self, authorization, *, spec, source_ids: Sequence[str], snapshot_ids: Mapping[str, str], table_ids_by_source: Mapping[str, Sequence[str]], identity_column_ids: Sequence[str], batch_ids: Sequence[str] = (), artifact_ids: Sequence[str] = ()) -> bool:
        from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionAuthorization

        if not isinstance(authorization, EntityResolutionAuthorization):
            return False
        issued = self._entity_resolution_authorizations.get(authorization.authorization_id)
        if issued is None or issued != authorization:
            return False
        return issued.spec_id == spec.spec_id and issued.spec_fingerprint == spec.fingerprint and issued.entity_family == spec.entity_family and issued.source_ids == tuple(source_ids) and dict(issued.snapshot_ids) == dict(snapshot_ids) and issued.table_ids_by_source == {key: tuple(value) for key, value in table_ids_by_source.items()} and issued.identity_column_ids == tuple(identity_column_ids) and (not batch_ids or issued.batch_ids == tuple(batch_ids)) and (not artifact_ids or issued.artifact_ids == tuple(artifact_ids)) and issued.policy_id == self.policy.policy_id and issued.policy_version == self.policy.version

    def entity_resolution_authorization_for_decision(self, decision: PrivacyDecision):
        return self._entity_resolution_authorizations.get(decision.authorization_id)

    def decide_exposure(self, classification: PrivacyClassification, request: ExposureRequest) -> PrivacyDecision:
        if request.context is ExposureContext.ENTITY_RESOLUTION_LOCAL_ANALYSIS and request.purpose == "ENTITY_RESOLUTION_LOCAL_ANALYSIS":
            return PrivacyDecision(allowed=True, action=PrivacyAction.RETAIN_RESTRICTED, reason="entity resolution raw values are allowed only within exact local ephemeral analysis scope", classification_id=classification.classification_id, required_transformation="aggregate_project_owned_evidence")
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

    def sanitize_log_value(self, value: Any, *, category: SensitiveDataCategory | None = None, trusted_safe_metadata: bool = False) -> Any:
        if isinstance(value, Mapping):
            return {str(key): ("<REDACTED_SECRET>" if self._secret_key(str(key)) else self.sanitize_log_value(item, category=category, trusted_safe_metadata=trusted_safe_metadata)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize_log_value(item, category=category, trusted_safe_metadata=trusted_safe_metadata) for item in value]
        if isinstance(value, str):
            sanitized = re.sub(r"[^@\s]+@[^@\s]+\.[^@\s]+", "<REDACTED_EMAIL>", value)
            sanitized = re.sub(r"\+?[0-9][0-9()\-\s]{6,}", "<REDACTED_PHONE>", sanitized)
            sanitized = re.sub(r"(?i)(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", "<REDACTED_SECRET>", sanitized)
            return sanitized if trusted_safe_metadata and sanitized == value else (sanitized if sanitized != value else "<REDACTED_UNKNOWN>")
        return value

    def sanitize_safe_metadata(self, value: Any) -> Any:
        return self.sanitize_log_value(value, trusted_safe_metadata=True)

    def build_debug_bundle(self, *, artifact: ArtifactSensitivity, metadata: Mapping[str, Any]) -> Mapping[str, Any]:
        if artifact.artifact_type.lower() in {"raw_staging", "raw", "source_snapshot"}:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-debug-raw", kind=PrivacyFailureKind.EXPOSURE_BLOCKED, detail="raw staging is excluded from debug bundles", context=ExposureContext.DEBUG))
        return {"artifact_id": artifact.artifact_id, "artifact_type": artifact.artifact_type, "sensitivity": artifact.sensitivity.value, "metadata": self.sanitize_log_value(metadata)}

    def prepare_external_payload(self, payload: Mapping[str, Any], *, aggregate_only: bool = False) -> ExternalProcessingDecision:
        if not aggregate_only:
            return ExternalProcessingDecision(allowed=False, action=PrivacyAction.BLOCK, reason="raw, sensitive and unknown external payloads are blocked by default", aggregate_only=False, blocked_fields=tuple(str(key) for key in payload), policy_id=self.policy.policy_id)
        blocked = tuple(str(key) for key, value in payload.items() if not isinstance(value, AggregateSafeMetric) and not (isinstance(value, (int, float)) and not isinstance(value, bool) and re.fullmatch(r"(?:row|record|table|column|batch|file|input|output|count|ratio|rate|sum|mean|average|min|max|amount|total|distinct|missing|valid|invalid|duplicate|null|coverage|percent|percentage)[_ -]?[a-z0-9_ -]*", str(key).lower())))
        if blocked:
            return ExternalProcessingDecision(allowed=False, action=PrivacyAction.BLOCK, reason="aggregate-safe payload contains a non-aggregate value", aggregate_only=True, blocked_fields=blocked, policy_id=self.policy.policy_id)
        return ExternalProcessingDecision(allowed=True, action=PrivacyAction.ALLOW_AGGREGATE, reason="only aggregate scalar values were supplied", aggregate_only=True, policy_id=self.policy.policy_id)

    def cleanup_ephemeral(self, path: Path, *, allowed_root: Path, retention: RetentionPolicy | None = None) -> PrivacyDecision:
        root = allowed_root.resolve()
        target = path.resolve()
        retention = retention or RetentionPolicy()
        if self.project_root is not None:
            expected_root = (self.project_root / retention.cleanup_root_name).resolve()
            try:
                root.relative_to(expected_root)
            except ValueError:
                raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-retention-owner", kind=PrivacyFailureKind.UNSAFE_RETENTION_PATH, detail="cleanup root is not the project-authorized privacy directory")) from None
        elif root.name != retention.cleanup_root_name:
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-retention-owner", kind=PrivacyFailureKind.UNSAFE_RETENTION_PATH, detail="cleanup root is not privacy-owned"))
        if any(part.lower() in {"src", "source", "staging", "code", ".git"} for part in root.parts):
            raise PrivacyOperationError(PrivacyFailure(failure_id="privacy-retention-owner", kind=PrivacyFailureKind.UNSAFE_RETENTION_PATH, detail="cleanup root is a protected source or code path"))
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
                if not allow_raw_staging:
                    violations.append("unknown value exposed outside restricted staging")
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
