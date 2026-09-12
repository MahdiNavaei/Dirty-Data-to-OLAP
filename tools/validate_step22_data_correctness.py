"""Behavioral Step22/G6 validator and evidence writer.

The transformation fixture is executed first by ``step22_transformation_support``.
This module loads the independent QA oracle only after that execution and then
proves both positive reconciliation and adversarial mutation detection.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader, ValidationTargetError
from dirty_data_to_olap.application.validation import ValidationInputs, ValidationOutcome, ValidationService
from dirty_data_to_olap.domain.contracts.canonical import RecordDisposition
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingArtifact,
    SourceTruthManifest,
    TargetSnapshot,
    TargetTableSnapshot,
    ValidationStatus,
)
from tools.step22_reference_support import (
    REQUIRED_CHECK_IDS,
    ReferenceValidationContext,
    build_reference_context,
)


class ValidationFailure(RuntimeError):
    pass


class StaticTargetReader:
    """Reader for mutation controls; it never changes the DuckDB target."""

    def __init__(self, snapshot: TargetSnapshot):
        self._snapshot = snapshot

    def snapshot(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        allowed_table_names: tuple[str, ...],
    ) -> TargetSnapshot:
        return self._snapshot


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def model_json(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def require(condition: bool, message: str, counter: list[int]) -> None:
    if not condition:
        raise ValidationFailure(message)
    counter[0] += 1


def status_map(outcome: ValidationOutcome) -> dict[str, ValidationStatus]:
    return {item.check_id: item.status for item in outcome.report.checks}


def validate_static(inputs: ValidationInputs, snapshot: TargetSnapshot) -> ValidationOutcome:
    return ValidationService().validate(inputs, StaticTargetReader(snapshot))


def mutate_table(
    snapshot: TargetSnapshot,
    table_name: str,
    mutate_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> TargetSnapshot:
    tables = []
    for table in snapshot.tables:
        if table.table_name == table_name:
            tables.append(table.model_copy(update={"rows": tuple(mutate_rows([dict(row) for row in table.rows]))}))
        else:
            tables.append(table)
    return snapshot.model_copy(update={"tables": tuple(tables)})


def fact_table_name(context: ReferenceValidationContext) -> str:
    return context.inputs.compiled_plan.fact_specs[0].table_name


def run_target_negative_controls(
    context: ReferenceValidationContext,
    base: ValidationOutcome,
) -> list[dict[str, Any]]:
    if base.target_snapshot is None:
        raise ValidationFailure("positive target snapshot is unavailable")
    fact_spec = context.inputs.compiled_plan.fact_specs[0]
    fact_name = fact_spec.table_name
    fact_table = base.target_snapshot.table_map[fact_name]
    rows = list(fact_table.rows)
    if len(rows) < 3:
        raise ValidationFailure("reference target does not contain enough fact rows for controls")
    grain = next(item for item in context.inputs.compiled_plan.grain_specs if item.grain_id == fact_spec.grain_spec_id)
    product_fk = next(
        (item for item in fact_spec.dimension_foreign_keys if item.dimension_id.endswith("product")),
        None,
    )
    customer_fk = next(
        (item for item in fact_spec.dimension_foreign_keys if item.dimension_id.endswith("customer")),
        None,
    )
    quantity_measure = next(
        (item for item in context.inputs.compiled_plan.measure_specs if item.field_name in {"quantity", "temperature"}),
        None,
    )
    controls: list[tuple[str, TargetSnapshot, tuple[str, ...]]] = []
    if product_fk is not None:
        def swap_product(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
            left = values[0][product_fk.fact_column]
            right = values[1][product_fk.fact_column]
            values[0][product_fk.fact_column] = right
            values[1][product_fk.fact_column] = left
            return values

        controls.append((
            "same_total_wrong_product_allocation",
            mutate_table(base.target_snapshot, fact_name, swap_product),
            ("fact_business_values", "quantity_slices"),
        ))

    def duplicate_grain(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        values[2] = {**values[2], **{column: values[0][column] for column in grain.key_columns}}
        return values

    controls.append((
        "same_count_remove_and_duplicate_grain",
        mutate_table(base.target_snapshot, fact_name, duplicate_grain),
        ("fact_grain", "fact_duplicates"),
    ))
    if customer_fk is not None:
        controls.append((
            "wrong_but_valid_customer_fk",
            mutate_table(
                base.target_snapshot,
                fact_name,
                lambda values: [values[0], values[1], {**values[2], customer_fk.fact_column: values[0][customer_fk.fact_column]}],
            ),
            ("fact_business_values",),
        ))
    if quantity_measure is not None:
        field = quantity_measure.field_name
        controls.append((
            "quantity_compensation",
            mutate_table(
                base.target_snapshot,
                fact_name,
                lambda values: [
                    {**values[0], field: values[1][field]},
                    {**values[1], field: values[0][field]},
                    values[2],
                ],
            ),
            ("fact_business_values",),
        ))
    controls.append((
        "lineage_loss",
        mutate_table(
            base.target_snapshot,
            fact_name,
            lambda values: [values[0], {**values[1], "source_record_refs": "[]"}, values[2]],
        ),
        ("fact_business_values", "lineage_source_to_target"),
    ))
    controls.append((
        "unexplained_filter",
        mutate_table(base.target_snapshot, fact_name, lambda values: values[:2]),
        ("fact_count", "lineage_source_to_target"),
    ))
    if customer_fk is not None:
        controls.append((
            "orphan_required_fk",
            mutate_table(
                base.target_snapshot,
                fact_name,
                lambda values: [
                    {**values[0], customer_fk.fact_column: 9223372036854775807},
                    values[1],
                    values[2],
                ],
            ),
            ("foreign_key_integrity",),
        ))
    dimension = next(
        (
            item
            for item in context.inputs.compiled_plan.dimension_specs
            if product_fk is not None and item.dimension_id == product_fk.dimension_id
        ),
        None,
    )
    if dimension is not None:
        controls.append((
            "warehouse_key_collision",
            mutate_table(
                base.target_snapshot,
                dimension.table_name,
                lambda values: [
                    values[0],
                    {**values[1], dimension.surrogate_key.key_name: values[0][dimension.surrogate_key.key_name]},
                ],
            ),
            ("key_uniqueness",),
        ))

    results = []
    for control_id, snapshot, expected_checks in controls:
        outcome = validate_static(context.inputs, snapshot)
        detected = [
            item.check_id
            for item in outcome.report.checks
            if item.check_id in expected_checks and item.status is ValidationStatus.FAIL
        ]
        if not detected:
            raise ValidationFailure(f"negative control was not detected: {control_id}")
        results.append({"control_id": control_id, "status": "DETECTED", "detected_checks": detected})
    return results


def run_negative_controls(
    context: ReferenceValidationContext,
    base: ValidationOutcome,
) -> list[dict[str, Any]]:
    """Backward-compatible name for the target mutation suite."""

    return run_target_negative_controls(context, base)


def replace_accounting_entry(
    accounting: RecordAccountingArtifact,
    boundary: AccountingBoundary,
    input_ref: str,
    **updates: Any,
) -> RecordAccountingArtifact:
    scopes = []
    for scope in accounting.scopes:
        if scope.boundary is not boundary:
            scopes.append(scope)
            continue
        entries = tuple(
            entry.model_copy(update=updates) if entry.input_record_ref == input_ref else entry
            for entry in scope.entries
        )
        scopes.append(scope.model_copy(update={"entries": entries}))
    return accounting.model_copy(update={"scopes": tuple(scopes)})


def with_accounting(inputs: ValidationInputs, accounting: RecordAccountingArtifact) -> ValidationInputs:
    return replace(
        inputs,
        accounting=accounting,
        bindings=inputs.bindings.model_copy(update={"record_accounting_content_hash": accounting.content_hash}),
    )


def run_accounting_negative_controls(
    context: ReferenceValidationContext,
    target_snapshot: TargetSnapshot,
) -> list[dict[str, Any]]:
    source_ref = next(iter(context.truth.record_refs))
    source_entry = next(
        item for item in context.inputs.accounting.scopes[0].entries if item.input_record_ref == source_ref
    )
    controls = [
        (
            "wrong_source_disposition",
            replace_accounting_entry(
                context.inputs.accounting,
                AccountingBoundary.SOURCE_TO_CANONICAL,
                source_ref,
                disposition=RecordDisposition.FILTERED_EXPLICIT,
                output_or_group_ref=None,
            ),
            "source_record_accounting",
        ),
        (
            "wrong_source_output_group",
            replace_accounting_entry(
                context.inputs.accounting,
                AccountingBoundary.SOURCE_TO_CANONICAL,
                source_ref,
                output_or_group_ref=(source_entry.output_or_group_ref or "output") + ":tampered",
            ),
            "source_record_accounting",
        ),
    ]
    filtered = next(
        (
            entry
            for scope in context.inputs.accounting.scopes
            if scope.boundary is AccountingBoundary.CANONICAL_TO_ANALYTICAL
            for entry in scope.entries
            if entry.disposition is RecordDisposition.FILTERED_EXPLICIT
        ),
        None,
    )
    if filtered is not None:
        controls.append((
            "wrong_canonical_filter_status",
            replace_accounting_entry(
                context.inputs.accounting,
                AccountingBoundary.CANONICAL_TO_ANALYTICAL,
                filtered.input_record_ref,
                disposition=RecordDisposition.EMITTED_DIRECT,
                output_or_group_ref="tampered_fact_output",
            ),
            "canonical_to_analytical_accounting",
        ))
    results = []
    for control_id, accounting, check_id in controls:
        outcome = validate_static(with_accounting(context.inputs, accounting), target_snapshot)
        item = next(item for item in outcome.report.checks if item.check_id == check_id)
        if item.status is not ValidationStatus.FAIL:
            raise ValidationFailure(f"accounting control was not detected: {control_id}")
        results.append({"control_id": control_id, "status": "DETECTED", "detected_checks": [check_id]})
    return results


def replace_maps(context: ReferenceValidationContext, updates: dict[str, dict[str, Any]]):
    maps = tuple(
        item.model_copy(update=updates[item.record_ref]) if item.record_ref in updates else item
        for item in context.inputs.canonical_model.source_record_maps
    )
    return context.inputs.canonical_model.model_copy(update={"source_record_maps": maps})


def run_canonical_negative_controls(
    context: ReferenceValidationContext,
    target_snapshot: TargetSnapshot,
) -> list[dict[str, Any]]:
    maps = {item.record_ref: item for item in context.inputs.canonical_model.source_record_maps}
    refs = sorted(maps)
    first_ref, second_ref = refs[0], refs[1]
    first_id, second_id = maps[first_ref].canonical_entity_id, maps[second_ref].canonical_entity_id
    event_ref = next(
        item.record_ref for item in context.truth.records if item.subject_type in {"order", "order_line", "orderline", "reading"}
    )
    customer_type = next(
        (
            item.canonical_entity_type_id
            for item in context.inputs.canonical_model.entity_types
            if item.semantic_id == "customer"
        ),
        next(
            (
                item.canonical_entity_type_id
                for item in context.inputs.canonical_model.entity_types
                if item.kind.value != "EVENT"
            ),
            context.inputs.canonical_model.entity_types[0].canonical_entity_type_id,
        ),
    )
    controls = [
        ("swapped_customer_maps", {first_ref: {"canonical_entity_id": second_id}, second_ref: {"canonical_entity_id": first_id}}, "canonical_membership"),
        ("wrong_map_entity", {first_ref: {"canonical_entity_id": second_id}}, "canonical_membership"),
        ("false_merge", {second_ref: {"canonical_entity_id": first_id}}, "canonical_membership"),
        ("false_split", {first_ref: {"canonical_entity_id": stable_id("cent", {"false_split": first_ref})}}, "canonical_membership"),
        ("wrong_event_map_type", {event_ref: {"canonical_entity_type_id": customer_type}}, "canonical_membership"),
        ("stale_source_snapshot", {first_ref: {"snapshot_id": "stale-source-snapshot"}}, "canonical_membership"),
    ]
    removed_id = context.inputs.canonical_model.instances[0].canonical_entity_id
    controls.append((
        "removed_canonical_instance",
        {},
        "canonical_entity_counts",
    ))
    results = []
    for control_id, updates, check_id in controls:
        model = replace_maps(context, updates)
        if control_id == "removed_canonical_instance":
            model = model.model_copy(update={
                "instances": tuple(
                    item for item in model.instances if item.canonical_entity_id != removed_id
                )
            })
        inputs = replace(
            context.inputs,
            canonical_model=model,
            bindings=context.inputs.bindings.model_copy(update={
                "canonical_model_content_hash": model.content_hash,
            }),
        )
        outcome = validate_static(inputs, target_snapshot)
        item = next(item for item in outcome.report.checks if item.check_id == check_id)
        if item.status is not ValidationStatus.FAIL:
            raise ValidationFailure(f"canonical control was not detected: {control_id}")
        results.append({"control_id": control_id, "status": "DETECTED", "detected_checks": [check_id]})
    return results


def run_oracle_mismatch_controls(
    context: ReferenceValidationContext,
    target_snapshot: TargetSnapshot,
) -> list[dict[str, Any]]:
    record = context.truth.records[0]
    changed_record = record.model_copy(update={"canonical_entity_id": "cent_" + "f" * 32})
    truth = context.truth.model_copy(update={
        "records": (changed_record, *context.truth.records[1:]),
    })
    truth_inputs = replace(
        context.inputs,
        source_truth=truth,
        bindings=context.inputs.bindings.model_copy(update={
            "source_truth_content_hash": truth.content_hash,
            "benchmark_truth_hash": truth.content_hash,
        }),
    )
    transformation = replace_maps(
        context,
        {context.inputs.canonical_model.source_record_maps[0].record_ref: {
            "canonical_entity_id": stable_id("cent", {"transformation_membership": True}),
        }},
    )
    transformation_inputs = replace(
        context.inputs,
        canonical_model=transformation,
        bindings=context.inputs.bindings.model_copy(update={
            "canonical_model_content_hash": transformation.content_hash,
        }),
    )
    results = []
    for control_id, inputs in (
        ("oracle_membership_mismatch", truth_inputs),
        ("transformation_membership_mismatch", transformation_inputs),
    ):
        outcome = validate_static(inputs, target_snapshot)
        item = next(item for item in outcome.report.checks if item.check_id == "canonical_membership")
        if item.status is not ValidationStatus.FAIL:
            raise ValidationFailure(f"oracle mismatch control was not detected: {control_id}")
        results.append({"control_id": control_id, "status": "DETECTED", "detected_checks": [item.check_id]})
    return results


def run_binding_negative_controls(
    context: ReferenceValidationContext,
    target_snapshot: TargetSnapshot,
) -> list[dict[str, str]]:
    base_inputs = context.inputs
    mutations = [
        ("stale_canonical", replace(base_inputs, canonical_model=base_inputs.canonical_model.model_copy(update={"model_version": "tampered-canonical"}))),
        ("stale_accounting", replace(base_inputs, accounting=base_inputs.accounting.model_copy(update={"policy_version": "tampered-accounting"}))),
        ("stale_analytical_dataset", replace(base_inputs, analytical_dataset=base_inputs.analytical_dataset.model_copy(update={"dataset_id": "tampered-dataset"}))),
        ("stale_input_binding", replace(base_inputs, analytical_input_binding=base_inputs.analytical_input_binding.model_copy(update={"binding_id": "tampered-input-binding"}))),
        ("stale_plan", replace(base_inputs, plan=base_inputs.plan.model_copy(update={"plan_version": "tampered-plan"}))),
        ("stale_semantic_model", replace(base_inputs, semantic_model=base_inputs.semantic_model.model_copy(update={"policy_version": "tampered-semantic-policy"}))),
        ("stale_truth", replace(base_inputs, source_truth=base_inputs.source_truth.model_copy(update={"truth_version": "tampered-truth"}))),
    ]
    results = []
    for control_id, inputs in mutations:
        outcome = validate_static(inputs, target_snapshot)
        item = next(item for item in outcome.report.checks if item.check_id == "artifact_binding")
        if item.status is not ValidationStatus.FAIL:
            raise ValidationFailure(f"binding control was not detected: {control_id}")
        results.append({"control_id": control_id, "status": "DETECTED", "check": "artifact_binding"})
    try:
        DuckDBValidationTargetReader(ROOT).snapshot(
            base_inputs.materialization.target_relative_path,
            expected_sha256="0" * 64,
            allowed_table_names=tuple(sorted(base_inputs.compiled_plan.table_names)),
        )
    except ValidationTargetError as exc:
        if "STALE_TARGET" not in str(exc):
            raise ValidationFailure(f"stale target control returned an unexpected error: {exc}")
        results.append({"control_id": "stale_target_hash", "status": "DETECTED", "check": "artifact_binding"})
    else:
        raise ValidationFailure("stale target hash was not rejected")
    return results


def run_multifact_control(
    context: ReferenceValidationContext,
    target_snapshot: TargetSnapshot,
) -> dict[str, Any]:
    if context.name != "generic":
        return {"control_id": "multi_fact_scope", "status": "NOT_APPLICABLE"}
    original = context.inputs.compiled_plan.fact_specs[0]
    second = original.model_copy(update={
        "fact_id": "fact_device_reading_second",
        "table_name": "fact_device_reading_second",
    })
    compiled = context.inputs.compiled_plan.model_copy(update={
        "fact_specs": (*context.inputs.compiled_plan.fact_specs, second),
        "table_names": (*context.inputs.compiled_plan.table_names, second.table_name),
    })
    original_table = target_snapshot.table_map[original.table_name]
    second_table = TargetTableSnapshot(
        table_name=second.table_name,
        columns=original_table.columns,
        rows=original_table.rows,
    )
    snapshot = target_snapshot.model_copy(update={
        "available_table_names": (*target_snapshot.available_table_names, second.table_name),
        "tables": (*target_snapshot.tables, second_table),
    })
    inputs = replace(context.inputs, compiled_plan=compiled)
    outcome = validate_static(inputs, snapshot)
    item = next(item for item in outcome.report.checks if item.check_id == "fact_count")
    if item.status is not ValidationStatus.FAIL:
        raise ValidationFailure("multi-fact control did not fail fact-scoped accounting")
    return {
        "control_id": "multi_fact_scope",
        "status": "DETECTED",
        "detected_checks": ["fact_count"],
        "fact_ids": [item.fact_id for item in compiled.fact_specs],
    }


def _scope_checks(context: ReferenceValidationContext, outcome: ValidationOutcome, scope: str) -> list[dict[str, Any]]:
    return [
        model_json(item)
        for item in outcome.report.checks
        if item.scope.value == scope
    ]


def write_reference_artifacts(
    context: ReferenceValidationContext,
    outcome: ValidationOutcome,
    controls: list[dict[str, Any]],
    binding_controls: list[dict[str, str]],
    accounting_controls: list[dict[str, Any]],
    canonical_controls: list[dict[str, Any]],
    oracle_controls: list[dict[str, Any]],
    multifact_control: dict[str, Any],
) -> Path:
    directory = ROOT / "workspace" / "runs" / (
        "step22-reference-run" if context.name == "retail" else "step22-generic-reference-run"
    ) / "validation"
    inputs = context.inputs
    truth = context.truth
    report = outcome.report
    dump(directory / "validation_policy.json", model_json(inputs.policy))
    dump(directory / "source_snapshot.json", {
        "source_snapshot_id": truth.source_snapshot_id,
        "source_snapshot_hash": truth.source_snapshot_fingerprint,
        "source_schema_fingerprints": dict(truth.source_schema_fingerprints),
        "record_count": len(truth.records),
        "record_refs": sorted(truth.record_refs),
    })
    dump(directory / "source_truth.json", {
        "truth": model_json(truth),
        "truth_content_hash": truth.content_hash,
        "independent_from_target": True,
        "runtime_transformation_input": False,
    })
    dump(directory / "artifact_bindings.json", model_json(inputs.bindings))
    dump(directory / "record_accounting.json", {
        "accounting": model_json(inputs.accounting),
        "content_hash": inputs.accounting.content_hash,
        "boundaries": [item.boundary.value for item in inputs.accounting.scopes],
    })
    dump(directory / "canonical_model.json", {
        "canonical_model": model_json(inputs.canonical_model),
        "content_hash": inputs.canonical_model.content_hash,
        "instance_count": len(inputs.canonical_model.instances),
        "source_record_map_count": len(inputs.canonical_model.source_record_maps),
        "finalization_service": "CanonicalFinalizationService",
    })
    dump(directory / "analytical_input_dataset.json", {
        "dataset": model_json(inputs.analytical_dataset),
        "content_hash": inputs.analytical_dataset.content_hash,
    })
    dump(directory / "analytical_input_binding.json", {
        "binding": model_json(inputs.analytical_input_binding),
        "content_hash": inputs.analytical_input_binding.content_hash,
    })
    dump(directory / "analytical_plan.json", model_json(inputs.plan) | {"content_hash": inputs.plan.content_hash})
    dump(directory / "compiled_plan.json", model_json(inputs.compiled_plan) | {"content_hash": inputs.compiled_plan.content_hash})
    dump(directory / "generated_sql.json", model_json(context.context.generated_sql) | {"sql_hash": context.context.generated_sql.sql_hash})
    dump(directory / "materialization.json", model_json(inputs.materialization) | {"content_hash": inputs.materialization.content_hash})
    dump(directory / "target_snapshot.json", model_json(outcome.target_snapshot) if outcome.target_snapshot else None)
    dump(directory / "canonical_reconciliation.json", {
        "checks": _scope_checks(context, outcome, "CANONICALIZATION"),
        "relationship_checks": _scope_checks(context, outcome, "RELATIONSHIPS"),
        "controls": canonical_controls + oracle_controls,
    })
    dump(directory / "accounting_reconciliation.json", {
        "checks": [
            model_json(item) for item in report.checks
            if item.check_id in {"source_record_accounting", "canonical_to_analytical_accounting"}
        ],
        "controls": accounting_controls,
    })
    for filename, scope in (
        ("relationship_reconciliation.json", "RELATIONSHIPS"),
        ("fact_reconciliation.json", "FACT"),
        ("dimension_reconciliation.json", "DIMENSIONS"),
        ("grain_reconciliation.json", "GRAIN"),
        ("referential_integrity.json", "REFERENTIAL_INTEGRITY"),
        ("aggregate_reconciliation.json", "AGGREGATE"),
        ("date_reconciliation.json", "DATES"),
        ("lineage_reconciliation.json", "LINEAGE"),
        ("semantic_reconciliation.json", "SEMANTIC_PROJECTION"),
    ):
        dump(directory / filename, {"checks": _scope_checks(context, outcome, scope)})
    dump(directory / "semantic_model.json", {
        "semantic_model": model_json(inputs.semantic_model),
        "content_hash": inputs.semantic_model.content_hash,
    })
    dump(directory / "semantic_validation.json", {
        "semantic_validation": model_json(inputs.semantic_validation),
        "content_hash": stable_digest(inputs.semantic_validation.model_dump(mode="json")),
    })
    dump(directory / "negative_controls.json", {
        "target_controls": controls,
        "binding_controls": binding_controls,
        "accounting_controls": accounting_controls,
        "canonical_controls": canonical_controls,
        "oracle_controls": oracle_controls,
        "multi_fact_control": multifact_control,
        "all_required_controls_detected": True,
    })
    dump(directory / "discrepancies.json", [model_json(item) for item in report.discrepancies])
    dump(directory / "validation_report.json", model_json(report) | {"content_hash": report.content_hash})
    dump(directory / "reconciliation_result.json", model_json(outcome.reconciliation) | {"content_hash": outcome.reconciliation.content_hash})
    dump(directory / "g6_gate_evidence.json", {
        "gate": "G6_DATA_CORRECTNESS",
        "status": report.g6_status.value,
        "eligible": report.g6_eligible,
        "overall_status": report.overall_status.value,
        "required_checks": list(REQUIRED_CHECK_IDS),
        "required_check_statuses": {item.check_id: item.status.value for item in report.checks if item.required},
        "non_pass_required_checks": [item.check_id for item in report.checks if item.required and item.status is not ValidationStatus.PASS],
        "discrepancy_count": len(report.discrepancies),
        "same_target_semantic_validation_not_used_as_source_truth": True,
        "monetary_reconciliation": inputs.policy.monetary_status.value,
        "monetary_reason": inputs.policy.monetary_reason,
        "step23_started": False,
    })
    dump(directory / "run_manifest.json", {
        "run_id": report.run_id,
        "validation_scope": context.name,
        "flow": [
            "STEP19_CANONICAL_FINALIZATION",
            "RUNTIME_STAGE_SCOPED_ACCOUNTING",
            "ANALYTICAL_INPUT_DATASET_BINDING",
            "STEP20_ANALYTICAL_ARTIFACTS",
            "READ_ONLY_TARGET_INSPECTION",
            "STEP21_SEMANTIC_ARTIFACTS",
            "STEP22_VALIDATION_RECONCILIATION",
        ],
        "source_truth_id": truth.truth_id,
        "source_truth_content_hash": truth.content_hash,
        "source_snapshot_id": truth.source_snapshot_id,
        "source_snapshot_hash": truth.source_snapshot_fingerprint,
        "canonical_model_id": inputs.canonical_model.model_id,
        "canonical_model_content_hash": inputs.canonical_model.content_hash,
        "canonical_instance_count": len(inputs.canonical_model.instances),
        "canonical_source_record_map_count": len(inputs.canonical_model.source_record_maps),
        "record_accounting_id": inputs.accounting.accounting_id,
        "record_accounting_content_hash": inputs.accounting.content_hash,
        "analytical_dataset_id": inputs.analytical_dataset.dataset_id,
        "analytical_dataset_content_hash": inputs.analytical_dataset.content_hash,
        "analytical_input_binding_id": inputs.analytical_input_binding.binding_id,
        "analytical_input_binding_content_hash": inputs.analytical_input_binding.content_hash,
        "analytical_plan_id": inputs.plan.plan_id,
        "analytical_plan_content_hash": inputs.plan.content_hash,
        "analytical_spec_package_hash": inputs.plan.analytical_spec_package_hash,
        "compiled_plan_id": inputs.compiled_plan.compiled_plan_id,
        "compiled_plan_content_hash": inputs.compiled_plan.content_hash,
        "fact_ids": [item.fact_id for item in inputs.compiled_plan.fact_specs],
        "materialization_artifact_id": inputs.materialization.artifact_id,
        "materialization_artifact_content_hash": inputs.materialization.content_hash,
        "target_relative_path": inputs.materialization.target_relative_path,
        "target_file_sha256": inputs.materialization.target_file_sha256,
        "semantic_model_id": inputs.semantic_model.semantic_model_id,
        "semantic_model_content_hash": inputs.semantic_model.content_hash,
        "semantic_validation_id": inputs.semantic_validation.validation_id,
        "semantic_validation_content_hash": stable_digest(inputs.semantic_validation.model_dump(mode="json")),
        "validation_policy_id": inputs.policy.policy_id,
        "validation_policy_version": inputs.policy.policy_version,
        "validation_report_id": report.report_id,
        "validation_report_content_hash": report.content_hash,
        "reconciliation_result_id": outcome.reconciliation.result_id,
        "reconciliation_result_content_hash": outcome.reconciliation.content_hash,
        "reconciliation_result_content_hash": outcome.reconciliation.content_hash,
        "g6_status": report.g6_status.value,
        "step22_status": "COMPLETED_G6_PASS",
        "step23_started": False,
    })
    return directory


def main() -> int:
    counter = [0]
    run_summaries = []
    for name in ("retail", "generic"):
        context = build_reference_context(name)
        # Make the positive target snapshot available to scoped controls without
        # adding it to the production validation input contract.
        base = ValidationService().validate(
            context.inputs,
            DuckDBValidationTargetReader(ROOT),
        )
        if base.target_snapshot is None:
            raise ValidationFailure(f"{name} positive target snapshot is unavailable")
        statuses = status_map(base)
        require(base.report.g6_status.value == "PASS" and base.report.g6_eligible, f"{name} did not pass G6", counter)
        require(all(statuses.get(check_id) is ValidationStatus.PASS for check_id in REQUIRED_CHECK_IDS), f"{name} has a non-PASS required check", counter)
        require(statuses.get("monetary_reconciliation") is ValidationStatus.NOT_APPLICABLE, f"{name} monetary status changed unexpectedly", counter)
        require(len(base.report.discrepancies) == 0, f"{name} has unexplained positive discrepancies", counter)
        require(set(statuses) == set(REQUIRED_CHECK_IDS) | {"monetary_reconciliation", "no_blocking_discrepancy"}, f"{name} emitted an undeclared check", counter)
        require(len(base.report.checks) == len(REQUIRED_CHECK_IDS) + 1, f"{name} check count is not 24 blocking plus monetary N/A", counter)
        source_scope = next(item for item in context.inputs.accounting.scopes if item.boundary is AccountingBoundary.SOURCE_TO_CANONICAL)
        canonical_scope = next(item for item in context.inputs.accounting.scopes if item.boundary is AccountingBoundary.CANONICAL_TO_ANALYTICAL)
        require(len(source_scope.entries) == len(context.truth.records), f"{name} source accounting denominator is not exact", counter)
        require(len(canonical_scope.entries) == len(context.inputs.canonical_model.instances), f"{name} canonical accounting denominator is not exact", counter)
        if name == "retail":
            group = context.truth.duplicate_groups[0]
            actual_group = next(item for item in context.inputs.canonical_model.instances if item.canonical_entity_id == group.canonical_entity_id)
            actual_group_maps = [item for item in context.inputs.canonical_model.source_record_maps if item.canonical_entity_id == group.canonical_entity_id]
            require(len(actual_group_maps) == 2, "retail dedup maps lost a source record", counter)
            require(set(actual_group.source_record_refs) == set(group.source_record_refs), "retail dedup membership group differs from truth", counter)
            require(len(actual_group.source_record_refs) == 2, "retail dedup output cardinality is not one canonical entity", counter)
            require(all(item.terminal_disposition is RecordDisposition.CONSOLIDATED for item in actual_group_maps), "retail dedup dispositions are not CONSOLIDATED", counter)
        target_controls = run_target_negative_controls(context, base)
        binding_controls = run_binding_negative_controls(context, base.target_snapshot)
        accounting_controls = run_accounting_negative_controls(context, base.target_snapshot)
        canonical_controls = run_canonical_negative_controls(context, base.target_snapshot)
        oracle_controls = run_oracle_mismatch_controls(context, base.target_snapshot)
        multifact_control = run_multifact_control(context, base.target_snapshot)
        directory = write_reference_artifacts(
            context,
            base,
            target_controls,
            binding_controls,
            accounting_controls,
            canonical_controls,
            oracle_controls,
            multifact_control,
        )
        run_summaries.append({
            "name": name,
            "g6_status": base.report.g6_status.value,
            "g6_eligible": base.report.g6_eligible,
            "checks": len(base.report.checks),
            "required_checks": len(REQUIRED_CHECK_IDS),
            "discrepancies": len(base.report.discrepancies),
            "canonical_instances": len(context.inputs.canonical_model.instances),
            "canonical_source_record_maps": len(context.inputs.canonical_model.source_record_maps),
            "accounting_scopes": [item.boundary.value for item in context.inputs.accounting.scopes],
            "artifact_directory": str(directory.relative_to(ROOT)).replace("\\", "/"),
        })
    print(json.dumps({
        "status": "PASS",
        "validator": "step22-data-correctness",
        "behavioral_checks": counter[0],
        "runs": run_summaries,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
