"""Canonical, bounded developer workflow for Dirty Data to OLAP.

The commands in this module are intentionally thin orchestration around the
accepted product composition root and repository validators.  They do not
implement a second data-processing engine and they keep environments, caches,
demo state, and disposable validation output inside the repository.
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence


PACKAGE_VERSION = "0.1.0"
PINNED_PYTHON = "3.11.16"
PINNED_NODE = "22.14.0"
PINNED_UV = "0.11.26"
PROFILE_EXTRAS: dict[str, tuple[str, ...]] = {
    "core": ("api", "files"),
    "api": ("api",),
    "matching": ("matching",),
    "profiling": ("profiling",),
    "entity_resolution": ("entity_resolution",),
    "ml": ("ml",),
}


class DevxFailure(RuntimeError):
    """An actionable developer-workflow failure."""


def repository_root(value: str | Path | None = None) -> Path:
    if value is not None:
        root = Path(value).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file():
        raise DevxFailure(f"repository root is missing pyproject.toml: {root}")
    return root


def _read_pin(root: Path, filename: str, fallback: str) -> str:
    path = root / filename
    if not path.is_file():
        return fallback
    value = path.read_text(encoding="utf-8").strip()
    return value or fallback


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _command_name(root: Path, name: str) -> str:
    if os.name == "nt" and name in {"npm", "npx"}:
        name = f"{name}.cmd"
    return str(shutil.which(name) or name)


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DevxFailure(f"command timed out after {timeout}s: {' '.join(command)}") from exc
    except OSError as exc:
        raise DevxFailure(f"could not execute {' '.join(command)}: {exc}") from exc


def _version(command: Sequence[str], *, cwd: Path, timeout: int = 20) -> tuple[str | None, str | None]:
    try:
        result = _run(command, cwd=cwd, timeout=timeout)
    except DevxFailure as exc:
        return None, str(exc)
    output = (result.stdout or result.stderr).strip().splitlines()
    return (output[0] if output else None), None if result.returncode == 0 else f"exit {result.returncode}"


def _python_version(executable: str = sys.executable) -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}" if executable == sys.executable else (_version([executable, "--version"], cwd=Path.cwd())[0] or "unknown").removeprefix("Python ")


def _venv_python(root: Path) -> Path:
    relative = Path(".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return root / relative


def _local_environment(root: Path) -> dict[str, str]:
    local = root / ".ddo"
    env = dict(os.environ)
    env.update(
        {
            "UV_CACHE_DIR": str(local / "cache" / "uv"),
            "UV_PYTHON_INSTALL_DIR": str(local / "python"),
            "UV_PROJECT_ENVIRONMENT": str(root / ".venv"),
            "npm_config_cache": str(local / "cache" / "npm"),
        }
    )
    return env


def _profile_extras(profile: str) -> tuple[str, ...]:
    try:
        return PROFILE_EXTRAS[profile]
    except KeyError as exc:
        raise DevxFailure(f"unknown profile {profile!r}; use: {', '.join(PROFILE_EXTRAS)}") from exc


def command_config(root: Path) -> dict[str, Any]:
    return {
        "package_version": PACKAGE_VERSION,
        "root": str(root),
        "python_pin": _read_pin(root, ".python-version", PINNED_PYTHON),
        "node_pin": _read_pin(root, ".node-version", PINNED_NODE),
        "uv_pin": PINNED_UV,
        "profiles": {name: list(extras) for name, extras in PROFILE_EXTRAS.items()},
        "local_paths": {
            "venv": str(root / ".venv"),
            "state": str(root / ".ddo"),
            "uv_cache": str(root / ".ddo" / "cache" / "uv"),
            "npm_cache": str(root / ".ddo" / "cache" / "npm"),
        },
        "product": {
            "api_contract": "frontend/openapi.json",
            "generated_api_types": "frontend/src/api/generated.ts",
            "serve_entrypoint": "tools/run_step29_local.py",
        },
    }


def doctor(root: Path) -> tuple[dict[str, Any], bool]:
    expected_python = _read_pin(root, ".python-version", PINNED_PYTHON)
    expected_node = _read_pin(root, ".node-version", PINNED_NODE)
    checks: list[dict[str, Any]] = []

    python_actual = _python_version()
    checks.append({"name": "python", "expected": expected_python, "actual": python_actual, "status": "PASS" if python_actual == expected_python else "FAIL"})

    node_command = _command_name(root, "node")
    node_version, node_error = _version([node_command, "--version"], cwd=root)
    node_actual = (node_version or "").removeprefix("v")
    checks.append({"name": "node", "expected": expected_node, "actual": node_actual or node_error or "missing", "status": "PASS" if node_actual == expected_node else "FAIL"})

    npm_command = _command_name(root, "npm")
    npm_version, npm_error = _version([npm_command, "--version"], cwd=root)
    checks.append({"name": "npm", "actual": npm_version or npm_error or "missing", "status": "PASS" if npm_version else "FAIL"})

    uv_command = _command_name(root, "uv")
    uv_version, uv_error = _version([uv_command, "--version"], cwd=root)
    uv_actual = ""
    if uv_version:
        match = re.search(r"uv\s+(\d+(?:\.\d+){1,2})", uv_version)
        uv_actual = match.group(1) if match else uv_version
    checks.append({"name": "uv", "expected": PINNED_UV, "actual": uv_actual or uv_error or "missing", "status": "PASS" if uv_actual == PINNED_UV else "FAIL"})

    required_files = ("pyproject.toml", "uv.lock", ".python-version", ".node-version", "config/product/order_v1.json", "policies/evidence-fusion/mapping_fusion_v1.yml")
    missing = [path for path in required_files if not (root / path).is_file()]
    checks.append({"name": "repository contract", "status": "PASS" if not missing else "FAIL", "missing": missing})
    checks.append({"name": "optional profiles", "status": "PASS", "profiles": sorted(PROFILE_EXTRAS)})

    passed = all(item["status"] == "PASS" for item in checks)
    return {"status": "PASS" if passed else "FAIL", "platform": platform.system(), "checks": checks}, passed


def bootstrap(root: Path, *, profile: str, dry_run: bool) -> int:
    extras = _profile_extras(profile)
    env = _local_environment(root)
    commands: list[list[str]] = [
        [_command_name(root, "uv"), "python", "install", _read_pin(root, ".python-version", PINNED_PYTHON)],
        [_command_name(root, "uv"), "sync", "--locked", "--python", _read_pin(root, ".python-version", PINNED_PYTHON), "--group", "dev", *sum((["--extra", extra] for extra in extras), [])],
        [_command_name(root, "npm"), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
    ]
    plan = {"status": "DRY_RUN" if dry_run else "RUN", "profile": profile, "project_local": True, "commands": [" ".join(command) for command in commands]}
    if dry_run:
        _json_print(plan)
        return 0
    (root / ".ddo" / "cache" / "uv").mkdir(parents=True, exist_ok=True)
    (root / ".ddo" / "cache" / "npm").mkdir(parents=True, exist_ok=True)
    (root / ".ddo" / "python").mkdir(parents=True, exist_ok=True)
    (root / ".venv").mkdir(parents=True, exist_ok=True)
    for label, command, cwd, timeout in (
        ("pinned Python", commands[0], root, 600),
        ("locked Python environment", commands[1], root, 3600),
        ("locked frontend dependencies", commands[2], root / "frontend", 900),
    ):
        result = _run(command, cwd=cwd, env=env, timeout=timeout, capture=False)
        if result.returncode != 0:
            raise DevxFailure(f"{label} failed with exit code {result.returncode}; rerun with the same profile after fixing the reported prerequisite")
    _json_print({**plan, "status": "PASS", "venv": str(_venv_python(root))})
    return 0


def _tracked_python_files(root: Path) -> list[Path]:
    result = _run(["git", "ls-files", "-z", "--", "src", "tools", "tests"], cwd=root, timeout=30)
    if result.returncode != 0:
        raise DevxFailure("git ls-files could not enumerate the supported Python quality surface")
    files = []
    for item in result.stdout.split("\0"):
        if not item or not item.endswith(".py") or item.startswith("tests/quality_unit_artifacts/"):
            continue
        path = root / item
        if path.is_file():
            files.append(path)
    return files


def compile_tracked_python(root: Path) -> dict[str, Any]:
    files = _tracked_python_files(root)
    failures = [str(path.relative_to(root)) for path in files if not compileall.compile_file(str(path), quiet=1, force=False)]
    return {"status": "PASS" if not failures else "FAIL", "files": len(files), "failures": failures}


def demo(root: Path, state_root: Path | None = None) -> int:
    from fastapi.testclient import TestClient

    from dirty_data_to_olap.composition import build_local_backend
    from dirty_data_to_olap.entrypoints.api import create_app

    state = (state_root or (root / ".ddo" / "demo")).resolve()
    platform_obj, backend = build_local_backend(state)
    try:
        with TestClient(create_app(backend)) as client:
            auth = {"X-Local-Principal": "devx-demo"}
            health = client.get("/api/v1/health")
            configuration = client.get("/api/v1/product/configuration", headers=auth)
            if health.status_code != 200 or configuration.status_code != 200:
                raise DevxFailure(f"demo product boundary failed: health={health.status_code}, configuration={configuration.status_code}")
            config = configuration.json()
            created = client.post(
                "/api/v1/runs",
                headers={**auth, "Idempotency-Key": "devx-demo-run-v1"},
                json={"project_id": "devx-demo", "configuration_fingerprint": config["configuration_fingerprint"]},
            )
            if created.status_code != 201:
                raise DevxFailure(f"demo run creation failed: HTTP {created.status_code}")
            run = created.json()
            fetched = client.get(f"/api/v1/runs/{run['run_id']}", headers=auth)
            if fetched.status_code != 200:
                raise DevxFailure(f"demo run read failed: HTTP {fetched.status_code}")
            result = {"status": "PASS", "behavior": "health -> configuration -> run creation -> run read", "health": health.json(), "run_id": run["run_id"], "run_status": run["status"], "state_root": str(state)}
    finally:
        platform_obj.close()
    state.mkdir(parents=True, exist_ok=True)
    output = state / "demo-result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _json_print({**result, "result_file": str(output)})
    return 0


def verify_openapi(root: Path) -> int:
    frontend = root / "frontend"
    if not (frontend / "package.json").is_file():
        raise DevxFailure("frontend/package.json is missing")
    temp_root = root / ".ddo" / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openapi-", dir=temp_root) as directory:
        generated = Path(directory)
        env = _local_environment(root)
        env.update({"DDO_OPENAPI_OUTPUT": str(generated / "openapi.json"), "DDO_GENERATED_TYPES_OUTPUT": str(generated / "generated.ts"), "DDO_PYTHON": sys.executable})
        npm = _command_name(root, "npm")
        result = _run([npm, "run", "generate:api"], cwd=frontend, env=env, timeout=360, capture=True)
        if result.returncode != 0:
            detail = (result.stdout + "\n" + result.stderr).strip()[-2500:]
            raise DevxFailure(f"OpenAPI generation failed without touching tracked outputs (exit {result.returncode}): {detail}")
        comparisons = []
        for tracked, candidate in ((frontend / "openapi.json", generated / "openapi.json"), (frontend / "src" / "api" / "generated.ts", generated / "generated.ts")):
            if not candidate.is_file():
                raise DevxFailure(f"OpenAPI generator did not produce {candidate.name}")
            equal = tracked.read_bytes() == candidate.read_bytes()
            comparisons.append({"file": str(tracked.relative_to(root)), "status": "PASS" if equal else "FAIL", "tracked_sha256": hashlib.sha256(tracked.read_bytes()).hexdigest(), "generated_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()})
        if any(item["status"] != "PASS" for item in comparisons):
            _json_print({"status": "FAIL", "comparisons": comparisons})
            return 1
        _json_print({"status": "PASS", "comparisons": comparisons, "interpreter": sys.executable, "python": _python_version()})
        return 0


def frontend_diagnose(root: Path, *, install: bool, timeout: int) -> int:
    npm = _command_name(root, "npm")
    node = _command_name(root, "node")
    node_version, node_error = _version([node, "--version"], cwd=root)
    npm_version, npm_error = _version([npm, "--version"], cwd=root)
    command = [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund", *([] if install else ["--dry-run"])]
    try:
        result = _run(command, cwd=root / "frontend", env=_local_environment(root), timeout=timeout, capture=True)
        status = "PASS" if result.returncode == 0 else "FAIL"
        detail = (result.stdout + "\n" + result.stderr).strip()[-2000:]
    except DevxFailure as exc:
        result = None
        status = "TIMEOUT_OR_EXECUTION_FAILURE"
        detail = str(exc)
    _json_print({"status": status, "node": node_version or node_error or "missing", "npm": npm_version or npm_error or "missing", "mode": "install" if install else "dry-run", "timeout_seconds": timeout, "command": " ".join(command), "detail": detail})
    return 0 if status == "PASS" else 1


def serve(root: Path, *, host: str, port: int) -> int:
    command = [sys.executable, str(root / "tools" / "run_step29_local.py"), "--root", str(root), "--graph-root", str(root), "--host", host, "--port", str(port)]
    result = _run(command, cwd=root, timeout=2_147_483_647, capture=False)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ddo", description="Dirty Data to OLAP developer workflow")
    parser.add_argument("--root", default=None, help="repository root; defaults to the installed project root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="show package and pinned toolchain versions")
    sub.add_parser("config", help="show safe project configuration and local paths")
    doctor_parser = sub.add_parser("doctor", help="check pinned tools and repository prerequisites")
    doctor_parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    bootstrap_parser = sub.add_parser("bootstrap", help="create project-local Python/frontend environments")
    bootstrap_parser.add_argument("--profile", choices=sorted(PROFILE_EXTRAS), default="core")
    bootstrap_parser.add_argument("--dry-run", action="store_true", help="show bounded commands without executing them")
    check_parser = sub.add_parser("check", help="run lightweight repository checks")
    check_parser.add_argument("--openapi", action="store_true", help="verify generated frontend contract without modifying tracked files")
    check_parser.add_argument("--tests", action="store_true", help="run focused developer-experience tests")
    demo_parser = sub.add_parser("demo", help="exercise the real local product boundary with a deterministic fixture")
    demo_parser.add_argument("--state-root", default=None, help="project-local demo state directory")
    frontend = sub.add_parser("frontend", help="bounded frontend diagnostics")
    frontend_sub = frontend.add_subparsers(dest="frontend_command", required=True)
    diagnose = frontend_sub.add_parser("diagnose", help="run a bounded npm dependency diagnostic")
    diagnose.add_argument("--install", action="store_true", help="run npm ci instead of the default dry-run")
    diagnose.add_argument("--timeout", type=int, default=180, help="timeout in seconds, bounded to 900")
    openapi = frontend_sub.add_parser("openapi", help="verify deterministic OpenAPI and TypeScript outputs")
    openapi.set_defaults(frontend_command="openapi")
    dev = sub.add_parser("dev", help="run the accepted local product server")
    dev_sub = dev.add_subparsers(dest="dev_command", required=True)
    serve_parser = dev_sub.add_parser("serve", help="serve the existing Step29 local API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = repository_root(args.root)
        if args.command == "version":
            _json_print({"package": PACKAGE_VERSION, "python": _read_pin(root, ".python-version", PINNED_PYTHON), "node": _read_pin(root, ".node-version", PINNED_NODE), "uv": PINNED_UV, "current_python": _python_version(), "root": str(root)})
            return 0
        if args.command == "config":
            _json_print(command_config(root))
            return 0
        if args.command == "doctor":
            report, passed = doctor(root)
            _json_print(report)
            return 0 if passed else 1
        if args.command == "bootstrap":
            return bootstrap(root, profile=args.profile, dry_run=args.dry_run)
        if args.command == "demo":
            return demo(root, Path(args.state_root) if args.state_root else None)
        if args.command == "frontend":
            if args.frontend_command == "openapi":
                return verify_openapi(root)
            return frontend_diagnose(root, install=args.install, timeout=max(10, min(args.timeout, 900)))
        if args.command == "dev":
            return serve(root, host=args.host, port=args.port)
        if args.command == "check":
            report: dict[str, Any] = {"status": "PASS", "compileall": compile_tracked_python(root)}
            if report["compileall"]["status"] != "PASS":
                report["status"] = "FAIL"
            if args.tests:
                test_result = _run([sys.executable, "-m", "pytest", "tests/unit/test_step40_developer_experience.py", "-q"], cwd=root, timeout=600)
                report["tests"] = {"status": "PASS" if test_result.returncode == 0 else "FAIL", "returncode": test_result.returncode}
                if test_result.returncode != 0:
                    report["status"] = "FAIL"
            if args.openapi:
                code = verify_openapi(root)
                if code != 0:
                    report["status"] = "FAIL"
            _json_print(report)
            return 0 if report["status"] == "PASS" else 1
    except DevxFailure as exc:
        print(f"ddo: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
