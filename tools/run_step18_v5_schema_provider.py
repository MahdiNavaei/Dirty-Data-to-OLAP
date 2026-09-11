"""Run the unchanged Valentine/Cupid provider into the Step18 v5 estate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_step18_v4_schema_provider as implementation

implementation.RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v5" / "evaluation" / "schema_matching"

if __name__ == "__main__":
    raise SystemExit(implementation.main())
