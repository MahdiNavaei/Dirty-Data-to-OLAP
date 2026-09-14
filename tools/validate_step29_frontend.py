"""Validate the Step29 frontend contract and the real browser product path."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FRONTEND = ROOT / "frontend"
OUTPUT = ROOT / "output" / "playwright" / "step29-g7-validator"
REPORT_PATH = ROOT / "output" / "step29_frontend_validation.json"
FIXTURE = FRONTEND / "e2e" / "fixtures" / "orders.csv"


def required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"required executable is not available: {name}")
    return executable


def run_checked(label: str, command: list[str], *, cwd: Path, timeout: int = 120, env: dict[str, str] | None = None) -> str:
    print(f"[step29] {label}")
    result = subprocess.run(command, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}: {output[-3000:]}")
    return output


def wait_for_http(url: str, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError(f"server did not become reachable: {url}")


def reserve_local_port() -> int:
    """Select an ephemeral loopback port so validation is not tied to a host reservation."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def validate_real_pipeline() -> dict[str, str]:
    """Run the real-provider product test, including typed provenance assertions."""

    from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordantePythonEngine

    if DesbordantePythonEngine.try_create() is None:
        return {
            "status": "SKIPPED_OPTIONAL_PROVIDER_UNAVAILABLE",
            "provider_path": "native Desbordante binding is not installed in this host environment",
            "typed_provenance": "NOT_RUN",
            "negative_control": "NOT_RUN",
        }
    run_checked(
        "Real pipeline provenance and negative controls",
        [sys.executable, "-m", "pytest", "-q", "tests/integration/test_step29_product_path.py"],
        cwd=ROOT,
        timeout=300,
    )
    return {
        "status": "PASS",
        "provider_path": "DataProfilerAdapter + DesbordanteDependencyAdapter + staged quality reader",
        "typed_provenance": "PASS",
        "negative_control": "duplicate order_id rejected after upload at dependency boundary",
    }


def validate_browser() -> dict[str, object]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordantePythonEngine

    if DesbordantePythonEngine.try_create() is None:
        return {
            "status": "SKIPPED_OPTIONAL_PROVIDER_UNAVAILABLE",
            "runner": "repository-owned @playwright/test via npx --no-install",
            "detail": "the mandatory containerized G7 path owns provider reconstruction",
        }
    api_process: subprocess.Popen[str] | None = None
    frontend_process: subprocess.Popen[str] | None = None
    temporary_directory: str | None = None
    api_log = (OUTPUT / "api.log").open("w", encoding="utf-8")
    frontend_log = (OUTPUT / "frontend.log").open("w", encoding="utf-8")
    try:
        temporary_directory = tempfile.mkdtemp(prefix="step29-g7-runtime-")
        temporary = temporary_directory
        if temporary:
            api_port = reserve_local_port()
            frontend_port = reserve_local_port()
            api_url = f"http://127.0.0.1:{api_port}"
            frontend_url = f"http://127.0.0.1:{frontend_port}"
            api_process = subprocess.Popen(
                [sys.executable, str(ROOT / "tools" / "run_step29_local.py"), "--root", temporary, "--graph-root", str(ROOT), "--port", str(api_port)],
                cwd=ROOT,
                stdout=api_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_for_http(f"{api_url}/api/v1/health")
            npm = required_executable("npm")
            node = required_executable("node")
            npx = required_executable("npx")
            run_checked("Frontend browser-port build", [npm, "run", "build"], cwd=FRONTEND, timeout=180, env={**os.environ, "VITE_API_BASE_URL": api_url})
            vite_entrypoint = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
            if not vite_entrypoint.is_file():
                raise RuntimeError("the installed Vite runtime is required for the frontend preview")
            frontend_process = subprocess.Popen(
                [node, str(vite_entrypoint), "preview", "--host", "127.0.0.1", "--port", str(frontend_port)],
                cwd=FRONTEND,
                env={**os.environ, "VITE_API_BASE_URL": api_url},
                stdout=frontend_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_for_http(frontend_url + "/")
            browser_path = OUTPUT / "playwright-browsers"
            browser_path.mkdir(parents=True, exist_ok=True)
            browser_env = {
                **os.environ,
                "PLAYWRIGHT_BASE_URL": frontend_url,
                "PLAYWRIGHT_BROWSERS_PATH": str(browser_path),
            }
            run_checked("Repository-owned Playwright browser install", [npx, "--no-install", "playwright", "install", "chromium"], cwd=FRONTEND, timeout=300, env=browser_env)
            run_checked("Repository-owned Playwright Step29 product path", [npx, "--no-install", "playwright", "test", "--config=playwright.config.ts"], cwd=FRONTEND, timeout=360, env=browser_env)
            return {
                "status": "PASS",
                "runner": "repository-owned @playwright/test via npx --no-install",
                "negative_controls": "PASS (asserted by e2e spec)",
                "accessibility_controls": "PASS (asserted by e2e spec)",
                "evidence_dir": str(OUTPUT.relative_to(ROOT)).replace("\\", "/"),
            }
    finally:
        for process in (frontend_process, api_process):
            if process is None or process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)
        api_log.close()
        frontend_log.close()
        if temporary_directory is not None:
            shutil.rmtree(temporary_directory, ignore_errors=True)


def main() -> int:
    report: dict[str, object] = {"step": 29, "validator": "frontend-and-g7-browser"}
    try:
        run_checked("OpenAPI generation", [sys.executable, "tools/generate_step29_openapi.py", "--output", str(FRONTEND / "openapi.json")], cwd=ROOT)
        for label, command in (
            ("Python compile", [sys.executable, "-m", "compileall", "-q", "src", "tools"]),
            ("Focused backend architecture test", [sys.executable, "-m", "pytest", "-q", "tests/integration/test_step29_product_path.py::test_step29_runtime_is_a_thin_service_composition_root"]),
        ):
            run_checked(label, command, cwd=ROOT, timeout=180)
        report["real_pipeline"] = validate_real_pipeline()
        npm = required_executable("npm")
        for script in ("typecheck", "lint", "test", "build"):
            run_checked(f"Frontend {script}", [npm, "run", script, "--", "--run"] if script == "test" else [npm, "run", script], cwd=FRONTEND, timeout=180)
        report["browser"] = validate_browser()
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:1000]
        raise
    finally:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
