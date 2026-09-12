"""Behavioral Step22 data correctness validator and reference-run writer."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader, ValidationTargetError
from dirty_data_to_olap.application.validation import ValidationOutcome, ValidationService
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel, RecordDisposition
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingEntry,
    RecordAccountingScope,
    SourceTruthDuplicateGroup,
    TargetSnapshot,
    TargetTableSnapshot,
    ValidationStatus,
)
from tools.step22_reference_support import (
    ROOT as PROJECT_ROOT,
    REQUIRED_CHECK_IDS,
    ReferenceValidationContext,
    build_reference_context,
)


class ValidationFailure(RuntimeError):
    pass


class StaticTargetReader:
    """Test-only reader used to prove mutations are detected by the service."""

    def __init__(self, snapshot: TargetSnapshot):
        self._snapshot = snapshot

    def snapshot(self, relative_path: str, *, expected_sha256: str, allowed_table_names: tuple[str, ...]) -> TargetSnapshot:
        return self._snapshot


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def model_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def check(condition: bool, message: str, counter: list[int]) -> None:
    if not condition:
        raise ValidationFailure(message)
    counter[0] += 1


def check_status(outcome: ValidationOutcome, check_ids: tuple[str, ...], expected: ValidationStatus = ValidationStatus.FAIL) -> bool:
    statuses = {item.check_id: item.status for item in outcome.report.checks}
    return any(statuses.get(check_id) is expected for check_id in check_ids)


def mutate_table(snapshot: TargetSnapshot, table_name: str, mutate_rows) -> TargetSnapshot:
    tables = []
    for table in snapshot.tables:
        if table.table_name == table_name:
            tables.append(table.model_copy(update={"rows": tuple(mutate_rows(list(table.rows)))}))
        else:
            tables.append(table)
    return snapshot.model_copy(update={"tables": tuple(tables)})


def fact_table_name(context: ReferenceValidationContext) -> str:
    return context.inputs.compiled_plan.fact_specs[0].table_name


def run_negative_controls(context: ReferenceValidationContext, base: ValidationOutcome) -> list[dict[str, Any]]:
    fact_name = fact_table_name(context)
    fact_rows = list(base.target_snapshot.table_map[fact_name].rows) if base.target_snapshot else []
    if len(fact_rows) < 3:
        raise ValidationFailure("reference target does not contain enough fact rows for mutation controls")
    fact_spec = context.inputs.compiled_plan.fact_specs[0]
    product_fk = next((item for item in fact_spec.dimension_foreign_keys if item.dimension_id.endswith("product")), None)
    customer_fk = next((item for item in fact_spec.dimension_foreign_keys if item.dimension_id.endswith("customer")), None)
    if product_fk is None and context.name == "retail":
        raise ValidationFailure("retail reference has no product foreign key")

    controls: list[tuple[str, TargetSnapshot, tuple[str, ...]]] = []
    if product_fk:
        def swap_product(rows):
            first_value = rows[0][product_fk.fact_column]
            second_value = rows[1][product_fk.fact_column]
            rows[0] = {**rows[0], product_fk.fact_column: second_value}
            rows[1] = {**rows[1], product_fk.fact_column: first_value}
            return rows
        controls.append(("same_total_wrong_product_allocation", mutate_table(base.target_snapshot, fact_name, swap_product), ("fact_business_values", "quantity_slices")))
    grain = next(item for item in context.inputs.compiled_plan.grain_specs if item.grain_id == fact_spec.grain_spec_id)
    controls.append(("same_count_remove_and_duplicate_grain", mutate_table(base.target_snapshot, fact_name, lambda rows: [*rows[:2], {**rows[2], **{column: rows[0][column] for column in grain.key_columns}}]), ("fact_grain", "fact_duplicates")))
    if customer_fk:
        controls.append(("wrong_but_valid_customer_fk", mutate_table(base.target_snapshot, fact_name, lambda rows: [rows[0], rows[1], {**rows[2], customer_fk.fact_column: rows[0][customer_fk.fact_column]}]), ("fact_business_values",)))
    quantity_measure = next((item for item in context.inputs.compiled_plan.measure_specs if item.field_name in {"quantity", "temperature"}), None)
    if quantity_measure:
        controls.append(("quantity_compensation", mutate_table(base.target_snapshot, fact_name, lambda rows: [{**rows[0], quantity_measure.field_name: rows[1][quantity_measure.field_name]}, {**rows[1], quantity_measure.field_name: rows[0][quantity_measure.field_name]}, rows[2]]), ("fact_business_values",)))
    controls.append(("lineage_loss", mutate_table(base.target_snapshot, fact_name, lambda rows: [rows[0], {**rows[1], "source_record_refs": "[]"}, rows[2]]), ("lineage_source_to_target", "fact_business_values")))
    controls.append(("unexplained_filter", mutate_table(base.target_snapshot, fact_name, lambda rows: rows[:2]), ("fact_count", "lineage_source_to_target")))
    if customer_fk:
        controls.append(("orphan_required_fk", mutate_table(base.target_snapshot, fact_name, lambda rows: [{**rows[0], customer_fk.fact_column: 9223372036854775807}, rows[1], rows[2]]), ("foreign_key_integrity",)))
    dimension = next((item for item in context.inputs.compiled_plan.dimension_specs if item.dimension_id == (product_fk.dimension_id if product_fk else "")), None)
    if dimension:
        controls.append(("warehouse_key_collision", mutate_table(base.target_snapshot, dimension.table_name, lambda rows: [rows[0], {**rows[1], dimension.surrogate_key.key_name: rows[0][dimension.surrogate_key.key_name]}]), ("key_uniqueness",)))

    results = []
    for control_id, snapshot, expected_checks in controls:
        outcome = ValidationService().validate(context.inputs, StaticTargetReader(snapshot))
        detected = [item.check_id for item in outcome.report.checks if item.check_id in expected_checks and item.status is ValidationStatus.FAIL]
        if not detected:
            raise ValidationFailure(f"negative control was not detected: {control_id}")
        results.append({"control_id": control_id, "status": "DETECTED", "detected_checks": detected})
    return results


def run_binding_negatives(context: ReferenceValidationContext, counter: list[int]) -> list[dict[str, str]]:
    results = []
    for label, field, value in (
        ("stale_canonical", "canonical_model", context.inputs.canonical_model.model_copy(update={"model_version": "tampered-canonical"})),
        ("stale_plan", "plan", context.inputs.plan.model_copy(update={"policy_version": "tampered-policy"})),
        ("stale_semantic_model", "semantic_model", context.inputs.semantic_model.model_copy(update={"policy_version": "tampered-semantic-policy"})),
    ):
        changed = replace(context.inputs, **{field: value})
        outcome = ValidationService().validate(changed, StaticTargetReader(DuckDBValidationTargetReader(PROJECT_ROOT).snapshot(
            context.inputs.materialization.target_relative_path,
            expected_sha256=context.inputs.materialization.target_file_sha256 or "",
            allowed_table_names=tuple(sorted(context.inputs.compiled_plan.table_names)),
        )))
        detected = next(item for item in outcome.report.checks if item.check_id == "artifact_binding")
        check(detected.status is ValidationStatus.FAIL, f"{label} binding mutation was not rejected", counter)
        results.append({"control_id": label, "status": "DETECTED", "check": "artifact_binding"})
    try:
        DuckDBValidationTargetReader(PROJECT_ROOT).snapshot(
            context.inputs.materialization.target_relative_path,
            expected_sha256="0" * 64,
            allowed_table_names=tuple(sorted(context.inputs.compiled_plan.table_names)),
        )
    except ValidationTargetError as exc:
        check("STALE_TARGET" in str(exc), "stale target hash was not rejected", counter)
        results.append({"control_id": "stale_target", "status": "DETECTED"})
    else:
        raise ValidationFailure("stale target hash was not rejected")
    return results


def write_reference_artifacts(context: ReferenceValidationContext, outcome: ValidationOutcome, controls: list[dict[str, Any]], binding_controls: list[dict[str, str]]) -> Path:
    directory = ROOT / "workspace" / "runs" / ("step22-reference-run" if context.name == "retail" else "step22-generic-reference-run") / "validation"
    directory.mkdir(parents=True, exist_ok=True)
    truth = context.truth
    inputs = context.inputs
    report = outcome.report
    dump(directory / "validation_policy.json", model_json(inputs.policy))
    dump(directory / "source_snapshot.json", {"source_snapshot_id": truth.source_snapshot_id, "source_snapshot_hash": truth.source_snapshot_fingerprint, "source_schema_fingerprints": dict(truth.source_schema_fingerprints), "record_count": len(truth.records), "record_refs": sorted(truth.record_refs)})
    dump(directory / "source_truth.json", {"truth": model_json(truth), "truth_content_hash": truth.content_hash, "independent_from_target": True, "runtime_transformation_input": False})
    dump(directory / "artifact_bindings.json", model_json(inputs.bindings))
    dump(directory / "record_accounting.json", {"accounting": model_json(inputs.accounting), "content_hash": inputs.accounting.content_hash})
    dump(directory / "canonical_model.json", {"canonical_model": model_json(inputs.canonical_model), "content_hash": inputs.canonical_model.content_hash})
    dump(directory / "canonical_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "CANONICALIZATION"], "duplicate_controls": controls})
    dump(directory / "relationship_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "RELATIONSHIPS"]})
    dump(directory / "fact_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "FACT"]})
    dump(directory / "dimension_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "DIMENSIONS"]})
    dump(directory / "grain_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "GRAIN"]})
    dump(directory / "referential_integrity.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "REFERENTIAL_INTEGRITY"]})
    dump(directory / "aggregate_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "AGGREGATE"]})
    dump(directory / "date_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "DATES"]})
    dump(directory / "lineage_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "LINEAGE"]})
    dump(directory / "semantic_reconciliation.json", {"checks": [model_json(item) for item in report.checks if item.scope.value == "SEMANTIC_PROJECTION"], "semantic_model_id": inputs.semantic_model.semantic_model_id, "semantic_model_content_hash": inputs.semantic_model.content_hash})
    dump(directory / "semantic_model.json", model_json(inputs.semantic_model))
    dump(directory / "semantic_validation.json", model_json(inputs.semantic_validation))
    dump(directory / "negative_controls.json", {"controls": controls, "binding_controls": binding_controls, "all_required_controls_detected": True})
    dump(directory / "discrepancies.json", [model_json(item) for item in report.discrepancies])
    dump(directory / "validation_report.json", model_json(report) | {"content_hash": report.content_hash})
    dump(directory / "reconciliation_result.json", model_json(outcome.reconciliation) | {"content_hash": outcome.reconciliation.content_hash})
    dump(directory / "g6_gate_evidence.json", {
        "gate": "G6_DATA_CORRECTNESS",
        "status": report.g6_status.value,
        "eligible": report.g6_eligible,
        "overall_status": report.overall_status.value,
        "required_checks": list(REQUIRED_CHECK_IDS),
        "non_pass_required_checks": [item.check_id for item in report.checks if item.required and item.check_id in REQUIRED_CHECK_IDS and item.status.value != "PASS"],
        "same_target_semantic_validation_not_used_as_source_truth": True,
        "monetary_reconciliation": inputs.policy.monetary_status.value,
        "monetary_reason": inputs.policy.monetary_reason,
        "step23_started": False,
    })
    dump(directory / "run_manifest.json", {
        "run_id": report.run_id,
        "validation_scope": context.name,
        "flow": ["INDEPENDENT_SOURCE_TRUTH_QA_ONLY", "EXISTING_STEP20_ANALYTICAL_ARTIFACTS", "READ_ONLY_TARGET_INSPECTION", "EXISTING_STEP21_SEMANTIC_ARTIFACTS", "STEP22_VALIDATION_RECONCILIATION"],
        "source_truth_id": truth.truth_id,
        "source_truth_content_hash": truth.content_hash,
        "source_snapshot_id": truth.source_snapshot_id,
        "source_snapshot_hash": truth.source_snapshot_fingerprint,
        "canonical_model_id": inputs.canonical_model.model_id,
        "canonical_model_content_hash": inputs.canonical_model.content_hash,
        "record_accounting_id": inputs.accounting.accounting_id,
        "record_accounting_content_hash": inputs.accounting.content_hash,
        "analytical_plan_id": inputs.plan.plan_id,
        "analytical_plan_content_hash": inputs.plan.content_hash,
        "analytical_spec_package_hash": inputs.plan.analytical_spec_package_hash,
        "compiled_plan_id": inputs.compiled_plan.compiled_plan_id,
        "compiled_plan_content_hash": inputs.compiled_plan.content_hash,
        "materialization_artifact_id": inputs.materialization.artifact_id,
        "materialization_artifact_content_hash": inputs.materialization.content_hash,
        "target_relative_path": inputs.materialization.target_relative_path,
        "target_file_sha256": inputs.materialization.target_file_sha256,
        "semantic_model_id": inputs.semantic_model.semantic_model_id,
        "semantic_model_content_hash": inputs.semantic_model.content_hash,
        "semantic_validation_id": inputs.semantic_validation.validation_id,
        "semantic_validation_content_hash": __import__("dirty_data_to_olap.domain.contracts.source", fromlist=["stable_digest"]).stable_digest(inputs.semantic_validation.model_dump(mode="json")),
        "validation_policy_id": inputs.policy.policy_id,
        "validation_policy_version": inputs.policy.policy_version,
        "g6_status": report.g6_status.value,
        "step22_status": "IMPLEMENTED_BLOCKED_G6_PENDING",
        "step23_started": False,
    })
    return directory


def main() -> int:
    counter = [0]
    contexts = {}
    all_run_summaries = []
    for name in ("retail", "generic"):
        context = build_reference_context(name)
        outcome = ValidationService().validate(context.inputs, DuckDBValidationTargetReader(ROOT))
        check(outcome.report.g6_status is not None and not outcome.report.g6_eligible, f"{name} unexpectedly promoted G6", counter)
        check(all(item.check_id in REQUIRED_CHECK_IDS or item.check_id == "monetary_reconciliation" for item in outcome.report.checks), f"{name} emitted an undeclared validation check", counter)
        check(outcome.report.checks[-1].check_id == "no_blocking_discrepancy", f"{name} did not emit derived blocking gate", counter)
        controls = run_negative_controls(context, outcome)
        binding_controls = run_binding_negatives(context, counter)
        dedup_scope = RecordAccountingScope(
            scope_id="dedup-positive-control",
            boundary=AccountingBoundary.SOURCE_TO_CANONICAL,
            input_object_ref="dedup-control-source",
            input_record_refs=("crm-customer-1", "erp-customer-alias-1"),
            entries=(
                RecordAccountingEntry(input_record_ref="crm-customer-1", disposition=RecordDisposition.CONSOLIDATED, output_or_group_ref="cent_customer_1", transformation_or_policy_ref="policy:reviewed-dedup", reason="legitimate duplicate customer representation", provenance_refs=("step22:control",)),
                RecordAccountingEntry(input_record_ref="erp-customer-alias-1", disposition=RecordDisposition.CONSOLIDATED, output_or_group_ref="cent_customer_1", transformation_or_policy_ref="policy:reviewed-dedup", reason="legitimate duplicate customer representation", provenance_refs=("step22:control",)),
            ),
            policy_version="step22-data-correctness-v1",
            provenance_refs=("step22:dedup-positive-control",),
        )
        dedup_control = SourceTruthDuplicateGroup(control_id="customer-dedup-positive", entity_type="customer", source_record_refs=("crm-customer-1", "erp-customer-alias-1"), canonical_entity_id="cent_customer_1", expected_canonical_entity_count=1, rationale="two reviewed source representations contribute to one canonical customer; two input records remain accounted", provenance_refs=("step22:dedup-positive-control",))
        check(dedup_scope.counts[RecordDisposition.CONSOLIDATED.value] == 2 and len({item.output_or_group_ref for item in dedup_scope.entries}) == 1, "deduplication was conflated with source-record loss", counter)
        directory = write_reference_artifacts(context, outcome, controls + [{"control_id": dedup_control.control_id, "status": "PASS", "input_count": 2, "canonical_output_count": 1, "accounting_scope_hash": dedup_scope.content_hash}], binding_controls)
        dump(directory / "deduplication_control.json", {"control": model_json(dedup_control), "accounting_scope": model_json(dedup_scope), "input_count": 2, "canonical_output_count": 1, "source_record_loss": False})
        contexts[name] = outcome
        all_run_summaries.append({"name": name, "g6_status": outcome.report.g6_status.value, "g6_eligible": outcome.report.g6_eligible, "checks": len(outcome.report.checks), "discrepancies": len(outcome.report.discrepancies), "artifact_directory": str(directory.relative_to(ROOT)).replace("\\", "/")})
    print(json.dumps({"status": "PASS", "validator": "step22-data-correctness", "behavioral_checks": counter[0], "runs": all_run_summaries}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
