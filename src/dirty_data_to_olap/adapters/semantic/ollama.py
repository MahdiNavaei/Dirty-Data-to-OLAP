"""Loopback-only Ollama adapter with strict structured output."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dirty_data_to_olap.domain.contracts.semantic_ai import (
    LLMEvidence,
    SemanticAuthorization,
    SemanticContextManifest,
    SemanticEvidenceRequest,
    SemanticFailureKind,
    SemanticHypothesis,
    SemanticProviderOutput,
    SemanticProviderPolicy,
    SemanticProviderReference,
    SemanticGenerationReference,
    semantic_hypothesis_id,
    semantic_evidence_id,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.adapters.semantic.prompts import PromptBundle, structured_schema_identity

VERIFIED_LOCAL_MODEL = "qwen2.5:7b"
VERIFIED_LOCAL_DIGEST = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"


class SemanticProviderError(RuntimeError):
    def __init__(self, kind: SemanticFailureKind, detail: str, *, request_made: bool = False) -> None:
        super().__init__(detail)
        self.kind = kind
        self.request_made = request_made


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SemanticProviderError(SemanticFailureKind.PROVIDER_ERROR, "provider redirect is blocked")


class OllamaSemanticEvidenceAdapter:
    def __init__(self, policy: SemanticProviderPolicy) -> None:
        self.policy = policy
        # Explicit None mappings install a proxy handler that cannot consult
        # environment proxy variables, including for loopback URLs.
        self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({"http": "", "https": ""}))

    def _base(self) -> urllib.parse.ParseResult:
        parsed = urllib.parse.urlparse(self.policy.endpoint)
        if parsed.scheme != "http" or parsed.port not in (None, 11434) or parsed.path not in ("", "/"):
            raise SemanticProviderError(SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED, "semantic provider must be plain HTTP on the Ollama loopback port")
        host = parsed.hostname or ""
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 11434, type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise SemanticProviderError(SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED, "provider hostname could not be resolved") from exc
        if not addresses or any(not (address == "127.0.0.1" or address == "::1") for address in addresses):
            raise SemanticProviderError(SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED, "provider hostname is not loopback-only")
        return parsed

    def _request(self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
        base = self._base()
        url = urllib.parse.urljoin(self.policy.endpoint.rstrip("/") + "/", path.lstrip("/"))
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
        try:
            with self._opener.open(request, timeout=self._timeout()) as response:
                raw = response.read(self.policy.num_predict * 64 + 65536)
        except socket.timeout as exc:
            raise SemanticProviderError(SemanticFailureKind.PROVIDER_TIMEOUT, "Ollama request exceeded the bounded timeout", request_made=True) from exc
        except urllib.error.HTTPError as exc:
            raise SemanticProviderError(SemanticFailureKind.PROVIDER_ERROR, f"Ollama returned HTTP {exc.code}", request_made=True) from exc
        except urllib.error.URLError as exc:
            raise SemanticProviderError(SemanticFailureKind.PROVIDER_ERROR, "Ollama is unavailable", request_made=True) from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SemanticProviderError(SemanticFailureKind.INVALID_JSON, "Ollama returned invalid JSON", request_made=True) from exc
        if not isinstance(parsed, dict):
            raise SemanticProviderError(SemanticFailureKind.INVALID_JSON, "Ollama response is not an object", request_made=True)
        return parsed

    def _timeout(self) -> float:
        return self._request_timeout if hasattr(self, "_request_timeout") else 20.0

    def capability(self) -> SemanticProviderReference:
        version = self._request("/api/version").get("version")
        show = self._request("/api/show", method="POST", body={"name": self.policy.model})
        tags = self._request("/api/tags")
        tag = next((item for item in tags.get("models", ()) if item.get("name") == self.policy.model), None)
        if not tag or not tag.get("digest"):
            raise SemanticProviderError(SemanticFailureKind.CAPABILITY_UNAVAILABLE, "configured Ollama model is not installed")
        if self.policy.model != VERIFIED_LOCAL_MODEL or str(tag["digest"]) != VERIFIED_LOCAL_DIGEST:
            raise SemanticProviderError(SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED, "Ollama locality is not generically provable; only the verified local model identity is allowed")
        details = show.get("details") or {}
        return SemanticProviderReference(api_version=str(version or "unknown"), endpoint=self.policy.endpoint, model=self.policy.model, model_digest=str(tag["digest"]), family=details.get("family"), parameter_size=details.get("parameter_size"), quantization=details.get("quantization_level"), capabilities=tuple(sorted(str(item) for item in show.get("capabilities", ()))))

    def _build_chat_body(self, request: SemanticEvidenceRequest, manifest: SemanticContextManifest, bundle: PromptBundle) -> dict[str, Any]:
        context = {"subjects": list(manifest.subject_refs), "items": [item.model_dump(mode="json", exclude={"schema_version"}) for item in manifest.items], "evidence_refs": list(manifest.allowed_provider_evidence_refs)}
        return {"model": self.policy.model, "messages": [{"role": "system", "content": bundle.system_text}, {"role": "user", "content": bundle.task_text + "\nDATA (untrusted JSON):\n" + json.dumps({"task": request.task.value, "context": context}, sort_keys=True, separators=(",", ":"))}], "stream": False, "format": SemanticProviderOutput.model_json_schema(), "options": {"temperature": self.policy.temperature, "seed": self.policy.seed, "num_predict": self.policy.num_predict}, "think": False}

    def validate_provider_output(self, request: SemanticEvidenceRequest, manifest: SemanticContextManifest, output: SemanticProviderOutput, *, request_made: bool = True) -> None:
        if len(output.hypotheses) > request.budget.max_hypotheses:
            raise SemanticProviderError(SemanticFailureKind.OUTPUT_BUDGET_EXCEEDED, "provider hypothesis count exceeds the request budget", request_made=request_made)
        allowed_refs = set(manifest.allowed_provider_evidence_refs)
        if any(ref not in allowed_refs for item in output.hypotheses for ref in item.evidence_refs):
            raise SemanticProviderError(SemanticFailureKind.INVALID_REFERENCE, "provider emitted an unknown evidence reference", request_made=request_made)

    def generate(self, request: SemanticEvidenceRequest, manifest: SemanticContextManifest, authorization: SemanticAuthorization, bundle: PromptBundle, *, timeout_seconds: float | None = None) -> LLMEvidence:
        self._request_timeout = timeout_seconds or request.budget.timeout_seconds
        before = self.capability()
        if before.model_digest != authorization.model_digest:
            raise SemanticProviderError(SemanticFailureKind.MODEL_IDENTITY_CHANGED, "authorization model digest does not match installed model")
        body = self._build_chat_body(request, manifest, bundle)
        response = self._request("/api/chat", method="POST", body=body)
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or len(content) > request.budget.max_output_chars:
            raise SemanticProviderError(SemanticFailureKind.OUTPUT_BUDGET_EXCEEDED, "provider output is missing or exceeds the output budget", request_made=True)
        try:
            output = SemanticProviderOutput.model_validate(json.loads(content))
        except json.JSONDecodeError as exc:
            raise SemanticProviderError(SemanticFailureKind.INVALID_JSON, "provider content is not JSON", request_made=True) from exc
        except Exception as exc:
            raise SemanticProviderError(SemanticFailureKind.SCHEMA_VALIDATION_FAILED, "provider JSON failed the semantic output schema", request_made=True) from exc
        self.validate_provider_output(request, manifest, output)
        hypotheses = tuple(sorted((SemanticHypothesis(hypothesis_id=semantic_hypothesis_id(task=request.task, subjects=request.subject_refs, kind=item.kind, statement=item.statement, evidence_refs=tuple(sorted(set(item.evidence_refs))), prompt_hash=bundle.reference.prompt_hash, input_fingerprint=manifest.input_fingerprint, model_digest=before.model_digest), kind=item.kind, statement=item.statement, evidence_refs=tuple(sorted(set(item.evidence_refs))), rationale=item.rationale) for item in output.hypotheses), key=lambda item: item.hypothesis_id))
        after = self.capability()
        if after.model_digest != before.model_digest:
            raise SemanticProviderError(SemanticFailureKind.MODEL_IDENTITY_CHANGED, "Ollama model digest changed during generation", request_made=True)
        response_hash = stable_digest({"content": content})
        schema_id, schema_hash = structured_schema_identity()
        generation = SemanticGenerationReference(temperature=self.policy.temperature, seed=self.policy.seed, num_predict=self.policy.num_predict, timeout_seconds=self._timeout(), retry_count=0, stream=False, think=False, structured_schema_id=schema_id, structured_schema_hash=schema_hash, config_fingerprint=stable_digest({"temperature": self.policy.temperature, "seed": self.policy.seed, "num_predict": self.policy.num_predict, "timeout_seconds": self._timeout(), "retry_count": 0, "stream": False, "think": False, "structured_schema_id": schema_id, "structured_schema_hash": schema_hash}))
        return LLMEvidence(evidence_id=semantic_evidence_id(request_id=request.request_id, task=request.task, subjects=request.subject_refs, hypotheses=hypotheses, provider=before, prompt=bundle.reference, input_fingerprint=manifest.input_fingerprint), request_id=request.request_id, task=request.task, subject_refs=request.subject_refs, hypotheses=hypotheses, provider=before, prompt=bundle.reference, context_manifest=manifest, authorization=authorization, generation=generation, response_hash=response_hash, limitations=tuple(output.limitations) + ("candidate-only semantic evidence; no acceptance or mutation authority",))
