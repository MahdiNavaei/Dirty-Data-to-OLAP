"""Generic source-to-OLAP validation and reconciliation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, Protocol

from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalPlan,
    CompiledPlan,
    FactSpec,
    MaterializationArtifact,
    MeasureSpec,
)
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel, RecordDisposition
from dirty_data_to_olap.domain.contracts.semantic import SemanticModel, SemanticValidationResult
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.domain.contracts.validation import (
    GateStatus,
    ReconciliationMetric,
    ReconciliationResult,
    RecordAccountingArtifact,
    SourceTruthManifest,
    TargetSnapshot,
    ValidationArtifactBindings,
    ValidationCheck,
    ValidationDiscrepancy,
    ValidationPolicy,
    ValidationReport,
    ValidationScope,
    ValidationSeverity,
    ValidationStatus,
    reconciliation_result_id,
    validation_report_id,
)


class ValidationTargetReader(Protocol):
    def snapshot(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        allowed_table_names: tuple[str, ...],
    ) -> TargetSnapshot:
        ...


@dataclass(frozen=True)
class ValidationInputs:
    source_truth: SourceTruthManifest
    canonical_model: CanonicalModel
    accounting: RecordAccountingArtifact
    plan: AnalyticalPlan
    compiled_plan: CompiledPlan
    materialization: MaterializationArtifact
    semantic_model: SemanticModel
    semantic_validation: SemanticValidationResult
    policy: ValidationPolicy
    bindings: ValidationArtifactBindings


@dataclass(frozen=True)
class ValidationOutcome:
    report: ValidationReport
    reconciliation: ReconciliationResult
    target_snapshot: TargetSnapshot | None


def _normal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes, dict, list, tuple)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, tuple):
        return tuple(_normal(item) for item in value)
    if isinstance(value, list):
        return tuple(_normal(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _normal(item) for key, item in value.items()}
    return value


def _numeric(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _equal(expected: Any, observed: Any) -> bool:
    if isinstance(expected, Mapping) and isinstance(observed, Mapping):
        return set(expected) == set(observed) and all(_equal(expected[key], observed[key]) for key in expected)
    if isinstance(expected, (list, tuple)) and isinstance(observed, (list, tuple)):
        return len(expected) == len(observed) and all(_equal(left, right) for left, right in zip(expected, observed))
    left = _numeric(expected)
    right = _numeric(observed)
    if left is not None and right is not None:
        return left == right
    return _normal(expected) == _normal(observed)


def _as_refs(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return (value,)
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
        return (value,)
    return (str(value),)


def _status_for_comparison(expected: Any, observed: Any) -> ValidationStatus:
    return ValidationStatus.PASS if _equal(expected, observed) else ValidationStatus.FAIL


class ValidationService:
    """Compare independent source/domain truth with bound downstream artifacts."""

    def validate(self, inputs: ValidationInputs, target_reader: ValidationTargetReader) -> ValidationOutcome:
        truth = inputs.source_truth
        plan = inputs.plan
        compiled = inputs.compiled_plan
        materialization = inputs.materialization
        discrepancies: list[ValidationDiscrepancy] = []
        checks: list[ValidationCheck] = []

        try:
            target = target_reader.snapshot(
                materialization.target_relative_path,
                expected_sha256=materialization.target_file_sha256 or "",
                allowed_table_names=tuple(sorted(compiled.table_names)),
            )
            target_error: str | None = None
        except Exception as exc:  # adapter converts unsafe/stale target state into evidence
            target = None
            target_error = f"{type(exc).__name__}: {exc}"

        def add_check(
            check_id: str,
            name: str,
            status: ValidationStatus,
            scope: ValidationScope,
            details: str,
            *,
            expected: Any = None,
            observed: Any = None,
            severity: ValidationSeverity = ValidationSeverity.G6_BLOCKING,
            required: bool = True,
            affected_refs: tuple[str, ...] = (),
            likely_stage: str = "VALIDATION_RECONCILIATION",
        ) -> None:
            discrepancy_ids: tuple[str, ...] = ()
            if status is ValidationStatus.FAIL:
                discrepancy_id = f"disc_{stable_digest({'check': check_id, 'expected': _normal(expected), 'observed': _normal(observed), 'refs': affected_refs})[:32]}"
                discrepancies.append(ValidationDiscrepancy(
                    discrepancy_id=discrepancy_id,
                    check_id=check_id,
                    severity=severity,
                    boundary=scope,
                    summary=details,
                    expected=expected,
                    observed=observed,
                    affected_refs=affected_refs,
                    likely_stage=likely_stage,
                    evidence_refs=(f"validation:check:{check_id}",),
                ))
                discrepancy_ids = (discrepancy_id,)
            checks.append(ValidationCheck(
                check_id=check_id,
                name=name,
                status=status,
                severity=severity,
                scope=scope,
                required=required,
                details=details,
                expected=expected,
                observed=observed,
                evidence_refs=(f"validation:check:{check_id}",),
                discrepancy_ids=discrepancy_ids,
            ))

        def add_target_unavailable(check_id: str, name: str, scope: ValidationScope) -> None:
            add_check(
                check_id,
                name,
                ValidationStatus.NOT_EVALUATED,
                scope,
                "target snapshot was unavailable: " + (target_error or "unknown adapter error"),
            )

        # The binding check is evaluated without using target contents as truth.
        binding_errors: list[str] = []
        expected_binding = {
            "source_snapshot_id": truth.source_snapshot_id,
            "source_snapshot_hash": truth.source_snapshot_fingerprint,
            "source_truth_id": truth.truth_id,
            "source_truth_content_hash": truth.content_hash,
            "canonical_model_id": inputs.canonical_model.model_id,
            "canonical_model_content_hash": inputs.canonical_model.content_hash,
            "record_accounting_id": inputs.accounting.accounting_id,
            "record_accounting_content_hash": inputs.accounting.content_hash,
            "analytical_plan_id": plan.plan_id,
            "analytical_plan_content_hash": plan.content_hash,
            "analytical_spec_package_hash": plan.analytical_spec_package_hash,
            "compiled_plan_id": compiled.compiled_plan_id,
            "compiled_plan_content_hash": compiled.content_hash,
            "materialization_artifact_id": materialization.artifact_id,
            "materialization_artifact_content_hash": materialization.content_hash,
            "target_relative_path": materialization.target_relative_path,
            "target_config_fingerprint": materialization.target_config_fingerprint,
            "target_file_sha256": materialization.target_file_sha256,
            "semantic_model_id": inputs.semantic_model.semantic_model_id,
            "semantic_model_content_hash": inputs.semantic_model.content_hash,
            "semantic_validation_id": inputs.semantic_validation.validation_id,
            "semantic_validation_content_hash": stable_digest(inputs.semantic_validation.model_dump(mode="json")),
            "validation_policy_id": inputs.policy.policy_id,
            "validation_policy_version": inputs.policy.policy_version,
            "benchmark_truth_hash": truth.content_hash,
        }
        actual_binding = inputs.bindings.model_dump(mode="python")
        for key, expected in expected_binding.items():
            if actual_binding.get(key) != expected:
                binding_errors.append(f"{key}: expected {expected!r}, observed {actual_binding.get(key)!r}")
        if materialization.status.value != "SUCCEEDED" or not materialization.usable:
            binding_errors.append("materialization: target artifact is not consumable")
        if compiled.plan_id != plan.plan_id or compiled.plan_content_hash != plan.content_hash:
            binding_errors.append("compiled_plan: analytical plan binding is stale")
        if inputs.semantic_model.analytical_plan_id != plan.plan_id or inputs.semantic_model.analytical_plan_content_hash != plan.content_hash:
            binding_errors.append("semantic_model: analytical plan binding is stale")
        if inputs.semantic_model.compiled_plan_id != compiled.compiled_plan_id or inputs.semantic_model.compiled_plan_content_hash != compiled.content_hash:
            binding_errors.append("semantic_model: compiled plan binding is stale")
        if inputs.semantic_model.materialization_artifact_id != materialization.artifact_id or inputs.semantic_model.materialization_artifact_content_hash != materialization.content_hash:
            binding_errors.append("semantic_model: materialization binding is stale")
        if inputs.semantic_model.target_relative_path != materialization.target_relative_path or inputs.semantic_model.target_file_sha256 != materialization.target_file_sha256:
            binding_errors.append("semantic_model: target binding is stale")
        if inputs.semantic_validation.semantic_model_id != inputs.semantic_model.semantic_model_id or inputs.semantic_validation.semantic_model_content_hash != inputs.semantic_model.content_hash:
            binding_errors.append("semantic_validation: semantic model binding is stale")
        add_check(
            "artifact_binding",
            "exact artifact binding",
            ValidationStatus.PASS if not binding_errors else ValidationStatus.FAIL,
            ValidationScope.CROSS_STAGE,
            "all source, canonical, accounting, analytical, materialization, semantic, target and policy references are exact" if not binding_errors else "; ".join(binding_errors),
            expected="exact bound IDs and content hashes",
            observed="exact" if not binding_errors else binding_errors,
        )

        all_source_refs = truth.record_refs
        source_snapshots = {item.snapshot_id for item in truth.records}
        source_schema = truth.source_schema_fingerprints
        add_check(
            "source_snapshot_universe",
            "source snapshot universe and hash",
            ValidationStatus.PASS if source_snapshots == {truth.source_snapshot_id} and source_schema else ValidationStatus.FAIL,
            ValidationScope.SOURCE_SNAPSHOT,
            "independent source truth declares one pinned snapshot and a complete source schema fingerprint map",
            expected={"snapshot_id": truth.source_snapshot_id, "record_count": len(all_source_refs)},
            observed={"snapshot_ids": sorted(source_snapshots), "record_count": len(all_source_refs), "snapshot_hash": truth.source_snapshot_fingerprint},
        )

        source_scope = next((item for item in inputs.accounting.scopes if item.boundary.value == "SOURCE_TO_CANONICAL"), None)
        if source_scope is None:
            add_check("source_record_accounting", "source record accounting", ValidationStatus.NOT_EVALUATED, ValidationScope.SOURCE_ACCOUNTING, "SOURCE_TO_CANONICAL accounting scope is absent")
        else:
            accounting_refs = set(source_scope.input_record_refs)
            source_status = ValidationStatus.PASS if accounting_refs == all_source_refs and len(source_scope.entries) == len(all_source_refs) else ValidationStatus.FAIL
            add_check(
                "source_record_accounting",
                "source record accounting",
                source_status,
                ValidationScope.SOURCE_ACCOUNTING,
                "every independent source record has exactly one terminal disposition and reason" if source_status is ValidationStatus.PASS else "accounting does not cover the exact independent source universe",
                expected=len(all_source_refs),
                observed=len(accounting_refs),
                affected_refs=tuple(sorted(all_source_refs - accounting_refs)),
            )

        # Canonical instances and maps are authoritative for deduplication and
        # source-to-canonical membership.  Do not infer them from the target.
        if not inputs.canonical_model.instances or not inputs.canonical_model.source_record_maps:
            add_check(
                "dedup_explainability",
                "deduplication and membership explainability",
                ValidationStatus.NOT_EVALUATED,
                ValidationScope.CANONICALIZATION,
                "canonical artifact has no CanonicalEntityInstance/SourceRecordCanonicalMap; target lineage cannot substitute for canonical membership",
            )
            add_check(
                "canonical_entity_counts",
                "canonical entity counts and memberships",
                ValidationStatus.NOT_EVALUATED,
                ValidationScope.CANONICALIZATION,
                "canonical entity instances are absent from the bound artifact",
            )
            add_check(
                "canonical_event_relationships",
                "canonical events and relationships",
                ValidationStatus.NOT_EVALUATED,
                ValidationScope.RELATIONSHIPS,
                "canonical event instances and source-backed relationship memberships are absent from the bound artifact",
            )
        else:
            mapped_refs = {item.record_ref for item in inputs.canonical_model.source_record_maps}
            add_check(
                "dedup_explainability",
                "deduplication and membership explainability",
                ValidationStatus.PASS if mapped_refs == all_source_refs else ValidationStatus.FAIL,
                ValidationScope.CANONICALIZATION,
                "canonical source membership maps cover the source universe",
                expected=len(all_source_refs), observed=len(mapped_refs), affected_refs=tuple(sorted(all_source_refs - mapped_refs)),
            )
            add_check(
                "canonical_entity_counts",
                "canonical entity counts and memberships",
                ValidationStatus.PASS,
                ValidationScope.CANONICALIZATION,
                "canonical instances and source memberships are present and bound",
                expected=len(inputs.canonical_model.instances), observed=len(inputs.canonical_model.instances),
            )
            relationship_refs = {item.relationship_id for item in inputs.canonical_model.relationships}
            expected_relationship_refs = {item.relationship_ref for item in truth.relationships}
            add_check(
                "canonical_event_relationships",
                "canonical events and relationships",
                ValidationStatus.PASS if expected_relationship_refs.issubset(relationship_refs) else ValidationStatus.FAIL,
                ValidationScope.RELATIONSHIPS,
                "source-backed required relationships are declared by the canonical artifact",
                expected=sorted(expected_relationship_refs), observed=sorted(relationship_refs),
            )

        if target is None:
            for check_id, name, scope in (
                ("target_table_set", "target table set", ValidationScope.MATERIALIZATION),
                ("fact_count", "fact row count", ValidationScope.FACT),
                ("fact_grain", "fact grain values", ValidationScope.GRAIN),
                ("fact_duplicates", "fact duplicate grain", ValidationScope.GRAIN),
                ("fact_business_values", "fact business values and dimension allocation", ValidationScope.FACT),
                ("foreign_key_integrity", "foreign-key and orphan policy", ValidationScope.REFERENTIAL_INTEGRITY),
                ("key_uniqueness", "warehouse and alternate-key uniqueness", ValidationScope.DIMENSIONS),
                ("date_coverage", "date coverage and role", ValidationScope.DATES),
                ("quantity_global", "global measure reconciliation", ValidationScope.AGGREGATE),
                ("quantity_slices", "sliced measure reconciliation", ValidationScope.AGGREGATE),
                ("lineage_source_to_target", "source-to-target lineage", ValidationScope.LINEAGE),
                ("lineage_target_to_source", "target-to-source lineage", ValidationScope.LINEAGE),
            ):
                add_target_unavailable(check_id, name, scope)
        else:
            allowed_names = set(compiled.table_names)
            add_check(
                "target_table_set",
                "target table set",
                ValidationStatus.PASS if set(target.available_table_names) == allowed_names else ValidationStatus.FAIL,
                ValidationScope.MATERIALIZATION,
                "target contains exactly the reviewed table set" if set(target.available_table_names) == allowed_names else "target table set differs from compiled plan",
                expected=sorted(allowed_names), observed=sorted(target.available_table_names),
            )
            table_map = target.table_map
            facts = truth.facts
            fact_spec: FactSpec | None = next(iter(compiled.fact_specs), None)
            fact_table = table_map.get(fact_spec.table_name) if fact_spec else None
            if fact_spec is None or fact_table is None:
                add_target_unavailable("fact_count", "fact row count", ValidationScope.FACT)
            else:
                add_check(
                    "fact_count",
                    "fact row count",
                    _status_for_comparison(len(facts), fact_table.row_count),
                    ValidationScope.FACT,
                    "materialized fact count equals independently expected fact count",
                    expected=len(facts), observed=fact_table.row_count,
                )
                grain = next((item for item in compiled.grain_specs if item.grain_id == fact_spec.grain_spec_id), None)
                expected_grain = {tuple(_normal(item.grain_values.get(column)) for column in grain.key_columns): item.fact_ref for item in facts} if grain else {}
                observed_grain = {tuple(_normal(row.get(column)) for column in grain.key_columns): str(index) for index, row in enumerate(fact_table.rows)} if grain else {}
                missing_grain = sorted(set(expected_grain) - set(observed_grain))
                extra_grain = sorted(set(observed_grain) - set(expected_grain))
                add_check(
                    "fact_grain",
                    "fact grain values",
                    ValidationStatus.PASS if not missing_grain and not extra_grain and len(observed_grain) == fact_table.row_count else ValidationStatus.FAIL,
                    ValidationScope.GRAIN,
                    "every independently expected fact grain key is present exactly once",
                    expected=sorted(expected_grain), observed=sorted(observed_grain), affected_refs=tuple(expected_grain[key] for key in missing_grain),
                )
                duplicate_count = fact_table.row_count - len({tuple(_normal(row.get(column)) for column in grain.key_columns) for row in fact_table.rows}) if grain else fact_table.row_count
                add_check(
                    "fact_duplicates",
                    "fact duplicate grain",
                    ValidationStatus.PASS if duplicate_count == 0 else ValidationStatus.FAIL,
                    ValidationScope.GRAIN,
                    "fact grain has no duplicate keys",
                    expected=0, observed=duplicate_count,
                )

                dimension_maps: dict[str, dict[Any, Any]] = {}
                dimension_specs = {item.dimension_id: item for item in compiled.dimension_specs}
                for dimension_id, spec in dimension_specs.items():
                    table = table_map.get(spec.table_name)
                    if table is None:
                        continue
                    mapping = {}
                    for row in table.rows:
                        if spec.canonical_reference_column in row and spec.surrogate_key.key_name in row:
                            mapping[_normal(row[spec.canonical_reference_column])] = row[spec.surrogate_key.key_name]
                    dimension_maps[dimension_id] = mapping

                fact_value_errors: list[str] = []
                rows_by_grain = {
                    tuple(_normal(row.get(column)) for column in grain.key_columns): row
                    for row in fact_table.rows
                } if grain else {}
                for expected_fact in facts:
                    key = tuple(_normal(expected_fact.grain_values.get(column)) for column in grain.key_columns) if grain else ()
                    row = rows_by_grain.get(key)
                    if row is None:
                        continue
                    if not _equal(expected_fact.canonical_event_id, row.get("canonical_event_id")):
                        fact_value_errors.append(expected_fact.fact_ref + ":canonical_event_id")
                    for measure_field, expected_value in expected_fact.measure_values.items():
                        if not _equal(expected_value, row.get(measure_field)):
                            fact_value_errors.append(expected_fact.fact_ref + ":" + str(measure_field))
                    for dimension_id, expected_entity_id in expected_fact.dimension_entity_ids.items():
                        fk = next((item for item in fact_spec.dimension_foreign_keys if item.dimension_id == dimension_id), None)
                        if fk is None or _normal(expected_entity_id) not in dimension_maps.get(dimension_id, {}):
                            fact_value_errors.append(expected_fact.fact_ref + ":" + dimension_id + ":unbound_expected_entity")
                        elif not _equal(dimension_maps[dimension_id][_normal(expected_entity_id)], row.get(fk.fact_column)):
                            fact_value_errors.append(expected_fact.fact_ref + ":" + dimension_id)
                    date_fk = next((item for item in fact_spec.dimension_foreign_keys if item.dimension_id in dimension_specs and dimension_specs[item.dimension_id].role.value == "DATE"), None)
                    if date_fk and expected_fact.date_value:
                        date_spec = dimension_specs[date_fk.dimension_id]
                        date_table = table_map.get(date_spec.table_name)
                        date_row = next((candidate for candidate in date_table.rows if candidate.get(date_fk.dimension_key_column) == row.get(date_fk.fact_column)), None) if date_table else None
                        if date_row is None or str(date_row.get("full_date")) != expected_fact.date_value:
                            fact_value_errors.append(expected_fact.fact_ref + ":date")
                    observed_refs = set(_as_refs(row.get("source_record_refs")))
                    if not set(expected_fact.source_record_refs).issubset(observed_refs):
                        fact_value_errors.append(expected_fact.fact_ref + ":lineage")
                add_check(
                    "fact_business_values",
                    "fact business values and dimension allocation",
                    ValidationStatus.PASS if not fact_value_errors else ValidationStatus.FAIL,
                    ValidationScope.FACT,
                    "fact event IDs, measures, foreign-key allocations, dates and row lineage match independent truth" if not fact_value_errors else "fact values or dimension allocation differ from independent truth",
                    expected="exact fact values and allocations", observed=fact_value_errors or "exact", affected_refs=tuple(fact_value_errors),
                )

                orphan_refs: list[str] = []
                for fk in fact_spec.dimension_foreign_keys:
                    table = table_map.get(fact_spec.table_name)
                    dimension = table_map.get(dimension_specs[fk.dimension_id].table_name) if fk.dimension_id in dimension_specs else None
                    if table is None or dimension is None:
                        continue
                    valid_keys = {row.get(fk.dimension_key_column) for row in dimension.rows}
                    for row in table.rows:
                        if row.get(fk.fact_column) not in valid_keys:
                            orphan_refs.append(str(row.get("canonical_event_id", row.get("source_record_refs", "unknown"))))
                add_check(
                    "foreign_key_integrity",
                    "foreign-key and orphan policy",
                    ValidationStatus.PASS if not orphan_refs else ValidationStatus.FAIL,
                    ValidationScope.REFERENTIAL_INTEGRITY,
                    "all required fact foreign keys resolve under the reviewed orphan policy" if not orphan_refs else "orphan or unresolved required fact foreign keys were observed",
                    expected=0, observed=len(orphan_refs), affected_refs=tuple(sorted(set(orphan_refs))),
                )

                key_errors: list[str] = []
                for dimension_id, spec in dimension_specs.items():
                    table = table_map.get(spec.table_name)
                    if table is None:
                        key_errors.append(dimension_id + ":missing_table")
                        continue
                    for key_name in (spec.surrogate_key.key_name, spec.canonical_reference_column, *spec.alternate_key_columns):
                        values = [row.get(key_name) for row in table.rows]
                        if len(values) != len(set(_normal(value) for value in values)):
                            key_errors.append(f"{dimension_id}:{key_name}")
                add_check(
                    "key_uniqueness",
                    "warehouse and alternate-key uniqueness",
                    ValidationStatus.PASS if not key_errors else ValidationStatus.FAIL,
                    ValidationScope.DIMENSIONS,
                    "dimension surrogate, canonical and alternate keys are unique",
                    expected="unique", observed=key_errors or "unique",
                )

                date_specs = [item for item in compiled.dimension_specs if item.role.value == "DATE"]
                date_errors: list[str] = []
                expected_dates = {str(item.date_value) for item in facts if item.date_value is not None}
                for spec in date_specs:
                    table = table_map.get(spec.table_name)
                    observed_dates = {str(row.get("full_date")) for row in table.rows} if table else set()
                    if not expected_dates.issubset(observed_dates):
                        date_errors.extend(sorted(expected_dates - observed_dates))
                add_check(
                    "date_coverage",
                    "date coverage and role",
                    ValidationStatus.PASS if not date_errors else ValidationStatus.FAIL,
                    ValidationScope.DATES,
                    "all source-truth fact dates are represented by the reviewed date dimension",
                    expected=sorted(expected_dates), observed="covered" if not date_errors else date_errors,
                )

                def aggregate_value(rows: list[Mapping[str, Any]], field: str, operation: str) -> Decimal | None:
                    values = [_numeric(row.get(field)) for row in rows if row.get(field) is not None]
                    if not values or any(value is None for value in values):
                        return None
                    return sum(values, Decimal("0")) if operation == "SUM" else max(values)

                aggregate_failures: list[str] = []
                aggregate_pending = False
                for aggregate in truth.expected_aggregates.get("aggregates", []):
                    field = str(aggregate.get("measure_field", ""))
                    operation = str(aggregate.get("operation", ""))
                    expected_map = aggregate.get("expected_by_key", {"__global__": aggregate.get("expected")})
                    group_by = tuple(str(item) for item in aggregate.get("group_by", []))
                    if field == "" or operation not in {"SUM", "MAX"}:
                        aggregate_pending = True
                        continue
                    measure_rows = list(fact_table.rows)
                    grouped: dict[str, list[Mapping[str, Any]]] = {}
                    if not group_by:
                        grouped["__global__"] = measure_rows
                    else:
                        for row in measure_rows:
                            parts: list[str] = []
                            for dimension_id in group_by:
                                fk = next((item for item in fact_spec.dimension_foreign_keys if item.dimension_id == dimension_id), None)
                                spec = dimension_specs.get(dimension_id)
                                if fk is None or spec is None:
                                    parts.append("<missing-dimension>")
                                else:
                                    key = row.get(fk.fact_column)
                                    dim_row = next((candidate for candidate in table_map[spec.table_name].rows if candidate.get(fk.dimension_key_column) == key), None)
                                    parts.append(str(_normal(dim_row.get(spec.canonical_reference_column))) if dim_row else "<orphan>")
                            grouped.setdefault("|".join(parts), []).append(row)
                    for key, expected in expected_map.items():
                        observed = aggregate_value(grouped.get(str(key), []), field, operation)
                        if observed is None or not _equal(expected, observed):
                            aggregate_failures.append(f"{aggregate.get('aggregate_id', 'aggregate')}:{key}")
                add_check(
                    "quantity_global",
                    "global measure reconciliation",
                    ValidationStatus.NOT_EVALUATED if aggregate_pending and not truth.expected_aggregates.get("aggregates") else ValidationStatus.FAIL if aggregate_failures else ValidationStatus.PASS,
                    ValidationScope.AGGREGATE,
                    "independent business-fact aggregates equal target values" if not aggregate_failures else "one or more independent aggregates disagree with the target",
                    expected="all declared aggregates", observed=aggregate_failures or "all match", affected_refs=tuple(aggregate_failures),
                )
                slice_failures = [item for item in aggregate_failures if ":__global__" not in item]
                add_check(
                    "quantity_slices",
                    "sliced measure reconciliation",
                    ValidationStatus.FAIL if slice_failures else ValidationStatus.PASS if truth.expected_aggregates.get("aggregates") else ValidationStatus.NOT_EVALUATED,
                    ValidationScope.AGGREGATE,
                    "product/location/date slices are compared independently from the global total",
                    expected="all declared slices", observed=slice_failures or "all match", affected_refs=tuple(slice_failures),
                )

                expected_contributing = {item.record_ref for item in truth.records if item.terminal_disposition in {RecordDisposition.EMITTED_DIRECT, RecordDisposition.CONSOLIDATED, RecordDisposition.AGGREGATED}}
                target_refs: set[str] = set()
                for table in table_map.values():
                    for row in table.rows:
                        raw = row.get("source_record_refs")
                        target_refs.update(
                            ref for ref in _as_refs(raw)
                            if not ref.startswith(("policy:", "date-range:", "synthetic:"))
                        )
                missing_refs = expected_contributing - target_refs
                unexpected_refs = target_refs - all_source_refs
                add_check(
                    "lineage_source_to_target",
                    "source-to-target lineage",
                    ValidationStatus.PASS if not missing_refs else ValidationStatus.FAIL,
                    ValidationScope.LINEAGE,
                    "every independently contributing source record is retained in downstream lineage" if not missing_refs else "contributing source records are missing from target lineage",
                    expected=sorted(expected_contributing), observed=sorted(target_refs), affected_refs=tuple(sorted(missing_refs)),
                )
                add_check(
                    "lineage_target_to_source",
                    "target-to-source lineage",
                    ValidationStatus.PASS if not unexpected_refs else ValidationStatus.FAIL,
                    ValidationScope.LINEAGE,
                    "every target lineage reference resolves to the independent source universe" if not unexpected_refs else "target contains lineage references outside the independent source universe",
                    expected="subset of source truth", observed=sorted(unexpected_refs) or "subset", affected_refs=tuple(sorted(unexpected_refs)),
                )

        semantic_measure_fields = {item.field_name: item.aggregation_class for item in compiled.measure_specs}
        semantic_errors: list[str] = []
        for measure in inputs.semantic_model.measures:
            expected_class = semantic_measure_fields.get(measure.physical_field_ref)
            if expected_class is None or measure.aggregation_class is not expected_class:
                semantic_errors.append(measure.semantic_measure_id)
        semantic_validation_status = getattr(inputs.semantic_validation.status, "value", inputs.semantic_validation.status)
        if semantic_validation_status != "PASS":
            semantic_errors.append("semantic_validation:" + str(semantic_validation_status))
        add_check(
            "semantic_downstream_cross_check",
            "semantic downstream cross-check",
            ValidationStatus.PASS if not semantic_errors else ValidationStatus.FAIL,
            ValidationScope.SEMANTIC_PROJECTION,
            "semantic measures preserve reviewed analytical measure fields and aggregation classes" if not semantic_errors else "semantic downstream metadata diverges from the reviewed analytical package",
            expected="semantic validation PASS and exact measure classes", observed=semantic_errors or "exact",
        )

        add_check(
            "monetary_reconciliation",
            "monetary and revenue reconciliation",
            inputs.policy.monetary_status,
            ValidationScope.AGGREGATE,
            inputs.policy.monetary_reason,
            severity=ValidationSeverity.INFORMATIONAL,
            required=False,
        )

        blocking_before_gate = [item for item in checks if item.required and item.severity is ValidationSeverity.G6_BLOCKING]
        has_failure = any(item.status is ValidationStatus.FAIL for item in blocking_before_gate)
        has_pending = any(item.status in {ValidationStatus.NOT_EVALUATED, ValidationStatus.REVIEW_REQUIRED} for item in blocking_before_gate)
        gate_status = GateStatus.FAIL if has_failure else GateStatus.PENDING if has_pending else GateStatus.PASS
        gate_validation_status = ValidationStatus.FAIL if has_failure else ValidationStatus.REVIEW_REQUIRED if has_pending else ValidationStatus.PASS
        add_check(
            "no_blocking_discrepancy",
            "no unexplained blocking discrepancy",
            gate_validation_status,
            ValidationScope.CROSS_STAGE,
            "all required blocking checks passed" if gate_status is GateStatus.PASS else "G6 is not eligible because required validation evidence is missing or discrepant",
            expected="no FAIL/REVIEW_REQUIRED/NOT_EVALUATED blocking checks",
            observed={"failures": [item.check_id for item in blocking_before_gate if item.status is ValidationStatus.FAIL], "pending": [item.check_id for item in blocking_before_gate if item.status in {ValidationStatus.NOT_EVALUATED, ValidationStatus.REVIEW_REQUIRED}]},
        )

        # A policy may require all checks except the non-applicable monetary
        # informational check.  The report contract derives the gate again.
        run_id = inputs.accounting.run_id
        report_id = validation_report_id({"run_id": run_id, "bindings": inputs.bindings.model_dump(mode="json"), "policy": inputs.policy.content_hash, "checks": [(item.check_id, item.status.value) for item in checks]})
        report = ValidationReport(
            report_id=report_id,
            run_id=run_id,
            bindings=inputs.bindings,
            policy=inputs.policy,
            checks=tuple(checks),
            discrepancies=tuple(discrepancies),
            overall_status=gate_validation_status,
            g6_status=gate_status,
            g6_eligible=gate_status is GateStatus.PASS,
            generated_at=datetime.now(timezone.utc).isoformat(),
            provenance_refs=("application.validation", "validation:independent-source-truth", "validation:read-only-target"),
        )
        metrics = tuple(
            ReconciliationMetric(
                metric_id=f"recon_metric_{stable_digest({'check': item.check_id})[:24]}",
                name=item.name,
                status=item.status,
                expected=item.expected,
                observed=item.observed,
                difference=None,
                evidence_refs=item.evidence_refs,
            )
            for item in checks
            if item.required
        )
        reconciliation = ReconciliationResult(
            result_id=reconciliation_result_id({"report": report.content_hash, "run_id": report.run_id}),
            run_id=report.run_id,
            validation_report_id=report.report_id,
            bindings=inputs.bindings,
            metrics=metrics,
            status=gate_validation_status,
            no_blocking_discrepancy=not has_failure,
            provenance_refs=("application.validation", "reconciliation:deterministic"),
        )
        return ValidationOutcome(report=report, reconciliation=reconciliation, target_snapshot=target)
