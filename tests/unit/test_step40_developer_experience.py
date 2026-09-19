from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirty_data_to_olap import devx


ROOT = Path(__file__).resolve().parents[2]


def test_step40_cli_help_and_version(capsys) -> None:
    assert devx.main(["--root", str(ROOT), "version"]) == 0
    version = json.loads(capsys.readouterr().out)
    assert version["python"] == "3.11.16"
    with pytest.raises(SystemExit) as help_exit:
        devx.main(["--help"])
    assert help_exit.value.code == 0
    assert "bootstrap" in capsys.readouterr().out


def test_step40_config_is_safe_and_project_local(capsys) -> None:
    assert devx.main(["--root", str(ROOT), "config"]) == 0
    config = json.loads(capsys.readouterr().out)
    assert config["local_paths"]["venv"].startswith(str(ROOT))
    assert "profiles" in config
    assert "secret" not in json.dumps(config).lower()


def test_step40_bootstrap_dry_run_is_bounded(capsys) -> None:
    assert devx.main(["--root", str(ROOT), "bootstrap", "--profile", "matching", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "DRY_RUN"
    assert all(str(ROOT) not in command or ".ddo" in command for command in plan["commands"])


def test_step40_bootstrap_reuses_exact_active_python(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DDO_PYTHON", "pinned-python")
    monkeypatch.setattr(devx, "_version", lambda *args, **kwargs: ("Python 3.11.16", None))

    assert devx.main(["--root", str(ROOT), "bootstrap", "--profile", "core", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["python_source"] == "existing-exact-interpreter"
    assert not any("python install" in command for command in plan["commands"])
    assert any("--python pinned-python" in command for command in plan["commands"])


def test_step40_demo_uses_real_product_boundary(tmp_path: Path, capsys) -> None:
    assert devx.main(["--root", str(ROOT), "demo", "--state-root", str(tmp_path / "demo")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS"
    assert result["health"]["status"] == "ok"
    assert result["run_status"] == "CREATED"
