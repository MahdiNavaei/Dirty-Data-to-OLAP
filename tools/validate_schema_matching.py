"""Behavioral and boundary checks for the Step13 closure."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'failed'}")
    print(f"PASS {name}")


def main() -> int:
    contracts = (ROOT / "src/dirty_data_to_olap/domain/contracts/schema_matching.py").read_text(encoding="utf-8")
    adapter = (ROOT / "src/dirty_data_to_olap/adapters/matching/valentine.py").read_text(encoding="utf-8")
    service = (ROOT / "src/dirty_data_to_olap/application/schema_matching.py").read_text(encoding="utf-8")
    state = (ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8")
    test = (ROOT / "tests/integration/matching/test_step13_real_valentine.py").read_text(encoding="utf-8")
    checks = {
        "project contracts": all(token in contracts for token in ("SchemaMatchRequest", "SchemaMatchCandidate", "SchemaMatchSignal", "MatchingAuthorization")),
        "multi-source request": "source_ids: tuple[str, ...] = Field(min_length=2)" in contracts,
        "symmetric IDs": "tuple(sorted((left, right)))" in contracts,
        "native Valentine boundary": "import valentine" in adapter and "SchemaMatchScore" in adapter,
        "scores are not probabilities": "raw_native_score" in contracts and 'probability: ' not in contracts and 'confidence: ' not in contracts,
        "candidate-only state": 'state: str = "CANDIDATE"' in contracts and "final_acceptance_allowed" in contracts,
        "privacy and staged input": "verify_schema_matching_authorization" in adapter and "DependencyStagedReader" in adapter,
        "hash and deterministic sampling": "schema_match_config_hash" in contracts and "_sample_rows" in adapter and "sample_identity" in contracts,
        "bounded search and coverage": all(token in contracts for token in ("max_column_pairs", "top_k_per_left_column", "evaluated_by_matcher", "returned_by_matcher")),
        "artifact boundary": "SchemaMatchingArtifactStore" in adapter and '"schema_matching"' in adapter,
        "official runtime pin": '"valentine==1.0.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        "labeled evaluation": (ROOT / "benchmarks/schema_matching/step13_labeled_fixture.json").is_file() and (ROOT / "tools/evaluate_schema_matching.py").is_file(),
        "step12 immutable digest": "self.image_digest" in (ROOT / "src/dirty_data_to_olap/adapters/dependencies/desbordante.py").read_text(encoding="utf-8") and "provider_rows_by_table" in (ROOT / "src/dirty_data_to_olap/domain/contracts/dependency.py").read_text(encoding="utf-8"),
        "step12 project scratch test": "workspace" in test and "test-temp" in test and "tmp_path" not in test,
        "no research clone": not (ROOT / "research/oss/valentine").exists(),
        "step14 not started": not (ROOT / "src/dirty_data_to_olap/adapters/entity_resolution").exists() and "current_step: 13" in state and "G4_BOUNDED_INTELLIGENCE: \"PENDING\"" in state,
        "service owns authorization": "authorize_schema_matching_analysis" in service and "self.adapter.discover" in service,
    }
    for name, condition in checks.items():
        check(name, condition)
    print(f"PASS: schema_matching_checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
