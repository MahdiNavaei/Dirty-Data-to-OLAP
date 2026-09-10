"""Project-owned contracts for optional, bounded semantic evidence."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .source import _SourceModel, stable_digest


class SemanticTask(str, Enum):
    COLUMN_BUSINESS_MEANING = "COLUMN_BUSINESS_MEANING"
    RELATIONSHIP_SEMANTIC_HYPOTHESIS = "RELATIONSHIP_SEMANTIC_HYPOTHESIS"
    TABLE_ROLE_HYPOTHESIS = "TABLE_ROLE_HYPOTHESIS"
    AMBIGUITY_EXPLANATION = "AMBIGUITY_EXPLANATION"


class SemanticHypothesisKind(str, Enum):
    SUPPORTS_HYPOTHESIS = "SUPPORTS_HYPOTHESIS"
    CONTRADICTS_HYPOTHESIS = "CONTRADICTS_HYPOTHESIS"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


class SemanticSupportState(str, Enum):
    CANDIDATE_ONLY = "CANDIDATE_ONLY"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"


class SemanticCapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class SemanticFailureKind(str, Enum):
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    NON_LOCAL_PROVIDER_BLOCKED = "NON_LOCAL_PROVIDER_BLOCKED"
    CONTEXT_BUDGET_EXCEEDED = "CONTEXT_BUDGET_EXCEEDED"
    PROMPT_BUILD_FAILED = "PROMPT_BUILD_FAILED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_JSON = "INVALID_JSON"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    FORBIDDEN_OUTPUT = "FORBIDDEN_OUTPUT"
    MODEL_IDENTITY_CHANGED = "MODEL_IDENTITY_CHANGED"
    OUTPUT_BUDGET_EXCEEDED = "OUTPUT_BUDGET_EXCEEDED"


class SemanticBudget(_SourceModel):
    max_subjects: int = Field(default=1, ge=1, le=8)
    max_context_items: int = Field(default=16, ge=1, le=64)
    max_input_chars: int = Field(default=8000, ge=256, le=24000)
    max_evidence_refs: int = Field(default=32, ge=1, le=128)
    max_hypotheses: int = Field(default=6, ge=1, le=16)
    max_output_chars: int = Field(default=2400, ge=256, le=12000)
    max_provider_calls: int = Field(default=1, ge=1, le=3)
    timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def retry_policy_is_truthful(self) -> "SemanticBudget":
        if self.max_retries != 0:
            raise ValueError("semantic provider retries are disabled; max_retries must be zero")
        return self


class SemanticPrivacyContext(_SourceModel):
    purpose: str = "SEMANTIC_AI_LOCAL_ANALYSIS"
    local_only: bool = True
    network_allowed: bool = False
    external_processing_allowed: bool = False
    project_temp_root: str = "workspace/test-temp/semantic-ai"

    @model_validator(mode="after")
    def fixed_local_policy(self) -> "SemanticPrivacyContext":
        if self.purpose != "SEMANTIC_AI_LOCAL_ANALYSIS" or not self.local_only or self.network_allowed or self.external_processing_allowed:
            raise ValueError("semantic AI requires the fixed local-only privacy policy")
        return self


class SemanticEvidenceRequest(_SourceModel):
    request_id: str = Field(min_length=1)
    task: SemanticTask
    subject_refs: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    budget: SemanticBudget = Field(default_factory=SemanticBudget)
    privacy: SemanticPrivacyContext = Field(default_factory=SemanticPrivacyContext)
    prompt_version: str = "semantic-ai-v1"
    schema_version: str = "semantic-contract-v1"
    requested_authorization_id: str | None = None

    @model_validator(mode="after")
    def bounded_request(self) -> "SemanticEvidenceRequest":
        if len(self.subject_refs) > self.budget.max_subjects or len(self.evidence_refs) > self.budget.max_evidence_refs:
            raise ValueError("semantic request exceeds subject or evidence reference budget")
        return self


class SemanticContextItem(_SourceModel):
    item_ref: str = Field(min_length=1)
    item_kind: str = Field(min_length=1)
    safe_fields: Mapping[str, Any] = Field(default_factory=dict)
    redaction: str = "aggregate_safe"

    @model_validator(mode="after")
    def no_raw_values(self) -> "SemanticContextItem":
        forbidden = re.compile(r"(?i)(raw|value|record|row|password|secret|token|credential|email|phone|address)")
        if any(forbidden.search(str(key)) for key in self.safe_fields):
            raise ValueError("semantic context contains a raw or sensitive field")
        return self


class SemanticContextManifest(_SourceModel):
    manifest_id: str
    subject_refs: tuple[str, ...]
    requested_evidence_refs: tuple[str, ...]
    provided_context_item_refs: tuple[str, ...]
    allowed_provider_evidence_refs: tuple[str, ...]
    items: tuple[SemanticContextItem, ...]
    scope: str = "one_subject_or_relationship"
    redaction_policy: str = "aggregate_safe_no_raw_values"
    input_char_count: int = Field(ge=0)
    context_builder_version: str
    input_fingerprint: str


class SemanticProviderPolicy(_SourceModel):
    policy_id: str = "semantic-ollama-loopback-v1"
    endpoint: str = "http://127.0.0.1:11434"
    model: str = Field(min_length=1)
    allow_redirects: bool = False
    trust_env: bool = False
    temperature: float = 0.0
    seed: int = 20260910
    num_predict: int = Field(default=512, ge=32, le=2048)
    local_only: bool = True


class SemanticProviderReference(_SourceModel):
    provider: str = "ollama"
    api_version: str
    endpoint: str
    model: str
    model_digest: str
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    capabilities: tuple[str, ...] = ()
    local_loopback_verified: bool = True
    locality_evidence: str = "loopback_plus_exact_installed_model_digest"


class SemanticPromptReference(_SourceModel):
    prompt_version: str
    system_prompt_hash: str
    task_template_hash: str
    schema_version: str
    context_builder_version: str
    prompt_hash: str


class SemanticAuthorization(_SourceModel):
    authorization_id: str
    policy_id: str
    policy_version: str
    request_id: str
    task: SemanticTask
    context_manifest_fingerprint: str
    subject_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    provider_mode: str = "LOCAL_LOOPBACK"
    model: str
    model_digest: str
    prompt_version: str


class SemanticProviderHypothesis(_SourceModel):
    hypothesis_id: str = Field(min_length=1)
    kind: SemanticHypothesisKind
    statement: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = ()
    rationale: str = Field(default="", max_length=600)

    @field_validator("statement", "rationale")
    @classmethod
    def safe_provider_text(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip().lower()
        patterns = (r"\bdrop\s+table\b", r"\bexecute\s+sql\b", r"\brun\s+tool\b", r"\bsend\s+(?:a\s+)?request\b", r"\bmodify\s+data\b", r"\baccepted\b", r"\bcanonical\s+merge\b", r"\bauto[-_ ]?approved\b", r"\bmerged\b")
        if any(re.search(pattern, normalized) for pattern in patterns):
            raise ValueError("provider text contains executable or authority language")
        return value

    @model_validator(mode="after")
    def candidate_language(self) -> "SemanticProviderHypothesis":
        return self


class SemanticProviderOutput(_SourceModel):
    hypotheses: tuple[SemanticProviderHypothesis, ...] = Field(max_length=16)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=8)

    @field_validator("limitations")
    @classmethod
    def safe_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            SemanticProviderHypothesis.safe_provider_text(item)
        return value


class SemanticHypothesis(_SourceModel):
    hypothesis_id: str
    kind: SemanticHypothesisKind
    statement: str
    evidence_refs: tuple[str, ...] = ()
    rationale: str = ""
    candidate_only: bool = True

    @model_validator(mode="after")
    def candidate_only_output(self) -> "SemanticHypothesis":
        if not self.candidate_only:
            raise ValueError("semantic output cannot claim authority")
        return self


class SemanticArtifactReference(_SourceModel):
    artifact_id: str
    artifact_type: str
    location: str
    content_hash: str
    raw_values_persisted: bool = False


class LLMEvidence(_SourceModel):
    evidence_id: str
    request_id: str
    task: SemanticTask
    subject_refs: tuple[str, ...]
    hypotheses: tuple[SemanticHypothesis, ...]
    provider: SemanticProviderReference
    prompt: SemanticPromptReference
    context_manifest: SemanticContextManifest
    authorization: SemanticAuthorization
    response_hash: str
    state: SemanticSupportState = SemanticSupportState.CANDIDATE_ONLY
    limitations: tuple[str, ...] = ()


class SemanticFailure(_SourceModel):
    failure_id: str
    kind: SemanticFailureKind
    detail: str
    retryable: bool = False
    provider_request_made: bool = False


class SemanticCapability(_SourceModel):
    capability_id: str
    status: SemanticCapabilityStatus
    provider: str
    model: str
    detail: str


class SemanticRepeatabilityObservation(_SourceModel):
    observation_id: str
    request_id: str
    repetitions: int = Field(ge=1, le=3)
    schema_valid_rate: float = Field(ge=0, le=1)
    exact_structured_response_rate: float = Field(ge=0, le=1)
    hypothesis_kind_agreement: float = Field(ge=0, le=1)
    reference_agreement: float = Field(ge=0, le=1)
    interpretation: str = "repeatability_observation_not_determinism_claim"


class SemanticSafetyEvaluation(_SourceModel):
    evaluation_id: str
    schema_valid_rate: float = Field(ge=0, le=1)
    reference_valid_rate: float = Field(ge=0, le=1)
    forbidden_action_count: int = Field(ge=0)
    hallucinated_reference_count: int = Field(ge=0)
    privacy_canary_count: int = Field(ge=0)
    prompt_injection_escape_count: int = Field(ge=0)
    provider_failure_containment_count: int = Field(ge=0)
    repeatability: SemanticRepeatabilityObservation | None = None


class SemanticEvidenceResult(_SourceModel):
    request_id: str
    state: SemanticSupportState
    context_manifest: SemanticContextManifest | None = None
    evidence: LLMEvidence | None = None
    failure: SemanticFailure | None = None
    capability: SemanticCapability
    artifact: SemanticArtifactReference | None = None

    @model_validator(mode="after")
    def result_consistency(self) -> "SemanticEvidenceResult":
        if self.state is SemanticSupportState.CANDIDATE_ONLY and self.evidence is None:
            raise ValueError("candidate-only semantic result requires evidence")
        if self.state is not SemanticSupportState.CANDIDATE_ONLY and self.evidence is not None:
            raise ValueError("failed or skipped semantic result cannot contain evidence")
        return self


def semantic_evidence_id(*, request_id: str, task: SemanticTask, subjects: tuple[str, ...], hypotheses: tuple[SemanticHypothesis, ...], provider: SemanticProviderReference, prompt: SemanticPromptReference, input_fingerprint: str) -> str:
    return "semantic_" + stable_digest({"request": request_id, "task": task.value, "subjects": subjects, "hypotheses": [item.model_dump(mode="json") for item in hypotheses], "provider": provider.model_dump(mode="json"), "prompt": prompt.model_dump(mode="json"), "input": input_fingerprint})[:32]


def semantic_hypothesis_id(*, task: SemanticTask, subjects: tuple[str, ...], kind: SemanticHypothesisKind, statement: str, evidence_refs: tuple[str, ...], prompt_hash: str, input_fingerprint: str, model_digest: str) -> str:
    return "hypothesis_" + stable_digest({"task": task.value, "subjects": subjects, "kind": kind.value, "statement": re.sub(r"\s+", " ", statement).strip().lower(), "evidence_refs": tuple(sorted(evidence_refs)), "prompt": prompt_hash, "input": input_fingerprint, "model": model_digest})[:32]
