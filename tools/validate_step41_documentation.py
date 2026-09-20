"""Validate the current Step41 documentation contract.

This is deliberately a bounded documentation validator. It checks the
authoritative execution state, current release claims, implementation anchors,
and relative Markdown links without reading or walking the protected quality
artifact directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
PROTECTED = ROOT / "tests" / "quality_unit_artifacts"
CURRENT_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "getting-started" / "README.md",
    ROOT / "docs" / "user-guide" / "README.md",
    ROOT / "docs" / "concepts" / "README.md",
    ROOT / "docs" / "architecture" / "README.md",
    ROOT / "docs" / "api" / "README.md",
    ROOT / "docs" / "configuration" / "README.md",
    ROOT / "docs" / "compatibility" / "README.md",
    ROOT / "docs" / "security" / "README.md",
    ROOT / "docs" / "validation" / "README.md",
    ROOT / "docs" / "operations" / "README.md",
    ROOT / "docs" / "troubleshooting" / "README.md",
    ROOT / "docs" / "release" / "README.md",
    ROOT / "docs" / "release" / "CLAIM_EVIDENCE_MATRIX.md",
    ROOT / "docs" / "release" / "LIMITATIONS.md",
    ROOT / "docs" / "release" / "CHANGELOG.md",
    ROOT / "docs" / "release" / "OSS_AND_LICENSE.md",
    ROOT / "docs" / "data-engineering" / "SOURCE_SUPPORT_MATRIX.md",
    ROOT / "docs" / "execution" / "gates" / "G14_USABILITY.md",
    ROOT / "docs" / "engineering" / "RELEASE_GATE_MAP.md",
    ROOT / "docs" / "execution" / "STEP41_TECHNICAL_WRITER_REVIEW.md",
)


def check(results: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    results.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})


def relative_links(path: Path, text: str) -> list[tuple[str, Path]]:
    links: list[tuple[str, Path]] = []
    for match in re.finditer(r"(?<!!)\[[^]]+\]\(([^)]+)\)", text):
        target = match.group(1).strip().split("#", 1)[0].split("?", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        links.append((target, (path.parent / target).resolve()))
    return links


def validate() -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    for path in CURRENT_DOCS:
        check(results, f"file exists: {path.relative_to(ROOT)}", path.is_file(), str(path))

    state: dict[str, Any] = {}
    try:
        state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
        check(results, "execution state parses as YAML", isinstance(state, dict), str(STATE_PATH))
    except Exception as exc:  # pragma: no cover - failure detail is the assertion
        check(results, "execution state parses as YAML", False, repr(exc))

    specialist = state.get("specialist_execution", {}) if isinstance(state, dict) else {}
    gates = state.get("gates", {}) if isinstance(state, dict) else {}
    terminal = (
        specialist.get("sequence_status") == "COMPLETE"
        and specialist.get("current_step") is None
        and specialist.get("current_role") is None
        and specialist.get("next_step") is None
    )
    handoff = specialist.get("current_step") == 41
    state_shape_ok = (
        (terminal and specialist.get("last_completed_step") == 41)
        or (handoff and specialist.get("last_completed_step") == 40)
    )
    check(results, "execution state is Step41 handoff or terminal", state_shape_ok, repr({k: specialist.get(k) for k in ("sequence_status", "current_step", "last_completed_step", "next_step")}))

    step41_started = specialist.get("step41_started")
    step41_status = specialist.get("step41_status")
    if terminal:
        step_state_ok = step41_started is True and step41_status == "COMPLETED_TECHNICAL_WRITER_G15_PASS"
    else:
        step_state_ok = step41_started is False and step41_status == "NOT_STARTED"
    check(results, "Step41 state is internally typed", step_state_ok, repr((step41_started, step41_status)))
    check(results, "G14 is PASS", gates.get("G14_USABILITY") == "PASS", repr(gates.get("G14_USABILITY")))
    if terminal:
        check(results, "G15 is PASS in terminal state", gates.get("G15_RELEASE") == "PASS", repr(gates.get("G15_RELEASE")))
        check(results, "terminal state has no Step42 pointer", specialist.get("current_step") != 42 and specialist.get("next_step") is None, repr(specialist.get("next_step")))
    else:
        check(results, "G15 remains pending before closure", gates.get("G15_RELEASE") == "PENDING", repr(gates.get("G15_RELEASE")))

    required_gate_values = {f"G{i}": "PASS" for i in range(0, 15)}
    gate_aliases = {
        "G0": "G0_PRODUCT_CONTRACT", "G1": "G1_DOMAIN_TRUTH", "G2": "G2_ARCHITECTURE_READY",
        "G3": "G3_SOURCE_SAFETY", "G4": "G4_BOUNDED_INTELLIGENCE", "G5": "G5_INFERENCE_VALIDITY",
        "G6": "G6_DATA_CORRECTNESS", "G7": "G7_END_TO_END_PRODUCT", "G8": "G8_REPRODUCIBLE_BUILD",
        "G9": "G9_FUNCTIONAL_SUPPORT", "G10": "G10_APPLICATION_SECURITY", "G11": "G11_RESILIENCE",
        "G12": "G12_CAPACITY", "G13": "G13_ADVERSARIAL_SECURITY", "G14": "G14_USABILITY",
    }
    for short, expected in required_gate_values.items():
        if short == "G15":
            continue
        key = gate_aliases[short]
        check(results, f"{key} is PASS", gates.get(key) == expected, repr(gates.get(key)))
    check(results, "repository is not blocked", state.get("blocked") is False, repr(state.get("blocked")))

    text_by_path: dict[Path, str] = {}
    for path in CURRENT_DOCS:
        if path.is_file():
            text_by_path[path] = path.read_text(encoding="utf-8")
    all_current = "\n".join(text_by_path.values())
    required_phrases = {
        "current README names managed CSV path": "managed CSV import",
        "source/UI distinction": "adapter boundary",
        "Oracle is deferred": "Oracle is deferred",
        "uncalibrated score boundary": "uncalibrated",
        "no automation threshold": "No inference automation threshold",
        "ER is not canonical truth": "not automatic canonical truth",
        "no invented revenue": "does not invent revenue",
        "project-local demo": "project-local",
        "no universal production claim": "universal production readiness",
        "license decision is explicit": "No top-level `LICENSE` or `NOTICE` file",
    }
    for name, phrase in required_phrases.items():
        check(results, name, phrase.lower() in all_current.lower(), phrase)

    root_text = text_by_path.get(ROOT / "README.md", "")
    stale_root_phrases = ("Steps 01-21 are complete", "G6-G15 remain PENDING", "Next: Step22")
    for phrase in stale_root_phrases:
        check(results, f"root README removes stale phrase: {phrase}", phrase not in root_text, phrase)

    for path, text in text_by_path.items():
        links = relative_links(path, text)
        bad = [(target, str(resolved)) for target, resolved in links if not resolved.exists() or PROTECTED in resolved.parents]
        check(results, f"relative links resolve: {path.relative_to(ROOT)}", not bad, repr(bad[:3]))

    try:
        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version_match = re.search(r"(?ms)^\[project\].*?^version\s*=\s*[\"']([^\"']+)[\"']", pyproject_text)
        project_version = version_match.group(1) if version_match else None
        check(results, "project version is documented V1 0.1.0", project_version == "0.1.0", repr(project_version))
        check(results, "Python pin matches current docs", (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.11.16", "3.11.16")
        check(results, "Node pin matches current docs", (ROOT / ".node-version").read_text(encoding="utf-8").strip() == "22.14.0", "22.14.0")
    except Exception as exc:
        check(results, "implementation metadata is readable", False, repr(exc))

    try:
        openapi = json.loads((ROOT / "frontend" / "openapi.json").read_text(encoding="utf-8"))
        paths = openapi.get("paths", {})
        expected_paths = {"/api/v1/health", "/api/v1/sources/import", "/api/v1/runs", "/api/v1/runs/{run_id}/product-summary"}
        check(results, "tracked OpenAPI contains documented representative routes", expected_paths <= set(paths), repr(sorted(expected_paths - set(paths))))
    except Exception as exc:
        check(results, "tracked OpenAPI parses", False, repr(exc))

    passed = all(item["status"] == "PASS" for item in results)
    return passed, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args()
    passed, results = validate()
    payload = {"status": "PASS" if passed else "FAIL", "checks": results, "protected_path": "NOT_USED"}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in results:
            print(f"[{item['status']}] {item['name']}: {item['detail']}")
        print(json.dumps({"status": payload["status"], "checks": len(results), "protected_path": "NOT_USED"}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
