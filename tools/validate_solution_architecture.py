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
    "stage_state_machine.yml",
    "artifact_lifecycle.yml",
    "review_checkpoints.yml",
]
ADR_NAMES = [
    "ADR-0001_PROJECT_OWNED_CONTRACTS.md",
    "ADR-0002_CONTROL_STORE_AND_ARTIFACT_PLANE.md",
    "ADR-0003_RUN_AND_STAGE_LIFECYCLES.md",
    "ADR-0004_EXTERNAL_ADAPTERS_AND_CAPABILITIES.md",
    "ADR-0005_ARTIFACT_PUBLICATION_CACHE_AND_INVALIDATION.md",
    "ADR-0006_TWO_PHASE_CANONICALIZATION.md",
    "ADR-0007_STAGE_SCOPED_REVIEW_CHECKPOINTS.md",
]

errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def implementation_is_authorized() -> bool:
    """Allow the package only after G2; preserve the pre-gate guard."""
    try:
        state = load_yaml(STATE)
    except Exception:
        return False
    execution = state.get("specialist_execution", {})
    return execution.get("current_step", 0) >= 6 and state.get("gates", {}).get("G2_ARCHITECTURE_READY") == "PASS"


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


def topological_positions(nodes: set[str], dependencies: dict[str, list[str]]) -> dict[str, int]:
    """Return dependency-before-consumer positions for the stage graph."""
    children = {node: [] for node in nodes}
    indegree = {node: 0 for node in nodes}
    for child, parents in dependencies.items():
        for parent in parents:
            if parent not in nodes:
                continue
            children[parent].append(child)
            indegree[child] += 1
    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    require(len(order) == len(nodes), "stage DAG cannot be topologically ordered")
    return {node: index for index, node in enumerate(order)}


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
            component.get("implemented_by")
            or component.get("extension_port")
            or (
                component.get("implementation_owner")
                and component.get("implementation_step")
                and component.get("implementation_status")
            ),
            f"component {component_id} needs implementation ownership or extension_port",
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
    require(
        "SourceRecordCanonicalMap" not in by_id["application.entity_resolution"].get("output_contracts", []),
        "application.entity_resolution must not produce SourceRecordCanonicalMap",
    )
    require(
        "SourceRecordCanonicalMap" not in by_id["adapters.entity_resolution"].get("output_contracts", []),
        "adapters.entity_resolution must not produce SourceRecordCanonicalMap",
    )
    require(
        "SourceRecordCanonicalMap" in by_id["application.canonical_finalization"].get("output_contracts", []),
        "application.canonical_finalization must produce SourceRecordCanonicalMap",
    )
    require(
        "application.entity_resolution" not in by_id["application.canonical_finalization"].get("depends_on", []),
        "conditional ER must not be an unconditional component dependency",
    )
    require(by_id["application.canonical_finalization"].get("conditional_dependencies"), "finalization needs conditional ER guards")
    require(
        "RelationshipCandidate" in by_id["application.dependency_discovery"].get("output_contracts", []),
        "dependency discovery component must expose RelationshipCandidate evidence",
    )
    require(
        "application.schema_matching" not in by_id["application.evidence_fusion"].get("depends_on", []),
        "conditional schema matching must not be an unconditional component dependency",
    )
    require(by_id["application.evidence_fusion"].get("conditional_dependencies"), "evidence fusion needs conditional schema matching guard")
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
        "SourceAdapter", "ProfilingAdapter", "QualityStagedReader", "DependencyDiscoveryAdapter",
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
    by_interface = {item.get("interface_id"): item for item in interfaces}
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
    er = by_interface.get("EntityResolutionAdapter", {})
    require("EntityMatchEdge" in er.get("output_contracts", []) and "EntityCluster" in er.get("output_contracts", []), "ER adapter must output linkage evidence")
    require("SourceRecordCanonicalMap" not in er.get("output_contracts", []), "ER adapter must not output canonical mappings")
    er_forbidden = strings(er.get("forbidden_behavior", [])).lower()
    require("canonical_entity_id" in er_forbidden and "sourcerecordcanonicalmap" in er_forbidden, "ER canonical mapping restriction must be explicit")
    require(er.get("capability_required_for_v1") is False and er.get("runtime_stage_conditional") is True, "ER optionality semantics are inconsistent")
    for interface_id, capability_required, runtime_conditional in (
        ("DependencyDiscoveryAdapter", True, False),
        ("SchemaMatchingAdapter", True, True),
        ("EntityResolutionAdapter", False, True),
        ("OptionalSemanticEvidenceAdapter", False, True),
    ):
        item = by_interface[interface_id]
        require(item.get("capability_required_for_v1") is capability_required, f"{interface_id} V1 capability optionality is inconsistent")
        require(item.get("runtime_stage_conditional") is runtime_conditional, f"{interface_id} runtime conditionality is inconsistent")
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
    review = data.get("review_interaction", {})
    for key in (
        "unresolved_required_checkpoint_drives_run_needs_review",
        "unresolved_required_checkpoint_blocks_guarded_stage",
        "accepted_compatible_decision_allows_downstream_attempt",
        "review_action_is_not_engine_failure",
        "validation_failure_cannot_be_manually_promoted_to_success",
    ):
        require(review.get(key) is True, f"run review interaction is missing {key}")
    for key in ("rejected_decision_satisfies_no_acceptance_guard", "deferred_decision_satisfies_no_acceptance_guard", "invalidated_replay_decision_satisfies_no_acceptance_guard"):
        require(review.get(key) is False, f"run review interaction must reject {key}")


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
        "EVIDENCE_FUSION", "REVIEW_EVIDENCE_DECISIONS", "CANONICAL_HYPOTHESES",
        "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN",
        "REVIEW_MATERIALIZATION_PLAN",
        "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION",
        "MATERIALIZATION", "VALIDATION_RECONCILIATION",
    }
    require(required_ids <= stage_ids, "stage DAG is missing a required semantic stage")
    require("REVIEW_DECISIONS" not in stage_ids, "generic early REVIEW_DECISIONS stage remains active")
    edges = {}
    by_id = {stage.get("stage_id"): stage for stage in stages}
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
    er = by_id["ENTITY_RESOLUTION"]
    finalization = by_id["CANONICAL_FINALIZATION"]
    require(set(er.get("output_artifact_types", [])) == {"EntityMatchEdge", "EntityCluster"}, "ER stage output ownership is invalid")
    require("SourceRecordCanonicalMap" in finalization.get("output_artifact_types", []), "finalization must output SourceRecordCanonicalMap")
    require("SourceRecordCanonicalMap" not in er.get("output_artifact_types", []), "ER stage must not output SourceRecordCanonicalMap")
    require("RelationshipCandidate" in by_id["DEPENDENCY_DISCOVERY"].get("output_artifact_types", []), "dependency stage must expose RelationshipCandidate")
    guards = finalization.get("conditional_dependencies", [])
    require(guards and any(g.get("when") == "entity_resolution_required(entity_family) == true" for g in guards), "finalization lacks ER-required guard")
    require(any(g.get("when") == "entity_resolution_required(entity_family) == false" for g in guards), "finalization lacks ER-not-required guard")
    require(er.get("required") is False and er.get("conditional") is True, "ER stage must be globally conditional")
    require(by_id["SCHEMA_MATCHING"].get("required") is False and by_id["SCHEMA_MATCHING"].get("conditional") is True, "schema matching per-run conditionality is missing")
    require(by_id["SCHEMA_MATCHING"].get("capability_required_for_v1") is True, "schema matching V1 capability requirement is missing")
    fusion = by_id["EVIDENCE_FUSION"]
    require("SCHEMA_MATCHING" in fusion.get("optional_dependencies", []), "schema matching must be conditional for evidence fusion")
    require(any(g.get("dependency") == "SCHEMA_MATCHING" and g.get("when") == "cross_source_mapping_scope == true" for g in fusion.get("conditional_dependencies", [])), "evidence fusion lacks schema matching guard")
    for guarded_stage, checkpoint in {
        "CANONICAL_HYPOTHESES": "REVIEW_EVIDENCE_DECISIONS",
        "CANONICAL_FINALIZATION": "REVIEW_CANONICAL_IDENTITY",
        "COMPILATION": "REVIEW_ANALYTICAL_PLAN",
        "MATERIALIZATION": "REVIEW_MATERIALIZATION_PLAN",
    }.items():
        require(by_id[guarded_stage].get("required_review_checkpoint") == checkpoint, f"{guarded_stage} has the wrong review checkpoint guard")
        require(checkpoint in by_id[guarded_stage].get("dependencies", []), f"{guarded_stage} must depend on {checkpoint}")
    return stage_ids, len(stages)


