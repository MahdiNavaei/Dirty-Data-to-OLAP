"""Run project-owned profiling and quality into the immutable v4 run."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_step18_v3_profile_quality as implementation

implementation.MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios_v3.json"
implementation.RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "profiling_quality"

if __name__ == "__main__":
    raise SystemExit(implementation.main())
