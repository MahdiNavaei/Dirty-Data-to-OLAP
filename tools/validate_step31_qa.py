"""Run the independent Step31 QA matrix against one isolated product stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "output" / "step31_qa_validation.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ValidationFailure(RuntimeError):
    pass


def port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def run(label: str, command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    print(f"[step31] {label}: {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise ValidationFailure(f"{label} failed with exit code {result.returncode}: {detail[-4000:]}")
    return result


def wait_http(url: str, *, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (OSError, URLError):
            time.sleep(0.5)
    raise ValidationFailure(f"HTTP readiness deadline expired: {url}")


def state_check() -> dict[str, str]:
    import yaml

    from tools.execution_state import step29_g7_closed, step30_g8_closed, step31_external_ci_blocked, step31_qa_closed

    state = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    execution = state["specialist_execution"]
    gates = state["gates"]
    step31_handoff = step30_g8_closed(state)
    step31_closed = step31_qa_closed(state)
    step31_blocked = step31_external_ci_blocked(state)
    if not step29_g7_closed(state) or not (step31_handoff or step31_closed or step31_blocked):
        raise ValidationFailure("Step30/G8 or Step29/G7 state is not a coherent Step31 handoff or accepted Step31 closure")
    if gates.get("G6_DATA_CORRECTNESS") != "PASS" or gates.get("G7_END_TO_END_PRODUCT") != "PASS" or gates.get("G8_REPRODUCIBLE_BUILD") != "PASS":
        raise ValidationFailure("G6, G7 and G8 must remain PASS before Step31 QA")
    if not step31_handoff and not step31_closed and not step31_blocked:
        raise ValidationFailure("authoritative pointer is neither the Step31 QA handoff nor the accepted Step31 closure")
    if step31_blocked:
        qa = execution.get("step31_qa_automation", {})
        return {
            "status": "BLOCKED_EXTERNAL",
            "phase": "final-head-ci-blocked",
            "current_step": str(execution["current_step"]),
            "g6": gates["G6_DATA_CORRECTNESS"],
            "g7": gates["G7_END_TO_END_PRODUCT"],
            "g8": gates["G8_REPRODUCIBLE_BUILD"],
            "final_head": str(qa.get("final_head", "")),
            "final_head_ci_run": str(qa.get("final_head_ci_run", "")),
        }
    if step31_handoff:
        if execution.get("step31_started") is not False or execution.get("step31_status") != "NOT_STARTED":
            raise ValidationFailure("Step31 must start from NOT_STARTED")
        if execution.get("current_step") != 31 or execution.get("current_role") != "qa_automation_engineer":
            raise ValidationFailure("authoritative pointer is not the Step31 QA handoff")
        phase = "pre-Step31"
    else:
        phase = "post-Step31"
    return {"status": "PASS", "phase": phase, "current_step": str(execution["current_step"]), "g6": gates["G6_DATA_CORRECTNESS"], "g7": gates["G7_END_TO_END_PRODUCT"], "g8": gates["G8_REPRODUCIBLE_BUILD"]}


def frontend_contract() -> dict[str, str]:
    frontend = ROOT / "frontend"
    tracked = frontend / "openapi.json"
    generated_types = frontend / "src" / "api" / "generated.ts"
    before = hashlib.sha256(tracked.read_bytes()).hexdigest()
    types_before = hashlib.sha256(generated_types.read_bytes()).hexdigest()
    npm = "npm.cmd" if os.name == "nt" else "npm"
    npx = "npx.cmd" if os.name == "nt" else "npx"
    run("frontend locked install", [npm, "ci"], cwd=frontend, timeout=900)
    run("frontend deterministic API generation", [npm, "run", "generate:api"], cwd=frontend, timeout=300)
    after = hashlib.sha256(tracked.read_bytes()).hexdigest()
    if before != after:
        raise ValidationFailure("frontend generated OpenAPI changed during Step31 validation")
    types_after = hashlib.sha256(generated_types.read_bytes()).hexdigest()
    if types_before != types_after:
        raise ValidationFailure("frontend generated API types changed during Step31 validation")
    return {
        "status": "PASS",
        "openapi_sha256": after,
        "generated_types_sha256": types_after,
        "playwright_runner": f"{npx} --no-install playwright",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Step31 QA validator")
    parser.add_argument("--ci", action="store_true", help="record CI execution mode")
    args = parser.parse_args()
    checks: list[dict[str, object]] = []
    report: dict[str, object] = {"step": 31, "validator": "independent-system-qa", "mode": "ci" if args.ci else "local", "status": "FAIL", "checks": checks}
    project = f"ddo-step31-{os.getpid()}"
    backend_port = port()
    frontend_port = port()
    while frontend_port == backend_port:
        frontend_port = port()
    compose = ["docker", "compose", "-p", project, "-f", "compose.yml"]
    env = {**os.environ, "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port), "STEP31_BASE_URL": f"http://127.0.0.1:{frontend_port}", "STEP31_COMPOSE_PROJECT": project, "STEP31_COMPOSE_FILE": "compose.yml"}
    npm = "npm.cmd" if os.name == "nt" else "npm"
    npx = "npx.cmd" if os.name == "nt" else "npx"
    started = False
    try:
        preflight = state_check()
        checks.append({"name": "authoritative Step31 preflight", **preflight})
        if preflight["status"] == "BLOCKED_EXTERNAL":
            if not args.ci:
                report["status"] = "BLOCKED_EXTERNAL"
                report["blocker"] = "GitHub Actions billing/spending-limit restriction"
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2
            checks[-1]["status"] = "PASS"
            checks[-1]["phase"] = "resuming-final-head-ci"
            checks[-1]["prior_status"] = "BLOCKED_EXTERNAL"
            report["resuming_external_final_ci"] = True
        checks.append({"name": "frontend contract", **frontend_contract()})
        run("Compose configuration", [*compose, "config", "--quiet"], env=env, timeout=120)
        run("reference backend/frontend image build", [*compose, "build", "backend", "frontend"], env=env, timeout=5400)
        run("isolated Compose startup", [*compose, "up", "-d", "backend", "frontend"], env=env, timeout=300)
        started = True
        backend_url = f"http://127.0.0.1:{backend_port}"
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        wait_http(f"{backend_url}/api/v1/health")
        wait_http(frontend_url)
        checks.append({"name": "isolated stack readiness", "status": "PASS", "backend_url": backend_url, "frontend_url": frontend_url})
        runtime_python = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        pytest_command = ["uv", "run", "--locked", "--python", runtime_python, "--group", "dev", "--extra", "api", "--extra", "files", "--extra", "profiling", "python", "-m", "pytest", "-q", "tests/system", "tests/api"]
        run("independent API/system QA matrix", pytest_command, env=env, timeout=2400)
        checks.append({"name": "API/system QA matrix", "status": "PASS", "suite": "tests/system + tests/api"})
        run("fresh browser QA stack cleanup", [*compose, "down", "--volumes", "--remove-orphans"], env=env, timeout=300)
        run("fresh browser QA stack startup", [*compose, "up", "-d", "backend", "frontend"], env=env, timeout=300)
        wait_http(f"{backend_url}/api/v1/health")
        wait_http(frontend_url)
        checks.append({"name": "fresh browser QA stack", "status": "PASS", "scope": f"Compose project {project} with a new named volume"})
        browser_path = ROOT / "output" / "playwright" / "step31-browsers"
        browser_env = {**env, "PLAYWRIGHT_BASE_URL": frontend_url, "PLAYWRIGHT_BROWSERS_PATH": str(browser_path)}
        run("repository-owned Chromium install", [npx, "--no-install", "playwright", "install", "chromium"], cwd=ROOT / "frontend", env=browser_env, timeout=1200)
        run("accepted Step29 G7 browser regression", [npx, "--no-install", "playwright", "test", "--config=playwright.config.ts", "e2e/step29_product_path.spec.ts"], cwd=ROOT / "frontend", env=browser_env, timeout=1200)
        checks.append({"name": "G7 browser regression", "status": "PASS", "spec": "frontend/e2e/step29_product_path.spec.ts"})
        run("fresh Step31 browser scenario cleanup", [*compose, "down", "--volumes", "--remove-orphans"], env=env, timeout=300)
        run("fresh Step31 browser scenario startup", [*compose, "up", "-d", "backend", "frontend"], env=env, timeout=300)
        wait_http(f"{backend_url}/api/v1/health")
        wait_http(frontend_url)
        checks.append({"name": "fresh Step31 browser scenario stack", "status": "PASS", "scope": f"Compose project {project} with a new named volume after G7 regression"})
        browser_command = [npx, "--no-install", "playwright", "test", "--config=playwright.config.ts", "e2e/step31_qa.spec.ts"]
        run("independent browser review scenario", [*browser_command, "--grep", "review state survives reload"], cwd=ROOT / "frontend", env=browser_env, timeout=600)
        checks.append({"name": "browser review scenario", "status": "PASS", "spec": "frontend/e2e/step31_qa.spec.ts"})
        run("browser scenario runtime boundary restart", [*compose, "restart", "backend"], env=env, timeout=180)
        wait_http(f"{backend_url}/api/v1/health", timeout=120)
        checks.append({"name": "browser scenario runtime boundary", "status": "PASS", "detail": "backend restarted before the independent failure scenario"})
        run("independent browser failure scenario", [*browser_command, "--grep", "failure path keeps duplicate"], cwd=ROOT / "frontend", env=browser_env, timeout=600)
        checks.append({"name": "browser failure scenario", "status": "PASS", "spec": "frontend/e2e/step31_qa.spec.ts"})
        report["status"] = "PASS"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:2000]
        raise
    finally:
        cleanup_failure: str | None = None
        if started:
            cleanup = subprocess.run([*compose, "down", "--volumes", "--remove-orphans"], cwd=ROOT, env=env, text=True, capture_output=True, timeout=300, check=False)
            remaining = subprocess.run([*compose, "ps", "-q"], cwd=ROOT, env=env, text=True, capture_output=True, timeout=60, check=False)
            if cleanup.returncode or remaining.stdout.strip():
                cleanup_failure = "QA Compose cleanup did not remove the owned project completely"
                report["status"] = "FAIL"
                report["cleanup_error"] = cleanup_failure
            checks.append({
                "name": "cleanup contract",
                "status": "PASS" if cleanup_failure is None else "FAIL",
                "scope": f"Compose project {project} and its named volume only",
            })
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if cleanup_failure is not None:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise ValidationFailure(cleanup_failure)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
