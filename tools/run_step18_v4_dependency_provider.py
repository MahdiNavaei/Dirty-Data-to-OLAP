"""Run the existing bounded Desbordante adapter into the immutable v4 run."""

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_step18_v3_dependency_provider as implementation

implementation.MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios_v3.json"
implementation.RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "dependency_discovery"

def main() -> int:
    output = implementation.RUN / "normalized_provider_results.json"
    previous_hash = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
    code = implementation.main()
    if output.is_file():
        def canonical(item):
            if isinstance(item, dict):
                return {key: ("2026-09-11T00:00:00Z" if key == "created_at" else canonical(child)) for key, child in item.items()}
            if isinstance(item, list):
                return [canonical(child) for child in item]
            return item
        artifact_root = implementation.RUN / "provider-artifacts"
        for artifact in artifact_root.rglob("*.json") if artifact_root.is_dir() else ():
            artifact.write_text(json.dumps(canonical(json.loads(artifact.read_text(encoding="utf-8"))), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        value = canonical(json.loads(output.read_text(encoding="utf-8")))
        for item in value.get("results", ()):
            for reference in item.get("result", {}).get("artifacts", ()):
                artifact = ROOT / reference["artifact_location"]
                if artifact.is_file():
                    reference["content_hash"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        output.write_text(json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        receipt_path = implementation.RUN / "provider_receipt.json"
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["output_hash"] = hashlib.sha256(output.read_bytes()).hexdigest()
            receipt.pop("receipt_content_hash", None)
            receipt["receipt_content_hash"] = hashlib.sha256((json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    current_hash = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
    if previous_hash and current_hash:
        path = implementation.RUN.parent / "reproducibility" / "relationship_provider.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status":"PASS" if previous_hash == current_hash else "FAIL","first_output_hash":previous_hash,"second_output_hash":current_hash,"same_normalized_output":previous_hash == current_hash}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