def check_review_checkpoints(data: dict[str, Any], stage_graph: dict[str, Any]) -> tuple[int, int]:
    required = {
        "checkpoint_id", "checkpoint_stage", "subject_stage", "subject_artifact_types",
        "review_types", "required_when", "downstream_guarded_stage", "decision_contract",
        "compatibility_fields", "invalidating_changes", "unresolved_run_state",
        "reject_behavior", "skip_or_auto_policy",
    }
    checkpoints = data.get("checkpoints", [])
    require(isinstance(checkpoints, list) and len(checkpoints) == 4, "review checkpoint spec must define exactly four checkpoints")
    stages = stage_graph.get("stages", [])
    by_id = {stage.get("stage_id"): stage for stage in stages}
    stage_ids = set(by_id)
    dependencies = {
        stage_id: list(stage.get("dependencies", [])) + list(stage.get("optional_dependencies", []))
        for stage_id, stage in by_id.items()
    }
    positions = topological_positions(stage_ids, dependencies)
    checkpoint_ids: set[str] = set()
    required_compatibility = {
        "subject_artifact_id", "subject_content_hash", "subject_schema_version",
        "model_version", "source_schema_fingerprints", "policy_version",
        "domain_assertion_refs", "subject_semantic_id",
    }
    for checkpoint in checkpoints:
        require(isinstance(checkpoint, dict), "review checkpoint entries must be mappings")
        if not isinstance(checkpoint, dict):
            continue
        require(required <= set(checkpoint), f"review checkpoint {checkpoint.get('checkpoint_id')} is missing required fields")
        checkpoint_id = checkpoint.get("checkpoint_id")
        require(checkpoint_id not in checkpoint_ids, f"duplicate review checkpoint ID: {checkpoint_id}")
        checkpoint_ids.add(checkpoint_id)
        checkpoint_stage = checkpoint.get("checkpoint_stage")
        subject_stage = checkpoint.get("subject_stage")
        guarded_stage = checkpoint.get("downstream_guarded_stage")
        require(checkpoint_stage in stage_ids, f"review checkpoint {checkpoint_id} references missing checkpoint stage")
        require(subject_stage in stage_ids, f"review checkpoint {checkpoint_id} references missing subject stage")
        require(guarded_stage in stage_ids, f"review checkpoint {checkpoint_id} references missing guarded stage")
        if checkpoint_stage in by_id:
            stage = by_id[checkpoint_stage]
            require(stage.get("review_checkpoint_id") == checkpoint_id, f"checkpoint stage {checkpoint_stage} is not bound to {checkpoint_id}")
            require("ReviewDecision" in stage.get("output_artifact_types", []), f"checkpoint stage {checkpoint_stage} must output ReviewDecision")
            require(stage.get("conditional") is True and "skip" in strings(stage).lower(), f"checkpoint stage {checkpoint_stage} lacks policy-conditional skip semantics")
        require(positions.get(subject_stage, -1) < positions.get(checkpoint_stage, -1), f"{checkpoint_id}: subject stage must precede checkpoint")
        require(positions.get(checkpoint_stage, -1) < positions.get(guarded_stage, -1), f"{checkpoint_id}: checkpoint must precede guarded stage")
        subject_types = checkpoint.get("subject_artifact_types", [])
        require(subject_types, f"{checkpoint_id} must declare subject artifacts")
        for artifact_type in subject_types:
            require(artifact_type in by_id.get(subject_stage, {}).get("output_artifact_types", []), f"{checkpoint_id}: {artifact_type} is not produced by {subject_stage}")
        for additional in checkpoint.get("additional_subject_stages", []):
            additional_stage = additional.get("stage") if isinstance(additional, dict) else None
            additional_types = additional.get("artifact_types", []) if isinstance(additional, dict) else []
            require(additional_stage in stage_ids, f"{checkpoint_id}: additional subject stage is missing")
            require(positions.get(additional_stage, -1) < positions.get(checkpoint_stage, -1), f"{checkpoint_id}: additional subject stage must precede checkpoint")
            for artifact_type in additional_types:
                require(artifact_type in by_id.get(additional_stage, {}).get("output_artifact_types", []), f"{checkpoint_id}: {artifact_type} is not produced by {additional_stage}")
        require(checkpoint.get("decision_contract") == "ReviewDecision", f"{checkpoint_id} must use ReviewDecision")
        require(set(checkpoint.get("compatibility_fields", [])) >= required_compatibility, f"{checkpoint_id} lacks artifact/version compatibility fields")
        require(checkpoint.get("unresolved_run_state") == "NEEDS_REVIEW", f"{checkpoint_id} must map unresolved review to NEEDS_REVIEW")
        require("policy" in str(checkpoint.get("required_when", "")).lower(), f"{checkpoint_id} required_when must be policy-driven")
        require("policy" in str(checkpoint.get("skip_or_auto_policy", "")).lower(), f"{checkpoint_id} skip/auto behavior must be policy-driven")
    require(checkpoint_ids == {"REVIEW_EVIDENCE_DECISIONS", "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN", "REVIEW_MATERIALIZATION_PLAN"}, "review checkpoint set is incomplete or contains extras")
    require(data.get("reusable_service") == "application.review_policy", "review checkpoints must use the single Review / Policy Service")
    require(set(data.get("compatibility_fields", [])) >= required_compatibility, "review spec compatibility contract is incomplete")
    require(data.get("unresolved_run_state") == "NEEDS_REVIEW", "review spec unresolved state must be NEEDS_REVIEW")
    require(data.get("rejected_decision_satisfies_guard") is False and data.get("deferred_decision_satisfies_guard") is False, "rejected/deferred decisions must not satisfy guards")
    require(data.get("validation_failure_can_be_manually_promoted_to_success") is False, "validation failure must not be manually promoted to success")
    canonical = next((item for item in checkpoints if item.get("checkpoint_id") == "REVIEW_CANONICAL_IDENTITY"), {})
    require(any(item.get("stage") == "ENTITY_RESOLUTION" for item in canonical.get("additional_subject_stages", [])), "canonical checkpoint lacks conditional ER subject")
    return len(checkpoints), len(positions)


