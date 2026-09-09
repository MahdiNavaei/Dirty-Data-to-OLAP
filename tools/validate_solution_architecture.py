"""Validate the Step 04 Software / Solution Architecture contract.

This is a documentation/specification gate. It does not import or execute
application code, and it must fail closed on missing or contradictory artifacts.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "architecture"
SPECS = ARCH / "specs"
KB = ROOT / "docs" / "Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base"
STATE = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
MANIFEST = KB / "manifest.json"

DOCS = [
    "SOFTWARE_ARCHITECTURE_CONTRACT.md",
    "COMPONENT_MODEL.md",
    "DEPENDENCY_RULES.md",
    "ENGINE_INTERFACES.md",
    "RUN_AND_STAGE_LIFECYCLE.md",
    "ARTIFACT_AND_CACHE_LIFECYCLE.md",
    "PERSISTENCE_BOUNDARIES.md",
    "FAILURE_RETRY_IDEMPOTENCY.md",
    "RUNTIME_TOPOLOGY.md",
    "EXTENSION_POINTS.md",
]
SPEC_NAMES = [
    "components.yml",
    "dependency_rules.yml",
    "engine_interfaces.yml",
    "run_state_machine.yml",
    "stage_graph.yml",
    "artifact_lifecycle.yml",
]
ADR_NAMES = [
    "ADR-0001_PROJECT_OWNED_CONTRACTS.md",
    "ADR-0002_CONTROL_STORE_AND_ARTIFACT_PLANE.md",
    "ADR-0003_RUN_AND_STAGE_LIFECYCLES.md",
    "ADR-0004_EXTERNAL_ADAPTERS_AND_CAPABILITIES.md",
    "ADR-0005_ARTIFACT_PUBLICATION_CACHE_AND_INVALIDATION.md",
    "ADR-0006_TWO_PHASE_CANONICALIZATION.md",
]

errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot parse {path.relative_to(ROOT)}: {exc}")
        return {}
    require(isinstance(value, dict), f"{path.relative_to(ROOT)} must contain a mapping")
    return value if isinstance(value, dict) else {}


def has_cycle(nodes: set[str], edges: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in edges.get(node, []):
            if child in nodes and visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in nodes)


def strings(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {strings(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(strings(item) for item in value)
    return str(value)


def check_required_files() -> None:
    for relative in DOCS:
        require((ARCH / relative).is_file(), f"missing architecture document: {relative}")
    for relative in SPEC_NAMES:
        require((SPECS / relative).is_file(), f"missing architecture spec: {relative}")
    for relative in ADR_NAMES:
        require((ROOT / "docs" / "adr" / relative).is_file(), f"missing ADR: {relative}")
    require((ROOT / "docs" / "03_SYSTEM_ARCHITECTURE.md").is_file(), "missing system architecture report")
    require(
        (KB / "base_reports" / "03_SYSTEM_ARCHITECTURE.md").is_file(),
        "missing synchronized Knowledge Base system report",
    )
    require(
        (ROOT / "docs" / "execution" / "STEP04_SOLUTION_ARCHITECTURE_REVIEW.md").is_file(),
        "missing Step 04 review artifact",
    )


def check_provenance(specs: list[dict[str, Any]]) -> None:
    for spec in specs:
        require(spec.get("schema_version") == 1, "every architecture spec must use schema_version 1")
        require(spec.get("scope"), "every architecture spec must declare scope")
        require(
            "Step 04" in str(spec.get("provenance")),
            "every architecture spec must cite Specialist Step 04 provenance",
        )


def check_components(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], int]:
    required = {
        "component_id", "name", "layer", "ownership", "responsibilities",
        "input_contracts", "output_contracts", "depends_on", "side_effect_class",
        "persistence_access", "optional", "failure_behavior",
    }
    components = data.get("components", [])
    require(isinstance(components, list) and len(components) >= 30, "component model must contain at least 30 components")
    by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        require(isinstance(component, dict), "component entries must be mappings")
        if not isinstance(component, dict):
            continue
        component_id = component.get("component_id")
        require(isinstance(component_id, str) and component_id, "component ID is required")
        if not isinstance(component_id, str):
            continue
        require(component_id not in by_id, f"duplicate component ID: {component_id}")
        by_id[component_id] = component
        require(required <= set(component), f"component {component_id} is missing required fields")
        require(
            component.get("implemented_by") or component.get("extension_port"),
            f"component {component_id} needs implemented_by or extension_port",
        )
        blob = strings(component).lower()
        require("research/oss" not in blob and "research\\oss" not in blob, f"component {component_id} references research/OSS")
        require("src/datafoundry" not in blob, f"component {component_id} uses the retired namespace")
        require(component.get("optional") in (True, False), f"component {component_id} optional must be boolean")
    edges = {key: list(value.get("depends_on", [])) for key, value in by_id.items()}
    for component_id, dependencies in edges.items():
        for dependency in dependencies:
            require(dependency in by_id, f"component {component_id} references missing dependency {dependency}")
    require(not has_cycle(set(by_id), edges), "component dependency graph contains a cycle")
    namespace = data.get("namespace", {})
    require(namespace.get("python_import") == "dirty_data_to_olap", "proposed namespace must be dirty_data_to_olap")
    require(namespace.get("proposed_package_root") == "src/dirty_data_to_olap/", "proposed package root is incorrect")
    require(namespace.get("implementation_created_in_step04") is False, "Step 04 must not create implementation")
    return by_id, len(components)


def check_dependency_rules(data: dict[str, Any]) -> None:
    require(data.get("allowed_edges"), "dependency rules need allowed edges")
    require(data.get("forbidden_edges"), "dependency rules need forbidden edges")
    rules = data.get("rules", {})
    for key in (
        "no_dependency_cycles", "core_must_use_project_owned_contracts",
        "adapters_must_normalize_external_types", "composition_is_only_concrete_wiring_boundary",
        "compiler_executes_source_cleaning", "materializer_redefines_model_semantics",
        "validation_mutates_sources",
    ):
        require(key in rules and isinstance(rules[key], bool), f"dependency rule {key} must be boolean")
    require(rules.get("no_dependency_cycles") is True, "dependency cycles must be forbidden")
    require(rules.get("native_objects_persisted") is False, "native objects must not be persisted")
    require(rules.get("pickle_contract_transport") is False, "pickle contract transport must be forbidden")
    require(rules.get("compiler_executes_source_cleaning") is False, "compiler must not execute source cleaning")
    require(rules.get("materializer_redefines_model_semantics") is False, "materializer must not redefine semantics")
    require(rules.get("validation_mutates_sources") is False, "validation must not mutate sources")


def check_interfaces(data: dict[str, Any], components: dict[str, dict[str, Any]]) -> int:
    expected = {
        "SourceAdapter", "ProfilingAdapter", "DependencyDiscoveryAdapter",
        "SchemaMatchingAdapter", "EntityResolutionAdapter",
        "OptionalSemanticEvidenceAdapter", "MaterializerPort", "ControlStorePort",
        "ArtifactStorePort", "StageExecutorPort", "CapabilityRegistryPort",
    }
    required = {
        "interface_id", "purpose", "input_contracts", "output_contracts",
        "side_effects", "idempotency", "optional", "capability_id",
        "failure_mode", "adapter_component", "forbidden_behavior",
    }
    interfaces = data.get("interfaces", [])
    require({item.get("interface_id") for item in interfaces} == expected, "engine interface set is incomplete or contains extras")
    for interface in interfaces:
        require(required <= set(interface), f"interface {interface.get('interface_id')} is missing required fields")
        require(interface.get("output_contracts"), f"interface {interface.get('interface_id')} needs project-owned outputs")
        adapter_id = interface.get("adapter_component")
        require(adapter_id in components, f"interface {interface.get('interface_id')} references unknown adapter component")
        if adapter_id in components:
            require(
                components[adapter_id].get("layer") in {"adapters", "persistence", "runtime"},
                f"interface {interface.get('interface_id')} adapter boundary has invalid layer",
            )
        blob = strings(interface.get("output_contracts")).lower()
        for native in ("native", "dlt", "dataprofiler", "desbordante", "valentine", "splink"):
            require(native not in blob, f"interface {interface.get('interface_id')} leaks native output type {native}")
        require(interface.get("optional") in (True, False), f"interface {interface.get('interface_id')} optional must be boolean")
    return len(interfaces)


def check_run_machine(data: dict[str, Any]) -> None:
    expected = {"CREATED", "RUNNING", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "SUCCEEDED"}
    states = set(data.get("states", []))
    require(states == expected, "run state set must be exact and must exclude processing stages")
    require("PARTIAL" not in states and "PARTIAL" in data.get("undefined_states", []), "PARTIAL must be rejected explicitly")
    require(data.get("stage_names_are_run_states") is False, "stage names must not be run states")
    for transition in data.get("transitions", []):
        require(transition.get("from") in states, "run transition has unknown source")
        for target in transition.get("to", []):
            require(target in states, "run transition has unknown target")
    guards = data.get("success_guards", {})
    for guard in (
        "all_required_stages_acceptable", "required_review_decisions_resolved",
        "materialization_complete", "validation_reconciliation_pass",
        "required_unresolved_record_count", "no_required_artifact_invalidated_or_incomplete",
    ):
        require(guard in guards, f"missing success guard {guard}")
    require(guards.get("validation_reconciliation_pass") is True, "final validation must guard success")
    require(guards.get("required_unresolved_record_count") == 0, "required unresolved count must be zero for success")
    semantics = data.get("semantics", {})
    require("NEEDS_REVIEW" in semantics and "human" in str(semantics["NEEDS_REVIEW"]).lower(), "NEEDS_REVIEW semantics are missing")
    require("BLOCKED" in semantics and ("unavailable" in str(semantics["BLOCKED"]).lower() or "prerequisite" in str(semantics["BLOCKED"]).lower()), "BLOCKED semantics are missing")
    require("FAILED" in semantics and ("failed" in str(semantics["FAILED"]).lower() or "invalid" in str(semantics["FAILED"]).lower()), "FAILED semantics are missing")
    require("FAILED" in data.get("resumable_states", []), "FAILED must be resumable only with a new attempt")


def check_stage_graph(data: dict[str, Any]) -> tuple[set[str], int]:
    required_fields = {
        "stage_id", "required", "dependencies", "optional_dependencies",
        "input_artifact_types", "output_artifact_types", "failure_consequence",
        "cacheable", "idempotency", "review_boundary",
    }
    stages = data.get("stages", [])
    stage_ids = {stage.get("stage_id") for stage in stages}
    required_ids = {
        "SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING",
        "DEPENDENCY_DISCOVERY", "SCHEMA_MATCHING", "QUALITY_ANALYSIS",
        "EVIDENCE_FUSION", "REVIEW_DECISIONS", "CANONICAL_HYPOTHESES",
        "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION",
        "MATERIALIZATION", "VALIDATION_RECONCILIATION",
    }
    require(required_ids <= stage_ids, "stage DAG is missing a required semantic stage")
    edges = {}
    for stage in stages:
        require(required_fields <= set(stage), f"stage {stage.get('stage_id')} is missing required fields")
        stage_id = stage.get("stage_id")
        edges[stage_id] = list(stage.get("dependencies", [])) + list(stage.get("optional_dependencies", []))
        for dependency in edges[stage_id]:
            require(dependency in stage_ids, f"stage {stage_id} references missing dependency {dependency}")
        if stage.get("required") is True:
            require(stage.get("failure_consequence") != "SKIPPED", f"required stage {stage_id} may not be silently skipped")
        if stage.get("conditional") is True:
            require("skip" in strings(stage).lower(), f"conditional stage {stage_id} needs explicit skip semantics")
    require(data.get("acyclic") is True, "stage DAG must declare acyclic=true")
    require(not has_cycle(stage_ids, edges), "stage DAG contains a cycle")
    return stage_ids, len(stages)


def check_artifacts(data: dict[str, Any]) -> None:
    require(set(data.get("statuses", [])) == {"WRITING", "COMPLETE", "INVALIDATED", "SUPERSEDED"}, "artifact statuses are incomplete")
    require(data.get("consumable_statuses") == ["COMPLETE"], "only COMPLETE artifacts may be consumed")
    require(data.get("publication_guards", {}).get("content_hash_required") is True, "artifact content hashes are mandatory")
    require(data.get("publication_guards", {}).get("atomic_registration_required") is True, "artifact publication must be atomic")
    require(data.get("publication_guards", {}).get("incomplete_not_consumable") is True, "incomplete artifacts must not be consumable")
    required_envelope = {"artifact_id", "artifact_type", "schema_version", "run_id", "stage_id", "attempt_id", "status", "content_hash", "upstream_artifact_refs", "upstream_artifact_hashes"}
    require(required_envelope <= set(data.get("envelope_fields", [])), "artifact envelope lacks provenance/hash fields")
    require(len(data.get("invalidation_triggers", [])) >= 6, "artifact invalidation triggers are incomplete")


def check_cross_artifact() -> None:
    top = ROOT / "docs" / "03_SYSTEM_ARCHITECTURE.md"
    kb_report = KB / "base_reports" / "03_SYSTEM_ARCHITECTURE.md"
    require(top.read_bytes() == kb_report.read_bytes(), "top-level and Knowledge Base system reports are not byte-identical")
    architecture_text = "\\n".join((ARCH / item).read_text(encoding="utf-8") for item in DOCS)
    architecture_text += "\\n" + top.read_text(encoding="utf-8")
    require("dirty_data_to_olap" in architecture_text, "architecture namespace is absent")
    require("src/datafoundry" not in architecture_text, "retired namespace remains active in architecture")
    require("Control Store" in architecture_text and "Artifact" in architecture_text, "control/data plane distinction is missing")
    require("canonical hypotheses" in architecture_text.lower() and "canonical finalization" in architecture_text.lower(), "two-phase canonical lifecycle is missing")
    require("compiler" in architecture_text.lower() and "materializer" in architecture_text.lower() and "validation" in architecture_text.lower(), "compiler/materializer/validation separation is missing")
    require("UNRESOLVED" in (ROOT / "docs" / "data-architecture" / "specs" / "record_accounting.yml").read_text(encoding="utf-8"), "record accounting UNRESOLVED semantics are missing")
    require(not (ROOT / "src").exists(), "Step 04 must not create src/")
    oss_root = ROOT / "research" / "oss"
    if oss_root.exists():
        oss_files = [path.relative_to(oss_root).as_posix() for path in oss_root.rglob("*") if path.is_file()]
        require(oss_files == ["README.md"], "research/oss must contain only its governance README, not a clone")
    for relative in ["architecture/SOFTWARE_ARCHITECTURE_CONTRACT.md", "architecture/specs/components.yml", "adr/ADR-0006_TWO_PHASE_CANONICALIZATION.md"]:
        require(relative in top.read_text(encoding="utf-8"), f"system report does not link {relative}")


def check_manifest() -> None:
    import json

    require(MANIFEST.is_file(), "Knowledge Base manifest is missing")
    if not MANIFEST.is_file():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"manifest is not valid JSON: {exc}")
        return
    entries = manifest.get("files", [])
    require(len(entries) == 56, f"manifest entry count must remain 56, got {len(entries)}")
    for entry in entries:
        relative = entry.get("path")
        target = KB / relative if isinstance(relative, str) else ROOT / "__missing__"
        require(target.is_file(), f"manifest target missing: {relative}")
        if target.is_file():
            data = target.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            require(len(data) == entry.get("bytes"), f"manifest byte mismatch: {relative}")
            require(digest == entry.get("sha256"), f"manifest SHA mismatch: {relative}")


def check_state() -> None:
    state = load_yaml(STATE)
    execution = state.get("specialist_execution", {})
    gates = state.get("gates", {})
    require(execution.get("last_completed_step") == 4, "execution state must record completed Step 04")
    require(execution.get("last_completed_role") == "solution_architect", "execution state role must be solution_architect")
    require(execution.get("current_step") == 5 and execution.get("current_role") == "technical_lead", "execution state must hand off to Step 05")
    require(execution.get("current_specialist") == "Step 05 — Technical Lead / Engineering Lead", "current specialist must be Step 05")
    require(execution.get("next_step") == "Step 05 — Technical Lead / Engineering Lead", "next step must be Step 05")
    require(gates.get("G0_PRODUCT_CONTRACT") == "PASS" and gates.get("G1_DOMAIN_TRUTH") == "PASS", "G0/G1 must remain PASS")
    require(gates.get("G2_ARCHITECTURE_READY") == "PENDING", "G2 must remain PENDING")
    later_gate_keys = [
        "G3_SOURCE_SAFETY", "G4_BOUNDED_INTELLIGENCE", "G5_INFERENCE_VALIDITY",
        "G6_DATA_CORRECTNESS", "G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD",
        "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY", "G11_RESILIENCE",
        "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE",
    ]
    require(all(gates.get(key) == "PENDING" for key in later_gate_keys), "G3-G15 must remain PENDING")
    require(state.get("blocked") is False, "execution state must not be blocked")


def lifecycle_rejects(proposal: dict[str, Any]) -> bool:
    run = proposal.get("run_status")
    stage = proposal.get("stage_status")
    if run in {"DISCOVERING", "PROFILING", "MATERIALIZING", "VALIDATING", "PARTIAL"}:
        return True
    if run == "SUCCEEDED" and (
        proposal.get("validation_pass") is not True
        or proposal.get("required_unresolved", 0) != 0
        or proposal.get("required_artifacts_complete") is not True
    ):
        return True
    if run == "FAILED" and proposal.get("overwrite_prior_artifact"):
        return True
    if run == "BLOCKED" and proposal.get("validation_error"):
        return True
    if run == "NEEDS_REVIEW" and proposal.get("engine_crash"):
        return True
    if stage == "SKIPPED" and proposal.get("required_stage"):
        return True
    if proposal.get("cancelled") and proposal.get("publish_incomplete"):
        return True
    if proposal.get("retry_same_attempt"):
        return True
    return False


def dependency_rejects(proposal: dict[str, Any]) -> bool:
    return any(
        proposal.get(key) for key in (
            "core_imports_adapter", "native_output", "native_persisted",
            "external_engine_in_contract", "adapter_to_adapter", "raw_rows_in_control_store",
            "compiler_cleans_source", "materializer_changes_grain", "validation_mutates_source",
            "runtime_depends_on_research_oss",
        )
    )


def artifact_cache_rejects(proposal: dict[str, Any]) -> bool:
    if proposal.get("consume_status") and proposal.get("consume_status") != "COMPLETE":
        return True
    if proposal.get("filename_only_key"):
        return True
    if proposal.get("schema_changed_without_invalidation"):
        return True
    if proposal.get("er_config_changed_without_invalidation"):
        return True
    if proposal.get("grain_changed_without_invalidation"):
        return True
    if proposal.get("incompatible_replay"):
        return True
    if proposal.get("failed_materialization_accepted"):
        return True
    return False


def check_negative_tests() -> tuple[int, int, int]:
    lifecycle_cases = [
        {"run_status": "PROFILING"},
        {"run_status": "PARTIAL"},
        {"run_status": "SUCCEEDED", "validation_pass": False},
        {"run_status": "SUCCEEDED", "validation_pass": True, "required_unresolved": 1},
        {"run_status": "FAILED", "overwrite_prior_artifact": True},
        {"run_status": "BLOCKED", "validation_error": True},
        {"run_status": "NEEDS_REVIEW", "engine_crash": True},
        {"stage_status": "SKIPPED", "required_stage": True},
        {"cancelled": True, "publish_incomplete": True},
        {"retry_same_attempt": True},
    ]
    dependency_cases = [
        {"core_imports_adapter": True}, {"native_output": True},
        {"native_persisted": True}, {"external_engine_in_contract": True},
        {"adapter_to_adapter": True}, {"raw_rows_in_control_store": True},
        {"compiler_cleans_source": True}, {"materializer_changes_grain": True},
        {"validation_mutates_source": True}, {"runtime_depends_on_research_oss": True},
    ]
    artifact_cases = [
        {"consume_status": "WRITING"}, {"filename_only_key": True},
        {"schema_changed_without_invalidation": True},
        {"er_config_changed_without_invalidation": True},
        {"grain_changed_without_invalidation": True},
        {"incompatible_replay": True}, {"failed_materialization_accepted": True},
    ]
    lifecycle_pass = sum(lifecycle_rejects(case) for case in lifecycle_cases)
    dependency_pass = sum(dependency_rejects(case) for case in dependency_cases)
    artifact_pass = sum(artifact_cache_rejects(case) for case in artifact_cases)
    require(lifecycle_pass == 10, f"lifecycle negative tests {lifecycle_pass}/10")
    require(dependency_pass == 10, f"dependency negative tests {dependency_pass}/10")
    require(artifact_pass == 7, f"artifact/cache negative tests {artifact_pass}/7")
    return lifecycle_pass, dependency_pass, artifact_pass


def main() -> int:
    check_required_files()
    loaded = [load_yaml(SPECS / name) for name in SPEC_NAMES]
    check_provenance(loaded)
    components, component_count = check_components(loaded[0])
    check_dependency_rules(loaded[1])
    interface_count = check_interfaces(loaded[2], components)
    stage_ids, stage_count = check_stage_graph(loaded[4])
    check_run_machine(loaded[3])
    check_artifacts(loaded[5])
    check_cross_artifact()
    check_manifest()
    check_state()
    negative_counts = check_negative_tests()
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: "
        f"components={component_count} interfaces={interface_count} stages={stage_count} "
        f"run_states=7 accounting=preserved "
        f"lifecycle_negative_tests={negative_counts[0]}/10 "
        f"dependency_negative_tests={negative_counts[1]}/10 "
        f"artifact_cache_negative_tests={negative_counts[2]}/7"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
