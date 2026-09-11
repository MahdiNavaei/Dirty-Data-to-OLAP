"""Provision persistent, isolated Step18 v4 provider runtimes."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "workspace" / "test-temp" / "step18-runtimes"
REPORT = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "provisioning" / "provisioning.json"
KNOWN_PROXY = ("127.0.0.1:10809", "localhost:10809")
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


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


def _provision(name: str, extra: str, expected: str) -> dict[str, object]:
    runtime = RUNTIME_ROOT / name
    python = runtime / "Scripts" / "python.exe"
    runtime.mkdir(parents=True, exist_ok=True)
    env, proxy = _child_environment(runtime)
    record: dict[str, object] = {"runtime": name, "path": runtime.relative_to(ROOT).as_posix(), "expected_provider": expected, "extra": extra, **proxy}
    if not python.is_file():
        created = _run([sys.executable, "-m", "venv", str(runtime)], env=env, cwd=ROOT)
        if created.returncode:
            record.update({"status": "FAILED", "phase": "venv", "error": _redacted(created.stderr, env)})
            return record
    version = _run([str(python), "-VV"], env=env, cwd=ROOT)
    record["python_version"] = version.stdout.strip() or version.stderr.strip()
    install = _run([str(python), "-m", "pip", "install", "-e", f".[{extra}]"], env=env, cwd=ROOT)
    if install.returncode:
        record.update({"status": "FAILED", "phase": "install", "resolver_error": _redacted(install.stderr, env), "stdout_tail": _redacted(install.stdout, env)})
        return record
    check = _run([str(python), "-c", "import importlib.metadata as m; print(m.version('" + expected + "'))"], env=env, cwd=ROOT)
    record["provider_version"] = check.stdout.strip()
    record["import_version_check"] = check.returncode == 0 and check.stdout.strip() == expected
    record["status"] = "READY" if record["import_version_check"] else "FAILED"
    if check.returncode:
        record["verification_error"] = _redacted(check.stderr, env)
    return record


def main() -> int:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    records = [_provision("matching-venv", "matching,files,test", "valentine"), _provision("er-venv", "entity_resolution,files,test", "splink")]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"schema_version": "4", "runtime_root": RUNTIME_ROOT.relative_to(ROOT).as_posix(), "records": records}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0 if all(item.get("status") == "READY" for item in records) else 2


if __name__ == "__main__":
    raise SystemExit(main())