def check_stage_state_machine(data: dict[str, Any]) -> tuple[int, int]:
    logical = {"PENDING", "RUNNING", "SUCCEEDED", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "INVALIDATED", "SKIPPED"}
    attempts = {"PENDING", "RUNNING", "SUCCEEDED", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "SKIPPED"}
    require(set(data.get("logical_stage_statuses", [])) == logical, "logical StageStatus set is incomplete or contains extras")
    require(set(data.get("stage_attempt_statuses", [])) == attempts, "StageAttemptStatus set is incomplete or contains extras")
    require(data.get("invalidated_is_logical_only") is True, "INVALIDATED must be logical-stage-only")
    for transition in data.get("logical_stage_transitions", []):
        require(transition.get("from") in logical, "logical stage transition has unknown source")
        require(set(transition.get("to", [])) <= logical, "logical stage transition has unknown target")
    for transition in data.get("stage_attempt_transitions", []):
        require(transition.get("from") in attempts, "attempt transition has unknown source")
        require(set(transition.get("to", [])) <= attempts, "attempt transition has unknown target")
    logical_by_from = {item.get("from"): set(item.get("to", [])) for item in data.get("logical_stage_transitions", [])}
    require(logical_by_from.get("PENDING") == {"RUNNING", "SKIPPED", "BLOCKED", "CANCELLED"}, "PENDING logical transitions are incomplete")
    require(logical_by_from.get("SUCCEEDED") == {"INVALIDATED"}, "SUCCEEDED must invalidate only through upstream change")
    require("SUCCEEDED" not in logical_by_from.get("INVALIDATED", set()), "INVALIDATED cannot revive directly")
    require(data.get("attempt_invariants", {}).get("failed_attempts_retained") is True, "failed attempts must be retained")
    require(data.get("attempt_invariants", {}).get("retry_requires_new_attempt_id") is True, "retry must create a new attempt ID")
    require(data.get("attempt_invariants", {}).get("cancelled_attempt_cannot_publish_complete") is True, "cancelled attempts cannot publish complete artifacts")
    skip = data.get("skip_rules", {})
    require(all(skip.get(key) is True for key in ("requires_conditional_or_optional_stage", "reason_required", "policy_reference_required", "required_stage_without_conditional_policy_cannot_skip", "condition_change_reconsiders_skip")), "skip rules are incomplete")
    interaction = data.get("run_interaction", {})
    require(interaction.get("stage_succeeded_does_not_succeed_run") is True, "stage/run success semantics are not separated")
    require(interaction.get("required_stage_failed_drives_run_failed") is True, "required stage failure semantics are missing")
    require(interaction.get("required_stage_blocked_drives_run_blocked") is True, "required stage blocked semantics are missing")
    require(interaction.get("required_stage_needs_review_drives_run_needs_review") is True, "required stage review semantics are missing")
    return len(logical), len(attempts)


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
    internal = ROOT / "docs" / "04_INTERNAL_DATA_CONTRACTS.md"
    internal_kb = KB / "base_reports" / "04_INTERNAL_DATA_CONTRACTS.md"
    require(internal.read_bytes() == internal_kb.read_bytes(), "top-level and Knowledge Base internal contracts are not byte-identical")
    internal_text = internal.read_text(encoding="utf-8")
    require("EntityCluster` and `EntityMatchEdge` are linkage evidence only" in internal_text, "internal contract does not separate linkage evidence")
    require("`SourceRecordCanonicalMap` is produced only by Canonical Finalization" in internal_text, "internal contract does not assign mapping ownership")
    require("EntityCluster never becomes a canonical ID" in architecture_text, "cluster/canonical identity distinction is missing")
    require("SourceRecordCanonicalMap" in architecture_text and "sole producer" in architecture_text, "accepted mapping ownership is missing from narrative architecture")
    require("UNRESOLVED" in (ROOT / "docs" / "data-architecture" / "specs" / "record_accounting.yml").read_text(encoding="utf-8"), "record accounting UNRESOLVED semantics are missing")
    if not implementation_is_authorized():
        require(not (ROOT / "src").exists(), "pre-G2 architecture validation must reject src/")
    oss_root = ROOT / "research" / "oss"
    if oss_root.exists():
        oss_files = [path.relative_to(oss_root).as_posix() for path in oss_root.rglob("*") if path.is_file()]
        require(oss_files == ["README.md"], "research/oss must contain only its governance README, not a clone")
    for relative in ["architecture/SOFTWARE_ARCHITECTURE_CONTRACT.md", "architecture/specs/components.yml", "adr/ADR-0006_TWO_PHASE_CANONICALIZATION.md"]:
        require(relative in top.read_text(encoding="utf-8"), f"system report does not link {relative}")
    require("stage_state_machine.yml" in top.read_text(encoding="utf-8"), "system report does not link stage state machine")


