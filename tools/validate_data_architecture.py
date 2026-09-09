"""Deterministic logical data-architecture contract validator."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "data-architecture"
SPECS = ARCH / "specs"


def main() -> int:
    errors: list[str] = []
    docs = [
        "DATA_ARCHITECTURE_CONTRACT.md",
        "SOURCE_REPRESENTATION.md",
        "KEY_STRATEGY.md",
        "CANONICAL_MODEL_PRINCIPLES.md",
        "CONFLICT_AND_SURVIVORSHIP.md",
        "LINEAGE_AND_PROVENANCE.md",
        "DIMENSIONAL_MODELING_RULES.md",
        "NULL_AND_UNKNOWN_SEMANTICS.md",
        "SCD_AND_TEMPORAL_BOUNDARIES.md",
        "REFERENCE_BENCHMARK_LOGICAL_MODEL.md",
    ]
    for name in docs:
        if not (ARCH / name).is_file():
            errors.append(f"missing architecture document: {name}")
    review_path = ROOT / "docs" / "execution" / "STEP03_DATA_ARCHITECTURE_REVIEW.md"
    if not review_path.is_file():
        errors.append("missing Step 03 completion review")
    else:
        review_text = review_path.read_text(encoding="utf-8")
        if "Status: `PASS`" not in review_text:
            errors.append("Step 03 completion review is not PASS")
        if "G2 remains `PENDING`" not in review_text:
            errors.append("Step 03 completion review does not preserve G2 pending")

    loaded: dict[str, object] = {}
    for name in ["architecture_invariants.yml", "reference_logical_model.yml"]:
        path = SPECS / name
        if not path.is_file():
            errors.append(f"missing architecture specification: {name}")
            continue
        try:
            loaded[name] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"invalid YAML {name}: {exc}")

    invariant_data = loaded.get("architecture_invariants.yml", {}) or {}
    invariants = invariant_data.get("invariants", []) if isinstance(invariant_data, dict) else []
    invariant_ids = [item.get("id") for item in invariants if isinstance(item, dict)]
    expected_invariant_ids = [f"DA-{i:03d}" for i in range(1, 28)]
    if invariant_ids != expected_invariant_ids:
        errors.append(f"architecture invariant IDs mismatch: {invariant_ids}")
    invariant_fields = {
        "id",
        "scope",
        "statement",
        "rationale",
        "downstream_risk",
        "verification_expectation",
        "status",
    }
    for item in invariants:
        if not invariant_fields.issubset(item):
            errors.append(f"invariant missing fields: {item.get('id')}")
        if item.get("status") != "DEFINED":
            errors.append(f"invariant not DEFINED: {item.get('id')}")

    model = loaded.get("reference_logical_model.yml", {}) or {}
    if not isinstance(model, dict):
        errors.append("reference logical model is not a mapping")
        model = {}
    expected_entities = {"Customer", "Product", "Branch", "Order", "OrderLine", "Payment"}
    if set(model.get("canonical_entities", [])) != expected_entities:
        errors.append(f"canonical entity mismatch: {model.get('canonical_entities')}")

    expected_relationships = {
        "Order.customer -> Customer",
        "OrderLine.order -> Order",
        "OrderLine.product -> Product",
        "Order.branch -> Branch",
        "Payment.order -> Order",
    }
    relationships = model.get("relationships", [])
    actual_relationships = {item.get("direction") for item in relationships if isinstance(item, dict)}
    if actual_relationships != expected_relationships:
        errors.append(f"relationship mismatch: {actual_relationships}")
    for item in relationships:
        if not {"direction", "source_entity", "target_entity", "meaning_ref"}.issubset(item):
            errors.append(f"relationship missing fields: {item}")

    expected_dimensions = {"dim_customer", "dim_product", "dim_branch", "dim_date"}
    dimensions = model.get("dimensions", [])
    if {item.get("name") for item in dimensions if isinstance(item, dict)} != expected_dimensions:
        errors.append("analytical dimension set mismatch")
    dimension_fields = {
        "name",
        "semantic_source",
        "row_meaning",
        "key_category",
        "lineage_requirement",
        "scd_expectation",
        "validation_requirement",
    }
    for item in dimensions:
        if not dimension_fields.issubset(item) or any(not str(item.get(k)).strip() for k in dimension_fields):
            errors.append(f"dimension missing field/value: {item.get('name')}")

    expected_facts = {"fact_order_line", "fact_payment"}
    facts = model.get("facts", [])
    if {item.get("name") for item in facts if isinstance(item, dict)} != expected_facts:
        errors.append("analytical fact set mismatch")
    fact_fields = {
        "name",
        "semantic_source",
        "row_meaning",
        "grain_ref",
        "required_relationships",
        "measure_refs",
        "lineage_requirement",
        "validation_requirement",
        "physical_grain_key",
    }
    for item in facts:
        if not fact_fields.issubset(item) or any(not str(item.get(k)).strip() for k in fact_fields):
            errors.append(f"fact missing field/value: {item.get('name')}")
        if "UNRESOLVED" not in str(item.get("physical_grain_key", "")):
            errors.append(f"fact physical key was fabricated: {item.get('name')}")
        if not item.get("grain_ref"):
            errors.append(f"fact has no grain: {item.get('name')}")

    grains = model.get("fact_grains", [])
    grain_fields = {"grain_id", "fact", "human_readable", "conceptual_business_key", "machine_validation", "physical_key_status"}
    for item in grains:
        if not grain_fields.issubset(item) or any(not str(item.get(k)).strip() for k in grain_fields):
            errors.append(f"grain missing field/value: {item.get('grain_id')}")
        if "UNRESOLVED" not in str(item.get("physical_key_status", "")):
            errors.append(f"grain physical key was fabricated: {item.get('grain_id')}")
    if {item.get("fact") for item in grains} != expected_facts:
        errors.append("fact grain set mismatch")

    measures = model.get("measure_semantics", [])
    measure_map = {item.get("name"): item for item in measures if isinstance(item, dict)}
    for name, expected_class in {"quantity": "ADDITIVE", "unit_price": "NON_ADDITIVE", "discount_rate": "NON_ADDITIVE"}.items():
        if name not in measure_map:
            errors.append(f"missing measure: {name}")
        elif measure_map[name].get("class") != expected_class:
            errors.append(f"wrong measure class for {name}: {measure_map[name].get('class')}")
    if "payment_amount" not in measure_map or measure_map["payment_amount"].get("class") != "UNRESOLVED":
        errors.append("payment_amount must remain unresolved")

    expected_keys = {
        "PHYSICAL_SOURCE_KEY",
        "DECLARED_BUSINESS_KEY",
        "CANDIDATE_KEY",
        "CANONICAL_IDENTITY",
        "CANONICAL_SURROGATE_ID",
        "ANALYTICAL_SURROGATE_KEY",
        "DEGENERATE_BUSINESS_IDENTIFIER",
        "SOURCE_RECORD_REFERENCE",
    }
    if set(model.get("key_categories", [])) != expected_keys:
        errors.append("key taxonomy mismatch")

    all_text = "\n".join(
        (ARCH / name).read_text(encoding="utf-8") for name in docs if (ARCH / name).is_file()
    )
    required_phrases = [
        "read-only",
        "cluster_id != canonical_entity_id",
        "source authority is never global",
        "losing values",
        "reversible traceability",
        "record accounting",
        "fact requires",
        "NON_ADDITIVE",
        "valid_from",
        "valid_to",
        "is_current",
        "full incremental SCD2",
        "not applicable",
        "invalid/unparseable",
        "UNKNOWN",
        "orphan",
        "canonical and analytical",
    ]
    for phrase in required_phrases:
        if phrase.lower() not in all_text.lower():
            errors.append(f"missing architecture phrase: {phrase}")
    for forbidden in ["CRM is always truth", "ERP is always truth", "CRM is truth", "ERP is truth", "numeric column = measure"]:
        if forbidden.lower() in all_text.lower():
            errors.append(f"blanket/forbidden architecture phrase present: {forbidden}")
    if "fabricated historical" not in all_text.lower() and "fabricate history" not in all_text.lower():
        errors.append("SCD fabrication prohibition missing")

    state_path = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    if state["gates"]["G0_PRODUCT_CONTRACT"] != "PASS":
        errors.append("G0 is not PASS")
    if state["gates"]["G1_DOMAIN_TRUTH"] != "PASS":
        errors.append("G1 is not PASS")
    if state["gates"]["G2_ARCHITECTURE_READY"] != "PENDING":
        errors.append("G2 is not PENDING")
    if any(
        value != "PENDING"
        for key, value in state["gates"].items()
        if key not in {"G0_PRODUCT_CONTRACT", "G1_DOMAIN_TRUTH", "G2_ARCHITECTURE_READY"}
    ):
        errors.append("G3-G15 are not all PENDING")
    if state["blocked"] is not False:
        errors.append("blocked is not false")

    for path in [*(ARCH / name for name in docs)]:
        if not path.is_file():
            continue
        for target in re.findall(r"\]\(([^)#]+)", path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            if not (path.parent / target).resolve().is_file():
                errors.append(f"broken Markdown link: {path}: {target}")
    for ref in [item.get("meaning_ref") for item in relationships if isinstance(item, dict)]:
        if ref and not (ROOT / ref).is_file():
            errors.append(f"broken logical-model reference: {ref}")

    research = ROOT / "research" / "oss"
    if research.is_dir() and any(path.name != "README.md" for path in research.rglob("*")):
        errors.append("unexpected OSS clone/content under research/oss")
    if (ROOT / "src").exists():
        errors.append("application src/ directory exists")

    if errors:
        print("FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(
        f"PASS: architecture_docs={len(docs)} invariants={len(invariants)} "
        f"entities={len(model.get('canonical_entities', []))} "
        f"relationships={len(relationships)} dimensions={len(dimensions)} facts={len(facts)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
