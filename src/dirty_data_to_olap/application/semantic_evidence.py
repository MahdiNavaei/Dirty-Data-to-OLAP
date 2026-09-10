"""Optional semantic evidence orchestration; never an acceptance authority."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from dirty_data_to_olap.adapters.semantic.context import SemanticContextBudgetError, SemanticContextBuilder
from dirty_data_to_olap.adapters.semantic.ollama import OllamaSemanticEvidenceAdapter, SemanticProviderError
from dirty_data_to_olap.adapters.semantic.prompts import prompt_reference
from dirty_data_to_olap.domain.contracts.semantic_ai import (
    SemanticArtifactReference,
    SemanticCapability,
    SemanticCapabilityStatus,
    SemanticContextItem,
    SemanticEvidenceRequest,
    SemanticEvidenceResult,
    SemanticFailure,
    SemanticFailureKind,
    SemanticProviderPolicy,
    SemanticSupportState,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService


class SemanticEvidenceService:
    def __init__(self, *, adapter: OllamaSemanticEvidenceAdapter, privacy_policy: PrivacyPolicyService, artifact_root: Path | None = None) -> None:
        self.adapter = adapter
        self.privacy_policy = privacy_policy
        self.artifact_root = artifact_root
        self.context_builder = SemanticContextBuilder()

    def analyze(self, request: SemanticEvidenceRequest, *, context_items: Iterable[SemanticContextItem]) -> SemanticEvidenceResult:
        try:
            manifest = self.context_builder.build(request, context_items)
            prompt = prompt_reference(request, context_builder_version=manifest.context_builder_version)
            provider = self.adapter.capability()
            decision = self.privacy_policy.authorize_semantic_ai_analysis(request.privacy, request=request, context_manifest=manifest, provider_ref=provider, prompt_ref=prompt)
            if not decision.allowed:
                return self._failure(request, manifest, SemanticFailureKind.PRIVACY_BLOCKED, decision.reason, provider_model=request.task.value)
            authorization = self.privacy_policy.semantic_authorization_for_decision(decision)
            if authorization is None or not self.privacy_policy.verify_semantic_ai_authorization(authorization, request=request, context_manifest=manifest, provider_ref=provider, prompt_ref=prompt):
                return self._failure(request, manifest, SemanticFailureKind.PRIVACY_BLOCKED, "semantic authorization could not be verified", provider_model=provider.model)
            evidence = self.adapter.generate(request, manifest, authorization, prompt, timeout_seconds=request.budget.timeout_seconds)
            result = SemanticEvidenceResult(request_id=request.request_id, state=SemanticSupportState.CANDIDATE_ONLY, context_manifest=manifest, evidence=evidence, capability=SemanticCapability(capability_id="semantic.ollama", status=SemanticCapabilityStatus.AVAILABLE, provider="ollama", model=provider.model, detail="verified local loopback provider"))
            return self._publish(result)
        except SemanticContextBudgetError as exc:
            return self._failure(request, None, SemanticFailureKind.CONTEXT_BUDGET_EXCEEDED, str(exc), provider_model=request.task.value)
        except SemanticProviderError as exc:
            state = SemanticSupportState.PRIVACY_BLOCKED if exc.kind in {SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED} else SemanticSupportState.UNAVAILABLE if exc.kind is SemanticFailureKind.CAPABILITY_UNAVAILABLE else SemanticSupportState.FAILED
            return self._failure(request, None, exc.kind, str(exc), provider_model=request.task.value, request_made=exc.request_made, state=state)
        except (FileNotFoundError, ValueError, OSError) as exc:
            return self._failure(request, None, SemanticFailureKind.PROMPT_BUILD_FAILED, str(exc), provider_model=request.task.value)

    def _failure(self, request, manifest, kind, detail, *, provider_model: str, request_made: bool = False, state: SemanticSupportState | None = None):
        status = state or (SemanticSupportState.PRIVACY_BLOCKED if kind is SemanticFailureKind.PRIVACY_BLOCKED else SemanticSupportState.FAILED)
        result = SemanticEvidenceResult(request_id=request.request_id, state=status, context_manifest=manifest, failure=SemanticFailure(failure_id="semantic-failure-" + stable_digest((request.request_id, kind.value, detail))[:20], kind=kind, detail=detail, provider_request_made=request_made), capability=SemanticCapability(capability_id="semantic.ollama", status=SemanticCapabilityStatus.UNAVAILABLE, provider="ollama", model=provider_model, detail=detail))
        return self._publish(result)

    def _publish(self, result: SemanticEvidenceResult) -> SemanticEvidenceResult:
        if self.artifact_root is None:
            return result
        root = self.artifact_root.resolve()
        allowed = (root / "semantic_evidence").resolve()
        allowed.mkdir(parents=True, exist_ok=True)
        for name in ("contexts", "evidence", "capabilities", "failures", "repeatability", "evaluations", "manifests"):
            (allowed / name).mkdir(parents=True, exist_ok=True)
        payload = result.model_dump(mode="json")
        if result.evidence:
            payload["evidence"].pop("response_hash", None)
        directory = allowed / "evidence"
        target = directory / f"{result.request_id}.json"
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        artifact = SemanticArtifactReference(artifact_id="semantic-artifact-" + stable_digest((result.request_id, serialized))[:24], artifact_type="semantic_evidence", location=str(target), content_hash=stable_digest(serialized), raw_values_persisted=False)
        result = result.model_copy(update={"artifact": artifact})
        payload = result.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, prefix=".semantic-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return result

    def repeatability(self, request: SemanticEvidenceRequest, *, context_items: Iterable[SemanticContextItem]):
        observations = []
        results = [self.analyze(request, context_items=context_items) for _ in range(min(3, request.budget.max_provider_calls))]
        valid = [item for item in results if item.evidence is not None]
        signatures = [stable_digest(item.evidence.model_dump(mode="json", exclude={"authorization"})) for item in valid]
        kind_sets = [tuple(item.kind.value for item in item.evidence.hypotheses) for item in valid]
        refs = [tuple(ref for hypothesis in item.evidence.hypotheses for ref in hypothesis.evidence_refs) for item in valid]
        exact = sum(signature == signatures[0] for signature in signatures) / len(results) if results and signatures else 0.0
        agreement = sum(value == kind_sets[0] for value in kind_sets) / len(results) if results and kind_sets else 0.0
        ref_agreement = sum(value == refs[0] for value in refs) / len(results) if results and refs else 0.0
        from dirty_data_to_olap.domain.contracts.semantic_ai import SemanticRepeatabilityObservation
        return SemanticRepeatabilityObservation(observation_id="repeat_" + stable_digest((request.request_id, len(results)))[:24], request_id=request.request_id, repetitions=len(results), schema_valid_rate=len(valid) / len(results) if results else 0.0, exact_structured_response_rate=exact, hypothesis_kind_agreement=agreement, reference_agreement=ref_agreement)