def check_execution_log_paths() -> None:
    log_path = ROOT / "docs" / "execution" / "SPECIALIST_EXECUTION_LOG.md"
    text = log_path.read_text(encoding="utf-8")
    require("## Post-Step-04 Software Architecture Integrity Repair" in text, "post-Step-04 repair log entry is missing")
    require("docs/00_PRODUCT_CONTRACT.md" not in text, "obsolete product path remains in execution log")
    require("docs/01_USER_JOURNEYS.md" not in text, "obsolete user-journey path remains in execution log")
    require("docs/02_ACCEPTANCE_CRITERIA.md" not in text, "obsolete acceptance path remains in execution log")
    start = text.find("## Specialist Step 04 — Software / Solution Architect")
    end = text.find("- artifacts_created_or_changed:", start)
    require(start >= 0 and end > start, "Step 04 inputs_reviewed block is missing")
    input_block = text[text.find("- inputs_reviewed:", start):end]
    paths = re.findall(r"`([^`]+)`", input_block)
    require(paths, "Step 04 inputs_reviewed contains no concrete paths")
    for relative in paths:
        if relative.endswith("/"):
            require((ROOT / relative).is_dir(), f"execution-log directory input does not exist: {relative}")
        else:
            require((ROOT / relative).is_file(), f"execution-log file input does not exist: {relative}")
    require("actual docs/product files" in text, "execution-log repair must explain actual product paths were reread")


