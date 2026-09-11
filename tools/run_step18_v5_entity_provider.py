"""Run the unchanged Splink provider into the Step18 v5 estate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_step18_v4_entity_provider as implementation

implementation.RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v5" / "evaluation" / "entity_resolution"

if __name__ == "__main__":
    raise SystemExit(implementation.main())
