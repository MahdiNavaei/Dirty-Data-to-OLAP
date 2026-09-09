"""Deterministic logical data-architecture contract validator."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "data-architecture"
SPECS = ARCH / "specs"

EXPECTED_TERMINAL_DISPOSITIONS = {
    "EMITTED_DIRECT",
    "CONSOLIDATED",
    "AGGREGATED",
    "FILTERED_EXPLICIT",
    "QUARANTINED",
    "UNRESOLVED",
}
EXPECTED_PROCESSING_ANNOTATIONS = {
    "MAPPED",
    "LINKED",
    "NORMALIZED",
    "MATCHED",
    "PROFILED",
    "REVIEWED",
}


def _accounting_record_is_valid(
    record: dict[str, object],
    terminal_dispositions: set[str],
    contributor_required: set[str],
    policy_required: set[str],
    validated_success: bool = False,
) -> bool:
    disposition = record.get("terminal_disposition")
    declared_dispositions = record.get("terminal_dispositions")
    if disposition not in terminal_dispositions:
        return False
    if declared_dispositions is not None:
        if not isinstance(declared_dispositions, list) or len(declared_dispositions) != 1:
            return False
        if declared_dispositions[0] != disposition:
            return False
    if not record.get("reason") or not record.get("provenance"):
        return False
    if disposition in {"EMITTED_DIRECT", "CONSOLIDATED", "AGGREGATED"} and not record.get("output_ref"):
        return False
    if disposition in contributor_required:
        required_fields = ("input_record_refs", "output_or_group_ref", "transformation_or_policy_ref")
        if any(not record.get(field) for field in required_fields):
            return False
    if disposition in policy_required and not record.get("policy_ref"):
        return False
    if disposition == "UNRESOLVED" and validated_success:
        return False
    if record.get("uses_output_row_count_for_contributor_reconciliation"):
        return False
    if record.get("consolidation_is_aggregation_without_provenance"):
        return False
    return True


def validate_record_accounting_spec(spec: object, errors: list[str]) -> tuple[int, int]:
    if not isinstance(spec, dict):
        errors.append("record accounting specification is not a mapping")
        return 0, 0

    required_top_level = {
        "schema_version",
        "scope",
        "terminal_dispositions",
        "processing_annotations",
        "contract",
        "validated_success",
        "reconciliation",
    }
    if not required_top_level.issubset(spec):
        errors.append("record accounting specification is missing required sections")

    terminal_items = spec.get("terminal_dispositions", [])
    terminal_names = [
        item.get("name") for item in terminal_items if isinstance(item, dict)
    ]
    if len(terminal_names) != len(set(terminal_names)):
        errors.append("record accounting terminal dispositions are not unique")
    if set(terminal_names) != EXPECTED_TERMINAL_DISPOSITIONS:
        errors.append(f"record accounting terminal set mismatch: {terminal_names}")
    if {"MAPPED", "LINKED"} & set(terminal_names):
        errors.append("MAPPED/LINKED must not be terminal dispositions")

    annotations = spec.get("processing_annotations", {})
    annotation_names = set(annotations.get("allowed", [])) if isinstance(annotations, dict) else set()
    if not EXPECTED_PROCESSING_ANNOTATIONS.issubset(annotation_names):
        errors.append("record accounting processing annotations are incomplete")
    if not isinstance(annotations, dict) or annotations.get("terminal_disposition_prohibited") is not True:
        errors.append("processing annotations must be prohibited as terminal dispositions")

    contract = spec.get("contract", {})
    if not isinstance(contract, dict):
        errors.append("record accounting contract is not a mapping")
        contract = {}
    if contract.get("exactly_one_terminal_disposition_per_input_record") is not True:
        errors.append("exactly-one-terminal-disposition is not declared")
    if contract.get("terminal_dispositions_mutually_exclusive") is not True:
        errors.append("terminal disposition mutual exclusivity is not declared")
    if contract.get("reason_required") is not True or contract.get("provenance_required") is not True:
        errors.append("record accounting reason/provenance requirements are incomplete")

    output_requirement = contract.get("output_reference_requirement", {})
    required_output_for = set(output_requirement.get("required_for", [])) if isinstance(output_requirement, dict) else set()
    if not {"EMITTED_DIRECT", "CONSOLIDATED", "AGGREGATED"}.issubset(required_output_for):
        errors.append("contributing terminal dispositions lack output-reference requirements")

    contributor_requirement = contract.get("contributor_reference_requirement", {})
    required_contributors = set(contributor_requirement.get("required_for", [])) if isinstance(contributor_requirement, dict) else set()
    contributor_fields = set(contributor_requirement.get("fields", [])) if isinstance(contributor_requirement, dict) else set()
    if not {"CONSOLIDATED", "AGGREGATED"}.issubset(required_contributors):
        errors.append("consolidation/aggregation contributor requirements are incomplete")
    if not {"input_record_refs", "output_or_group_ref", "transformation_or_policy_ref", "provenance"}.issubset(contributor_fields):
        errors.append("consolidation/aggregation contributor fields are incomplete")

    validated_success = spec.get("validated_success", {})
    if not isinstance(validated_success, dict) or validated_success.get("required_unresolved_count") != 0:
        errors.append("validated success must require zero unresolved records")
    if not isinstance(validated_success, dict) or validated_success.get("unresolved_required_records_block_completed_and_validated") is not True:
        errors.append("unresolved required records must block validated success")

    reconciliation = spec.get("reconciliation", {})
    expected_reconciliation = {
        "count(EMITTED_DIRECT input records)",
        "count(CONSOLIDATED input records)",
        "count(AGGREGATED input records)",
        "count(FILTERED_EXPLICIT input records)",
        "count(QUARANTINED input records)",
        "count(UNRESOLVED input records)",
    }
    actual_reconciliation = set(reconciliation.get("input_record_count_equals", [])) if isinstance(reconciliation, dict) else set()
    if actual_reconciliation != expected_reconciliation:
        errors.append("record accounting reconciliation does not count input records by terminal disposition")
    if not isinstance(reconciliation, dict) or reconciliation.get("output_row_counts_validated_separately") is not True:
        errors.append("output-row counts must be validated separately")
    if not isinstance(reconciliation, dict) or reconciliation.get("contributor_counts_are_not_output_row_counts") is not True:
        errors.append("input contributor counts must not be replaced by output-row counts")
    if not isinstance(reconciliation, dict) or reconciliation.get("canonical_consolidation_and_analytical_aggregation_are_distinct") is not True:
        errors.append("consolidation and analytical aggregation must remain distinct")

    negative_cases = [
        {"terminal_disposition": "LINKED", "reason": "linked", "provenance": "p"},
        {"terminal_disposition": "MAPPED", "reason": "mapped", "provenance": "p"},
        {
            "terminal_dispositions": ["AGGREGATED", "FILTERED_EXPLICIT"],
            "reason": "ambiguous",
            "provenance": "p",
        },
        {
            "terminal_disposition": "AGGREGATED",
            "reason": "aggregate",
            "provenance": "p",
            "output_ref": "out-1",
            "input_record_refs": ["in-1"],
            "transformation_or_policy_ref": "transform-1",
        },
        {"terminal_disposition": "FILTERED_EXPLICIT", "reason": "filtered", "provenance": "p"},
        {"terminal_disposition": "UNRESOLVED", "reason": "pending", "provenance": "p"},
        {
            "terminal_disposition": "EMITTED_DIRECT",
            "reason": "count substitution",
            "provenance": "p",
            "output_ref": "out-1",
            "uses_output_row_count_for_contributor_reconciliation": True,
        },
        {
            "terminal_disposition": "CONSOLIDATED",
            "reason": "consolidated",
            "provenance": "p",
            "output_ref": "out-1",
            "input_record_refs": ["in-1"],
            "output_or_group_ref": "out-1",
            "transformation_or_policy_ref": "transform-1",
            "consolidation_is_aggregation_without_provenance": True,
        },
    ]
    contributor_required = required_contributors or {"CONSOLIDATED", "AGGREGATED"}
    policy_required = set()
    for item in terminal_items:
        if isinstance(item, dict) and item.get("policy_reference_required") is True:
            policy_required.add(str(item.get("name")))
    negative_passed = 0
    for index, case in enumerate(negative_cases, start=1):
        if not _accounting_record_is_valid(case, set(terminal_names), contributor_required, policy_required, validated_success=index == 6):
            negative_passed += 1
        else:
            errors.append(f"record accounting negative case {index} was accepted")
    return len(negative_cases), negative_passed


def validate_revenue_semantics(errors: list[str]) -> int:
    benchmark_paths = [
        ROOT / "docs" / "08_BENCHMARK_AND_VALIDATION_PLAN.md",
        ROOT / "docs" / "Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base" / "base_reports" / "08_BENCHMARK_AND_VALIDATION_PLAN.md",
    ]
    benchmark_text = "\n".join(path.read_text(encoding="utf-8") for path in benchmark_paths if path.is_file())
    forbidden_active_patterns = [
        r"###\s+Revenue reconciliation",
        r"expected gross sales from source truth",
        r"SUM\s*\(\s*f\.net_amount\s*\)\s+AS\s+revenue",
        r"SUM\s*\(\s*f\.net_amount\s*\)\s+AS\s+recognized\s+revenue",
        r"unconditional monetary reconciliation",
    ]
    for pattern in forbidden_active_patterns:
        if re.search(pattern, benchmark_text, flags=re.IGNORECASE):
            errors.append(f"unsupported active benchmark monetary claim: {pattern}")
    for required in [
        "Monetary reconciliation is **CONDITIONAL**",
        "SUM(f.quantity) AS units_ordered",
        "not recognized revenue",
        "currency/unit semantics",
    ]:
        if required.lower() not in benchmark_text.lower():
            errors.append(f"benchmark monetary policy missing: {required}")

    modeling_paths = [
        ROOT / "docs" / "07_CANONICAL_AND_OLAP_MODELING_STRATEGY.md",
        ROOT / "docs" / "Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base" / "base_reports" / "07_CANONICAL_AND_OLAP_MODELING_STRATEGY.md",
    ]
    modeling_text = "\n".join(path.read_text(encoding="utf-8") for path in modeling_paths if path.is_file())
    for required in ["CONDITIONAL", "not an accepted reference-benchmark measure", "not recognized revenue"]:
        if required.lower() not in modeling_text.lower():
            errors.append(f"modeling monetary example is not explicitly conditional: {required}")
    if re.search(r"gross_amount\s*=\s*SUM\(qty \* price\).*\n\nThis is a real OLAP-ready output", modeling_text, flags=re.IGNORECASE):
        errors.append("modeling report still presents gross_amount as unconditional output")

    domain_text = (ROOT / "docs" / "domain" / "DOMAIN_CONTRACT.md").read_text(encoding="utf-8")
    if "recognized revenue" not in domain_text.lower() or "outside current benchmark domain" not in domain_text.lower():
        errors.append("domain revenue boundary is missing")
    if re.search(r"SUM\s*\(\s*f\.net_amount\s*\)\s+AS\s+revenue", "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.md")), flags=re.IGNORECASE):
        errors.append("repository still contains the unsupported net_amount revenue demo")

    invalid_monetary_proposals = [
        {"label": "revenue", "domain_contract": False},
        {"expression": "qty * unit_price", "recognized_revenue": True, "domain_contract": False},
        {"currency_conversion": True, "currency_metadata": "UNRESOLVED"},
        {"measure": "payment_amount", "as": "revenue", "domain_contract": False},
        {"invented_discount_behavior": True, "domain_contract": False},
        {"benchmark_requires_monetary_reconciliation": True, "domain_contract": False},
    ]

    def proposal_is_allowed(proposal: dict[str, object]) -> bool:
        if proposal.get("domain_contract") is not True:
            return False
        if proposal.get("recognized_revenue") or proposal.get("as") == "revenue":
            return True
        if proposal.get("currency_conversion") and proposal.get("currency_metadata") == "UNRESOLVED":
            return False
        if proposal.get("invented_discount_behavior") or proposal.get("benchmark_requires_monetary_reconciliation"):
            return False
        return True

    revenue_negative_passed = 0
    for index, proposal in enumerate(invalid_monetary_proposals, start=1):
        if not proposal_is_allowed(proposal):
            revenue_negative_passed += 1
        else:
            errors.append(f"revenue semantic negative case {index} was accepted")
    return revenue_negative_passed


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
    for name in ["architecture_invariants.yml", "reference_logical_model.yml", "record_accounting.yml"]:
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

    da017 = next((item for item in invariants if isinstance(item, dict) and item.get("id") == "DA-017"), {})
    da017_text = " ".join(str(da017.get(field, "")) for field in ("scope", "statement", "rationale", "verification_expectation"))
    accounting_path = SPECS / "record_accounting.yml"
    accounting_text = accounting_path.read_text(encoding="utf-8") if accounting_path.is_file() else ""
    for required in [
        "EMITTED_DIRECT",
        "CONSOLIDATED",
        "AGGREGATED",
        "FILTERED_EXPLICIT",
        "QUARANTINED",
        "UNRESOLVED",
        "MAPPED",
        "LINKED",
        "exactly_one_terminal_disposition_per_input_record",
    ]:
        if required.lower() not in da017_text.lower() and required.lower() not in accounting_text.lower():
            errors.append(f"DA-017/accounting specification missing: {required}")
    if "not terminal" not in da017_text.lower() or "terminal_disposition_prohibited" not in accounting_text.lower():
        errors.append("DA-017 must explicitly keep mapping/linking out of terminal outcomes")
    accounting_negative_total = accounting_negative_passed = 0
    accounting_spec = loaded.get("record_accounting.yml")
    if accounting_spec is not None:
        accounting_negative_total, accounting_negative_passed = validate_record_accounting_spec(accounting_spec, errors)
    revenue_negative_passed = validate_revenue_semantics(errors)

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
    for line in all_text.splitlines():
        lowered = line.lower()
        if ("mapped" in lowered or "linked" in lowered) and "terminal" in lowered:
            safe_explanations = ("not terminal", "never terminal", "orthogonal", "prohibited")
            if not any(marker in lowered for marker in safe_explanations):
                errors.append(f"architecture document calls MAPPED/LINKED terminal: {line.strip()}")

    state_path = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    if state["gates"]["G0_PRODUCT_CONTRACT"] != "PASS":
        errors.append("G0 is not PASS")
    if state["gates"]["G1_DOMAIN_TRUTH"] != "PASS":
        errors.append("G1 is not PASS")
    if state["gates"]["G2_ARCHITECTURE_READY"] not in {"PENDING", "PASS"}:
        errors.append("G2 is not PENDING or PASS")
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
        f"relationships={len(relationships)} dimensions={len(dimensions)} facts={len(facts)} "
        f"accounting_negative_tests={accounting_negative_passed}/{accounting_negative_total} "
        f"revenue_negative_tests={revenue_negative_passed}/6"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
