import hashlib
import json
from pathlib import Path
import subprocess
import sys

import duckdb


def test_step20_generic_reference_flow_is_deterministic_and_non_retail():
    root = Path(__file__).parents[2]
    runner = root / "tools" / "run_step20_generic_reference.py"
    first = subprocess.run([sys.executable, str(runner)], cwd=root, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    run = root / "workspace" / "runs" / "step20-generic-reference-run" / "olap"
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["target_relative_path"] == "workspace/runs/step20-generic-reference-run/olap/target.duckdb"
    assert manifest["step21_semantic_layer"] == "NOT_IMPLEMENTED"
    assert manifest["g6_data_correctness"] == "PENDING_STEP22"
    assert "?" in (run / "load_facts.sql").read_text(encoding="utf-8")
    target_hash = hashlib.sha256((run / "target.duckdb").read_bytes()).hexdigest()
    connection = duckdb.connect(str(run / "target.duckdb"), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM fact_device_reading").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM (SELECT device_key FROM dim_device GROUP BY 1 HAVING COUNT(*) > 1)").fetchone()[0] == 0
    finally:
        connection.close()
    second = subprocess.run([sys.executable, str(runner)], cwd=root, capture_output=True, text=True)
    assert second.returncode == 0, second.stdout + second.stderr
    second_manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert second_manifest["analytical_plan_content_hash"] == manifest["analytical_plan_content_hash"]
    assert second_manifest["compiled_plan_content_hash"] == manifest["compiled_plan_content_hash"]
    assert hashlib.sha256((run / "target.duckdb").read_bytes()).hexdigest() == target_hash
