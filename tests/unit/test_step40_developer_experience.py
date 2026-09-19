from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirty_data_to_olap import devx
from tools.execution_state import is_authorized_specialist_handoff, step40_g14_closed


ROOT = Path(__file__).resolve().parents[2]


def _step40_closed_state() -> dict:
    return {
        "specialist_execution": {
            "current_step": 41,
            "current_role": "technical_writer",
            "current_specialist": "Step41 - Technical Writer",
            "last_completed_step": 40,
            "last_completed_role": "developer_experience_engineer",
            "last_completed_specialist": "Step40 - Developer Experience Engineer",
            "last_completed_content_commit": "3b3d11276989f8bea05d83124de02e651fb86d4a",
            "next_step": "Step41 - Technical Writer",
            "step40_started": True,
            "step40_status": "COMPLETED_DEVELOPER_EXPERIENCE_G14_PASS",
            "step41_started": False,
            "step41_status": "NOT_STARTED",
            "step30_started": True,
            "step30_status": "COMPLETED_DEVOPS_G8_PASS",
            "step31_started": True,
            "step31_status": "COMPLETED_QA_AUTOMATION",
            "step32_started": True,
            "step32_status": "COMPLETED_COMPATIBILITY_G9_PASS",
            "step33_started": True,
            "step33_status": "COMPLETED_APPLICATION_SECURITY_G10_PASS",
            "step34_started": True,
            "step34_status": "COMPLETED_OBSERVABILITY",
            "step35_started": True,
            "step35_status": "COMPLETED_SRE",
            "step36_started": True,
            "step36_status": "COMPLETED_RESILIENCE_G11_PASS",
            "step37_started": True,
            "step37_status": "COMPLETED_PERFORMANCE",
            "step38_started": True,
            "step38_status": "COMPLETED_LOAD_STRESS_G12_PASS",
            "step39_started": True,
            "step39_status": "COMPLETED_RED_TEAM_G13_PASS",
        },
        "gates": {
            "G6_DATA_CORRECTNESS": "PASS",
            "G7_END_TO_END_PRODUCT": "PASS",
            "G8_REPRODUCIBLE_BUILD": "PASS",
            "G9_FUNCTIONAL_SUPPORT": "PASS",
            "G10_APPLICATION_SECURITY": "PASS",
            "G11_RESILIENCE": "PASS",
            "G12_CAPACITY": "PASS",
            "G13_ADVERSARIAL_SECURITY": "PASS",
            "G14_USABILITY": "PASS",
            "G15_RELEASE": "PENDING",
        },
        "blocked": False,
    }


def test_step40_cli_help_and_version(capsys) -> None:
    assert devx.main(["--root", str(ROOT), "version"]) == 0
    version = json.loads(capsys.readouterr().out)
    assert version["python"] == "3.11.16"
    with pytest.raises(SystemExit) as help_exit:
        devx.main(["--help"])
    assert help_exit.value.code == 0
    assert "bootstrap" in capsys.readouterr().out


def test_step40_final_handoff_is_accepted_by_execution_state() -> None:
    state = _step40_closed_state()
    assert step40_g14_closed(state)
    assert is_authorized_specialist_handoff(state)


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
    assert all(
        str(ROOT) not in command or any(local in command for local in (".ddo", ".venv"))
        for command in plan["commands"]
    )


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
