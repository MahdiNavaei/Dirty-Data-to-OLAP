"""Validate the Step29 frontend contract and the real browser product path."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
OUTPUT = ROOT / "output" / "playwright" / "step29-g7-validator"
REPORT_PATH = ROOT / "output" / "step29_frontend_validation.json"
FIXTURE = FRONTEND / "e2e" / "fixtures" / "orders.csv"


def run_checked(label: str, command: list[str], *, cwd: Path, timeout: int = 120) -> str:
    print(f"[step29] {label}")
    result = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
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


class BrowserCli:
    def __init__(self, output_dir: Path) -> None:
        cli = shutil.which("playwright-cli.cmd") or shutil.which("playwright-cli")
        if cli is None:
            raise RuntimeError("playwright-cli is required for the real G7 browser check")
        self.cli = cli
        self.output_dir = output_dir
        self.session = f"step29-g7-{os.getpid()}"
        self.log_path = output_dir / "browser-cli.log"

    def run(self, *args: str, timeout: int = 45) -> str:
        command = [self.cli, "--session", self.session, *args]
        result = subprocess.run(command, cwd=self.output_dir, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
        output = (result.stdout + result.stderr).strip()
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"$ {' '.join(command)}\n{output}\n\n")
        if result.returncode != 0:
            raise RuntimeError(f"browser command failed: {' '.join(args)}: {output[-3000:]}")
        return output

    def close(self) -> None:
        try:
            self.run("close", timeout=20)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass

    def run_code(self, code: str, *, timeout: int = 45) -> str:
        return self.run("run-code", code, timeout=timeout)

    def snapshot(self) -> str:
        return self.run("snapshot")

    def state(self) -> dict[str, str | None]:
        raw = self.run(
            "--raw",
            "eval",
            "JSON.stringify({status: document.querySelector('[data-testid=run-status]')?.textContent?.trim() ?? null, checkpoint: document.querySelector('.checkpoint')?.textContent?.trim() ?? null, reviewState: document.querySelector('.review-object .state-chip')?.textContent?.trim() ?? null})",
        )
        result_line = next((line.strip() for line in reversed(raw.splitlines()) if line.strip().startswith('"') and line.strip().endswith('"')), None)
        if result_line is None:
            raise RuntimeError(f"browser state did not return JSON: {raw[-1000:]}")
        value = json.loads(result_line)
        return json.loads(value) if isinstance(value, str) else value


def validate_browser() -> dict[str, object]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    browser = BrowserCli(OUTPUT)
    api_process: subprocess.Popen[str] | None = None
    frontend_process: subprocess.Popen[str] | None = None
    temporary_directory: str | None = None
    api_log = (OUTPUT / "api.log").open("w", encoding="utf-8")
    frontend_log = (OUTPUT / "frontend.log").open("w", encoding="utf-8")
    accepted: list[str] = []
    expected = [
        "REVIEW_EVIDENCE_DECISIONS",
        "REVIEW_CANONICAL_IDENTITY",
        "REVIEW_ANALYTICAL_PLAN",
        "REVIEW_MATERIALIZATION_PLAN",
    ]
    try:
        temporary_directory = tempfile.mkdtemp(prefix="step29-g7-runtime-")
        temporary = temporary_directory
        if temporary:
            api_process = subprocess.Popen(
                [sys.executable, str(ROOT / "tools" / "run_step29_local.py"), "--root", temporary, "--graph-root", str(ROOT), "--port", "8765"],
                cwd=ROOT,
                stdout=api_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_for_http("http://127.0.0.1:8765/api/v1/health")
            node = shutil.which("node.exe") or shutil.which("node")
            vite_entrypoint = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
            if node is None or not vite_entrypoint.is_file():
                raise RuntimeError("the installed Vite runtime is required for the frontend preview")
            frontend_process = subprocess.Popen(
                [node, str(vite_entrypoint), "preview", "--host", "127.0.0.1", "--port", "4173"],
                cwd=FRONTEND,
                stdout=frontend_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_for_http("http://127.0.0.1:4173/")

            browser.run("open", "http://127.0.0.1:4173/")
            browser.snapshot()
            browser.run_code(
                "async () => { const checks = await page.evaluate(() => ({label: Boolean(document.querySelector('label[for=source-file]')), project: Boolean(document.querySelector('#project-id')), nav: Boolean(document.querySelector('nav[aria-label=\"Primary navigation\"]'))})); if (!checks.label) throw new Error('source input is not labelled'); if (!checks.project) throw new Error('project context input is missing'); if (!checks.nav) throw new Error('primary navigation landmark is missing'); }"
            )
            fixture_path = json.dumps(str(FIXTURE.resolve()).replace("\\", "/"))
            browser.run_code(f"async () => {{ await page.locator('#source-file').setInputFiles({fixture_path}); }}")
            browser.snapshot()
            browser.run_code(
                "async () => { await page.getByRole('button', {name: 'Import and register'}).click(); await page.getByRole('status').filter({hasText: 'Source imported'}).waitFor({state: 'visible'}); }"
            )
            browser.snapshot()
            browser.run_code(
                "async () => { await page.getByRole('button', {name: 'Create run and start discovery'}).click(); await page.waitForURL(/\\/runs\\//); }"
            )
            browser.snapshot()

            deadline = time.monotonic() + 110
            while time.monotonic() < deadline:
                state = browser.state()
                checkpoint = state.get("checkpoint")
                if checkpoint and checkpoint not in accepted:
                    browser.snapshot()
                    browser.run_code("async () => { await page.getByRole('button', {name: /Accept and resume/}).first().click(); }")
                    accepted.append(checkpoint)
                    browser.snapshot()
                if state.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.35)

            final_state = browser.state()
            if final_state.get("status") != "SUCCEEDED":
                raise RuntimeError(f"browser product path did not succeed: {final_state}")
            if accepted != expected:
                raise RuntimeError(f"browser accepted checkpoint sequence was {accepted}, expected {expected}")

            browser.snapshot()
            browser.run("screenshot")
            browser.run_code(
                "async () => { const body = await page.locator('body').innerText(); const forbidden = [/\\bSELECT\\b/i, /\\bCREATE\\s+TABLE\\b/i, /\\bINSERT\\s+INTO\\b/i, /workspace[\\\\/]platform/i, /workspace[\\\\/]runs/i, /file_locator/i, /O-100/i, /C-1/i, /10\\.50/i]; if (forbidden.some((pattern) => pattern.test(body))) throw new Error('browser projection exposed raw SQL, path, or source-row content'); if (!/G6\\s+PASS[^\\n]*eligible/i.test(body) || !/Validated OLAP output available/i.test(body)) throw new Error('final product projection does not visibly prove G6 and output readiness'); const urls = await page.evaluate(() => performance.getEntriesByType('resource').map((entry) => entry.name)); if (urls.some((url) => /\\.sqlite|\\.duckdb|workspace[\\\\/]/i.test(url))) throw new Error('browser made a direct storage request'); const headings = await page.locator('h1, h2, h3').count(); const caption = await page.locator('table caption').count(); if (headings < 8 || !caption) throw new Error('final product projection is missing accessible structure'); }"
            )
            console_output = browser.run("console", "error")
            if "Errors: 0" not in console_output:
                raise RuntimeError(f"browser console errors were reported: {console_output[-2000:]}")
            requests_output = browser.run("requests", "--static")
            if any(token in requests_output.casefold() for token in (".sqlite", ".duckdb", "workspace/")):
                raise RuntimeError("browser network evidence contains a direct storage or workspace request")
            return {
                "status": "PASS",
                "final_status": final_state["status"],
                "accepted_checkpoints": accepted,
                "expected_checkpoints": expected,
                "negative_controls": "PASS",
                "accessibility_controls": "PASS",
                "console_errors": "0",
                "evidence_dir": str(OUTPUT.relative_to(ROOT)).replace("\\", "/"),
            }
    finally:
        browser.close()
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
            ("Focused backend product test", [sys.executable, "-m", "pytest", "-q", "tests/integration/test_step29_product_path.py"]),
        ):
            run_checked(label, command, cwd=ROOT, timeout=180)
        for script in ("typecheck", "lint", "test", "build"):
            run_checked(f"Frontend {script}", ["npm.cmd", "run", script, "--", "--run"] if script == "test" else ["npm.cmd", "run", script], cwd=FRONTEND, timeout=180)
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