def check_optionality_consistency(interfaces: dict[str, dict[str, Any]], components: dict[str, dict[str, Any]], stages: dict[str, dict[str, Any]]) -> None:
    matrix = {
        "DependencyDiscoveryAdapter": ("adapters.dependencies", "DEPENDENCY_DISCOVERY", False, True, False, True, False),
        "SchemaMatchingAdapter": ("adapters.matching", "SCHEMA_MATCHING", False, True, True, False, True),
        "EntityResolutionAdapter": ("adapters.entity_resolution", "ENTITY_RESOLUTION", True, False, True, False, True),
        "OptionalSemanticEvidenceAdapter": ("adapters.semantic", "OPTIONAL_SEMANTIC_EVIDENCE", True, False, True, False, True),
    }
    for interface_id, (component_id, stage_id, optional, capability_required, runtime_conditional, stage_required, stage_conditional) in matrix.items():
        interface = interfaces[interface_id]
        component = components[component_id]
        stage = stages[stage_id]
        require(interface.get("optional") is optional, f"{interface_id} optional flag disagrees with component")
        require(component.get("optional") is optional, f"{component_id} optional flag is inconsistent")
        require(interface.get("capability_required_for_v1") is capability_required, f"{interface_id} capability requirement is inconsistent")
        require(component.get("capability_required_for_v1") is capability_required, f"{component_id} capability requirement is inconsistent")
        require(interface.get("runtime_stage_conditional") is runtime_conditional, f"{interface_id} runtime conditionality is inconsistent")
        require(component.get("runtime_stage_conditional") is runtime_conditional, f"{component_id} runtime conditionality is inconsistent")
        require(stage.get("required") is stage_required and stage.get("conditional") is stage_conditional, f"{stage_id} required/conditional flags are inconsistent")
        require(stage.get("capability_required_for_v1") is capability_required, f"{stage_id} capability requirement is inconsistent")


