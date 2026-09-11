"""Run Valentine from the dedicated matching venv into the v4 run."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_step18_v3_schema_provider as implementation

implementation.MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"
implementation.RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "schema_matching"
# The v3 integration helper has a module-level optional-runtime guard.  This
# sentinel is disposable; the provider itself is imported from this venv.
(ROOT / "workspace" / "test-temp" / "valentine-runtime").mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    raise SystemExit(implementation.main())
