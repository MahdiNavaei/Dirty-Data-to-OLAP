from pathlib import Path
import urllib.request

import pytest

from dirty_data_to_olap.adapters.semantic.ollama import OllamaSemanticEvidenceAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.semantic_evidence import SemanticEvidenceService
from dirty_data_to_olap.domain.contracts.semantic_ai import *


def _available() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


@pytest.mark.skipif(not _available(), reason="Ollama loopback is unavailable")
def test_step16_real_ollama_candidate_only_evidence():
    root = Path.cwd()
    request = SemanticEvidenceRequest(request_id="step16-real-ollama", task=SemanticTask.RELATIONSHIP_SEMANTIC_HYPOTHESIS, subject_refs=("relationship:customer_id->orders.customer_id",), evidence_refs=("schema-score-1",), budget=SemanticBudget(max_input_chars=2400, max_output_chars=1000, timeout_seconds=60, max_provider_calls=1))
    item = SemanticContextItem(item_ref="schema-score-1", item_kind="schema_match", safe_fields={"source_column": "customer_id", "target_column": "customer_id", "matcher_support_count": 2, "type_compatible": True, "target_uniqueness_ratio": 0.98})
    service = SemanticEvidenceService(adapter=OllamaSemanticEvidenceAdapter(SemanticProviderPolicy(model="qwen2.5:7b", num_predict=128)), privacy_policy=PrivacyPolicyService(project_root=root), artifact_root=root / "workspace" / "runs" / request.request_id)
    result = service.analyze(request, context_items=(item,))
    assert result.state is SemanticSupportState.CANDIDATE_ONLY
    assert result.evidence is not None
    assert result.evidence.provider.model_digest == "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
    assert all(h.candidate_only for h in result.evidence.hypotheses)
    assert not (root / "workspace" / "runs" / request.request_id / "semantic_evidence" / "evidence" / f"{request.request_id}.json").read_text(encoding="utf-8").find("synthetic.person@example.test") >= 0
