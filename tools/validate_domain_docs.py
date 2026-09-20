"""Deterministic contract checks for Specialist Step 02 domain artifacts."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "docs" / "domain"
LABELS = ROOT / "benchmarks" / "labels" / "domain-reviewed"
STATE = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
sys.path.insert(0, str(ROOT))
from tools.execution_state import step41_g15_closed


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def implementation_is_authorized() -> bool:
    """Allow the package only after G2; preserve the pre-gate guard."""
    try:
        state = yaml.safe_load(STATE.read_text(encoding="utf-8"))
    except Exception:
        return False
    execution = state.get("specialist_execution", {})
    return step41_g15_closed(state) or (isinstance(execution.get("current_step"), int) and execution.get("current_step") >= 6 and state.get("gates", {}).get("G2_ARCHITECTURE_READY") == "PASS")


def main() -> int:
    errors: list[str] = []
    docs = [
        "DOMAIN_CONTRACT.md",
        "GLOSSARY.md",
        "SOURCE_SYSTEM_MAP.md",
        "BUSINESS_RULES.md",
        "IDENTITY_AND_KEYS.md",
        "RELATIONSHIP_SEMANTICS.md",
        "AMBIGUITY_CATALOGUE.md",
        "DOMAIN_ASSERTION_MODEL.md",
        "REFERENCE_BENCHMARK_DOMAIN.md",
        "SEMANTIC_WALKTHROUGH.md",
    ]
    label_files = [
        "entities.yml",
        "relationships.yml",
        "source_authority.yml",
        "business_rules.yml",
        "ambiguities.yml",
    ]
    for name in docs:
        if not (DOMAIN / name).is_file():
            fail(errors, f"missing domain document: {name}")
    parsed: dict[str, object] = {}
    for name in label_files:
        path = LABELS / name
        if not path.is_file():
            fail(errors, f"missing benchmark specification: {name}")
            continue
        try:
            parsed[name] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            fail(errors, f"invalid YAML {name}: {exc}")

    def text(name: str) -> str:
        path = DOMAIN / name
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    glossary = text("GLOSSARY.md")
    contract = text("DOMAIN_CONTRACT.md")
    assertion = text("DOMAIN_ASSERTION_MODEL.md")
    reference = text("REFERENCE_BENCHMARK_DOMAIN.md")
    walkthrough = text("SEMANTIC_WALKTHROUGH.md")
    business_rules = text("BUSINESS_RULES.md")

    expected_entities = {"Customer", "Product", "Branch", "Order", "OrderLine", "Payment"}
    entity_data = parsed.get("entities.yml", {}) or {}
    entities = entity_data.get("entities", []) if isinstance(entity_data, dict) else []
    entity_ids = [item.get("entity_id") for item in entities if isinstance(item, dict)]
    if len(entity_ids) != len(set(entity_ids)):
        fail(errors, "entity IDs are not unique")
    actual_entities = {item.get("canonical_name") for item in entities if isinstance(item, dict)}
    if actual_entities != expected_entities:
        fail(errors, f"entity set mismatch: {actual_entities}")
    glossary_aliases = {"OrderLine": r"Order Line / OrderLine"}
    for entity in expected_entities:
        pattern = glossary_aliases.get(entity, re.escape(entity))
        if re.search(rf"\|\s*{pattern}\s*\|", glossary) is None:
            fail(errors, f"missing glossary definition: {entity}")

    relationship_data = parsed.get("relationships.yml", {}) or {}
    relationships = relationship_data.get("relationships", []) if isinstance(relationship_data, dict) else []
    relationship_ids = [item.get("relationship_id") for item in relationships if isinstance(item, dict)]
    if len(relationship_ids) != len(set(relationship_ids)):
        fail(errors, "relationship IDs are not unique")
    expected_relationships = {
        "Order.customer -> Customer",
        "OrderLine.order -> Order",
        "OrderLine.product -> Product",
        "Order.branch -> Branch",
        "Payment.order -> Order",
    }
    actual_relationships = {item.get("direction") for item in relationships if isinstance(item, dict)}
    if actual_relationships != expected_relationships:
        fail(errors, f"relationship set mismatch: {actual_relationships}")
    relationship_fields = {
        "source_entity",
        "target_entity",
        "business_meaning",
        "cardinality",
        "optional_in_valid_benchmark",
        "corruption_interpretation",
    }
    for item in relationships:
        if not relationship_fields.issubset(item):
            fail(errors, f"relationship missing fields: {item.get('relationship_id')}")

    authority_data = parsed.get("source_authority.yml", {}) or {}
    authorities = authority_data.get("authority_expectations", []) if isinstance(authority_data, dict) else []
    source_names = {item.get("source") for item in authorities if isinstance(item, dict)}
    if source_names != {"CRM", "Sales", "ERP", "Legacy files"}:
        fail(errors, f"source map mismatch: {source_names}")
    authority_fields = {"authority_id", "source", "scope", "authority", "not_authoritative_for", "temporal_scope"}
    for item in authorities:
        if not authority_fields.issubset(item):
            fail(errors, f"source authority missing fields: {item.get('authority_id')}")

    rule_data = parsed.get("business_rules.yml", {}) or {}
    rules = rule_data.get("rules", []) if isinstance(rule_data, dict) else []
    rule_ids = [item.get("rule_id") for item in rules if isinstance(item, dict)]
    if len(rule_ids) != len(set(rule_ids)) or not all(re.fullmatch(r"BR-\d{3}", value or "") for value in rule_ids):
        fail(errors, "business rule IDs are invalid or not unique")
    rule_fields = {"rule_id", "status", "scope", "statement", "rationale", "exceptions", "verification_path"}
    for item in rules:
        if not rule_fields.issubset(item):
            fail(errors, f"business rule missing fields: {item.get('rule_id')}")

    ambiguity_data = parsed.get("ambiguities.yml", {}) or {}
    ambiguities = ambiguity_data.get("ambiguities", []) if isinstance(ambiguity_data, dict) else []
    ambiguity_ids = [item.get("ambiguity_id") for item in ambiguities if isinstance(item, dict)]
    if len(ambiguity_ids) != len(set(ambiguity_ids)) or not all(re.fullmatch(r"AMB-\d{3}", value or "") for value in ambiguity_ids):
        fail(errors, "ambiguity IDs are invalid or not unique")
    if len(ambiguities) < 10:
        fail(errors, "ambiguity catalogue is too small")
    ambiguity_fields = {"ambiguity_id", "example", "status", "evidence_needed", "downstream_risk"}
    for item in ambiguities:
        if not ambiguity_fields.issubset(item):
            fail(errors, f"ambiguity missing fields: {item.get('ambiguity_id')}")

    for required in [
        "generic product",
        "reference benchmark",
        "not universal",
        "conflict",
        "provenance",
        "supersed",
    ]:
        if required not in (contract + assertion).lower():
            fail(errors, f"missing assertion/domain boundary text: {required}")
    for required in [
        "customer registration",
        "order creation",
        "order-line creation",
        "payment event",
        "product representation",
        "branch attribution",
    ]:
        if required not in reference.lower():
            fail(errors, f"missing benchmark process: {required}")
    for required in ["physical validity", "business validity", "syntactically valid phone", "non-null status"]:
        if required not in business_rules.lower():
            fail(errors, f"missing quality semantic distinction: {required}")
    for required in [
        "customer multi-source path",
        "order → orderline → product path",
        "payment → order path",
        "order → branch path",
        "can a domain assertion silently override contradictory evidence",
    ]:
        if required not in walkthrough.lower():
            fail(errors, f"missing walkthrough evidence: {required}")
    for forbidden in ["CRM is always truth", "ERP is always truth", "CRM is truth", "ERP is truth"]:
        if forbidden.lower() in (contract + glossary + text("SOURCE_SYSTEM_MAP.md")).lower():
            fail(errors, f"blanket authority phrase present: {forbidden}")
    if "record_level_labels: not_created" not in "\n".join(
        (LABELS / name).read_text(encoding="utf-8") for name in label_files if (LABELS / name).is_file()
    ):
        fail(errors, "benchmark label specs do not explicitly reject fabricated record-level labels")
    if "confidence_kind: HUMAN_DOMAIN_ASSERTION" not in assertion:
        fail(errors, "human assertion confidence kind is missing")
    if "source-destructive" in (contract + assertion).lower() or "delete source" in (contract + assertion).lower():
        fail(errors, "domain artifacts contain a source-destructive rule")

    for path in [*(DOMAIN / name for name in docs)]:
        if not path.is_file():
            continue
        for target in re.findall(r"\]\(([^)#]+)", path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            if not (path.parent / target).resolve().is_file():
                fail(errors, f"broken markdown link: {path}: {target}")

    research = ROOT / "research" / "oss"
    if research.is_dir():
        unexpected = [p for p in research.rglob("*") if p.name != "README.md"]
        if unexpected:
            fail(errors, "unexpected OSS clone/content under research/oss")
    if (ROOT / "src").exists() and not implementation_is_authorized():
        fail(errors, "application source directory exists before implementation authorization")

    if errors:
        print("FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(
        f"PASS: domain_docs={len(docs)} benchmark_specs={len(label_files)} "
        f"entities={len(entities)} relationships={len(relationships)} "
        f"business_rules={len(rules)} ambiguities={len(ambiguities)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