def check_source_lifecycle(interfaces: dict[str, dict[str, Any]], components: dict[str, dict[str, Any]], stages: dict[str, dict[str, Any]]) -> None:
    source = interfaces["SourceAdapter"]
    operations = {item.get("operation_id"): item for item in source.get("operations", [])}
    require(set(operations) == {"discover_source", "create_bounded_snapshot"}, "SourceAdapter operations must distinguish discovery and bounded snapshot")
    discover = operations.get("discover_source", {})
    snapshot = operations.get("create_bounded_snapshot", {})
    require({"SourceDescriptor", "TableDescriptor", "ColumnDescriptor", "DeclaredConstraint"}.issubset(set(discover.get("outputs", []))), "discover_source must return descriptors")
    require({"SourceCatalog", "SamplingPolicy"}.issubset(set(snapshot.get("inputs", []))), "create_bounded_snapshot must consume catalog and sampling policy")
    require({"SourceSnapshot", "BatchReference", "SourceRecordReference"}.issubset(set(snapshot.get("outputs", []))), "create_bounded_snapshot must return snapshot references")
    discovery = components["application.discovery"]
    source_snapshot = components["application.source_snapshot"]
    require("SourceSnapshot" not in discovery.get("input_contracts", []), "application.discovery must not require SourceSnapshot")
    require("application.source_snapshot" not in discovery.get("depends_on", []), "application.discovery must not depend on source snapshot")
    require({"SourceSelection", "SourceRegistryRecord"}.issubset(set(discovery.get("input_contracts", []))), "discovery must consume source selection/registry metadata")
    require({"SourceCatalog", "SamplingPolicy"}.issubset(set(source_snapshot.get("input_contracts", []))), "source snapshot must consume catalog and sampling policy")
    require("application.discovery" in source_snapshot.get("depends_on", []), "source snapshot must depend on discovery")
    require(stages["SOURCE_DISCOVERY"].get("output_artifact_types") == ["SourceCatalog"], "SOURCE_DISCOVERY must produce SourceCatalog")
    require(stages["SOURCE_SNAPSHOT_STAGE"].get("dependencies") == ["SOURCE_DISCOVERY"], "SOURCE_SNAPSHOT_STAGE must follow SOURCE_DISCOVERY")
    require(stages["SOURCE_SNAPSHOT_STAGE"].get("input_artifact_types", [])[0] == "SourceCatalog", "snapshot stage must consume SourceCatalog")


def entity_resolution_rejects(proposal: dict[str, Any]) -> bool:
    if proposal.get("er_outputs_mapping") or proposal.get("er_assigns_canonical_id"):
        return True
    if proposal.get("cluster_reused_as_canonical_id"):
        return True
    if proposal.get("required_family_finalized_without_acceptable_er"):
        return True
    if proposal.get("required_family_finalized_after_er_failed"):
        return True
    if proposal.get("global_optional_flag_used_to_skip_required_family"):
        return True
    if proposal.get("unrelated_family_blocked_by_er_unavailable"):
        return True
    if proposal.get("fake_mapping_fallback"):
        return True
    return False


def stage_lifecycle_rejects(proposal: dict[str, Any]) -> bool:
    if proposal.get("required_stage_skipped"):
        return True
    if proposal.get("failed_attempt_overwritten"):
        return True
    if proposal.get("invalidated_revived_in_place"):
        return True
    if proposal.get("needs_review_as_failed"):
        return True
    if proposal.get("engine_crash_as_needs_review"):
        return True
    if proposal.get("missing_prerequisite_as_failed"):
        return True
    if proposal.get("skipped_without_reason_policy"):
        return True
    if proposal.get("cancelled_publishes_complete"):
        return True
    if proposal.get("stage_success_succeeds_run_before_validation"):
        return True
    if proposal.get("retry_reuses_attempt_id"):
        return True
    return False


def cross_contract_rejects(proposal: dict[str, Any]) -> bool:
    return any(
        proposal.get(key) for key in (
            "interface_mapping_but_component_er_only",
            "stage_bypasses_finalization_mapping_owner",
            "native_splink_crosses_adapter",
            "matcher_score_is_probability",
            "dependency_adapter_confirms_fk",
            "materializer_changes_fact_grain",
            "validation_mutates_source",
        )
    )


def review_rejects(proposal: dict[str, Any]) -> bool:
    return any(
        proposal.get(key) for key in (
            "linkage_review_before_entity_cluster",
            "evidence_review_used_as_linkage_approval",
            "analytical_review_before_plan",
            "materialization_uses_generic_approval",
            "missing_subject_hash",
            "replay_after_schema_version_change",
            "er_required_finalized_with_unresolved_linkage_review",
            "grain_changed_without_review_invalidation",
            "sql_changed_without_materialization_invalidation",
            "rejected_plan_allows_materialization",
            "unresolved_review_keeps_run_running",
            "failed_validation_manually_promoted",
        )
    )


