import subprocess
import sys
from pathlib import Path


def test_step19_reference_flow_and_behavioral_validator_pass():
    root = Path(__file__).parents[2]
    run = subprocess.run([sys.executable, str(root / "tools" / "run_step19_reference.py")], cwd=root, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    validation = subprocess.run([sys.executable, str(root / "tools" / "validate_step19_canonical.py")], cwd=root, capture_output=True, text=True)
    assert validation.returncode == 0, validation.stderr + validation.stdout
