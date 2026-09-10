"""Static and behavioral contract checks for Step16 bounded semantic evidence."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main() -> None:
    contract = (ROOT / "src/dirty_data_to_olap/domain/contracts/semantic_ai.py").read_text(encoding="utf-8")
    provider = (ROOT / "src/dirty_data_to_olap/adapters/semantic/ollama.py").read_text(encoding="utf-8")
    service = (ROOT / "src/dirty_data_to_olap/application/semantic_evidence.py").read_text(encoding="utf-8")
    require("SemanticEvidenceRequest" in contract and "LLMEvidence" in contract, "semantic contracts are missing")
    require('"temperature": 0' in provider, "zero temperature is not bound")
    require('"stream": False' in provider and '"think": False' in provider, "streaming or thinking is not disabled")
    require("ProxyHandler({})" in provider and "HTTPRedirectHandler" in provider, "provider transport is not fail-closed")
    require("model_digest" in provider and "MODEL_IDENTITY_CHANGED" in provider, "model identity is not bound")
    require("response_hash" not in service or "pop(\"response_hash\", None)" in service, "native response persistence boundary is missing")
    require((ROOT / "benchmarks/semantic_ai/step16_semantic_safety_fixture.json").exists(), "semantic safety fixture is missing")
    fixture = json.loads((ROOT / "benchmarks/semantic_ai/step16_semantic_safety_fixture.json").read_text(encoding="utf-8"))
    require(len(fixture["cases"]) >= 10, "semantic safety fixture is incomplete")
    require(not (ROOT / "src/dirty_data_to_olap/application/evidence_fusion.py").exists(), "Step17 fusion implementation must not be started")
    print("PASS: semantic AI validator")


if __name__ == "__main__":
    main()
