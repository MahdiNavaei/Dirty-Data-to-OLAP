"""Deterministic Step 05 engineering-plan and G2 state validator."""

from __future__ import annotations

import argparse
import copy
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
    "integration_contract_matrix.yml", "technical_risks.yml",
]

CANONICAL_GATES = {
    "G0": ("Product Contract", 1, 1, {"product_contract", "acceptance_criteria", "scope_boundary"}),
    "G1": ("Domain Truth", 2, 2, {"domain_docs", "benchmark_labels", "domain_validator"}),
    "G2": ("Architecture Ready", 5, 5, {"engineering_plan", "ownership_map", "test_strategy", "enforcement", "readiness_audit", "post_gate_validator"}),
    "G3": ("Source Safety", 11, 11, {"safe_ingestion", "privacy_controls", "read_only_source_tests", "db_security_review"}),
    "G4": ("Bounded Intelligence", 16, 16, {"independent_evidence_producers", "bounded_ml_llm", "privacy_failure_isolation", "llm_evidence_not_truth"}),
    "G5": ("Inference Validity", 18, 18, {"benchmark_evaluation", "calibration", "threshold_justification", "score_semantics"}),
    "G6": ("Data Correctness", 22, 22, {"grain_tests", "accounting", "lineage", "reconciliation"}),
    "G7": ("End-to-End Product", 29, 29, {"user_path", "api_path", "ui_path", "engine_path", "review_flow_e2e"}),
    "G8": ("Reproducible Build", 30, 30, {"clean_build", "reproducible_environment", "ci", "dependency_scan", "release_foundation"}),
    "G9": ("Functional Support", 32, 32, {"functional_verification", "source_compatibility", "adapter_matrix", "claims_audit"}),
    "G10": ("Application Security", 33, 33, {"security_tests", "secret_scan", "threat_model", "remediation"}),
    "G11": ("Resilience", 36, 36, {"fault_injection", "truthful_state", "safe_retry", "recoverability", "audit_trail"}),
    "G12": ("Capacity", 38, 38, {"stage_benchmarks", "memory", "load_report", "overload_recovery"}),
    "G13": ("Adversarial Security", 39, 39, {"authorized_red_team_report", "ssrf_sql_abuse_credential_tests", "remediations"}),
    "G14": ("Usability", 40, 40, {"fresh_clone", "docs", "examples", "usability_review"}),
    "G15": ("Release", 41, 41, {"final_docs", "limitation_audit", "release_manifest", "unsupported_claims_audit"}),
}

CRITICAL_TOPOLOGY = {
    "SourceCatalog": ("application.discovery", "SOURCE_DISCOVERY"),
    "SourceSnapshot": ("application.source_snapshot", "SOURCE_SNAPSHOT_STAGE"),
    "ColumnProfile": ("application.profiling", "PROFILING"),
    "TableProfile": ("application.profiling", "PROFILING"),
    "EntityMatchEdge": ("application.entity_resolution", "ENTITY_RESOLUTION"),
    "EntityCluster": ("application.entity_resolution", "ENTITY_RESOLUTION"),
    "SourceRecordCanonicalMap": ("application.canonical_finalization", "CANONICAL_FINALIZATION"),
    "AnalyticalPlan": ("application.analytical_planner", "ANALYTICAL_PLANNING"),
    "GrainSpec": ("application.analytical_planner", "ANALYTICAL_PLANNING"),
    "CompiledPlan": ("application.compiler", "COMPILATION"),
    "GeneratedSQL": ("application.compiler", "COMPILATION"),
    "MaterializationArtifact": ("application.materializer", "MATERIALIZATION"),
    "ValidationReport": ("application.validation", "VALIDATION_RECONCILIATION"),
    "ReconciliationResult": ("application.validation", "VALIDATION_RECONCILIATION"),
}


