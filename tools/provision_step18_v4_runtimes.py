"""Provision persistent, isolated Step18 v4 provider runtimes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "workspace" / "test-temp" / "step18-runtimes"
WHEELHOUSE = ROOT / "workspace" / "test-temp" / "step18-wheelhouse"
REPORT = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "provisioning" / "provisioning.json"
KNOWN_PROXY = ("127.0.0.1:10809", "localhost:10809")
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

RUNTIMES = (
    {"runtime": "matching-venv", "package_name": "valentine", "module_name": "valentine", "expected_version": "1.0.0", "dependencies": ("valentine==1.0.0", "pyarrow>=16,<23", "pydantic>=2.12,<3")},
    {"runtime": "er-venv", "package_name": "splink", "module_name": "splink", "expected_version": "4.0.17", "dependencies": ("splink==4.0.17", "pyarrow>=16,<23", "pydantic>=2.12,<3")},
)


def _child_environment(runtime: Path) -> tuple[dict[str, str], dict[str, object]]:
    env = os.environ.copy()
    present = any(env.get(key) for key in PROXY_KEYS)
    removed = False
    for key in PROXY_KEYS:
        value = env.get(key, "")
        if any(marker in value for marker in KNOWN_PROXY):
            env.pop(key, None)
            removed = True
    temp = runtime / "temp"
    cache = runtime / "pip-cache"
    temp.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    env.update({"TEMP": str(temp), "TMP": str(temp), "PIP_CACHE_DIR": str(cache), "PYTHONNOUSERSITE": "1"})
    return env, {"proxy_present": present, "proxy_values_redacted": True, "known_proxy_removed_from_child": removed}


def _run(command: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def _redacted(value: str, env: dict[str, str]) -> str:
    result = value
    for key in PROXY_KEYS:
        if env.get(key):
            result = result.replace(env[key], "<redacted-proxy>")
    return result[-12000:]


def _host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlparse(value).hostname
    except ValueError:
        return None


def _failure_category(output: str) -> str:
    text = output.lower()
    if "readtimeout" in text or "timed out" in text or "timeout" in text:
        return "NETWORK_TIMEOUT"
    if "could not fetch url" in text or ("connection" in text and "failed" in text):
        return "INDEX_UNREACHABLE"
    if "installing build dependencies" in text or "setuptools>=" in text:
        return "BUILD_DEPENDENCY_UNAVAILABLE"
    if "no matching distribution" in text or "could not find a version" in text:
        return "PACKAGE_NOT_FOUND"
    return "PROVIDER_IMPORT_FAILED"


def _provision(spec: dict[str, object]) -> dict[str, object]:
    runtime = RUNTIME_ROOT / str(spec["runtime"])
    python = runtime / "Scripts" / "python.exe"
    pip = runtime / "Scripts" / "pip.exe"
    runtime.mkdir(parents=True, exist_ok=True)
    env, proxy = _child_environment(runtime)
    index_url = env.get("PIP_INDEX_URL") or env.get("PIP_EXTRA_INDEX_URL")
    record: dict[str, object] = {"runtime": spec["runtime"], "path": runtime.relative_to(ROOT).as_posix(), "package_name": spec["package_name"], "module_name": spec["module_name"], "expected_version": spec["expected_version"], "dependencies": list(spec["dependencies"]), "wheelhouse": WHEELHOUSE.relative_to(ROOT).as_posix(), "wheelhouse_present": WHEELHOUSE.is_dir(), "pip_index_host": _host(index_url), **proxy}
    if not python.is_file():
        created = _run([sys.executable, "-m", "venv", str(runtime)], env=env, cwd=ROOT)
        if created.returncode:
            record.update({"status": "FAILED", "phase": "venv", "command_return_code": created.returncode, "failure_category": "BUILD_DEPENDENCY_UNAVAILABLE", "error": _redacted(created.stderr, env)})
            return record
    version = _run([str(python), "-VV"], env=env, cwd=ROOT)
    pip_version = _run([str(pip), "--version"], env=env, cwd=ROOT)
    record.update({"python_version": version.stdout.strip() or version.stderr.strip(), "pip_version": pip_version.stdout.strip(), "interpreter_path_relative": python.relative_to(ROOT).as_posix(), "pip_path_relative": pip.relative_to(ROOT).as_posix()})
    command = [str(pip), "install"]
    if WHEELHOUSE.is_dir():
        command.extend(["--no-index", "--find-links", str(WHEELHOUSE)])
    command.extend(str(item) for item in spec["dependencies"])
    install = _run(command, env=env, cwd=ROOT)
    if install.returncode:
        combined = install.stdout + "\n" + install.stderr
        record.update({"status": "FAILED", "phase": "install", "command_return_code": install.returncode, "failure_category": _failure_category(combined), "resolver_error": _redacted(install.stderr, env), "stdout_tail": _redacted(install.stdout, env)})
        return record
    check_code = "import importlib, importlib.metadata as m, json; package = %r; module = %r; expected = %r; imported = importlib.import_module(module); print(json.dumps({'package_version': m.version(package), 'module_file': getattr(imported, '__file__', None), 'version_matches': m.version(package) == expected}))" % (spec["package_name"], spec["module_name"], spec["expected_version"])
    check = _run([str(python), "-c", check_code], env=env, cwd=ROOT)
    record["command_return_code"] = check.returncode
    if check.returncode:
        record.update({"status": "FAILED", "phase": "import_check", "failure_category": "PROVIDER_IMPORT_FAILED", "verification_error": _redacted(check.stderr, env)})
        return record
    verified = json.loads(check.stdout)
    module_file = Path(verified["module_file"]).resolve()
    record.update({"provider_version": verified["package_version"], "module_location_relative": module_file.relative_to(runtime.resolve()).as_posix(), "import_succeeded": True, "import_version_check": bool(verified["version_matches"]), "status": "READY" if verified["version_matches"] else "FAILED"})
    if record["status"] != "READY":
        record["failure_category"] = "PROVIDER_IMPORT_FAILED"
    return record


def main() -> int:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    records = [_provision(spec) for spec in RUNTIMES]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"schema_version": "5", "runtime_root": RUNTIME_ROOT.relative_to(ROOT).as_posix(), "records": records}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0 if all(item.get("status") == "READY" for item in records) else 2


if __name__ == "__main__":
    raise SystemExit(main())