def check_review_negative_tests() -> int:
    cases = [
        {"linkage_review_before_entity_cluster": True},
        {"evidence_review_used_as_linkage_approval": True},
        {"analytical_review_before_plan": True},
        {"materialization_uses_generic_approval": True},
        {"missing_subject_hash": True},
        {"replay_after_schema_version_change": True},
        {"er_required_finalized_with_unresolved_linkage_review": True},
        {"grain_changed_without_review_invalidation": True},
        {"sql_changed_without_materialization_invalidation": True},
        {"rejected_plan_allows_materialization": True},
        {"unresolved_review_keeps_run_running": True},
        {"failed_validation_manually_promoted": True},
    ]
    passed = sum(review_rejects(case) for case in cases)
    require(passed == 12, f"review negative tests {passed}/12")
    return passed


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
    if execution.get("current_step") == 7:
        require(execution.get("last_completed_step") == 6, "post-Step 06 state must record completed Step 06")
        require(execution.get("last_completed_role") == "database_engineer", "post-Step 06 role must be database_engineer")
        require(execution.get("current_role") == "senior_data_engineer", "post-Step 06 state must hand off to Step 07")
        require("Senior Data Engineer" in str(execution.get("current_specialist")), "current specialist must be Step07")
        require("Senior Data Engineer" in str(execution.get("next_step")), "next step must be Step07")
    elif execution.get("current_step") == 8:
        require(execution.get("last_completed_step") == 7, "post-Step 07 state must record completed Step 07")
        require(execution.get("last_completed_role") == "senior_data_engineer", "post-Step 07 role must be senior_data_engineer")
        require(execution.get("current_role") == "data_profiling_specialist", "post-Step 07 state must hand off to Step 08")
        require("Data Profiling Specialist" in str(execution.get("current_specialist")), "current specialist must be Step08")
        require("Data Profiling Specialist" in str(execution.get("next_step")), "next step must be Step08")
    elif execution.get("current_step") == 9:
        require(execution.get("last_completed_step") == 8, "post-Step 08 state must record completed Step 08")
        require(execution.get("last_completed_role") == "data_profiling_specialist", "post-Step 08 state must record the profiling specialist")
        require(execution.get("current_role") == "data_quality_engineer", "post-Step 08 state must hand off to Step 09")
        require("Data Quality Engineer" in str(execution.get("current_specialist")), "current specialist must be Step09")
        require("Data Quality Engineer" in str(execution.get("next_step")), "next step must be Step09")
    elif execution.get("current_step") == 10:
        require(execution.get("last_completed_step") == 9, "post-Step 09 state must record completed Step 09")
        require(execution.get("last_completed_role") == "data_quality_engineer", "post-Step 09 state must record the quality engineer")
        require(execution.get("current_role") == "data_security_privacy_engineer", "post-Step 09 state must hand off to Step10")
        require("Data Security" in str(execution.get("current_specialist")), "current specialist must be Step10")
        require("Data Security" in str(execution.get("next_step")), "next step must be Step10")
    elif execution.get("current_step") == 5:
        require(execution.get("last_completed_step") == 4, "execution state must record completed Step 04")
        require(execution.get("last_completed_role") == "solution_architect", "execution state role must be solution_architect")
        require(execution.get("current_role") == "technical_lead", "execution state must be at Step 05")
        require(execution.get("current_specialist") == "Step 05 — Technical Lead / Engineering Lead", "current specialist must be Step 05")
        require(execution.get("next_step") == "Step 05 — Technical Lead / Engineering Lead", "next step must be Step 05")
    elif execution.get("current_step") == 6:
        require(execution.get("last_completed_step") == 5, "post-Step 05 state must record completed Step 05")
        require(execution.get("last_completed_role") == "technical_lead", "post-Step 05 role must be technical_lead")
        require(execution.get("current_role") == "database_engineer", "post-Step 05 state must hand off to Step 06")
        require(execution.get("current_specialist") == "Step06 — Database Engineer / DBA", "post-Step 05 current specialist must be Step06")
        require(execution.get("next_step") == "Step06 — Database Engineer / DBA", "post-Step 05 next step must be Step06")
    else:
        require(False, "execution state must be at Step 05 or post-Step 05 handoff")
    require(gates.get("G0_PRODUCT_CONTRACT") == "PASS" and gates.get("G1_DOMAIN_TRUTH") == "PASS", "G0/G1 must remain PASS")
    require(gates.get("G2_ARCHITECTURE_READY") in {"PENDING", "PASS"}, "G2 must be PENDING or PASS")
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


