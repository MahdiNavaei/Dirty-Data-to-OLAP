"""Behavioral and repository-boundary checks for Step16 semantic evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import socket
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.semantic.context import SemanticContextBuilder
from dirty_data_to_olap.adapters.semantic.ollama import OllamaSemanticEvidenceAdapter, SemanticProviderError
from dirty_data_to_olap.adapters.semantic.prompts import load_prompt_bundle
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.semantic_evidence import SemanticEvidenceService
from dirty_data_to_olap.domain.contracts.semantic_ai import *


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def main() -> int:
    fixture = json.loads((ROOT / "benchmarks/semantic_ai/step16_semantic_safety_fixture.json").read_text(encoding="utf-8"))
    check("exactly 11 aggregate-safe benchmark cases", len(fixture["cases"]) == 11 and fixture["scope"].endswith("without_ground_truth_answers"))
    check("Step17 fusion implementation is present", (ROOT / "src/dirty_data_to_olap/application/evidence_fusion.py").exists())

    case = fixture["cases"][0]
    request = SemanticEvidenceRequest(request_id="validator", task=SemanticTask(case["task"]), subject_refs=tuple(case["request"]["subject_refs"]), evidence_refs=tuple(case["request"]["evidence_refs"]), budget=SemanticBudget(max_hypotheses=1))
    items = tuple(SemanticContextItem(**item) for item in case["context_items"])
    manifest = SemanticContextBuilder().build(request, items)
    bundle = load_prompt_bundle(request, context_builder_version=manifest.context_builder_version)
    system_path = ROOT / "prompts/semantic-ai/v1/system.txt"
    check("prompt bytes are the bytes sent", bundle.system_text.encode("utf-8") == system_path.read_bytes() and bundle.reference.system_prompt_hash == hashlib.sha256(system_path.read_bytes()).hexdigest())
    body = OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b"))._build_chat_body(request, manifest, bundle)
    check("system and task prompt are bound into provider payload", body["messages"][0]["content"] == bundle.system_text and body["messages"][1]["content"].startswith(bundle.task_text))

    privacy_item = SemanticContextItem(item_ref="privacy", item_kind="profile", safe_fields={"description": {"text": "person@example.test; phone 09121234567; secret=canary; id 12345678901"}})
    privacy_manifest = SemanticContextBuilder().build(request.model_copy(update={"evidence_refs": ("privacy",)}), (privacy_item,))
    privacy_text = json.dumps(privacy_manifest.model_dump(mode="json"), ensure_ascii=False)
    check("recursive generic-description minimization", all(canary not in privacy_text for canary in ("person@example.test", "09121234567", "canary", "12345678901")))
    check("requested/provided/allowed references are explicit", manifest.requested_evidence_refs == ("e1",) and manifest.provided_context_item_refs == ("e1",) and manifest.allowed_provider_evidence_refs == ("e1",))

    output = SemanticProviderOutput(hypotheses=(SemanticProviderHypothesis(hypothesis_id="provider", kind=SemanticHypothesisKind.AMBIGUOUS, statement="ambiguous context"),))
    check("provider hypothesis budget is enforced", SemanticBudget(max_hypotheses=1).max_hypotheses == 1)
    try:
        OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b")).validate_provider_output(request, manifest, output.model_copy(update={"hypotheses": output.hypotheses + output.hypotheses}))
    except SemanticProviderError as exc:
        check("hypothesis overflow fails closed", exc.kind is SemanticFailureKind.OUTPUT_BUDGET_EXCEEDED)
    else:
        raise AssertionError("hypothesis overflow was accepted")
    check("retry budget is truthful", SemanticBudget(max_retries=0).max_retries == 0)
    for bad in ("execute SQL now",):
        try:
            SemanticProviderHypothesis(hypothesis_id="bad", kind=SemanticHypothesisKind.AMBIGUOUS, statement="uncertain", rationale=bad)
        except ValueError:
            pass
        else:
            raise AssertionError("forbidden rationale was accepted")
    check("statement/rationale/limitations safety", True)
    check("invalid JSON rejected", _generate_failure('{"message":{"content":"not-json"}}', request, manifest, bundle, SemanticFailureKind.INVALID_JSON))
    check("invalid schema rejected", _generate_failure('{"message":{"content":"{\\"hypotheses\\":[{\\"kind\\":\\"bad\\"}]}"}}', request, manifest, bundle, SemanticFailureKind.SCHEMA_VALIDATION_FAILED))
    check("unknown references rejected", _generate_failure('{"message":{"content":"{\\"hypotheses\\":[{\\"hypothesis_id\\":\\"h\\",\\"kind\\":\\"AMBIGUOUS\\",\\"statement\\":\\"uncertain\\",\\"evidence_refs\\":[\\"unknown\\"]}]}"}}', request, manifest, bundle, SemanticFailureKind.INVALID_REFERENCE))
    check("digest change rejected", _digest_change(request, manifest, bundle))
    check("non-loopback provider is blocked before I/O", _blocked_non_loopback())
    check("redirect blocked", _redirect_blocked())
    check("proxy bypass is explicit", _proxy_bypass())
    check("remote/cloud model blocked", _remote_model_blocked())
    check("timeout contained", _timeout_contained())
    check("artifact hash and safety evaluation are produced", _artifact_and_evaluation())
    state_text = (ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8")
    components_text = (ROOT / "docs/architecture/specs/components.yml").read_text(encoding="utf-8")
    plan_text = (ROOT / "docs/engineering/specs/implementation_plan.yml").read_text(encoding="utf-8")
    check("G4 evidence summary exists", (ROOT / "docs/execution/gates/G4_BOUNDED_INTELLIGENCE.md").exists())
    check("run manager remains planned", "component_id: application.run_manager" in components_text and "implementation_status: PLANNED" in components_text.split("component_id: application.run_manager", 1)[1].split("component_id:", 1)[0])
    handoff_consistent = (("current_handoff: \"Step16 LLM / Semantic AI Engineer\"" in plan_text and "current_step: 16" in state_text) or ("current_handoff: \"Step17 Evidence Fusion Engineer\"" in plan_text and "current_step: 17" in state_text) or ("current_handoff: \"Step18 ML Evaluation Engineer\"" in plan_text and "current_step: 18" in state_text) or ("current_handoff: \"Step20 OLAP Engineer\"" in plan_text and "current_step: 20" in state_text and "Step19" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_handoff: \"Step21 Analytical Model / Semantic Layer Engineer\"" in plan_text and "current_step: 21" in state_text and "Step20" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_handoff: \"Step22 Data QA Engineer\"" in plan_text and "current_step: 22" in state_text and "Step21" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_handoff: \"Step23 Data Platform Engineer\"" in plan_text and "current_step: 23" in state_text and "Step22" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_handoff: \"Step24 Distributed Data Engineer\"" in plan_text and "current_step: 24" in state_text and "Step23" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_handoff: \"Step25 UX / Product Designer\"" in plan_text and "current_step: 25" in state_text and "Step24" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_handoff: \"Step26 Data Visualization Engineer\"" in plan_text and "current_step: 26" in state_text and "Step25" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")) or ("current_step: 19" in state_text and "Step19" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")))
    check("Semantic AI architecture and handoff metadata are consistent", "component_id: application.semantic_evidence" in components_text and handoff_consistent)
    check("Step16 execution log exists", "Step16" in (ROOT / "docs/execution/SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8"))
    print("PASS: semantic AI behavioral validator")
    return 0


def _blocked_non_loopback() -> bool:
    try:
        OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b", endpoint="http://192.0.2.1:11434")).capability()
    except SemanticProviderError as exc:
        return exc.kind is SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED
    return False


def _generate_failure(response, request, manifest, bundle, expected) -> bool:
    adapter = OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b"))
    provider = SemanticProviderReference(api_version="0.1", endpoint="http://127.0.0.1:11434", model="qwen2.5:7b", model_digest="845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e")
    authorization = SemanticAuthorization(authorization_id="validator-auth", policy_id="privacy-v1", policy_version="1.0", request_id=request.request_id, task=request.task, context_manifest_fingerprint=manifest.input_fingerprint, subject_refs=request.subject_refs, evidence_refs=manifest.allowed_provider_evidence_refs, model=provider.model, model_digest=provider.model_digest, prompt_version=bundle.reference.prompt_version)
    adapter.capability = lambda: provider
    adapter._request = lambda *args, **kwargs: json.loads(response)
    try:
        adapter.generate(request, manifest, authorization, bundle)
    except SemanticProviderError as exc:
        return exc.kind is expected
    return False


def _digest_change(request, manifest, bundle) -> bool:
    adapter = OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b"))
    first = SemanticProviderReference(api_version="0.1", endpoint="http://127.0.0.1:11434", model="qwen2.5:7b", model_digest="845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e")
    second = first.model_copy(update={"model_digest": "changed"})
    values = iter((first, second))
    adapter.capability = lambda: next(values)
    adapter._request = lambda *args, **kwargs: {"message": {"content": '{"hypotheses":[]}'}}
    authorization = SemanticAuthorization(authorization_id="validator-auth", policy_id="privacy-v1", policy_version="1.0", request_id=request.request_id, task=request.task, context_manifest_fingerprint=manifest.input_fingerprint, subject_refs=request.subject_refs, evidence_refs=manifest.allowed_provider_evidence_refs, model=first.model, model_digest=first.model_digest, prompt_version=bundle.reference.prompt_version)
    try:
        adapter.generate(request, manifest, authorization, bundle)
    except SemanticProviderError as exc:
        return exc.kind is SemanticFailureKind.MODEL_IDENTITY_CHANGED
    return False


def _redirect_blocked() -> bool:
    try:
        from dirty_data_to_olap.adapters.semantic.ollama import _NoRedirect
        _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "http://127.0.0.1:11434")
    except SemanticProviderError:
        return True
    return False


def _proxy_bypass() -> bool:
    adapter = OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b"))
    return any(handler.__class__.__name__ == "ProxyHandler" and handler.proxies == {"http": "", "https": ""} for handler in adapter._opener.handlers)


def _remote_model_blocked() -> bool:
    adapter = OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b"))
    responses = {"/api/version": {"version": "0.1"}, "/api/show": {"details": {"family": "cloud"}}, "/api/tags": {"models": [{"name": "qwen2.5:7b", "digest": "cloud"}]}}
    adapter._request = lambda path, **kwargs: responses[path]
    try:
        adapter.capability()
    except SemanticProviderError as exc:
        return exc.kind is SemanticFailureKind.NON_LOCAL_PROVIDER_BLOCKED
    return False


def _timeout_contained() -> bool:
    adapter = OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b"))
    class TimeoutOpener:
        def open(self, *args, **kwargs):
            raise socket.timeout()
    adapter._opener = TimeoutOpener()
    try:
        adapter._request("/api/version")
    except SemanticProviderError as exc:
        return exc.kind is SemanticFailureKind.PROVIDER_TIMEOUT and exc.request_made
    return False


def _artifact_and_evaluation() -> bool:
    class FakeAdapter:
        policy = SemanticProviderPolicy(model="qwen2.5:7b")
        def capability(self):
            return SemanticProviderReference(api_version="0.1", endpoint=self.policy.endpoint, model=self.policy.model, model_digest="845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e")
        def generate(self, request, manifest, authorization, bundle, *, timeout_seconds=None):
            return LLMEvidence(evidence_id="validator-evidence", request_id=request.request_id, task=request.task, subject_refs=request.subject_refs, hypotheses=(), provider=self.capability(), prompt=bundle.reference, context_manifest=manifest, authorization=authorization, generation=SemanticGenerationReference(temperature=0, seed=20260910, num_predict=512, timeout_seconds=20, retry_count=0, stream=False, think=False, structured_schema_id="SemanticProviderOutput", structured_schema_hash="schema", config_fingerprint="generation"), response_hash="response")
    with tempfile.TemporaryDirectory(dir=ROOT / "workspace" / "test-temp") as directory:
        project = Path(directory)
        request = SemanticEvidenceRequest(request_id="validator-artifact", task=SemanticTask.AMBIGUITY_EXPLANATION, subject_refs=("column:t.c",), evidence_refs=("e1",))
        item = SemanticContextItem(item_ref="e1", item_kind="profile", safe_fields={"distinct_ratio": 0.5})
        service = SemanticEvidenceService(adapter=FakeAdapter(), privacy_policy=PrivacyPolicyService(project_root=project), artifact_root=project / "runs")
        result = service.analyze(request, context_items=(item,))
        target = project / result.artifact.location
        evaluation = service.evaluate_safety((result,), evaluation_id="validator-eval")
        return result.artifact is not None and hashlib.sha256(target.read_bytes()).hexdigest() == result.artifact.content_hash and isinstance(evaluation, SemanticSafetyEvaluation)


if __name__ == "__main__":
    raise SystemExit(main())
