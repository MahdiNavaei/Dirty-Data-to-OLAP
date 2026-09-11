import json
import hashlib
from pathlib import Path
import subprocess
import sys

import duckdb


def test_step20_reference_flow_materializes_reviewed_star_schema():
    root = Path(__file__).parents[2]
    result = subprocess.run([sys.executable, str(root / "tools" / "run_step20_reference.py")], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    run = root / "workspace" / "runs" / "step20-reference-run" / "olap"
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["step21_semantic_layer"] == "NOT_IMPLEMENTED"
    assert manifest["g6_data_correctness"] == "PENDING_STEP22"
    assert manifest["target_relative_path"] == "workspace/runs/step20-reference-run/olap/target.duckdb"
    connection = duckdb.connect(str(root / manifest["target_relative_path"]), read_only=True)
    try:
        assert connection.execute("SELECT SUM(quantity) FROM fact_order_line").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM fact_order_line WHERE unit_price IS NOT NULL").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM dim_customer").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM dim_product").fetchone()[0] == 2
    finally:
        connection.close()
    target_hash = hashlib.sha256((run / "target.duckdb").read_bytes()).hexdigest()
    second = subprocess.run([sys.executable, str(root / "tools" / "run_step20_reference.py")], cwd=root, capture_output=True, text=True)
    assert second.returncode == 0, second.stdout + second.stderr
    second_manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert second_manifest["analytical_plan_content_hash"] == manifest["analytical_plan_content_hash"]
    assert second_manifest["compiled_plan_content_hash"] == manifest["compiled_plan_content_hash"]
    assert second_manifest["generated_sql_hash"] == manifest["generated_sql_hash"]
    assert hashlib.sha256((run / "target.duckdb").read_bytes()).hexdigest() == target_hash
