"""Behavioral validator for the Step40 developer-experience contract."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap import devx


def run(label: str, args: list[str], *, timeout: int = 600) -> dict[str, Any]:
    result = subprocess.run([sys.executable, *args], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    return {"name": label, "status": "PASS" if result.returncode == 0 else "FAIL", "returncode": result.returncode, "stdout_tail": result.stdout[-1200:], "stderr_tail": result.stderr[-1200:]}


def run_expected_failure(label: str, args: list[str], *, timeout: int = 60) -> dict[str, Any]:
    result = subprocess.run([sys.executable, *args], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    return {"name": label, "status": "PASS" if result.returncode != 0 else "FAIL", "returncode": result.returncode, "stdout_tail": result.stdout[-800:], "stderr_tail": result.stderr[-800:]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Step40 developer experience")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--include-frontend", action="store_true")
    args = parser.parse_args()
    checks: list[dict[str, Any]] = []
    shim = str(ROOT / "tools" / "ddo.py")
    checks.append({"name": "version", **run("version", [shim, "--root", str(ROOT), "version"], timeout=60)})
    checks.append({"name": "help", **run("help", [shim, "--help"], timeout=60)})
    checks.append({"name": "config", **run("config", [shim, "--root", str(ROOT), "config"], timeout=60)})
    checks.append({"name": "doctor", **run("doctor", [shim, "--root", str(ROOT), "doctor"], timeout=60)})
    checks.append({"name": "CLI negative control", **run_expected_failure("CLI negative control", [shim, "--root", str(ROOT), "bootstrap", "--profile", "not-a-profile"], timeout=60)})
    checks.append({"name": "bootstrap dry-run", **run("bootstrap dry-run", [shim, "--root", str(ROOT), "bootstrap", "--dry-run"], timeout=60)})
    checks.append({"name": "bootstrap dry-run idempotency", **run("bootstrap dry-run idempotency", [shim, "--root", str(ROOT), "bootstrap", "--dry-run"], timeout=60)})
    with tempfile.TemporaryDirectory(prefix="ddo-step40-demo-", dir=ROOT / ".ddo") as directory:
        state = Path(directory)
        checks.append({"name": "demo", **run("demo", [shim, "--root", str(ROOT), "demo", "--state-root", str(state)], timeout=300)})
        checks.append({"name": "demo idempotency", **run("demo idempotency", [shim, "--root", str(ROOT), "demo", "--state-root", str(state)], timeout=300)})
    compiled = devx.compile_tracked_python(ROOT)
    checks.append({"name": "tracked Python compileall", **compiled})
    if args.include_frontend:
        checks.append({"name": "OpenAPI verify", **run("OpenAPI verify", [shim, "--root", str(ROOT), "frontend", "openapi"], timeout=600)})
        checks.append({"name": "frontend bounded diagnostic", **run("frontend bounded diagnostic", [shim, "--root", str(ROOT), "frontend", "diagnose", "--timeout", "180"], timeout=240)})
    failed = [item["name"] for item in checks if item.get("status") == "FAIL"]
    report = {"step": 40, "validator": "developer-experience", "mode": "ci" if args.ci else "local", "status": "PASS" if not failed else "FAIL", "checks": checks, "clean_room_boundary": "tracked files only; protected quality artifacts are not read"}
    output = ROOT / ".ddo" / "validation" / "step40_developer_experience.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output), "failed": failed}, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
