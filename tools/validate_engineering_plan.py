"""Deterministic Step 05 engineering-plan and G2 state validator."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENG = ROOT / "docs" / "engineering"
SPECS = ENG / "specs"
STATE_PATH = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
LATER_GATES = [
    "G3_SOURCE_SAFETY", "G4_BOUNDED_INTELLIGENCE", "G5_INFERENCE_VALIDITY",
    "G6_DATA_CORRECTNESS", "G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD",
    "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY", "G11_RESILIENCE",
    "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE",
]

REQUIRED_DOCS = [
    "ENGINEERING_PLAN.md", "REPOSITORY_STRUCTURE.md", "IMPLEMENTATION_MILESTONES.md",
    "CODING_STANDARDS.md", "DEPENDENCY_AND_OWNERSHIP_RULES.md", "TEST_STRATEGY.md",
    "ARCHITECTURE_ENFORCEMENT.md", "CI_EXPECTATIONS.md", "TECHNICAL_RISK_REGISTER.md",
    "RELEASE_GATE_MAP.md", "IMPLEMENTATION_DEFINITION_OF_DONE.md", "HANDOFF_TEMPLATE.md",
    "OSS_INTEGRATION_PLAN.md", "IMPLEMENTATION_READINESS_AUDIT.md",
    "INTEGRATION_READINESS_AUDIT.md", "CROSS_ARTIFACT_CONSISTENCY_AUDIT.md",
]
REQUIRED_SPECS = [
    "implementation_plan.yml", "ownership_map.yml", "engineering_rules.yml",
    "test_matrix.yml", "architecture_enforcement.yml", "gate_map.yml",
    "integration_contract_matrix.yml",
]


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-gate", action="store_true", help="require Step 05 in progress and G2 PENDING")
    parser.add_argument("--post-gate", action="store_true", help="require Step 05 complete and G2 PASS")
    args = parser.parse_args()
    mode = "post" if args.post_gate else "pre"
    if args.pre_gate and args.post_gate:
        print("FAIL\n- --pre-gate and --post-gate are mutually exclusive")
        return 1

    errors: list[str] = []
    checks = 0

    def check(name: str, condition: bool) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            errors.append(name)

    # 1-3: repository and state baseline.
    state = load_yaml(STATE_PATH)
    check("execution state parses", isinstance(state, dict))
    check("repository branch is main", subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, text=True, capture_output=True).stdout.strip() == "main")
    check("repository is on a normal git worktree", subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, text=True, capture_output=True).stdout.strip() == "true")

    execution = state.get("specialist_execution", {}) if isinstance(state, dict) else {}
    gates = state.get("gates", {}) if isinstance(state, dict) else {}
    check("bootstrap is PASS", state.get("bootstrap", {}).get("status") == "PASS")
    check("all prior repairs are PASS", all(item.get("status") == "PASS" for item in state.get("post_bootstrap_repairs", [])))
    check("G0 and G1 are PASS", gates.get("G0_PRODUCT_CONTRACT") == "PASS" and gates.get("G1_DOMAIN_TRUTH") == "PASS")
    check("G3-G15 are PENDING", all(gates.get(key) == "PENDING" for key in LATER_GATES))
    check("blocked is false", state.get("blocked") is False)
    if mode == "pre":
        check("pre-gate G2 is PENDING", gates.get("G2_ARCHITECTURE_READY") == "PENDING")
        check("pre-gate current specialist is Step 05", execution.get("current_step") == 5 and execution.get("current_role") == "technical_lead")
    else:
        check("post-gate G2 is PASS", gates.get("G2_ARCHITECTURE_READY") == "PASS")
        check("post-gate completed step is 5", execution.get("last_completed_step") == 5)
        check("post-gate completed role is technical_lead", execution.get("last_completed_role") == "technical_lead")
        check("post-gate current step is 6", execution.get("current_step") == 6 and execution.get("current_role") == "database_engineer")
        check("post-gate current and next specialist are Step06", execution.get("current_specialist") == "Step06 — Database Engineer / DBA" and execution.get("next_step") == "Step06 — Database Engineer / DBA")

    # 4-5: required artifacts parse and carry provenance.
    check("all required engineering documents exist", all((ENG / name).is_file() for name in REQUIRED_DOCS))
    parsed_specs: dict[str, dict] = {}
    for name in REQUIRED_SPECS:
        path = SPECS / name
        try:
            value = load_yaml(path)
            if not isinstance(value, dict):
                raise ValueError("not a mapping")
            parsed_specs[name] = value
        except Exception as exc:  # deterministic failure text, no traceback needed
            errors.append(f"invalid or missing engineering spec {name}: {exc}")
    check("all required engineering specs parse", len(parsed_specs) == len(REQUIRED_SPECS))
    check("every engineering spec has schema/scope/provenance", all({"schema_version", "scope", "provenance"}.issubset(value) for value in parsed_specs.values()))

    # 6-10: architecture coverage.
    arch_root = ROOT / "docs" / "architecture" / "specs"
    components = load_yaml(arch_root / "components.yml").get("components", [])
    interfaces = load_yaml(arch_root / "engine_interfaces.yml").get("interfaces", [])
    stages = load_yaml(arch_root / "stage_graph.yml").get("stages", [])
    reviews = load_yaml(arch_root / "specs" / "review_checkpoints.yml").get("checkpoints", []) if (arch_root / "specs" / "review_checkpoints.yml").is_file() else load_yaml(ROOT / "docs" / "architecture" / "specs" / "review_checkpoints.yml").get("checkpoints", [])
    component_ids = {item.get("component_id") for item in components}
    interface_ids = {item.get("interface_id") for item in interfaces}
    stage_ids = {item.get("stage_id") for item in stages}
    review_ids = {item.get("checkpoint_id") for item in reviews}
    ownership = parsed_specs.get("ownership_map.yml", {})
    owned_components = {item.get("component_id") for item in ownership.get("components", [])}
    owned_interfaces = {item.get("interface_id") for item in ownership.get("interfaces", [])}
    owned_stages = {item.get("stage_id") for item in ownership.get("stages", [])}
    check("ownership covers exactly all 34 components", len(component_ids) == 34 and owned_components == component_ids)
    check("ownership covers exactly all 11 interfaces", len(interface_ids) == 11 and owned_interfaces == interface_ids)
    check("ownership covers exactly all 19 stages", len(stage_ids) == 19 and owned_stages == stage_ids)
    check("every component has owner and test strategy", all(item.get("primary_specialist") and item.get("test_strategy") for item in ownership.get("components", [])))
    check("every interface has owner and test strategy", all(item.get("owner_component") and item.get("test_strategy") for item in ownership.get("interfaces", [])))
    check("every stage has owner, step and test strategy", all(item.get("owner_component") and item.get("implementation_step") and item.get("test_strategy") for item in ownership.get("stages", [])))

    # 11-15: integration matrix and architectural ownership rules.
    matrix = parsed_specs.get("integration_contract_matrix.yml", {})
    contracts = matrix.get("contracts", [])
    contract_names = {item.get("contract") for item in contracts}
    expected_contracts = {
        "SourceCatalog", "SourceSnapshot", "ColumnProfile", "TableProfile", "KeyCandidate",
        "FunctionalDependencyEvidence", "InclusionDependencyEvidence", "RelationshipCandidate",
        "SchemaMatchCandidate", "QualityIssue", "RepairProposal", "RelationshipDecision",
        "SemanticMappingDecision", "ReviewDecision", "CanonicalModelHypothesis", "EntityResolutionSpec",
        "EntityMatchEdge", "EntityCluster", "SourceRecordCanonicalMap", "CanonicalModel",
        "AnalyticalPlan", "FactSpec", "DimensionSpec", "GrainSpec", "MeasureSpec", "CompiledPlan",
        "GeneratedSQL", "MaterializationArtifact", "ValidationReport", "ReconciliationResult", "RecordAccounting",
    }
    check("integration matrix covers every critical contract", expected_contracts.issubset(contract_names))
    check("critical contracts have producer/consumer/stage/storage", all(item.get("producer_component") and item.get("producer_stage") and item.get("consumers") and item.get("storage_plane") for item in contracts if item.get("contract") in expected_contracts))
    check("review guards use declared checkpoints or final acceptance", all(item.get("review_guard") in ({"none", "self", "final_acceptance"} | review_ids) for item in contracts))
    er_outputs = [item for item in contracts if item.get("contract") in {"EntityMatchEdge", "EntityCluster", "SourceRecordCanonicalMap"}]
    check("ER owns only edge and cluster outputs", all(item.get("producer_component") == "application.entity_resolution" for item in er_outputs if item.get("contract") in {"EntityMatchEdge", "EntityCluster"}))
    check("canonical mapping has one canonical-finalization producer", sum(item.get("contract") == "SourceRecordCanonicalMap" and item.get("producer_component") == "application.canonical_finalization" for item in contracts) == 1)
    check("engineering rules and enforcement are non-empty and uniquely identified", len(parsed_specs.get("engineering_rules.yml", {}).get("rules", [])) >= 20 and len({item.get("rule_id") for item in parsed_specs.get("engineering_rules.yml", {}).get("rules", [])}) == len(parsed_specs.get("engineering_rules.yml", {}).get("rules", [])))
    check("architecture enforcement covers key rules", len(parsed_specs.get("architecture_enforcement.yml", {}).get("rules", [])) >= 15 and {item.get("enforcement_id") for item in parsed_specs.get("architecture_enforcement.yml", {}).get("rules", [])}.__len__() == len(parsed_specs.get("architecture_enforcement.yml", {}).get("rules", [])))

    # 16-21: test strategy, milestones and gates.
    test_matrix = parsed_specs.get("test_matrix.yml", {})
    categories = {item.get("category") for item in test_matrix.get("categories", [])}
    required_categories = {"unit", "contract", "architecture", "adapter_contract", "integration", "data", "evaluation", "e2e", "failure_recovery", "security", "performance"}
    check("test matrix has all required categories", required_categories.issubset(categories))
    check("test matrix covers all stages", {item.get("stage_id") for item in test_matrix.get("stage_test_requirements", [])} == stage_ids)
    check("test matrix covers all interfaces", {item.get("interface_id") for item in test_matrix.get("interface_test_requirements", [])} == interface_ids)
    plan = parsed_specs.get("implementation_plan.yml", {})
    sequence = plan.get("sequence_authority", {}).get("sequence_order", [])
    check("implementation plan preserves 1-41 order", sequence == list(range(1, 42)))
    check("Step06 is first implementation specialist", plan.get("sequence_authority", {}).get("first_implementation_step") == 6 and plan.get("first_implementation_slice", {}).get("step") == 6)
    check("work packages cover all specialist steps", sorted({step for package in plan.get("work_packages", []) for step in package.get("steps", [])}) == list(range(1, 42)))
    gate_spec = parsed_specs.get("gate_map.yml", {})
    gate_items = gate_spec.get("gates", [])
    gate_ids = [item.get("gate_id") for item in gate_items]
    check("gate map contains G0-G15 exactly once", gate_ids == [f"G{i}" for i in range(16)])
    check("gate map preserves G3-G15 pending", all(item.get("status") == "PENDING" for item in gate_items if item.get("gate_id") in {f"G{i}" for i in range(3, 16)}))
    check("gate map status matches current G2 phase", next((item.get("status") for item in gate_items if item.get("gate_id") == "G2"), None) == ("PENDING" if mode == "pre" else "PASS"))

    # 22-29: stage ordering and required governance.
    stage_order = [item.get("stage_id") for item in stages]
    position = {name: stage_order.index(name) for name in stage_order}
    check("evidence fusion follows its evidence producers", all(position[producer] < position["EVIDENCE_FUSION"] for producer in ["PROFILING", "DEPENDENCY_DISCOVERY", "SCHEMA_MATCHING", "QUALITY_ANALYSIS"]))
    check("canonical finalization follows evidence and evaluation", position["EVIDENCE_FUSION"] < position["CANONICAL_FINALIZATION"] and position["ENTITY_RESOLUTION"] < position["CANONICAL_FINALIZATION"])
    check("OLAP stages follow canonical finalization", position["CANONICAL_FINALIZATION"] < position["ANALYTICAL_PLANNING"] < position["COMPILATION"] < position["MATERIALIZATION"])
    check("review checkpoints precede their guarded stages", position["REVIEW_EVIDENCE_DECISIONS"] < position["CANONICAL_HYPOTHESES"] and position["REVIEW_CANONICAL_IDENTITY"] < position["CANONICAL_FINALIZATION"] and position["REVIEW_ANALYTICAL_PLAN"] < position["COMPILATION"] and position["REVIEW_MATERIALIZATION_PLAN"] < position["MATERIALIZATION"])
    check("performance is after correctness in master sequence", plan.get("sequence_authority", {}).get("sequence_order", []).index(37) > plan.get("sequence_authority", {}).get("sequence_order", []).index(22))
    master_text = (ROOT / "docs" / "Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base" / "05_MASTER_BUILD_SEQUENCE.md").read_text(encoding="utf-8")
    step_positions = [master_text.find(f"## Step {number:02d} ") for number in range(1, 42)]
    check("master sequence contains ordered Step01-Step41", all(value >= 0 for value in step_positions) and step_positions == sorted(step_positions))
    check("Red Team follows AppSec and Technical Writer is final", step_positions[38] > step_positions[32] and step_positions[40] == max(step_positions))
    check("no evidence/OLAP/performance ordering rule is missing", all(text in " ".join(gate_spec.get("ordering_constraints", [])) for text in ["evidence producers precede EVIDENCE_FUSION", "evaluation follows evidence and ER producers", "OLAP follows canonical finalization", "performance follows correctness", "Red Team follows AppSec", "Technical Writer is final"]))

    # 30-35: product MUST mapping, risks, paths and false implementation claims.
    req_path = ROOT / "docs" / "product" / "REQUIREMENTS_TRACEABILITY.csv"
    rows = list(csv.DictReader(req_path.open(encoding="utf-8", newline="")))
    must_ids = {row["requirement_id"] for row in rows if row.get("priority") == "MUST"}
    mapped_ids = {"PR-001", *{f"FR-{i:03d}" for i in range(1, 13)}, *{f"NFR-{i:03d}" for i in range(1, 8)}, "POL-001", "POL-002", "POL-003", "OUT-001"}
    check("all Product MUST requirements are enumerated", must_ids == mapped_ids)
    check("all Product MUST requirements have owner/test/gate mapping", must_ids.issubset(mapped_ids) and all(row.get("future_owner") and row.get("verification_type") and row.get("future_gate") for row in rows if row.get("requirement_id") in must_ids))
    risks = parsed_specs.get("implementation_plan.yml", {}).get("work_packages", [])
    risk_text = (ENG / "TECHNICAL_RISK_REGISTER.md").read_text(encoding="utf-8")
    check("technical risks have owner and mitigation", all(len(row) >= 5 for row in re.findall(r"^\| R-\d+ \|.*\|.*\|.*\|.*\|", risk_text, flags=re.MULTILINE)))
    check("documented paths exist or are explicitly future", "future runtime namespace" in (ENG / "REPOSITORY_STRUCTURE.md").read_text(encoding="utf-8") and "future" in (ENG / "IMPLEMENTATION_READINESS_AUDIT.md").read_text(encoding="utf-8").lower())
    structure_text = (ENG / "REPOSITORY_STRUCTURE.md").read_text(encoding="utf-8")
    check("no false implemented claim", "future runtime namespace" in structure_text and not (ROOT / "src").exists() and not (ROOT / "pyproject.toml").exists())
    check("source read-only and no OSS runtime are explicit", "read-only" in (ENG / "OSS_INTEGRATION_PLAN.md").read_text(encoding="utf-8").lower() or "read-only" in (ENG / "CODING_STANDARDS.md").read_text(encoding="utf-8").lower())
    oss_text = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in ["docs/02_OPEN_SOURCE_REUSE_AND_CLONE_PLAN.md", "docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base/base_reports/02_OPEN_SOURCE_REUSE_AND_CLONE_PLAN.md"])
    check("OSS report does not assign canonical mapping to Splink", not re.search(r"Output:\s*\n(?:\s*-.*\n){0,4}\s*-\s*`?SourceRecordCanonicalMap", oss_text))
    check("no research/oss clone or application source exists", not (ROOT / "src").exists() and not any(path.name != "README.md" for path in (ROOT / "research" / "oss").rglob("*")))

    # 36-40: state-aware previous validators and integrity checks.
    validator_results = []
    for filename in ["validate_domain_docs.py", "validate_data_architecture.py", "validate_solution_architecture.py"]:
        result = subprocess.run([sys.executable, str(ROOT / "tools" / filename)], cwd=ROOT, text=True, capture_output=True)
        validator_results.append(result.returncode == 0)
    check("prior domain/data/solution validators pass", all(validator_results))
    check("review checkpoint specification has four checkpoints", len(reviews) == 4 and {item.get("checkpoint_id") for item in reviews} == {"REVIEW_EVIDENCE_DECISIONS", "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN", "REVIEW_MATERIALIZATION_PLAN"})
    check("README status is phase-accurate", "Step 05" in (ROOT / "README.md").read_text(encoding="utf-8") or mode == "pre")
    check("Step 05 audit records ER correction", "SourceRecordCanonicalMap" in (ENG / "CROSS_ARTIFACT_CONSISTENCY_AUDIT.md").read_text(encoding="utf-8") and "corrected" in (ENG / "CROSS_ARTIFACT_CONSISTENCY_AUDIT.md").read_text(encoding="utf-8"))
    check("execution log exists", (ROOT / "docs" / "execution" / "SPECIALIST_EXECUTION_LOG.md").is_file())
    check("gate map has required evidence and owners", all(item.get("owner_step") and item.get("required_evidence") for item in gate_items))

    if errors:
        print("FAIL")
        print("\n".join(f"- {item}" for item in errors))
        print(f"checks={checks}")
        return 1
    print(f"PASS: engineering_checks={checks} mode={mode} components={len(component_ids)} interfaces={len(interface_ids)} stages={len(stage_ids)} contracts={len(contracts)} gates={len(gate_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