def source_lifecycle_valid(components: list[dict], stages: list[dict], contracts: list[dict], interface: dict) -> bool:
    by_component = {item.get("component_id"): item for item in components}
    by_stage = {item.get("stage_id"): item for item in stages}
    by_contract = {item.get("contract"): item for item in contracts}
    discovery = by_component.get("application.discovery", {})
    snapshot = by_component.get("application.source_snapshot", {})
    discovery_stage = by_stage.get("SOURCE_DISCOVERY", {})
    snapshot_stage = by_stage.get("SOURCE_SNAPSHOT_STAGE", {})
    catalog = by_contract.get("SourceCatalog", {})
    snap = by_contract.get("SourceSnapshot", {})
    operations = {item.get("operation_id"): item for item in interface.get("operations", [])}
    return (
        "SourceSnapshot" not in set(discovery.get("input_contracts", []))
        and "application.source_snapshot" not in set(discovery.get("depends_on", []))
        and set(discovery.get("output_contracts", [])) >= {"SourceCatalog"}
        and set(discovery.get("input_contracts", [])) >= {"SourceSelection", "SourceRegistryRecord"}
        and set(snapshot.get("input_contracts", [])) >= {"SourceCatalog", "SamplingPolicy"}
        and "application.discovery" in set(snapshot.get("depends_on", []))
        and discovery_stage.get("output_artifact_types") == ["SourceCatalog"]
        and snapshot_stage.get("dependencies") == ["SOURCE_DISCOVERY"]
        and snapshot_stage.get("input_artifact_types", [])[0] == "SourceCatalog"
        and catalog.get("producer_component") == "application.discovery"
        and "application.source_snapshot" in set(catalog.get("consumers", []))
        and snap.get("producer_component") == "application.source_snapshot"
        and "application.discovery" not in set(snap.get("consumers", []))
        and set(operations.get("discover_source", {}).get("outputs", [])) >= {"SourceDescriptor", "TableDescriptor", "ColumnDescriptor", "DeclaredConstraint"}
        and set(operations.get("create_bounded_snapshot", {}).get("inputs", [])) >= {"SourceCatalog", "SamplingPolicy"}
        and set(operations.get("create_bounded_snapshot", {}).get("outputs", [])) >= {"SourceSnapshot", "BatchReference", "SourceRecordReference"}
    )


def implementation_is_authorized(state: dict) -> bool:
    execution = state.get("specialist_execution", {}) if isinstance(state, dict) else {}
    return execution.get("current_step", 0) >= 6 and state.get("gates", {}).get("G2_ARCHITECTURE_READY") == "PASS"


def critical_topology_valid(components: list[dict], stages: list[dict], ownership_stages: list[dict], contracts: list[dict]) -> bool:
    component_by_id = {item.get("component_id"): item for item in components}
    stage_by_id = {item.get("stage_id"): item for item in stages}
    owner_by_stage = {item.get("stage_id"): item.get("owner_component") for item in ownership_stages}
    contract_by_name = {item.get("contract"): item for item in contracts}
    stage_order = {item.get("stage_id"): index for index, item in enumerate(stages)}
    for contract_name, (component_id, stage_id) in CRITICAL_TOPOLOGY.items():
        contract = contract_by_name.get(contract_name, {})
        component = component_by_id.get(component_id, {})
        stage = stage_by_id.get(stage_id, {})
        if contract.get("producer_component") != component_id or contract.get("producer_stage") != stage_id:
            return False
        if owner_by_stage.get(stage_id) != component_id:
            return False
        if contract_name not in set(component.get("output_contracts", [])):
            return False
        if contract_name not in set(stage.get("output_artifact_types", [])):
            return False
    # A component cannot consume an artifact produced by a later stage.
    for stage_id, component_id in owner_by_stage.items():
        component = component_by_id.get(component_id, {})
        component_position = stage_order.get(stage_id, -1)
        for input_contract in component.get("input_contracts", []):
            producer = contract_by_name.get(input_contract)
            if producer and stage_order.get(producer.get("producer_stage"), -1) > component_position:
                return False
    return True


def post_gate_evidence_valid(review_text: str, gate_text: str, log_text: str) -> bool:
    command = "python tools/validate_engineering_plan.py --post-gate"
    return all(command in text and re.search(r"PASS: engineering_checks=\d+ mode=post", text) for text in [review_text, gate_text, log_text])


def ownership_consistent(components: list[dict], ownership_components: list[dict]) -> bool:
    left = {item.get("component_id"): item for item in components}
    right = {item.get("component_id"): item for item in ownership_components}
    if set(left) != set(right):
        return False
    for component_id, component in left.items():
        record = right[component_id]
        if component.get("implementation_owner") != record.get("primary_specialist"):
            return False
        if component.get("implementation_step") != record.get("implementation_step"):
            return False
        if component.get("implementation_status") not in {"PLANNED", "IMPLEMENTED"}:
            return False
    return True


