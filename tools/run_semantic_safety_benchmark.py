"""Execute the aggregate-safe Step16 semantic safety fixture.

Without ``--live`` this validates and materializes the request/context boundary.
With ``--live`` it calls only the verified local Ollama model and writes the
aggregate ``SemanticSafetyEvaluation`` through the application service.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.semantic.context import SemanticContextBuilder
from dirty_data_to_olap.adapters.semantic.ollama import OllamaSemanticEvidenceAdapter
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.semantic_evidence import SemanticEvidenceService
from dirty_data_to_olap.domain.contracts.semantic_ai import (
    SemanticContextItem,
    SemanticEvidenceRequest,
    SemanticProviderPolicy,
    SemanticTask,
)


FIXTURE = ROOT / "benchmarks" / "semantic_ai" / "step16_semantic_safety_fixture.json"


def load_cases() -> tuple[SemanticEvidenceRequest, tuple[SemanticContextItem, ...]]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if len(fixture.get("cases", ())) != 11:
        raise ValueError("Step16 semantic safety fixture must contain exactly 11 cases")
    output = []
    for case in fixture["cases"]:
        request = SemanticEvidenceRequest(
            request_id="step16-benchmark-" + case["case_id"],
            task=SemanticTask(case["task"]),
            subject_refs=tuple(case["request"]["subject_refs"]),
            evidence_refs=tuple(case["request"]["evidence_refs"]),
        )
        items = tuple(SemanticContextItem(**item) for item in case["context_items"])
        SemanticContextBuilder().build(request, items)
        output.append((request, items))
    return tuple(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="call verified local Ollama")
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "workspace" / "runs" / "step16-semantic-benchmark")
    args = parser.parse_args()
    cases = load_cases()
    if not args.live:
        print(json.dumps({"fixture": str(FIXTURE.relative_to(ROOT)), "cases": len(cases), "aggregate_safe": True}, sort_keys=True))
        return 0
    policy = SemanticProviderPolicy(model="qwen2.5:7b", num_predict=256)
    service = SemanticEvidenceService(adapter=OllamaSemanticEvidenceAdapter(policy), privacy_policy=PrivacyPolicyService(project_root=ROOT), artifact_root=args.artifact_root)
    results = tuple(service.analyze(request, context_items=items) for request, items in cases)
    repeat_request, repeat_items = cases[0]
    repeat_request = repeat_request.model_copy(update={"request_id": "step16-benchmark-repeatability", "budget": repeat_request.budget.model_copy(update={"max_provider_calls": 3})})
    repeatability = service.repeatability(repeat_request, context_items=repeat_items)
    evaluation = service.evaluate_safety(results, repeatability=repeatability, evaluation_id="step16-semantic-safety-v2")
    print(json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