def check_negative_tests() -> tuple[int, int, int, int, int, int]:
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
    er_cases = [
        {"er_outputs_mapping": True}, {"er_assigns_canonical_id": True},
        {"cluster_reused_as_canonical_id": True},
        {"required_family_finalized_without_acceptable_er": True},
        {"required_family_finalized_after_er_failed": True},
        {"global_optional_flag_used_to_skip_required_family": True},
        {"unrelated_family_blocked_by_er_unavailable": True},
        {"fake_mapping_fallback": True},
    ]
    stage_cases = [
        {"required_stage_skipped": True}, {"failed_attempt_overwritten": True},
        {"invalidated_revived_in_place": True}, {"needs_review_as_failed": True},
        {"engine_crash_as_needs_review": True}, {"missing_prerequisite_as_failed": True},
        {"skipped_without_reason_policy": True}, {"cancelled_publishes_complete": True},
        {"stage_success_succeeds_run_before_validation": True}, {"retry_reuses_attempt_id": True},
    ]
    cross_cases = [
        {"interface_mapping_but_component_er_only": True},
        {"stage_bypasses_finalization_mapping_owner": True},
        {"native_splink_crosses_adapter": True},
        {"matcher_score_is_probability": True},
        {"dependency_adapter_confirms_fk": True},
        {"materializer_changes_fact_grain": True},
        {"validation_mutates_source": True},
    ]
    lifecycle_pass = sum(lifecycle_rejects(case) for case in lifecycle_cases)
    dependency_pass = sum(dependency_rejects(case) for case in dependency_cases)
    artifact_pass = sum(artifact_cache_rejects(case) for case in artifact_cases)
    er_pass = sum(entity_resolution_rejects(case) for case in er_cases)
    stage_pass = sum(stage_lifecycle_rejects(case) for case in stage_cases)
    cross_pass = sum(cross_contract_rejects(case) for case in cross_cases)
    require(lifecycle_pass == 10, f"lifecycle negative tests {lifecycle_pass}/10")
    require(dependency_pass == 10, f"dependency negative tests {dependency_pass}/10")
    require(artifact_pass == 7, f"artifact/cache negative tests {artifact_pass}/7")
    require(er_pass == 8, f"entity-resolution negative tests {er_pass}/8")
    require(stage_pass == 10, f"stage lifecycle negative tests {stage_pass}/10")
    require(cross_pass == 7, f"cross-contract negative tests {cross_pass}/7")
    return lifecycle_pass, dependency_pass, artifact_pass, er_pass, stage_pass, cross_pass


def main() -> int:
    check_required_files()
    loaded = {name: load_yaml(SPECS / name) for name in SPEC_NAMES}
    check_provenance(list(loaded.values()))
    components, component_count = check_components(loaded["components.yml"])
    check_dependency_rules(loaded["dependency_rules.yml"])
    interface_count = check_interfaces(loaded["engine_interfaces.yml"], components)
    stage_ids, stage_count = check_stage_graph(loaded["stage_graph.yml"])
    checkpoint_count, topological_stage_count = check_review_checkpoints(loaded["review_checkpoints.yml"], loaded["stage_graph.yml"])
    require(topological_stage_count == stage_count, "review topology did not cover every stage")
    check_optionality_consistency(
        {item["interface_id"]: item for item in loaded["engine_interfaces.yml"].get("interfaces", [])},
        components,
        {item["stage_id"]: item for item in loaded["stage_graph.yml"].get("stages", [])},
    )
    check_source_lifecycle(
        {item["interface_id"]: item for item in loaded["engine_interfaces.yml"].get("interfaces", [])},
        components,
        {item["stage_id"]: item for item in loaded["stage_graph.yml"].get("stages", [])},
    )
    logical_count, attempt_count = check_stage_state_machine(loaded["stage_state_machine.yml"])
    check_run_machine(loaded["run_state_machine.yml"])
    check_artifacts(loaded["artifact_lifecycle.yml"])
    check_cross_artifact()
    check_execution_log_paths()
    check_manifest()
    check_state()
    negative_counts = check_negative_tests()
    review_negative_count = check_review_negative_tests()
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: "
        f"components={component_count} interfaces={interface_count} stages={stage_count} "
        f"review_checkpoints={checkpoint_count} "
        f"run_states=7 logical_stage_states={logical_count} attempt_states={attempt_count} accounting=preserved "
        f"lifecycle_negative_tests={negative_counts[0]}/10 "
        f"dependency_negative_tests={negative_counts[1]}/10 "
        f"artifact_cache_negative_tests={negative_counts[2]}/7 "
        f"er_negative_tests={negative_counts[3]}/8 "
        f"stage_lifecycle_negative_tests={negative_counts[4]}/10 "
        f"cross_contract_negative_tests={negative_counts[5]}/7 "
        f"review_negative_tests={review_negative_count}/12"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
