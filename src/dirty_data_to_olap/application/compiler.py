"""Deterministic compiler for an accepted, bounded analytical V1 plan."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalCell,
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalPlan,
    AnalyticalReviewState,
    CompiledOperation,
    CompiledPlan,
    DimensionRole,
    DimensionSpec,
    FactSpec,
    GeneratedSQL,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    QuarantineRecord,
    TargetConfig,
    UnknownMemberPolicy,
    as_analytical_dataset,
    compiled_plan_id,
    deterministic_warehouse_key,
)
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, ArtifactRef
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id


class AnalyticalCompilationError(ValueError):
    """The approved analytical plan cannot be translated safely."""


@dataclass(frozen=True)
class CompilationOutputRefs:
    """Registered immutable outputs of one COMPILATION attempt."""

    compiled_plan: ArtifactRef
    generated_sql: ArtifactRef
    target_config: ArtifactRef


class CompilationArtifactPublisher:
    """Publish the exact compiler inputs and outputs used by materialization."""

    def __init__(self, artifact_store: ArtifactStorePort, control_store: ControlStorePort) -> None:
        self.artifact_store = artifact_store
        self.control_store = control_store

    def publish(
        self,
        *,
        run_id: str,
        attempt_id: str,
        compiled_plan: CompiledPlan,
        generated_sql: GeneratedSQL,
        target_config: TargetConfig,
    ) -> CompilationOutputRefs:
        if compiled_plan.generated_sql_id != generated_sql.generated_sql_id or compiled_plan.generated_sql_hash != generated_sql.sql_hash:
            raise AnalyticalCompilationError("compiler outputs are not bound to the same GeneratedSQL")
        if compiled_plan.target_config_fingerprint != target_config.config_fingerprint:
            raise AnalyticalCompilationError("compiler output is not bound to the exact TargetConfig")
        values = (
            (compiled_plan.compiled_plan_id, "CompiledPlan", compiled_plan, (generated_sql.generated_sql_id,)),
            (generated_sql.generated_sql_id, "GeneratedSQL", generated_sql, (compiled_plan.compiled_plan_id,)),
            (stable_id("target-config", {"run_id": run_id, "attempt_id": attempt_id, "compiled_plan_id": compiled_plan.compiled_plan_id, "config": target_config.config_fingerprint}), "TargetConfig", target_config, (compiled_plan.compiled_plan_id, generated_sql.generated_sql_id)),
        )
        refs: list[ArtifactRef] = []
        for artifact_id, artifact_kind, value, provenance in values:
            manifest = ArtifactManifest(
                artifact_id=artifact_id,
                run_id=run_id,
                stage_id="COMPILATION",
                attempt_id=attempt_id,
                artifact_kind=artifact_kind,
                media_type="application/json",
                producer="application.compiler",
                logical_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
                provenance_refs=tuple(str(item) for item in provenance),
            )
            ref = self.artifact_store.publish(manifest, value.model_dump_json().encode("utf-8"))
            refs.append(self.control_store.register_artifact(ref))
        return CompilationOutputRefs(compiled_plan=refs[0], generated_sql=refs[1], target_config=refs[2])


_LOGICAL_TYPES = {
    "STRING": "VARCHAR",
    "STRING_LIST": "VARCHAR",
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
    "DECIMAL": "DECIMAL(38, 9)",
    "FLOAT": "DOUBLE",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
}


def _identifier(value: str) -> str:
    if not value or not value[0].islower() or any(not (char.islower() or char.isdigit() or char == "_") for char in value):
        raise AnalyticalCompilationError(f"unsafe SQL identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def _sql_type(logical_type: str) -> str:
    try:
        return _LOGICAL_TYPES[logical_type.upper()]
    except KeyError as exc:
        raise AnalyticalCompilationError(f"unsupported logical type: {logical_type}") from exc


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, date):
        return "DATE " + _literal(value.isoformat())
    if isinstance(value, (tuple, list)):
        return _literal(json.dumps(list(value), ensure_ascii=False, separators=(",", ":")))
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def _insert_sql(table_name: str, values: tuple[object, ...], *, literal_sql: bool) -> str:
    payload = ", ".join(_literal(value) for value in values) if literal_sql else ", ".join("?" for _ in values)
    return f"INSERT INTO {_identifier(table_name)} VALUES ({payload});"


def _as_tuple(value: object) -> tuple:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


class AnalyticalCompilerService:
    COMPILER_VERSION = "duckdb-compiler-v1"

    def compile(
        self,
        plan: AnalyticalPlan,
        dimensions: tuple[DimensionSpec, ...],
        facts: FactSpec | tuple[FactSpec, ...],
        grains: GrainSpec | tuple[GrainSpec, ...],
        measures: tuple[MeasureSpec, ...],
        binding: AnalyticalInputBinding,
        input_data: AnalyticalInputDataset,
        target_config: TargetConfig,
        analytical_review,
        *,
        reviewed_at: datetime | None = None,
    ) -> tuple[CompiledPlan, GeneratedSQL]:
        dataset = as_analytical_dataset(input_data)
        fact_specs = (facts,) if isinstance(facts, FactSpec) else tuple(facts)
        grain_specs = (grains,) if isinstance(grains, GrainSpec) else tuple(grains)
        self._validate_plan_and_review(plan, dimensions, fact_specs, grain_specs, measures, binding, dataset, analytical_review)
        self._validate_dataset_binding(binding, dataset)
        sql = self._generate_sql(dimensions, fact_specs, grain_specs, measures, dataset)
        generated_id = stable_id("sql", {"plan_id": plan.plan_id, "plan_hash": plan.content_hash, "sql_hash": sql.sql_hash})
        sql = sql.model_copy(update={
            "generated_sql_id": generated_id,
            "plan_id": plan.plan_id,
            "plan_content_hash": plan.content_hash,
        })
        operation_payload = {
            "plan": plan.plan_id,
            "plan_hash": plan.content_hash,
            "spec_package": plan.analytical_spec_package_hash,
            "sql_hash": sql.sql_hash,
            "target": target_config.config_fingerprint,
            "compiler": self.COMPILER_VERSION,
        }
        operations = (
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "create_schema"}), operation_name="create_schema", statement_kind="DDL", sql_hash=stable_digest(sql.create_schema_sql)),
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "load_date"}), operation_name="load_date", dependencies=("create_schema",), statement_kind="DML", sql_hash=stable_digest(sql.load_date_sql)),
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "load_dimensions"}), operation_name="load_dimensions", dependencies=("create_schema", "load_date"), statement_kind="DML", sql_hash=stable_digest(sql.load_dimensions_sql)),
            CompiledOperation(operation_id=stable_id("op", {**operation_payload, "name": "load_facts"}), operation_name="load_facts", dependencies=("create_schema", "load_dimensions"), statement_kind="DML", sql_hash=stable_digest(sql.load_facts_sql)),
        )
        compiled_payload = {
            **operation_payload,
            "generated_sql_id": sql.generated_sql_id,
            "operations": [item.model_dump(mode="json") for item in operations],
            "binding": binding.content_hash,
            "tables": sorted({item.table_name for item in dimensions} | {item.table_name for item in fact_specs}),
        }
        compiled = CompiledPlan(
            compiled_plan_id=compiled_plan_id(compiled_payload),
            plan_id=plan.plan_id,
            plan_content_hash=plan.content_hash,
            compiler_version=self.COMPILER_VERSION,
            operations=operations,
            generated_sql_id=sql.generated_sql_id,
            generated_sql_hash=sql.sql_hash,
            target_config_fingerprint=target_config.config_fingerprint,
            input_binding_id=binding.binding_id,
            input_binding_content_hash=binding.content_hash,
            canonical_model_id=plan.canonical_model_id,
            canonical_model_content_hash=plan.canonical_model_content_hash,
            analytical_spec_package_hash=plan.analytical_spec_package_hash,
            table_names=tuple(sorted({item.table_name for item in dimensions} | {item.table_name for item in fact_specs})),
            dimension_specs=dimensions,
            fact_specs=fact_specs,
            grain_specs=grain_specs,
            measure_specs=measures,
            domain_assertion_refs=tuple(sorted(plan.domain_assertion_refs)),
            provenance_refs=("compiler:project-owned-duckdb-v1", "review:analytical-plan", "lineage:typed-input-dataset"),
            created_at=reviewed_at or datetime.now(timezone.utc),
        )
        return compiled, sql

    @staticmethod
    def _validate_dataset_binding(binding: AnalyticalInputBinding, dataset: AnalyticalInputDataset) -> None:
        if binding.dataset_id is not None:
            if binding.dataset_id != dataset.dataset_id or binding.dataset_content_hash != dataset.content_hash:
                raise AnalyticalCompilationError("input dataset is stale")
        elif binding.fixture_id != dataset.dataset_id:
            raise AnalyticalCompilationError("input dataset does not match the binding")
        if dataset.canonical_model_id != binding.canonical_model_id or dataset.canonical_model_content_hash != binding.canonical_model_content_hash:
            raise AnalyticalCompilationError("input dataset is not bound to the planned canonical model")

    @staticmethod
    def _validate_plan_and_review(
        plan: AnalyticalPlan,
        dimensions: tuple[DimensionSpec, ...],
        facts: tuple[FactSpec, ...],
        grains: tuple[GrainSpec, ...],
        measures: tuple[MeasureSpec, ...],
        binding: AnalyticalInputBinding,
        dataset: AnalyticalInputDataset,
        analytical_review,
    ) -> None:
        if plan.review_state is not AnalyticalReviewState.REVIEW_REQUIRED:
            raise AnalyticalCompilationError("compiler accepts only a review-required plan with a separate compatible review")
        try:
            ReviewPolicyService().require_compatible(analytical_review, ReviewPolicyService().analytical_plan_context(plan))
        except ReviewCompatibilityError as exc:
            raise AnalyticalCompilationError("REVIEW_ANALYTICAL_PLAN_INCOMPATIBLE:" + ",".join(exc.errors)) from exc
        if plan.unresolved_items:
            raise AnalyticalCompilationError("UNRESOLVED_ANALYTICAL_PLAN:" + ",".join(plan.unresolved_items))
        if binding.binding_id != plan.input_binding_id or binding.content_hash != plan.input_binding_content_hash:
            raise AnalyticalCompilationError("input binding is stale")
        if dataset.canonical_model_id != plan.canonical_model_id or dataset.canonical_model_content_hash != plan.canonical_model_content_hash:
            raise AnalyticalCompilationError("input dataset is not bound to the planned canonical model")

        expected = {
            "dimension": dict(plan.dimension_spec_content_hashes),
            "fact": dict(plan.fact_spec_content_hashes),
            "grain": dict(plan.grain_spec_content_hashes),
            "measure": dict(plan.measure_spec_content_hashes),
        }
        actual = {
            "dimension": {item.dimension_id: item.semantic_content_hash for item in dimensions},
            "fact": {item.fact_id: item.semantic_content_hash for item in facts},
            "grain": {item.grain_id: item.semantic_content_hash for item in grains},
            "measure": {item.measure_id: item.semantic_content_hash for item in measures},
        }
        stale_parts = [label + ":" + ",".join(sorted(set(expected[label]) | set(actual[label]))) for label in expected if not expected[label] or expected[label] != actual[label]]
        if stale_parts:
            raise AnalyticalCompilationError("REVIEW_ANALYTICAL_PLAN_SPEC_PACKAGE_STALE:" + ";".join(stale_parts))
        if plan.analytical_spec_package_hash != stable_digest(expected):
            raise AnalyticalCompilationError("REVIEW_ANALYTICAL_PLAN_SPEC_PACKAGE_INVALID")
        if set(actual["fact"]) != set(plan.materialized_fact_ids) or set(actual["dimension"]) != set(plan.materialized_dimension_ids):
            raise AnalyticalCompilationError("analytical specifications do not match the reviewed plan IDs")
        if set(actual["grain"]) != set(plan.grain_spec_ids) or set(actual["measure"]) != set(plan.measure_spec_ids):
            raise AnalyticalCompilationError("analytical specifications do not match the reviewed plan IDs")
        if len({item.dimension_id for item in dimensions}) != len(dimensions) or len({item.fact_id for item in facts}) != len(facts):
            raise AnalyticalCompilationError("analytical table IDs must be unique")
        if len({item.grain_id for item in grains}) != len(grains) or len({item.measure_id for item in measures}) != len(measures):
            raise AnalyticalCompilationError("analytical child IDs must be unique")
        dimension_map = {item.dimension_id: item for item in dimensions}
        grain_map = {item.grain_id: item for item in grains}
        measure_map = {item.measure_id: item for item in measures}
        fact_ids = {item.fact_id for item in facts}
        for fact in facts:
            grain = grain_map.get(fact.grain_spec_id)
            if grain is None or grain.fact_id != fact.fact_id or not grain.validated:
                raise AnalyticalCompilationError("fact implementation requires the exact validated GrainSpec")
            if any(measure_map.get(measure_id) is None or measure_map[measure_id].fact_id != fact.fact_id for measure_id in fact.measure_ids):
                raise AnalyticalCompilationError("fact measure references are incomplete")
            for foreign_key in fact.dimension_foreign_keys:
                dimension = dimension_map.get(foreign_key.dimension_id)
                if dimension is None:
                    raise AnalyticalCompilationError("fact references an unplanned dimension")
                if foreign_key.required is False and dimension.unknown_member_policy.policy is not UnknownMemberPolicy.NULLABLE_FK:
                    raise AnalyticalCompilationError("nullable FK requires the reviewed NULLABLE_FK policy")
            if fact.fact_id not in fact_ids:
                raise AnalyticalCompilationError("fact ID is not reviewed")

    @staticmethod
    def _row_reference(row: AnalyticalInputRow, dimension: DimensionSpec) -> str:
        if dimension.canonical_reference_column in {"canonical_entity_id", "canonical_reference"}:
            return row.canonical_reference
        try:
            value = row.value_for(dimension.canonical_reference_column)
        except KeyError as exc:
            raise AnalyticalCompilationError(f"dimension canonical reference column is missing: {exc.args[0]}") from exc
        if value is None or not str(value):
            raise AnalyticalCompilationError(f"dimension canonical reference is null: {dimension.dimension_id}")
        return str(value)

    @staticmethod
    def _dimension_rows(
        dimensions: tuple[DimensionSpec, ...],
        dataset: AnalyticalInputDataset,
    ) -> tuple[dict[str, list[tuple[object, ...]]], dict[str, dict[str, int]], dict[str, dict[str, object]]]:
        rows_by_table: dict[str, list[tuple[object, ...]]] = {}
        keys_by_dimension: dict[str, dict[str, int]] = {}
        unknown_rows: dict[str, dict[str, object]] = {}
        for dimension in dimensions:
            if dimension.role is DimensionRole.DATE:
                continue
            if not dimension.input_table_id:
                raise AnalyticalCompilationError(f"dimension input table is missing: {dimension.dimension_id}")
            table = dataset.table(dimension.input_table_id)
            ref_keys: dict[str, int] = {}
            output: list[tuple[object, ...]] = []
            for row in sorted(table.rows, key=lambda item: item.canonical_reference):
                reference = AnalyticalCompilerService._row_reference(row, dimension)
                if reference in ref_keys:
                    raise AnalyticalCompilationError(f"duplicate dimension canonical reference: {dimension.dimension_id}")
                warehouse_key = deterministic_warehouse_key(dimension.surrogate_key.namespace, reference)
                if warehouse_key in ref_keys.values():
                    raise AnalyticalCompilationError(f"WAREHOUSE_KEY_COLLISION:{dimension.dimension_id}")
                ref_keys[reference] = warehouse_key
                values: list[object] = [warehouse_key, reference]
                target_names = {"canonical_entity_id"}
                for attribute in dimension.attributes:
                    if attribute.column_name in target_names:
                        raise AnalyticalCompilationError(f"duplicate dimension target column: {attribute.column_name}")
                    source_column = attribute.input_column_name or attribute.column_name
                    try:
                        value = row.value_for(source_column)
                    except KeyError as exc:
                        raise AnalyticalCompilationError(f"dimension input column is missing: {source_column}") from exc
                    if value is None and not attribute.nullable:
                        raise AnalyticalCompilationError(f"non-null dimension attribute is null: {dimension.dimension_id}.{attribute.column_name}")
                    values.append(value)
                    target_names.add(attribute.column_name)
                if "source_record_refs" not in target_names:
                    values.append(row.source_record_refs)
                output.append(tuple(values))
            if dimension.unknown_member_policy.policy is UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER:
                unknown_key = dimension.unknown_member_policy.unknown_member_key
                if unknown_key is None or unknown_key in ref_keys.values():
                    raise AnalyticalCompilationError(f"unknown member key collides for dimension: {dimension.dimension_id}")
                unknown_ref = f"__UNKNOWN__:{dimension.dimension_id}"
                unknown_values: list[object] = [unknown_key, unknown_ref]
                target_names = {"canonical_entity_id"}
                for attribute in dimension.attributes:
                    if attribute.column_name in target_names:
                        raise AnalyticalCompilationError(f"duplicate dimension target column: {attribute.column_name}")
                    unknown_values.append(_unknown_value(attribute.logical_type))
                    target_names.add(attribute.column_name)
                if "source_record_refs" not in target_names:
                    unknown_values.append((f"policy:explicit-unknown-member:{dimension.dimension_id}",))
                output.append(tuple(unknown_values))
                ref_keys[unknown_ref] = unknown_key
                unknown_rows[dimension.dimension_id] = {"reference": unknown_ref, "key": unknown_key}
            rows_by_table[dimension.table_name] = output
            keys_by_dimension[dimension.dimension_id] = ref_keys
        return rows_by_table, keys_by_dimension, unknown_rows

    def _generate_sql(
        self,
        dimensions: tuple[DimensionSpec, ...],
        facts: tuple[FactSpec, ...],
        grains: tuple[GrainSpec, ...],
        measures: tuple[MeasureSpec, ...],
        dataset: AnalyticalInputDataset,
    ) -> GeneratedSQL:
        grain_map = {item.grain_id: item for item in grains}
        measure_map = {item.measure_id: item for item in measures}
        dimension_map = {item.dimension_id: item for item in dimensions}
        dimension_rows, dimension_keys, _ = self._dimension_rows(dimensions, dataset)
        create_statements: list[str] = []
        date_load: list[str] = []
        dimension_load: list[str] = []
        fact_load: list[str] = []
        quarantine: list[QuarantineRecord] = []
        date_values: dict[str, set[date]] = {item.dimension_id: set() for item in dimensions if item.role is DimensionRole.DATE}

        for dimension in sorted(dimensions, key=lambda item: item.table_name):
            columns: list[str] = [f"{_identifier(dimension.surrogate_key.key_name)} BIGINT PRIMARY KEY", '"canonical_entity_id" VARCHAR NOT NULL UNIQUE']
            target_names = {"canonical_entity_id", dimension.surrogate_key.key_name}
            for attribute in dimension.attributes:
                if attribute.column_name in target_names:
                    raise AnalyticalCompilationError(f"duplicate dimension target column: {attribute.column_name}")
                nullability = "" if attribute.nullable else " NOT NULL"
                columns.append(f"{_identifier(attribute.column_name)} {_sql_type(attribute.logical_type)}{nullability}")
                target_names.add(attribute.column_name)
            if "source_record_refs" not in target_names:
                columns.append('"source_record_refs" VARCHAR NOT NULL')
            for attribute in dimension.attributes:
                _sql_type(attribute.logical_type)
            create_statements.append(f"CREATE TABLE {_identifier(dimension.table_name)} (\n  " + ",\n  ".join(columns) + "\n);")
            if dimension.role is not DimensionRole.DATE:
                for values in dimension_rows.get(dimension.table_name, []):
                    dimension_load.append(_insert_sql(dimension.table_name, tuple(values), literal_sql=dataset.allow_literal_sql))

        for fact in sorted(facts, key=lambda item: item.table_name):
            table = dataset.table(fact.input_table_id)
            grain = grain_map[fact.grain_spec_id]
            columns: list[str] = []
            target_names: set[str] = set()
            input_types = {item.column_name: item.logical_type for item in table.columns}
            for column in grain.key_columns:
                columns.append(f"{_identifier(column)} {_sql_type(input_types[column])} NOT NULL")
                target_names.add(column)
            columns.append('"canonical_event_id" VARCHAR NOT NULL')
            target_names.add("canonical_event_id")
            for foreign_key in fact.dimension_foreign_keys:
                if foreign_key.fact_column in target_names:
                    raise AnalyticalCompilationError(f"duplicate fact target column: {foreign_key.fact_column}")
                dimension = dimension_map[foreign_key.dimension_id]
                nullable = "" if foreign_key.required else " NULL"
                columns.append(f"{_identifier(foreign_key.fact_column)} BIGINT{nullable} REFERENCES {_identifier(dimension.table_name)} ({_identifier(dimension.surrogate_key.key_name)})")
                target_names.add(foreign_key.fact_column)
            for column in fact.degenerate_dimension_columns:
                if column in target_names:
                    if column in grain.key_columns:
                        continue
                    raise AnalyticalCompilationError(f"duplicate fact target column: {column}")
                columns.append(f"{_identifier(column)} {_sql_type(input_types[column])} NOT NULL")
                target_names.add(column)
            fact_measures = [measure_map[measure_id] for measure_id in fact.measure_ids]
            for measure in fact_measures:
                if measure.field_name in target_names:
                    raise AnalyticalCompilationError(f"duplicate fact target column: {measure.field_name}")
                _sql_type(measure.logical_type)
                nullability = "" if not measure.nullable else " NULL"
                columns.append(f"{_identifier(measure.field_name)} {_sql_type(measure.logical_type)}{nullability}")
                target_names.add(measure.field_name)
            if "source_record_refs" not in target_names:
                columns.append('"source_record_refs" VARCHAR NOT NULL')
            columns.append("PRIMARY KEY (" + ", ".join(_identifier(item) for item in grain.key_columns) + ")")
            create_statements.append(f"CREATE TABLE {_identifier(fact.table_name)} (\n  " + ",\n  ".join(columns) + "\n);")

            dimension_lookup = {item.dimension_id: dimension_keys.get(item.dimension_id, {}) for item in dimensions}
            rows = table.rows
            for row in sorted(rows, key=lambda item: item.row_ref):
                row_values: list[object] = []
                fact_lineage = list(row.source_record_refs)
                try:
                    for column in grain.key_columns:
                        row_values.append(row.value_for(column))
                except KeyError as exc:
                    raise AnalyticalCompilationError(f"fact grain column is missing: {exc.args[0]}") from exc
                if any(value is None for value in row_values):
                    if grain.null_policy is GrainNullPolicy.REJECT_NULLS:
                        raise AnalyticalCompilationError("fact grain contains a null key")
                    quarantine.append(QuarantineRecord(fact_id=fact.fact_id, fact_table_name=fact.table_name, row_ref=row.row_ref, source_record_refs=row.source_record_refs, reason="NULL_GRAIN_KEY", policy=UnknownMemberPolicy.QUARANTINE_FACT, provenance_refs=row.lineage_refs))
                    continue
                row_values.append(row.canonical_reference)
                skip_row = False
                fk_values: list[object] = []
                for foreign_key in fact.dimension_foreign_keys:
                    source_column = foreign_key.input_reference_column or foreign_key.fact_column
                    try:
                        reference_value = row.value_for(source_column)
                    except KeyError as exc:
                        raise AnalyticalCompilationError(f"fact reference column is missing: {exc.args[0]}") from exc
                    dimension = dimension_map[foreign_key.dimension_id]
                    if dimension.role is DimensionRole.DATE:
                        current = _coerce_date(reference_value)
                        if current is not None:
                            date_values.setdefault(dimension.dimension_id, set()).add(current)
                        lookup_reference = current.isoformat() if current is not None else None
                        warehouse_key = (
                            deterministic_warehouse_key(dimension.surrogate_key.namespace, lookup_reference)
                            if lookup_reference is not None else None
                        )
                    else:
                        lookup_reference = None if reference_value is None else str(reference_value)
                        lookup = dimension_lookup[foreign_key.dimension_id]
                        warehouse_key = lookup.get(lookup_reference) if lookup_reference is not None else None
                    if warehouse_key is None:
                        policy = dimension.unknown_member_policy.policy
                        if policy is UnknownMemberPolicy.QUARANTINE_FACT:
                            quarantine.append(QuarantineRecord(fact_id=fact.fact_id, fact_table_name=fact.table_name, row_ref=row.row_ref, source_record_refs=row.source_record_refs, reason="MISSING_DIMENSION_REFERENCE", missing_dimension_id=dimension.dimension_id, missing_reference=lookup_reference, policy=policy, provenance_refs=row.lineage_refs))
                            skip_row = True
                            break
                        if policy is UnknownMemberPolicy.NULLABLE_FK:
                            if foreign_key.required:
                                raise AnalyticalCompilationError("required relationship cannot use NULLABLE_FK")
                            warehouse_key = None
                        elif policy is UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER:
                            unknown = lookup.get(f"__UNKNOWN__:{dimension.dimension_id}")
                            if unknown is None:
                                raise AnalyticalCompilationError("explicit unknown member is not materialized")
                            warehouse_key = unknown
                            fact_lineage.append(f"policy:explicit-unknown-member:{dimension.dimension_id}")
                    fk_values.append(warehouse_key)
                if skip_row:
                    continue
                row_values.extend(fk_values)
                for column in fact.degenerate_dimension_columns:
                    if column in grain.key_columns:
                        continue
                    row_values.append(row.value_for(column))
                for measure in fact_measures:
                    value = row.value_for(measure.field_name)
                    _assert_value_compatible(value, measure.logical_type, measure.field_name)
                    row_values.append(value)
                if "source_record_refs" not in target_names:
                    row_values.append(tuple(fact_lineage))
                    fact_load.append(_insert_sql(fact.table_name, tuple(row_values), literal_sql=dataset.allow_literal_sql))

        for dimension in sorted((item for item in dimensions if item.role is DimensionRole.DATE), key=lambda item: item.table_name):
            dates = set(date_values.get(dimension.dimension_id, set()))
            if dimension.date_range_start is not None:
                dates.update(_date_range(dimension.date_range_start, dimension.date_range_end))
            if not dates:
                raise AnalyticalCompilationError(f"date dimension has no reviewed/input date range: {dimension.dimension_id}")
            for current in sorted(dates):
                values: list[object] = [deterministic_warehouse_key(dimension.surrogate_key.namespace, current.isoformat()), current]
                for attribute in dimension.attributes:
                    if attribute.derivation is None:
                        raise AnalyticalCompilationError(f"date attribute lacks a reviewed derivation: {attribute.column_name}")
                    values.append(_date_derivation(attribute.derivation, current))
                if "source_record_refs" not in {item.column_name for item in dimension.attributes}:
                    values.append((f"date-range:{dimension.dimension_id}",))
                date_load.append(_insert_sql(dimension.table_name, tuple(values), literal_sql=dataset.allow_literal_sql))

        return GeneratedSQL(
            generated_sql_id="pending",
            plan_id="pending",
            plan_content_hash="pending",
            compiler_version=self.COMPILER_VERSION,
            create_schema_sql="\n".join(create_statements) + "\n",
            load_date_sql="\n".join(date_load) + "\n",
            load_dimensions_sql="\n".join(dimension_load) + "\n",
            load_facts_sql="\n".join(fact_load) + "\n",
            statement_counts={"create_schema": len(create_statements), "load_date": len(date_load), "load_dimensions": len(dimension_load), "load_facts": len(fact_load)},
            quarantine_records=tuple(quarantine),
            provenance_refs=("sql:deterministic-project-owned", "source:typed-input-dataset", "policy:reviewed-measures", "sql:synthetic-literals" if dataset.allow_literal_sql else "sql:parameterized-load"),
        )

    @staticmethod
    def _validate_references(input_data: object) -> None:
        """Compatibility entry point for callers that want fail-closed checks."""

        try:
            dataset = as_analytical_dataset(input_data)
        except (TypeError, ValueError) as exc:
            raise AnalyticalCompilationError(str(exc)) from exc
        for table in dataset.tables:
            for row in table.rows:
                if not row.canonical_reference:
                    raise AnalyticalCompilationError("MISSING_CANONICAL_REFERENCE")

    @staticmethod
    def _validate_warehouse_key_collisions(input_data: object, dimensions: Iterable[DimensionSpec] = ()) -> None:
        dataset = as_analytical_dataset(input_data)
        for dimension in dimensions:
            if dimension.role is DimensionRole.DATE:
                continue
            table = dataset.table(dimension.input_table_id or "")
            seen: dict[int, str] = {}
            for row in table.rows:
                reference = AnalyticalCompilerService._row_reference(row, dimension)
                key = deterministic_warehouse_key(dimension.surrogate_key.namespace, reference)
                if key in seen and seen[key] != reference:
                    raise AnalyticalCompilationError(f"WAREHOUSE_KEY_COLLISION:{dimension.dimension_id}")
                seen[key] = reference


def _unknown_value(logical_type: str) -> object:
    kind = logical_type.upper()
    if kind in {"STRING", "STRING_LIST"}:
        return "UNKNOWN"
    if kind in {"DATE", "DATETIME", "TIMESTAMP"}:
        return date(1970, 1, 1)
    if kind == "BOOLEAN":
        return False
    if kind in {"INTEGER", "BIGINT"}:
        return 0
    if kind in {"DECIMAL", "FLOAT"}:
        return 0
    raise AnalyticalCompilationError(f"unsupported logical type: {logical_type}")


def _coerce_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise AnalyticalCompilationError("date role contains an invalid Gregorian date") from exc
    raise AnalyticalCompilationError("date role contains an unsupported value")


def _date_range(start: date, end: date | None) -> tuple[date, ...]:
    if end is None:
        raise AnalyticalCompilationError("date range endpoint is missing")
    result: list[date] = []
    current = start
    while current <= end:
        result.append(current)
        current += timedelta(days=1)
    return tuple(result)


def _date_derivation(derivation: str, current: date) -> object:
    values = {
        "FULL_DATE": current,
        "YEAR": current.year,
        "QUARTER": (current.month - 1) // 3 + 1,
        "MONTH": current.month,
        "DAY": current.day,
        "WEEKDAY": current.isoweekday(),
    }
    try:
        return values[derivation.upper()]
    except KeyError as exc:
        raise AnalyticalCompilationError(f"unsupported reviewed date derivation: {derivation}") from exc


def _assert_value_compatible(value: object, logical_type: str, field_name: str) -> None:
    if value is None:
        return
    kind = logical_type.upper()
    compatible = {
        "STRING": isinstance(value, str),
        "STRING_LIST": isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value),
        "INTEGER": isinstance(value, int) and not isinstance(value, bool),
        "BIGINT": isinstance(value, int) and not isinstance(value, bool),
        "DECIMAL": isinstance(value, (Decimal, int, float)) and not isinstance(value, bool),
        "FLOAT": isinstance(value, (Decimal, int, float)) and not isinstance(value, bool),
        "BOOLEAN": isinstance(value, bool),
        "DATE": isinstance(value, (date, datetime, str)),
        "DATETIME": isinstance(value, (date, datetime, str)),
        "TIMESTAMP": isinstance(value, (date, datetime, str)),
    }.get(kind)
    if compatible is not True:
        raise AnalyticalCompilationError(f"measure value is incompatible with {field_name}:{logical_type}")