def semantic_ownership_valid(ownership: dict) -> bool:
    families = ownership.get("contract_family_owners", {})
    return (
        families.get("canonical") == "Step19 Canonical Model Engineer"
        and families.get("analytical_and_olap") == "Step20 OLAP Engineer"
        and families.get("entity_resolution") == "Step14 Entity Resolution Engineer"
        and families.get("validation_and_reconciliation") == "Step22 Data QA Engineer"
        and families.get("canonical") != "Step06 Database Engineer / DBA"
    )


def step06_plan_valid(plan: dict) -> bool:
    slice_spec = plan.get("first_implementation_slice", {})
    required = {"read-only database connection substrate and external connection-profile reference contract", "normalized database capability description and normalized database failure representation", "safe metadata introspection boundary with identifier/schema qualification rules", "transaction/isolation, timeout, pooling, cleanup and bounded sampling semantics"}
    required_text = set(slice_spec.get("required_outputs", []))
    forbidden = " ".join(str(item).lower() for item in slice_spec.get("forbidden_outputs", []))
    non_ownership = " ".join(str(item).lower() for item in slice_spec.get("non_ownership", []))
    work_packages = plan.get("work_packages", [])
    wp02 = next((item for item in work_packages if item.get("package_id") == "WP02"), {})
    return (
        slice_spec.get("step") == 6
        and required.issubset(required_text)
        and "dlt integration" in forbidden
        and "complete sourceadapter ingestion" in forbidden
        and "source write path" in forbidden
        and "canonical semantics" in non_ownership
        and "olap semantics" in non_ownership
        and "tested database access/introspection foundations" in " ".join(slice_spec.get("handoff_to_step_7", [])).lower()
        and "database-access" in str(wp02.get("objective", "")).lower()
    )


def gate_semantics_valid(gate_items: list[dict], require_pass: bool = True) -> bool:
    by_id = {item.get("gate_id"): item for item in gate_items}
    if list(item.get("gate_id") for item in gate_items) != [f"G{i}" for i in range(16)]:
        return False
    for gate_id, (name, after_step, owner_step, evidence) in CANONICAL_GATES.items():
        item = by_id.get(gate_id, {})
        if item.get("name") != name or item.get("after_step") != after_step or item.get("owner_step") != owner_step:
            return False
        if not evidence.issubset(set(item.get("required_evidence", []))):
            return False
    return (by_id.get("G2", {}).get("status") == "PASS") if require_pass else (by_id.get("G2", {}).get("status") == "PENDING")


