"""Validate the Step09 quality rule catalog and architecture boundaries."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "quality"
REQUIRED_DOCS = (
    "QUALITY_CONTRACT.md",
    "RULE_CATALOG.md",
    "MEASUREMENT_AND_SEVERITY.md",
    "REPAIR_PROPOSALS.md",
    "QUALITY_VECTOR.md",
)


def main() -> int:
    errors: list[str] = []
    for name in REQUIRED_DOCS:
        if not (ROOT / "docs" / "quality" / name).is_file():
            errors.append(f"missing quality document: {name}")
    rule_files = sorted(RULES.glob("*.yml"))
    if not rule_files:
        errors.append("no versioned quality rule files found")

    sys.path.insert(0, str(ROOT / "src"))
    try:
        from dirty_data_to_olap.domain.contracts.quality import QualityRuleSet
    except Exception as exc:
        errors.append(f"quality contracts cannot import: {exc.__class__.__name__}")
    else:
        for path in rule_files:
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                rule_set = QualityRuleSet.model_validate(payload)
                if rule_set.runtime_default and any(rule.scope.value == "REFERENCE_BENCHMARK" for rule in rule_set.rules):
                    errors.append(f"runtime default includes benchmark rule: {path.name}")
            except Exception as exc:
                errors.append(f"invalid quality rules {path.name}: {exc}")

    quality_source = ROOT / "src" / "dirty_data_to_olap"
    for path in quality_source.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"syntax error in {path}: {exc}")
            continue
        if "domain" in path.parts or "application" in path.parts:
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
                    if any(name in {"pandas", "numpy", "pyarrow", "dataprofiler"} or name.startswith(("pandas.", "numpy.", "pyarrow.", "dataprofiler.")) for name in names + [module]):
                        errors.append(f"vendor quality import outside adapter: {path}")
        text = path.read_text(encoding="utf-8").lower()
        if "source write" in text and "source writes" in text:
            pass

    architecture = "\n".join((ROOT / relative).read_text(encoding="utf-8") for relative in (
        "docs/architecture/COMPONENT_MODEL.md",
        "docs/architecture/ENGINE_INTERFACES.md",
        "docs/architecture/specs/components.yml",
        "docs/architecture/specs/engine_interfaces.yml",
        "docs/architecture/specs/stage_graph.yml",
    )).lower()
    for required in ("qualitystagedreader", "qualityissue", "repairproposal", "qualityresult", "sourcerecordreference", "dependencyevidence"):
        if required not in architecture:
            errors.append(f"quality architecture is missing {required}")
    components_spec = yaml.safe_load((ROOT / "docs/architecture/specs/components.yml").read_text(encoding="utf-8"))
    quality_component = next((item for item in components_spec.get("components", []) if item.get("component_id") == "application.quality"), {})
    if "application.entity_resolution" in quality_component.get("depends_on", []) or "step12" in str(quality_component).lower():
        errors.append("quality architecture must not depend on a later entity-resolution step")

    if errors:
        print("FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"PASS: {len(rule_files)} quality rule files, contracts, staged boundary and quality architecture verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
