"""Run the Step32 source/environment compatibility matrix.

The validator keeps the local reference matrix useful on a developer machine
while making CI fail closed when a required live database fixture is missing.
It invokes the same project-owned source discovery and snapshot boundary used
by the application; the database tests provide only runtime credentials and
security verification at the test seam.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.execution_state import (
    step33_application_security_closed,
    step35_sre_closed,
    step36_resilience_closed,
    step37_performance_closed,
    step38_load_stress_closed,
    step39_red_team_closed,
    step40_g14_closed,
)
REPORT = ROOT / "output" / "step32_compatibility_validation.json"
REQUIRED_DATABASES = {
    "PostgreSQL": ("DDO_STEP32_POSTGRES_ADMIN_URL", "DDO_STEP32_POSTGRES_URL"),
    "MySQL": ("DDO_STEP32_MYSQL_ADMIN_URL", "DDO_STEP32_MYSQL_URL"),
    "MariaDB": ("DDO_STEP32_MARIADB_ADMIN_URL", "DDO_STEP32_MARIADB_URL"),
    "SQL Server": ("DDO_STEP32_SQLSERVER_ADMIN_URL", "DDO_STEP32_SQLSERVER_URL"),
}


class ValidationFailure(RuntimeError):
    pass


def _count(summary: str, label: str) -> int:
    match = re.search(rf"(\d+) {label}", summary)
    return int(match.group(1)) if match else 0


def _state() -> dict[str, object]:
    import yaml

    state = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state.get("specialist_execution", {})
    gates = state.get("gates", {})
    current = execution.get("current_step")
    closed = execution.get("step32_started") is True and execution.get("step32_status") == "COMPLETED_COMPATIBILITY_G9_PASS"
    pre = (
        execution.get("current_step") == 32
        and execution.get("current_role") == "compatibility_test_engineer"
        and execution.get("last_completed_step") == 31
        and execution.get("last_completed_role") == "qa_automation_engineer"
        and execution.get("step32_started") is False
        and execution.get("step32_status") == "NOT_STARTED"
        and gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and gates.get("G9_FUNCTIONAL_SUPPORT") == "PENDING"
        and state.get("blocked") is False
    )
    post = closed and gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS" and (
        current == 33
        or (current in {34, 35} and step33_application_security_closed(state))
        or (current == 36 and step35_sre_closed(state))
        or (current == 37 and step36_resilience_closed(state))
        or (current == 38 and step37_performance_closed(state))
        or (current == 39 and step38_load_stress_closed(state))
        or (current == 40 and step39_red_team_closed(state))
        or (current == 41 and step40_g14_closed(state))
    )
    if not (pre or post):
        raise ValidationFailure("authoritative execution state is neither the Step32 handoff nor the closed Step32/G9 handoff")
    return {"phase": "pre-Step32" if pre else "post-Step32", "current_step": current, "g9": gates.get("G9_FUNCTIONAL_SUPPORT"), "blocked": state.get("blocked")}


def _matrix(require_live: bool) -> tuple[dict[str, object], list[str]]:
    missing = []
    databases: dict[str, object] = {}
    for label, env_names in REQUIRED_DATABASES.items():
        configured = all(os.environ.get(name) for name in env_names)
        databases[label] = "LIVE_CI_REQUIRED" if configured else "UNTESTED_NOT_CONFIGURED"
        if require_live and not configured:
            missing.append(label)
    return databases, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Step32 compatibility matrix")
    parser.add_argument("--ci", action="store_true", help="require all live provider fixtures")
    args = parser.parse_args()
    report: dict[str, object] = {"step": 32, "gate": "G9_FUNCTIONAL_SUPPORT", "mode": "ci" if args.ci else "local", "status": "FAIL"}
    try:
        state = _state()
        databases, missing = _matrix(args.ci)
        if missing:
            raise ValidationFailure(f"required live provider fixtures are missing: {', '.join(missing)}")
        command = [sys.executable, "-m", "pytest", "-q", "tests/compatibility/test_step32_compatibility.py"]
        result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
        combined = (result.stdout + "\n" + result.stderr).strip()
        passed = _count(combined, "passed")
        skipped = _count(combined, "skipped")
        failed = _count(combined, "failed")
        report.update(
            {
                "status": "PASS" if result.returncode == 0 and failed == 0 and (not args.ci or passed >= 8) else "FAIL",
                "state": state,
                "claim_inventory": {
                    "current_product_boundary": "managed CSV import; Step29 real product path",
                    "source_adapter_boundary": "SQLite, PostgreSQL, MySQL, MariaDB, SQL Server, CSV, Parquet, optional XLSX",
                    "deferred": "Oracle",
                },
                "database_matrix": databases,
                "file_matrix": {"CSV": "REFERENCE_TESTED", "Parquet": "REFERENCE_TESTED", "Excel": "REFERENCE_TESTED"},
                "runtime_matrix": {"python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "runner": "repository pytest source-boundary tests"},
                "tests": {"command": command, "returncode": result.returncode, "passed": passed, "skipped": skipped, "failed": failed},
                "skipped_or_untested": missing or (["PostgreSQL", "MySQL", "MariaDB", "SQL Server"] if skipped else []),
            }
        )
        if report["status"] != "PASS":
            raise ValidationFailure("Step32 compatibility tests did not satisfy the required matrix")
    except Exception as error:
        report["error"] = str(error)
        if "tests" not in report:
            report["tests"] = {"command": "not executed"}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "error": report["error"]}, ensure_ascii=False))
        return 1
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "phase": state["phase"], "passed": passed, "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