def risks_valid(risk_spec: dict) -> bool:
    required = {"risk_id", "description", "likelihood", "impact", "detection", "mitigation", "owner", "future_step", "gate_relevance", "status"}
    risks = risk_spec.get("risks", [])
    return (
        len(risks) == 15
        and len({item.get("risk_id") for item in risks}) == 15
        and all(required.issubset(item) for item in risks)
        and all(item.get("likelihood") in {"LOW", "MEDIUM", "HIGH"} and item.get("impact") in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} for item in risks)
        and all(str(item.get(field, "")).strip() for item in risks for field in ["detection", "mitigation", "owner", "future_step", "status"])
    )


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
    if not args.pre_gate and execution.get("current_step", 0) >= 6:
        mode = "post"
    check("bootstrap is PASS", state.get("bootstrap", {}).get("status") == "PASS")
    check("all prior repairs are PASS", all(item.get("status") == "PASS" for item in state.get("post_bootstrap_repairs", [])))
    check("G0 and G1 are PASS", gates.get("G0_PRODUCT_CONTRACT") == "PASS" and gates.get("G1_DOMAIN_TRUTH") == "PASS")
    check("G3-G15 preserve evidenced G3/G4/G5 state", gates.get("G3_SOURCE_SAFETY") in {"PENDING", "PASS", "BLOCKED"} and gates.get("G4_BOUNDED_INTELLIGENCE") in {"PENDING", "PASS"} and gates.get("G5_INFERENCE_VALIDITY") in {"PENDING", "REVIEW_ONLY_VALIDATED", "PASS"} and all(gates.get(key) == "PENDING" for key in LATER_GATES[3:]))
    check("blocked is false", state.get("blocked") is False)
    if mode == "pre" and execution.get("current_step", 0) < 6:
        check("pre-gate G2 is PENDING", gates.get("G2_ARCHITECTURE_READY") == "PENDING")
        check("pre-gate current specialist is Step 05", execution.get("current_step") == 5 and execution.get("current_role") == "technical_lead")
    else:
        check("post-gate G2 is PASS", gates.get("G2_ARCHITECTURE_READY") == "PASS")
        check("post-gate completed step is at least 5", execution.get("last_completed_step", 0) >= 5)
        check("post-gate completed role is an authorized upstream specialist", execution.get("last_completed_role") in {"technical_lead", "database_engineer", "senior_data_engineer", "data_profiling_specialist", "data_quality_engineer", "data_security_privacy_engineer", "database_security_specialist", "dependency_discovery_engineer", "schema_matching_engineer", "entity_resolution_engineer", "applied_ml_engineer", "llm_semantic_ai_engineer", "evidence_fusion_engineer", "ml_evaluation_engineer"})
        check("post-gate implementation remains after G2", implementation_is_authorized(state))
        check(
            "post-gate current specialist is Step06 through Step18 when G3 passes",
            (execution.get("current_step") == 6 and execution.get("current_role") == "database_engineer")
            or (execution.get("current_step") == 7 and execution.get("current_role") == "senior_data_engineer")
            or (execution.get("current_step") == 8 and execution.get("current_role") == "data_profiling_specialist")
            or (execution.get("current_step") == 9 and execution.get("current_role") == "data_quality_engineer")
            or (execution.get("current_step") == 10 and execution.get("current_role") == "data_security_privacy_engineer")
            or (execution.get("current_step") == 11 and execution.get("current_role") == "database_security_specialist")
            or (execution.get("current_step") == 12 and execution.get("current_role") == "dependency_discovery_engineer" and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 13 and execution.get("current_role") == "schema_matching_engineer" and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 14 and execution.get("current_role") == "entity_resolution_engineer" and execution.get("last_completed_step") == 13 and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 15 and execution.get("current_role") == "applied_ml_engineer" and execution.get("last_completed_step") == 14 and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 16 and execution.get("current_role") == "llm_semantic_ai_engineer" and execution.get("last_completed_step") == 15 and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 17 and execution.get("current_role") == "evidence_fusion_engineer" and execution.get("last_completed_step") == 16 and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 18 and execution.get("current_role") == "ml_evaluation_engineer" and execution.get("last_completed_step") == 17 and gates.get("G3_SOURCE_SAFETY") == "PASS")
            or (execution.get("current_step") == 19 and execution.get("current_role") == "canonical_model_engineer" and execution.get("last_completed_step") == 18 and gates.get("G3_SOURCE_SAFETY") == "PASS"),
        )

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
    reviews = load_yaml(arch_root / "review_checkpoints.yml").get("checkpoints", [])
    component_ids = {item.get("component_id") for item in components}
    interface_ids = {item.get("interface_id") for item in interfaces}
    stage_ids = {item.get("stage_id") for item in stages}
    review_ids = {item.get("checkpoint_id") for item in reviews}
    ownership = parsed_specs.get("ownership_map.yml", {})
    owned_components = {item.get("component_id") for item in ownership.get("components", [])}
    owned_interfaces = {item.get("interface_id") for item in ownership.get("interfaces", [])}
    owned_stages = {item.get("stage_id") for item in ownership.get("stages", [])}
    check("ownership covers exactly all 40 components", len(component_ids) == 40 and owned_components == component_ids)
    check("ownership covers exactly all 13 interfaces", len(interface_ids) == 13 and owned_interfaces == interface_ids)
    check("ownership covers exactly all 20 stages", len(stage_ids) == 20 and owned_stages == stage_ids)
    check("every component has owner and test strategy", all(item.get("primary_specialist") and item.get("test_strategy") for item in ownership.get("components", [])))
    check("every interface has owner and test strategy", all(item.get("owner_component") and item.get("test_strategy") for item in ownership.get("interfaces", [])))
    check("every stage has owner, step and test strategy", all(item.get("owner_component") and item.get("implementation_step") and item.get("test_strategy") for item in ownership.get("stages", [])))
    component_by_id = {item.get("component_id"): item for item in components}
    ownership_by_id = {item.get("component_id"): item for item in ownership.get("components", [])}
    check("component implementation ownership matches ownership map", ownership_consistent(components, ownership.get("components", [])))
    check("no component retains deferred Step05 ownership", not any("deferred to Step 05" in str(item) for item in components))
    check("component statuses are planned or explicitly implemented", all(item.get("implementation_status") in {"PLANNED", "IMPLEMENTED"} for item in components))
    check("domain contract family ownership is separated from DBA bootstrap", semantic_ownership_valid(ownership) and ownership_by_id.get("domain.contracts", {}).get("implementation_scope") == "bootstrap-only")
    check("composition root and ControlStore Step06 ownership are scoped", ownership_by_id.get("composition.root", {}).get("implementation_scope") == "bootstrap-only" and ownership_by_id.get("persistence.control_store", {}).get("comprehensive_platform_owner") == "Step23 Data Platform Engineer")

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
    source_interface = next((item for item in interfaces if item.get("interface_id") == "SourceAdapter"), {})
    check("SourceAdapter exposes explicit discovery and bounded-snapshot operations", {item.get("operation_id") for item in source_interface.get("operations", [])} == {"discover_source", "create_bounded_snapshot"})
    check("source lifecycle component/stage/matrix topology is coherent", source_lifecycle_valid(components, stages, contracts, source_interface))
    check("critical producer/component/stage/output topology is coherent", critical_topology_valid(components, stages, ownership.get("stages", []), contracts))

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
    check("Step06 handoff is a real database foundation", step06_plan_valid(plan))
    check("implementation plan delegates component ownership to the canonical ownership map", plan.get("component_ownership_policy", {}).get("source") == "docs/engineering/specs/ownership_map.yml")
    check("work packages cover all specialist steps", sorted({step for package in plan.get("work_packages", []) for step in package.get("steps", [])}) == list(range(1, 42)))
    gate_spec = parsed_specs.get("gate_map.yml", {})
    gate_items = gate_spec.get("gates", [])
    gate_ids = [item.get("gate_id") for item in gate_items]
    check("gate map contains G0-G15 exactly once", gate_ids == [f"G{i}" for i in range(16)])
    check("gate map preserves G3-G15 pending", all(item.get("status") == "PENDING" for item in gate_items if item.get("gate_id") in {f"G{i}" for i in range(3, 16)}))
    check("gate map status matches current G2 phase", next((item.get("status") for item in gate_items if item.get("gate_id") == "G2"), None) == ("PENDING" if mode == "pre" else "PASS"))
    check("gate map matches canonical Master Sequence semantics", gate_semantics_valid(gate_items, require_pass=mode == "post"))

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
    risk_spec = parsed_specs.get("technical_risks.yml", {})
    check("technical risk spec has required qualitative fields", risks_valid(risk_spec))
    check("documented paths exist or are explicitly future", "future runtime namespace" in (ENG / "REPOSITORY_STRUCTURE.md").read_text(encoding="utf-8") and "future" in (ENG / "IMPLEMENTATION_READINESS_AUDIT.md").read_text(encoding="utf-8").lower())
    structure_text = (ENG / "REPOSITORY_STRUCTURE.md").read_text(encoding="utf-8")
    check(
        "implementation phase claims are state-aware",
        "future runtime namespace" in structure_text
        and (implementation_is_authorized(state) or (not (ROOT / "src").exists() and not (ROOT / "pyproject.toml").exists())),
    )
    check("source read-only and no OSS runtime are explicit", "read-only" in (ENG / "OSS_INTEGRATION_PLAN.md").read_text(encoding="utf-8").lower() or "read-only" in (ENG / "CODING_STANDARDS.md").read_text(encoding="utf-8").lower())
    oss_text = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in ["docs/02_OPEN_SOURCE_REUSE_AND_CLONE_PLAN.md", "docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base/base_reports/02_OPEN_SOURCE_REUSE_AND_CLONE_PLAN.md"])
    check("OSS report does not assign canonical mapping to Splink", not re.search(r"Output:\s*\n(?:\s*-.*\n){0,4}\s*-\s*`?SourceRecordCanonicalMap", oss_text))
    check("no research/oss clone exists", not any(path.name != "README.md" for path in (ROOT / "research" / "oss").rglob("*")))

    # 36-40: state-aware previous validators and integrity checks.
    validator_results = []
    for filename in ["validate_domain_docs.py", "validate_data_architecture.py", "validate_solution_architecture.py"]:
        result = subprocess.run([sys.executable, str(ROOT / "tools" / filename)], cwd=ROOT, text=True, capture_output=True)
        validator_results.append(result.returncode == 0)
    check("prior domain/data/solution validators pass", all(validator_results))
    check("review checkpoint specification has four checkpoints", len(reviews) == 4 and {item.get("checkpoint_id") for item in reviews} == {"REVIEW_EVIDENCE_DECISIONS", "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN", "REVIEW_MATERIALIZATION_PLAN"})
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README status is phase-accurate", ("Step 05" in readme_text or "Steps 01–05" in readme_text) and (mode == "post" or mode == "pre"))
    check("Step 05 audit records ER correction", "SourceRecordCanonicalMap" in (ENG / "CROSS_ARTIFACT_CONSISTENCY_AUDIT.md").read_text(encoding="utf-8") and "corrected" in (ENG / "CROSS_ARTIFACT_CONSISTENCY_AUDIT.md").read_text(encoding="utf-8"))
    check("execution log exists", (ROOT / "docs" / "execution" / "SPECIALIST_EXECUTION_LOG.md").is_file())
    check("gate map has required evidence and owners", all(item.get("owner_step") and item.get("required_evidence") for item in gate_items))
    if mode == "post":
        review_text = (ROOT / "docs" / "execution" / "STEP05_TECHNICAL_LEAD_REVIEW.md").read_text(encoding="utf-8")
        gate_text = (ROOT / "docs" / "execution" / "gates" / "G2_ARCHITECTURE_READY.md").read_text(encoding="utf-8")
        log_text = (ROOT / "docs" / "execution" / "SPECIALIST_EXECUTION_LOG.md").read_text(encoding="utf-8")
        check("post-gate validator output is persisted in all three receipts", post_gate_evidence_valid(review_text, gate_text, log_text))

    negative_total = 0
    negative_passed = 0

    def negative(name: str, condition: bool) -> None:
        nonlocal negative_total, negative_passed
        negative_total += 1
        if condition:
            negative_passed += 1
        else:
            errors.append(f"negative test failed: {name}")

    source_components = copy.deepcopy(components)
    source_stages = copy.deepcopy(stages)
    source_contracts = copy.deepcopy(contracts)
    source_interface_copy = copy.deepcopy(source_interface)
    next(item for item in source_components if item.get("component_id") == "application.discovery")["input_contracts"].append("SourceSnapshot")
    negative("discovery cannot require SourceSnapshot", not source_lifecycle_valid(source_components, source_stages, source_contracts, source_interface_copy))
    source_components = copy.deepcopy(components)
    next(item for item in source_components if item.get("component_id") == "application.discovery")["depends_on"].append("application.source_snapshot")
    negative("discovery cannot depend on source snapshot", not source_lifecycle_valid(source_components, source_stages, source_contracts, source_interface_copy))
    source_stages = copy.deepcopy(stages)
    next(item for item in source_stages if item.get("stage_id") == "SOURCE_SNAPSHOT_STAGE")["dependencies"] = []
    negative("snapshot cannot precede discovery", not source_lifecycle_valid(components, source_stages, source_contracts, source_interface_copy))
    source_contracts = copy.deepcopy(contracts)
    next(item for item in source_contracts if item.get("contract") == "SourceCatalog")["consumers"].remove("application.source_snapshot")
    negative("catalog must feed snapshot", not source_lifecycle_valid(components, stages, source_contracts, source_interface_copy))
    source_contracts = copy.deepcopy(contracts)
    next(item for item in source_contracts if item.get("contract") == "SourceSnapshot")["consumers"].append("application.discovery")
    negative("snapshot cannot feed discovery", not source_lifecycle_valid(components, stages, source_contracts, source_interface_copy))
    source_interface_copy = copy.deepcopy(source_interface)
    next(item for item in source_interface_copy["operations"] if item.get("operation_id") == "discover_source")["outputs"] = ["SourceSnapshot"]
    negative("discovery operation must return descriptors", not source_lifecycle_valid(components, stages, contracts, source_interface_copy))

    broken_ownership = copy.deepcopy(components)
    next(item for item in broken_ownership if item.get("component_id") == "application.profiling")["implementation_owner"] = "Step05 Technical Lead / Engineering Lead"
    negative("profiling cannot be owned by completed Step05", not ownership_consistent(broken_ownership, ownership.get("components", [])))
    broken_ownership = copy.deepcopy(components)
    next(item for item in broken_ownership if item.get("component_id") == "application.entity_resolution")["implementation_owner"] = "Step05 Technical Lead / Engineering Lead"
    negative("ER cannot be owned by completed Step05", not ownership_consistent(broken_ownership, ownership.get("components", [])))
    broken_family = copy.deepcopy(ownership)
    broken_family["contract_family_owners"]["canonical"] = "Step06 Database Engineer / DBA"
    negative("DBA cannot own canonical semantics", not semantic_ownership_valid(broken_family))
    broken_scope = copy.deepcopy(ownership)
    next(item for item in broken_scope["components"] if item.get("component_id") == "composition.root").pop("implementation_scope", None)
    negative("composition ownership must be scoped", next(item for item in broken_scope["components"] if item.get("component_id") == "composition.root").get("implementation_scope") != "bootstrap-only")

    broken_plan = copy.deepcopy(plan)
    broken_plan["first_implementation_slice"]["required_outputs"] = ["generic scaffold"]
    negative("Step06 must include introspection", not step06_plan_valid(broken_plan))
    broken_plan = copy.deepcopy(plan)
    next(item for item in broken_plan["work_packages"] if item.get("package_id") == "WP02")["objective"] = "Create generic scaffold"
    negative("Step06 cannot be generic scaffold only", not step06_plan_valid(broken_plan))
    broken_plan = copy.deepcopy(plan)
    broken_plan["first_implementation_slice"]["forbidden_outputs"].remove("dlt integration")
    negative("Step06 cannot implement dlt", not step06_plan_valid(broken_plan))
    broken_plan = copy.deepcopy(plan)
    broken_plan["first_implementation_slice"]["non_ownership"].remove("canonical semantics")
    negative("Step06 cannot define canonical semantics", not step06_plan_valid(broken_plan))
    broken_plan = copy.deepcopy(plan)
    broken_plan["first_implementation_slice"]["forbidden_outputs"].remove("source write path")
    negative("Step06 must remain read-only", not step06_plan_valid(broken_plan))

    broken_gates = copy.deepcopy(gate_items)
    next(item for item in broken_gates if item.get("gate_id") == "G4")["name"] = "Dependency and Adapter Baseline"
    negative("G4 must be Bounded Intelligence", not gate_semantics_valid(broken_gates))
    broken_gates = copy.deepcopy(gate_items)
    next(item for item in broken_gates if item.get("gate_id") == "G7")["name"] = "Frontend Acceptance"
    negative("G7 must be End-to-End Product", not gate_semantics_valid(broken_gates))
    broken_gates = copy.deepcopy(gate_items)
    next(item for item in broken_gates if item.get("gate_id") == "G9")["name"] = "Compatibility"
    negative("G9 must be Functional Support", not gate_semantics_valid(broken_gates))
    broken_gates = copy.deepcopy(gate_items)
    next(item for item in broken_gates if item.get("gate_id") == "G4")["owner_step"] = 12
    negative("gate owner and after-step must match Master Sequence", not gate_semantics_valid(broken_gates))

    broken_risks = copy.deepcopy(risk_spec)
    broken_risks["risks"][0].pop("likelihood")
    negative("risk likelihood is required", not risks_valid(broken_risks))
    broken_risks = copy.deepcopy(risk_spec)
    broken_risks["risks"][0].pop("detection")
    negative("risk detection is required", not risks_valid(broken_risks))
    broken_risks = copy.deepcopy(risk_spec)
    broken_risks["risks"][0].pop("future_step")
    broken_risks["risks"][0].pop("status")
    negative("risk future step and status are required", not risks_valid(broken_risks))
    negative("post-gate evidence is required", not post_gate_evidence_valid("", "", ""))

    if errors:
        print("FAIL")
        print("\n".join(f"- {item}" for item in errors))
        print(f"checks={checks}")
        return 1
    print(f"PASS: engineering_checks={checks} mode={mode} components={len(component_ids)} interfaces={len(interface_ids)} stages={len(stage_ids)} contracts={len(contracts)} gates={len(gate_items)} negative_tests={negative_passed}/{negative_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
