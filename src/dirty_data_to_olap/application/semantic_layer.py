"""Generic semantic modeling and bounded read-only query planning.

This service is deliberately downstream from Step20.  It projects reviewed
analytical contracts into business-readable metadata and generates only
parameterized SELECT templates over the exact materialized target.  It does
not write DuckDB, alter an analytical plan or create a second planner.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    DimensionRole,
    FactRelationshipScope,
    FactSpec,
    GrainSpec,
    MeasureSpec,
    AnalyticalPlan,
    CompiledPlan,
    MaterializationArtifact,
    MaterializationStatus,
)
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel, ReviewDecision, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.semantic import (
    DimensionHierarchy,
    HierarchyLevel,
    MetricExpression,
    MetricSpec,
    SemanticAttribute,
    SemanticAvailability,
    SemanticDimension,
    SemanticExposureState,
    SemanticExpressionType,
    SemanticFilterOperator,
    SemanticHierarchyValidationState,
    SemanticKeyExposurePolicy,
    SemanticMeasure,
    SemanticMetricKind,
    SemanticModel,
    SemanticModelStatus,
    SemanticQueryCompilation,
    SemanticQueryFilterShape,
    SemanticQueryPlan,
    SemanticQueryRequest,
    SemanticRelationship,
    SemanticRelationshipKind,
    SemanticRelationshipScope,
    SemanticValue,
    SemanticQueryResult,
    TimeRole,
    semantic_model_id,
    semantic_query_plan_id,
    metric_id as semantic_metric_id,
)
from dirty_data_to_olap.domain.semantic_query_renderer import SemanticQueryRenderError, render_semantic_query_sql
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.application.review_policy import ReviewPolicyService


class SemanticLayerError(ValueError):
    """A semantic model or bounded query cannot be safely represented."""


class SemanticStaleError(SemanticLayerError):
    """A semantic model no longer matches its Step20 inputs or target."""


_OPERATION_PATTERN = re.compile(r"\b(SUM|MAX|MIN|AVG|COUNT|LAST|FIRST)\s*\(", re.I)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_AGGREGATE_SQL = {"SUM", "MAX", "MIN", "COUNT"}


def _items(value: object) -> tuple[object, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    for prefix in ("dim_", "fact_", "measure_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text or "semantic_object"


def _display(value: str) -> str:
    return " ".join(part.capitalize() for part in _slug(value).split("_"))


def _quoted(identifier: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise SemanticLayerError(f"unsafe semantic physical identifier: {identifier!r}")
    return '"' + identifier + '"'


def _normalized(value: object) -> str:
    if isinstance(value, datetime):
        return "DATETIME"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, (Decimal, float)):
        return "DECIMAL"
    if isinstance(value, str):
        return "STRING"
    if value is None:
        return "NULL"
    return type(value).__name__.upper()


def _explicit_operations(measure: MeasureSpec) -> tuple[str, ...]:
    """Read only explicitly reviewed operation tokens from aggregation_rule."""

    return tuple(dict.fromkeys(match.upper() for match in _OPERATION_PATTERN.findall(measure.aggregation_rule)))


def _inherited_operations(measure: MeasureSpec) -> tuple[str, ...]:
    explicit = _explicit_operations(measure)
    if measure.aggregation_class is AggregationClass.ADDITIVE:
        return ("SUM",) if "SUM" in explicit else ()
    if measure.aggregation_class is AggregationClass.SEMI_ADDITIVE:
        return tuple(item for item in explicit if item in {"MAX", "MIN", "LAST", "FIRST"})[:1]
    # AT_GRAIN is a projection restriction, not an aggregate.  It lets a
    # NON_ADDITIVE field remain visible without making it executable as SUM.
    return ("AT_GRAIN",)


def _references(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


class SemanticLayerService:
    """Build semantic contracts and resolve bounded semantic queries."""

    semantic_model_version = "semantic-layer-v1"
    policy_version = "semantic-query-policy-v1"

    def _validate_upstream(
        self,
        plan: AnalyticalPlan,
        dimensions: tuple[object, ...],
        facts: tuple[FactSpec, ...],
        grains: tuple[GrainSpec, ...],
        measures: tuple[MeasureSpec, ...],
        compiled_plan: CompiledPlan,
        materialization: MaterializationArtifact,
        canonical_model: CanonicalModel | None,
        analytical_review: ReviewDecision | None,
    ) -> None:
        if compiled_plan.plan_id != plan.plan_id or compiled_plan.plan_content_hash != plan.content_hash:
            raise SemanticLayerError("STALE_ANALYTICAL_PLAN: compiled plan does not bind the exact analytical plan")
        if compiled_plan.analytical_spec_package_hash != plan.analytical_spec_package_hash:
            raise SemanticLayerError("STALE_ANALYTICAL_SPEC_PACKAGE: compiled package differs from plan")
        expected = {
            "dimension": {item.dimension_id: item.semantic_content_hash for item in dimensions},
            "fact": {item.fact_id: item.semantic_content_hash for item in facts},
            "grain": {item.grain_id: item.semantic_content_hash for item in grains},
            "measure": {item.measure_id: item.semantic_content_hash for item in measures},
        }
        actual = {
            "dimension": {item.dimension_id: item.semantic_content_hash for item in compiled_plan.dimension_specs},
            "fact": {item.fact_id: item.semantic_content_hash for item in compiled_plan.fact_specs},
            "grain": {item.grain_id: item.semantic_content_hash for item in compiled_plan.grain_specs},
            "measure": {item.measure_id: item.semantic_content_hash for item in compiled_plan.measure_specs},
        }
        if expected != actual:
            raise SemanticLayerError("STALE_ANALYTICAL_SPEC_PACKAGE: child spec content differs from compiled plan")
        if plan.dimension_spec_content_hashes and dict(plan.dimension_spec_content_hashes) != expected["dimension"]:
            raise SemanticLayerError("ANALYTICAL_PLAN_SPEC_HASH_MISMATCH: dimension package")
        if plan.fact_spec_content_hashes and dict(plan.fact_spec_content_hashes) != expected["fact"]:
            raise SemanticLayerError("ANALYTICAL_PLAN_SPEC_HASH_MISMATCH: fact package")
        if plan.grain_spec_content_hashes and dict(plan.grain_spec_content_hashes) != expected["grain"]:
            raise SemanticLayerError("ANALYTICAL_PLAN_SPEC_HASH_MISMATCH: grain package")
        if plan.measure_spec_content_hashes and dict(plan.measure_spec_content_hashes) != expected["measure"]:
            raise SemanticLayerError("ANALYTICAL_PLAN_SPEC_HASH_MISMATCH: measure package")
        if analytical_review is not None:
            if analytical_review.decision is not ReviewDecisionStatus.ACCEPTED:
                raise SemanticLayerError("REVIEW_REQUIRED: analytical-plan review is not accepted")
            review_errors = analytical_review.compatibility_errors(
                ReviewPolicyService().analytical_plan_context(plan).model_copy(
                    update={"subject_content_hash": analytical_review.subject_content_hash}
                )
            )
            if review_errors:
                raise SemanticLayerError("STALE_ANALYTICAL_REVIEW: " + ",".join(review_errors))
        elif compiled_plan.review_state.value != "ACCEPTED":
            raise SemanticLayerError("REVIEW_REQUIRED: accepted analytical review is required")
        if materialization.status is not MaterializationStatus.SUCCEEDED or not materialization.usable:
            raise SemanticLayerError("NON_CONSUMABLE_TARGET: materialization must be SUCCEEDED and usable")
        if materialization.plan_id != plan.plan_id or materialization.plan_content_hash != plan.content_hash:
            raise SemanticLayerError("STALE_TARGET: materialization does not bind the exact analytical plan")
        if materialization.compiled_plan_id != compiled_plan.compiled_plan_id or materialization.compiled_plan_content_hash != compiled_plan.content_hash:
            raise SemanticLayerError("STALE_TARGET: materialization does not bind the exact compiled plan")
        if materialization.generated_sql_hash != compiled_plan.generated_sql_hash:
            raise SemanticLayerError("STALE_TARGET: materialization SQL hash differs from compiled plan")
        if materialization.target_config_fingerprint != compiled_plan.target_config_fingerprint:
            raise SemanticLayerError("STALE_TARGET: target configuration differs from compiled plan")
        if canonical_model is not None and (
            canonical_model.model_id != plan.canonical_model_id
            or canonical_model.content_hash != plan.canonical_model_content_hash
        ):
            raise SemanticLayerError("STALE_CANONICAL_MODEL: canonical model binding differs from plan")

    def _relationship(
        self,
        plan: AnalyticalPlan,
        fact: FactSpec,
        foreign_key: object,
        dimension: object,
        canonical_model: CanonicalModel | None,
        lineage: tuple[str, ...],
    ) -> SemanticRelationship:
        scope = getattr(foreign_key, "relationship_scope")
        relationship_ref = getattr(foreign_key, "relationship_ref")
        if relationship_ref not in fact.relationship_refs:
            raise SemanticLayerError("UNKNOWN_RELATIONSHIP: FK relationship is not declared by FactSpec")
        if dimension.role is DimensionRole.DATE:
            if scope is not FactRelationshipScope.ANALYTICAL_TIME_ROLE:
                raise SemanticLayerError("UNCLASSIFIED_TIME_RELATIONSHIP: date FK lacks ANALYTICAL_TIME_ROLE scope")
            if getattr(foreign_key, "input_reference_column", None) not in fact.date_role_columns:
                raise SemanticLayerError("UNCLASSIFIED_TIME_RELATIONSHIP: date FK is not a declared fact time role")
            semantic_scope = SemanticRelationshipScope.ANALYTICAL_TIME_ROLE
            relationship_kind = SemanticRelationshipKind.TIME_ROLE
            cardinality = "FACT_TO_DATE_ROLE"
        else:
            if scope is not FactRelationshipScope.CANONICAL_ACCEPTED:
                raise SemanticLayerError("UNCLASSIFIED_RELATIONSHIP: non-date FK must use canonical scope")
            if relationship_ref not in plan.accepted_relationship_refs:
                raise SemanticLayerError("UNACCEPTED_RELATIONSHIP: FK is outside the reviewed relationship scope")
            if canonical_model is None:
                raise SemanticLayerError("REVIEW_REQUIRED: canonical relationship evidence is unavailable")
            canonical = next((item for item in canonical_model.relationships if item.relationship_id == relationship_ref), None)
            if canonical is None:
                raise SemanticLayerError("UNKNOWN_RELATIONSHIP: canonical relationship does not exist")
            if canonical.to_entity_type_id != dimension.canonical_entity_type_id or (canonical.from_entity_type_id not in fact.canonical_event_refs and canonical.from_entity_type_id != fact.canonical_event_type_id):
                raise SemanticLayerError("RELATIONSHIP_ENDPOINT_MISMATCH: canonical relationship cannot bind this fact FK")
            semantic_scope = SemanticRelationshipScope.CANONICAL_ACCEPTED
            relationship_kind = SemanticRelationshipKind.FACT_TO_DIMENSION
            cardinality = canonical.cardinality
        relationship_id = semantic_metric_id({
            "kind": "relationship",
            "fact": fact.fact_id,
            "dimension": dimension.dimension_id,
            "fact_column": foreign_key.fact_column,
            "relationship_scope": semantic_scope.value,
            "relationship_ref": relationship_ref,
        }).replace("metric_", "srel_", 1)
        return SemanticRelationship(
            relationship_id=relationship_id,
            source_semantic_object_id=fact.fact_id,
            target_semantic_object_id=dimension.dimension_id,
            relationship_kind=relationship_kind,
            relationship_scope=semantic_scope,
            cardinality=cardinality,
            physical_fact_id=fact.fact_id,
            physical_fact_table=fact.table_name,
            physical_fact_column=foreign_key.fact_column,
            physical_dimension_id=dimension.dimension_id,
            physical_dimension_table=dimension.table_name,
            physical_dimension_key_column=foreign_key.dimension_key_column,
            upstream_relationship_ref=relationship_ref,
            lineage_refs=lineage,
            provenance_refs=lineage,
        )

    def _assert_current(
        self,
        model: SemanticModel,
        *,
        analytical_plan: AnalyticalPlan | None = None,
        compiled_plan: CompiledPlan | None = None,
        materialization: MaterializationArtifact | None = None,
    ) -> None:
        if analytical_plan is not None and (
            analytical_plan.plan_id != model.analytical_plan_id
            or analytical_plan.content_hash != model.analytical_plan_content_hash
            or analytical_plan.analytical_spec_package_hash != model.analytical_spec_package_hash
        ):
            raise SemanticStaleError("STALE_ANALYTICAL_PLAN: semantic model must be regenerated")
        if compiled_plan is not None and (
            compiled_plan.compiled_plan_id != model.compiled_plan_id
            or compiled_plan.content_hash != model.compiled_plan_content_hash
            or compiled_plan.analytical_spec_package_hash != model.analytical_spec_package_hash
        ):
            raise SemanticStaleError("STALE_COMPILED_PLAN: semantic model must be regenerated")
        if materialization is not None and (
            materialization.artifact_id != model.materialization_artifact_id
            or materialization.content_hash != model.materialization_artifact_content_hash
            or materialization.target_relative_path != model.target_relative_path
            or materialization.target_config_fingerprint != model.target_config_fingerprint
            or materialization.status is not MaterializationStatus.SUCCEEDED
            or not materialization.usable
        ):
            raise SemanticStaleError("STALE_TARGET: semantic model must be regenerated for the current materialization")

    def build_model(
        self,
        plan: AnalyticalPlan,
        dimensions: Sequence[object],
        facts: Sequence[FactSpec] | FactSpec,
        grains: Sequence[GrainSpec] | GrainSpec,
        measures: Sequence[MeasureSpec] | MeasureSpec,
        compiled_plan: CompiledPlan,
        materialization: MaterializationArtifact,
        canonical_model: CanonicalModel | None = None,
        *,
        analytical_review: ReviewDecision | None = None,
        unresolved_semantic_items: Sequence[str] = (),
        additional_provenance_refs: Sequence[str] = (),
        derived_metrics: Sequence[MetricSpec] = (),
    ) -> SemanticModel:
        dimensions_t = tuple(dimensions)
        facts_t = tuple(_items(facts))
        grains_t = tuple(_items(grains))
        measures_t = tuple(_items(measures))
        if not all(isinstance(item, FactSpec) for item in facts_t) or not all(isinstance(item, GrainSpec) for item in grains_t) or not all(isinstance(item, MeasureSpec) for item in measures_t):
            raise SemanticLayerError("semantic model inputs must be Step20 analytical contracts")
        facts_t = tuple(item for item in facts_t if isinstance(item, FactSpec))
        grains_t = tuple(item for item in grains_t if isinstance(item, GrainSpec))
        measures_t = tuple(item for item in measures_t if isinstance(item, MeasureSpec))
        if not dimensions_t or not facts_t or not grains_t or not measures_t:
            raise SemanticLayerError("semantic model requires non-empty Step20 specification families")
        self._validate_upstream(plan, dimensions_t, facts_t, grains_t, measures_t, compiled_plan, materialization, canonical_model, analytical_review)
        dimension_map = {item.dimension_id: item for item in dimensions_t}
        fact_map = {item.fact_id: item for item in facts_t}
        grain_map = {item.grain_id: item for item in grains_t}
        measure_map = {item.measure_id: item for item in measures_t}
        if set(dimension_map) != set(plan.materialized_dimension_ids) or set(fact_map) != set(plan.materialized_fact_ids):
            raise SemanticLayerError("semantic model specification IDs do not match the reviewed analytical plan")
        if set(grain_map) != set(plan.grain_spec_ids) or set(measure_map) != set(plan.measure_spec_ids):
            raise SemanticLayerError("semantic model grain/measure IDs do not match the reviewed analytical plan")
        canonical_entities = {item.canonical_entity_type_id: item for item in canonical_model.entity_types} if canonical_model else {}
        lineage = _references(
            tuple(plan.lineage_refs)
            + tuple(plan.provenance_refs)
            + tuple(materialization.provenance_refs)
            + tuple(additional_provenance_refs)
            + (
                f"analytical_plan:{plan.plan_id}",
                f"canonical_model:{plan.canonical_model_id}",
                f"compiled_plan:{compiled_plan.compiled_plan_id}",
                f"materialization_artifact:{materialization.artifact_id}",
            )
        )

        semantic_dimensions: list[SemanticDimension] = []
        attribute_id_map: dict[tuple[str, str], str] = {}
        for dimension in dimensions_t:
            canonical = canonical_entities.get(dimension.canonical_entity_type_id)
            business_name = canonical.business_name if canonical else _display(dimension.dimension_id)
            attrs: list[SemanticAttribute] = []
            for attribute in dimension.attributes:
                semantic_attribute_id = _slug(f"{dimension.dimension_id}_{attribute.attribute_id}")
                attribute_id_map[(dimension.dimension_id, attribute.attribute_id)] = semantic_attribute_id
                implementation_only = attribute.attribute_id.casefold() in {"source_record_refs", "lineage_refs"}
                attrs.append(SemanticAttribute(
                    semantic_attribute_id=semantic_attribute_id,
                    name=_display(attribute.attribute_id),
                    description=f"Reviewed analytical attribute {_display(attribute.attribute_id)} of {business_name}.",
                    dimension_id=dimension.dimension_id,
                    physical_column_ref=attribute.column_name,
                    logical_type=attribute.logical_type,
                    canonical_attribute_refs=attribute.canonical_attribute_refs,
                    source_lineage_refs=attribute.lineage_refs,
                    nullable=attribute.nullable,
                    exposure_state=SemanticExposureState.IMPLEMENTATION_ONLY if implementation_only else SemanticExposureState.EXPOSED,
                    aliases=(),
                    lineage_refs=_references(tuple(attribute.lineage_refs) + lineage),
                    provenance_refs=lineage,
                ))
            semantic_dimensions.append(SemanticDimension(
                semantic_dimension_id=dimension.dimension_id,
                business_name=business_name,
                description=dimension.eligibility_reason,
                physical_dimension_spec_id=dimension.dimension_id,
                physical_table_ref=dimension.table_name,
                canonical_concept_ref=dimension.canonical_entity_type_id,
                canonical_entity_type_id=dimension.canonical_entity_type_id,
                role=dimension.role,
                attributes=tuple(attrs),
                key_exposure_policy=SemanticKeyExposurePolicy.EXPOSE_ALTERNATE_KEY,
                hierarchy_ids=(),
                aliases=(),
                unknown_member_policy=dimension.unknown_member_policy.policy.value,
                scd_mode=dimension.scd_policy.mode.value,
                lineage_refs=lineage,
                provenance_refs=lineage,
            ))

        semantic_hierarchies: list[DimensionHierarchy] = []
        for dimension, semantic_dimension in zip(dimensions_t, semantic_dimensions):
            if dimension.role is not DimensionRole.DATE:
                continue
            by_source = {item.attribute_id: attribute_id_map[(dimension.dimension_id, item.attribute_id)] for item in dimension.attributes}
            required = ("year", "quarter", "month", "day")
            if not all(item in by_source for item in required):
                continue
            hierarchy_id = _slug(f"{dimension.dimension_id}_calendar")
            semantic_hierarchies.append(DimensionHierarchy(
                hierarchy_id=hierarchy_id,
                dimension_id=dimension.dimension_id,
                levels=tuple(HierarchyLevel(level_id=_slug(f"{hierarchy_id}_{item}"), semantic_attribute_id=by_source[item], ordinal=index, name=_display(item)) for index, item in enumerate(required, 1)),
                description=f"Reviewed Gregorian calendar hierarchy for {semantic_dimension.business_name}.",
                strict_rollup=False,
                domain_assertion_refs=plan.domain_assertion_refs,
                evidence_refs=tuple(dimension.provenance_refs),
                provenance_refs=lineage,
                validation_state=SemanticHierarchyValidationState.VALIDATED,
            ))
        hierarchy_ids_by_dimension = {item.dimension_id: item.hierarchy_id for item in semantic_hierarchies}
        semantic_dimensions = [item.model_copy(update={"hierarchy_ids": (hierarchy_ids_by_dimension[item.semantic_dimension_id],) if item.semantic_dimension_id in hierarchy_ids_by_dimension else ()}) for item in semantic_dimensions]

        semantic_time_roles: list[TimeRole] = []
        semantic_relationships: list[SemanticRelationship] = []
        for fact in facts_t:
            for foreign_key in fact.dimension_foreign_keys:
                dimension = dimension_map.get(foreign_key.dimension_id)
                if dimension is None:
                    raise SemanticLayerError("semantic FK references an unplanned dimension")
                semantic_relationships.append(self._relationship(plan, fact, foreign_key, dimension, canonical_model, lineage))
                if dimension.role is DimensionRole.DATE:
                    source_column = foreign_key.input_reference_column
                    if source_column is None:
                        raise SemanticLayerError("time role requires an explicit physical date binding")
                    date_attrs = [item for item in dimension.attributes if item.logical_type.upper() == "DATE"]
                    if not date_attrs:
                        raise SemanticLayerError("time role requires a reviewed DATE dimension attribute")
                    time_role_id = _slug(f"{fact.fact_id}_{source_column}_time_role")
                    semantic_time_roles.append(TimeRole(
                        time_role_id=time_role_id,
                        fact_id=fact.fact_id,
                        relationship_ref=foreign_key.relationship_ref,
                        semantic_name=_slug(source_column),
                        physical_date_column=source_column,
                        date_dimension_id=dimension.dimension_id,
                        date_dimension_attribute_id=attribute_id_map[(dimension.dimension_id, date_attrs[0].attribute_id)],
                        grain_id=fact.grain_spec_id,
                        time_semantics=f"{_display(source_column)} role at reviewed {grain_map[fact.grain_spec_id].human_readable_grain}.",
                        supported_calendar="GREGORIAN",
                        lineage_refs=lineage,
                        provenance_refs=lineage,
                    ))

        roles_by_fact = {}
        for role in semantic_time_roles:
            roles_by_fact.setdefault(role.fact_id, []).append(role.time_role_id)
        semantic_measures: list[SemanticMeasure] = []
        metric_specs: list[MetricSpec] = []
        automatic_unresolved = list(unresolved_semantic_items)
        for measure in measures_t:
            fact = fact_map.get(measure.fact_id)
            if fact is None or fact.grain_spec_id not in grain_map:
                raise SemanticLayerError("semantic measure references an unknown fact/grain")
            operations = _inherited_operations(measure)
            sem_measure_id = _slug(f"semantic_{measure.measure_id}")
            compatible_dimensions = tuple(foreign_key.dimension_id for foreign_key in fact.dimension_foreign_keys)
            compatible_roles = tuple(roles_by_fact.get(fact.fact_id, ()))
            semantic_measure = SemanticMeasure(
                semantic_measure_id=sem_measure_id,
                name=_slug(measure.semantic_name),
                description=measure.aggregation_rule,
                fact_id=fact.fact_id,
                measure_spec_id=measure.measure_id,
                measure_spec_content_hash=measure.semantic_content_hash,
                physical_fact_table_ref=fact.table_name,
                physical_field_ref=measure.field_name,
                grain_id=fact.grain_spec_id,
                aggregation_class=measure.aggregation_class,
                allowed_aggregation_operations=operations,
                unit_semantics=measure.unit_semantics,
                currency_semantics=measure.currency_semantics,
                nullable=measure.nullable,
                compatible_dimension_ids=compatible_dimensions,
                compatible_time_role_ids=compatible_roles,
                domain_assertion_refs=measure.domain_assertion_refs,
                lineage_refs=lineage,
                provenance_refs=lineage,
            )
            semantic_measures.append(semantic_measure)
            metric_payload = {"measure_id": measure.measure_id, "measure_hash": measure.semantic_content_hash, "package": plan.analytical_spec_package_hash}
            generated_metric_id = semantic_metric_id(metric_payload)
            display_name = _display(measure.semantic_name)
            available = bool(operations) and operations[0] in _AGGREGATE_SQL
            if measure.aggregation_class is AggregationClass.NON_ADDITIVE:
                available = False
                failure_reason = "UNSUPPORTED_AGGREGATION: NON_ADDITIVE measure has no executable aggregate"
            elif not operations:
                failure_reason = "REVIEW_REQUIRED: reviewed aggregation rule does not declare a supported operation"
            else:
                failure_reason = None
            metric_specs.append(MetricSpec(
                metric_id=generated_metric_id,
                semantic_name=_slug(measure.semantic_name),
                display_name=display_name,
                description=f"Base metric projected from reviewed {measure.measure_id}.",
                metric_kind=SemanticMetricKind.BASE_AGGREGATE,
                fact_ids=(fact.fact_id,),
                grain_ids=(fact.grain_spec_id,),
                measure_ids=(measure.measure_id,),
                expression=MetricExpression(expression_type=SemanticExpressionType.AGGREGATE_MEASURE, measure_id=measure.measure_id, aggregation=operations[0] if operations else "AT_GRAIN"),
                aggregation_class=measure.aggregation_class,
                allowed_aggregation_operations=(operations[0],) if available else (),
                compatible_dimension_ids=compatible_dimensions,
                compatible_time_role_ids=compatible_roles,
                unit_semantics=measure.unit_semantics,
                currency_semantics=measure.currency_semantics,
                domain_assertion_refs=measure.domain_assertion_refs,
                lineage_refs=lineage,
                provenance_refs=lineage,
                availability=SemanticAvailability.AVAILABLE if available else SemanticAvailability.UNAVAILABLE,
                failure_reason=failure_reason,
            ))
            if not available:
                automatic_unresolved.append(f"metric:{generated_metric_id}:{failure_reason}")

        base_metric_ids = {item.metric_id for item in metric_specs}
        derived_metric_ids = {item.metric_id for item in derived_metrics}
        all_metric_ids = base_metric_ids | derived_metric_ids
        known_measure_ids = {item.measure_spec_id for item in semantic_measures}
        metric_by_id = {item.metric_id: item for item in metric_specs}
        for derived_metric in derived_metrics:
            if derived_metric.availability is SemanticAvailability.AVAILABLE:
                raise SemanticLayerError("DERIVED_METRIC_NOT_EXECUTABLE_V1: derived metrics cannot be executable in V1")
            if derived_metric.metric_kind is not SemanticMetricKind.DERIVED:
                raise SemanticLayerError("DERIVED_METRIC_REQUIRED: supplied derived metric is not marked DERIVED")
            if any(item not in all_metric_ids for item in (derived_metric.expression.numerator_metric_id, derived_metric.expression.denominator_metric_id) if item):
                raise SemanticLayerError("UNKNOWN_DERIVED_METRIC: ratio references an undeclared metric")
            if any(item not in known_measure_ids for item in derived_metric.measure_ids):
                raise SemanticLayerError("UNKNOWN_DERIVED_MEASURE: derived metric lineage references an undeclared measure")
            for reference_id in (derived_metric.expression.numerator_metric_id, derived_metric.expression.denominator_metric_id):
                if reference_id and reference_id in metric_by_id:
                    referenced = metric_by_id[reference_id]
                    if referenced.fact_ids != derived_metric.fact_ids or referenced.grain_ids != derived_metric.grain_ids:
                        raise SemanticLayerError("INCOMPATIBLE_GRAIN: derived metric references a different fact or grain")
            metric_by_id[derived_metric.metric_id] = derived_metric
            metric_specs.append(derived_metric)

        model_payload = {
            "version": self.semantic_model_version,
            "plan_id": plan.plan_id,
            "plan_hash": plan.content_hash,
            "package": plan.analytical_spec_package_hash,
            "compiled_id": compiled_plan.compiled_plan_id,
            "compiled_hash": compiled_plan.content_hash,
            "materialization_id": materialization.artifact_id,
            "materialization_hash": materialization.content_hash,
            "target": {"path": materialization.target_relative_path, "config": materialization.target_config_fingerprint, "file": materialization.target_file_sha256},
            "dimensions": [item.model_dump(mode="json") for item in semantic_dimensions],
            "measures": [item.model_dump(mode="json") for item in semantic_measures],
            "metrics": [item.model_dump(mode="json") for item in metric_specs],
            "hierarchies": [item.model_dump(mode="json") for item in semantic_hierarchies],
            "time_roles": [item.model_dump(mode="json") for item in semantic_time_roles],
            "relationships": [item.model_dump(mode="json") for item in semantic_relationships],
            "unresolved": sorted(set(automatic_unresolved)),
        }
        generated_id = semantic_model_id(model_payload)
        return SemanticModel(
            semantic_model_id=generated_id,
            semantic_model_version=self.semantic_model_version,
            analytical_plan_id=plan.plan_id,
            analytical_plan_content_hash=plan.content_hash,
            analytical_spec_package_hash=plan.analytical_spec_package_hash,
            canonical_model_id=plan.canonical_model_id,
            canonical_model_content_hash=plan.canonical_model_content_hash,
            compiled_plan_id=compiled_plan.compiled_plan_id,
            compiled_plan_content_hash=compiled_plan.content_hash,
            materialization_artifact_id=materialization.artifact_id,
            materialization_artifact_content_hash=materialization.content_hash,
            target_config_fingerprint=materialization.target_config_fingerprint,
            target_relative_path=materialization.target_relative_path,
            target_file_sha256=materialization.target_file_sha256,
            status=SemanticModelStatus.READY,
            dimensions=tuple(semantic_dimensions),
            measures=tuple(semantic_measures),
            metrics=tuple(metric_specs),
            hierarchies=tuple(semantic_hierarchies),
            time_roles=tuple(semantic_time_roles),
            relationships=tuple(semantic_relationships),
            domain_assertion_refs=_references(tuple(plan.domain_assertion_refs) + tuple(ref for item in measures_t for ref in item.domain_assertion_refs)),
            lineage_refs=lineage,
            provenance_refs=lineage,
            policy_version=self.policy_version,
            unresolved_semantic_items=tuple(sorted(set(automatic_unresolved))),
        )

    def compile_query(
        self,
        model: SemanticModel,
        request: SemanticQueryRequest,
        *,
        analytical_plan: AnalyticalPlan | None = None,
        compiled_plan: CompiledPlan | None = None,
        materialization: MaterializationArtifact | None = None,
    ) -> SemanticQueryCompilation:
        self._assert_current(model, analytical_plan=analytical_plan, compiled_plan=compiled_plan, materialization=materialization)
        metrics = {item.metric_id: item for item in model.metrics}
        dimensions = {item.semantic_dimension_id: item for item in model.dimensions}
        attributes = {item.semantic_attribute_id: item for dimension in model.dimensions for item in dimension.attributes}
        if any(metric_id not in metrics for metric_id in request.metric_ids):
            raise SemanticLayerError("UNKNOWN_METRIC: semantic query may select only declared metrics")
        selected_metrics = tuple(metrics[item] for item in request.metric_ids)
        derived = next((item for item in selected_metrics if item.metric_kind is SemanticMetricKind.DERIVED), None)
        if derived is not None:
            raise SemanticLayerError("DERIVED_METRIC_NOT_EXECUTABLE_V1: derived metrics have no V1 executable compiler")
        unavailable = next((item for item in selected_metrics if item.availability is not SemanticAvailability.AVAILABLE), None)
        if unavailable is not None:
            raise SemanticLayerError(unavailable.failure_reason or "UNAVAILABLE_METRIC")
        fact_ids = {item for metric in selected_metrics for item in metric.fact_ids}
        grain_ids = {item for metric in selected_metrics for item in metric.grain_ids}
        if len(fact_ids) != 1 or len(grain_ids) != 1:
            raise SemanticLayerError("INCOMPATIBLE_GRAIN: cross-fact or cross-grain metric combinations are disabled in V1")
        fact_id = next(iter(fact_ids))
        grain_id = next(iter(grain_ids))
        selected_dimension_ids = list(request.dimension_ids)
        for attribute_id in request.group_by_attribute_ids:
            attribute = attributes.get(attribute_id)
            if attribute is None:
                raise SemanticLayerError("UNKNOWN_ATTRIBUTE: semantic query may select only declared attributes")
            if attribute.dimension_id not in selected_dimension_ids:
                selected_dimension_ids.append(attribute.dimension_id)
        if any(item not in dimensions for item in selected_dimension_ids):
            raise SemanticLayerError("UNKNOWN_DIMENSION: semantic query may select only declared dimensions")
        for metric in selected_metrics:
            if any(dimension_id not in metric.compatible_dimension_ids for dimension_id in selected_dimension_ids):
                raise SemanticLayerError("INCOMPATIBLE_DIMENSION: selected dimension cannot safely slice the metric")
        selected_time_role = None
        if request.time_role_id is not None:
            selected_time_role = next((item for item in model.time_roles if item.time_role_id == request.time_role_id), None)
            if selected_time_role is None or selected_time_role.fact_id != fact_id or any(request.time_role_id not in metric.compatible_time_role_ids for metric in selected_metrics):
                raise SemanticLayerError("INCOMPATIBLE_TIME_ROLE: time role is not compatible with selected metrics")
        if any(metric.aggregation_class is AggregationClass.SEMI_ADDITIVE for metric in selected_metrics) and selected_time_role is None:
            raise SemanticLayerError("SEMI_ADDITIVE_TIME_SCOPE_REQUIRED: semi-additive metrics require an explicit time role")
        filter_attributes = []
        for semantic_filter in request.filters:
            attribute = attributes.get(semantic_filter.attribute_id)
            if attribute is None:
                raise SemanticLayerError("UNKNOWN_FILTER_ATTRIBUTE: filter must use a declared semantic attribute")
            if attribute.exposure_state is not SemanticExposureState.EXPOSED:
                raise SemanticLayerError("IMPLEMENTATION_ONLY_ATTRIBUTE: implementation lineage fields are not queryable")
            if attribute.dimension_id not in selected_dimension_ids:
                selected_dimension_ids.append(attribute.dimension_id)
            if any(attribute.dimension_id not in metric.compatible_dimension_ids for metric in selected_metrics):
                raise SemanticLayerError("INCOMPATIBLE_DIMENSION: filter dimension cannot safely reach the metric")
            for value in semantic_filter.values:
                if value is not None and not self._value_matches_type(value, attribute.logical_type):
                    raise SemanticLayerError(f"FILTER_TYPE_MISMATCH: {attribute.semantic_attribute_id}")
            if semantic_filter.operator is SemanticFilterOperator.DATE_RANGE and any(value is None for value in semantic_filter.values):
                raise SemanticLayerError("FILTER_TYPE_MISMATCH: date ranges cannot contain null")
            filter_attributes.append((semantic_filter, attribute))
        relationships = []
        aliases: dict[str, str] = {}
        for index, dimension_id in enumerate(selected_dimension_ids):
            candidates = [item for item in model.relationships if item.source_semantic_object_id == fact_id and item.target_semantic_object_id == dimension_id]
            if selected_time_role is not None and dimension_id == selected_time_role.date_dimension_id:
                candidates = [item for item in candidates if item.upstream_relationship_ref == selected_time_role.relationship_ref]
            if len(candidates) != 1:
                raise SemanticLayerError("AMBIGUOUS_RELATIONSHIP: explicit time role is required for multiple physical roles")
            relationship = candidates[0]
            if relationship.relationship_scope is SemanticRelationshipScope.CANONICAL_ACCEPTED and relationship.cardinality != "MANY_TO_ONE":
                raise SemanticLayerError("UNSAFE_FANOUT: only reviewed many-to-one dimension joins are supported")
            relationships.append(relationship)
            aliases[dimension_id] = f"d{index}"
        group_attributes = []
        for attribute_id in request.group_by_attribute_ids:
            attribute = attributes[attribute_id]
            if attribute.exposure_state is not SemanticExposureState.EXPOSED:
                raise SemanticLayerError("IMPLEMENTATION_ONLY_ATTRIBUTE: cannot group by lineage-only fields")
            group_attributes.append(attribute)
        if any(metric.aggregation_class is AggregationClass.SEMI_ADDITIVE for metric in selected_metrics):
            date_dimension_id = selected_time_role.date_dimension_id if selected_time_role is not None else None
            if not any(attribute.dimension_id == date_dimension_id for attribute in group_attributes) and not any(attribute.dimension_id == date_dimension_id for _, attribute in filter_attributes):
                raise SemanticLayerError("SEMI_ADDITIVE_TIME_SCOPE_REQUIRED: query must group or filter within the reviewed time role")
        if selected_dimension_ids and not group_attributes:
            for dimension_id in selected_dimension_ids:
                candidate = next((item for item in dimensions[dimension_id].attributes if item.exposure_state is SemanticExposureState.EXPOSED), None)
                if candidate is None:
                    raise SemanticLayerError("NO_EXPOSED_ATTRIBUTE: selected dimension has no business-readable attribute")
                group_attributes.append(candidate)
        if len({item.semantic_attribute_id for item in group_attributes}) != len(group_attributes):
            raise SemanticLayerError("DUPLICATE_GROUP_ATTRIBUTE")
        measure_by_spec = {item.measure_spec_id: item for item in model.measures}
        select_parts: list[str] = []
        group_parts: list[str] = []
        physical_bindings: dict[str, str] = {}
        for attribute in group_attributes:
            expression = f'{aliases[attribute.dimension_id]}.{_quoted(attribute.physical_column_ref)}'
            select_parts.append(f'{expression} AS {_quoted(attribute.semantic_attribute_id)}')
            group_parts.append(expression)
            physical_bindings[attribute.semantic_attribute_id] = f'{dimensions[attribute.dimension_id].physical_table_ref}.{attribute.physical_column_ref}'
        aggregation_operations: dict[str, str] = {}
        for metric in selected_metrics:
            operation = request.aggregation_overrides.get(metric.metric_id, metric.allowed_aggregation_operations[0])
            if operation not in metric.allowed_aggregation_operations or operation not in _AGGREGATE_SQL:
                raise SemanticLayerError(f"UNSUPPORTED_AGGREGATION: {metric.display_name} cannot execute {operation}")
            measure = measure_by_spec[metric.expression.measure_id or ""]
            select_parts.append(f'{operation}(f.{_quoted(measure.physical_field_ref)}) AS {_quoted(metric.metric_id)}')
            aggregation_operations[metric.metric_id] = operation
            physical_bindings[metric.metric_id] = f'{measure.physical_fact_table_ref}.{measure.physical_field_ref}'
        if not select_parts:
            raise SemanticLayerError("EMPTY_SEMANTIC_SELECT")
        fact_table = next(iter(measure_by_spec[metric.expression.measure_id].physical_fact_table_ref for metric in selected_metrics))
        sql_parts = ["SELECT " + ", ".join(select_parts), f'FROM {_quoted(fact_table)} AS f']
        for relationship in relationships:
            alias = aliases[relationship.target_semantic_object_id]
            sql_parts.append(
                f'LEFT JOIN {_quoted(relationship.physical_dimension_table)} AS {alias} '
                f'ON f.{_quoted(relationship.physical_fact_column)} = {alias}.{_quoted(relationship.physical_dimension_key_column)}'
            )
        parameters: list[SemanticValue] = []
        parameter_types: list[str] = []
        where_parts: list[str] = []
        for semantic_filter, attribute in filter_attributes:
            expression = f'{aliases[attribute.dimension_id]}.{_quoted(attribute.physical_column_ref)}'
            if semantic_filter.operator is SemanticFilterOperator.EQUALS and semantic_filter.values[0] is None:
                if not attribute.nullable:
                    raise SemanticLayerError("NULL_FILTER_ON_NON_NULLABLE_ATTRIBUTE")
                where_parts.append(f"{expression} IS NULL")
            elif semantic_filter.operator is SemanticFilterOperator.EQUALS:
                where_parts.append(f"{expression} = ?")
                parameters.append(semantic_filter.values[0])
                parameter_types.append(attribute.logical_type)
            elif semantic_filter.operator is SemanticFilterOperator.IN:
                placeholders = ", ".join("?" for _ in semantic_filter.values)
                where_parts.append(f"{expression} IN ({placeholders})")
                parameters.extend(semantic_filter.values)
                parameter_types.extend(attribute.logical_type for _ in semantic_filter.values)
            else:
                where_parts.append(f"{expression} >= ? AND {expression} <= ?")
                parameters.extend(semantic_filter.values)
                parameter_types.extend(attribute.logical_type for _ in semantic_filter.values)
        if where_parts:
            sql_parts.append("WHERE " + " AND ".join(where_parts))
        if group_parts:
            sql_parts.append("GROUP BY " + ", ".join(group_parts))
        sort_parts = []
        for sort in request.sort:
            if sort.field_id not in {item.semantic_attribute_id for item in group_attributes} and sort.field_id not in aggregation_operations:
                raise SemanticLayerError("UNKNOWN_SORT_FIELD: sort must use a selected semantic field")
            sort_parts.append(f'{_quoted(sort.field_id)} {"DESC" if sort.descending else "ASC"}')
        if sort_parts:
            sql_parts.append("ORDER BY " + ", ".join(sort_parts))
        sql_parts.append(f"LIMIT {request.limit}")
        sql_template = "\n".join(sql_parts)
        request_shape = request.model_dump(mode="json")
        for item in request_shape.get("filters", []):
            item["values"] = [_normalized(value) for value in item.get("values", [])]
        query_payload = {"model": model.content_hash, "request": request_shape, "sql": sql_template, "parameter_types": parameter_types}
        query_hash = stable_digest(query_payload)
        query_plan = SemanticQueryPlan(
            query_plan_id=semantic_query_plan_id(query_payload),
            request_id=request.request_id,
            semantic_model_id=model.semantic_model_id,
            semantic_model_content_hash=model.content_hash,
            metric_ids=request.metric_ids,
            dimension_ids=tuple(selected_dimension_ids),
            fact_ids=(fact_id,),
            grain_ids=(grain_id,),
            join_path_relationship_ids=tuple(item.relationship_id for item in relationships),
            group_by_attribute_ids=tuple(item.semantic_attribute_id for item in group_attributes),
            time_role_id=request.time_role_id,
            aggregation_operations=aggregation_operations,
            physical_bindings=physical_bindings,
            filter_shapes=tuple(
                SemanticQueryFilterShape(
                    attribute_id=semantic_filter.attribute_id,
                    operator=semantic_filter.operator,
                    value_count=len(semantic_filter.values),
                    logical_type=attribute.logical_type,
                    null_value=semantic_filter.operator is SemanticFilterOperator.EQUALS and semantic_filter.values[0] is None,
                )
                for semantic_filter, attribute in filter_attributes
            ),
            sort_specs=tuple(request.sort),
            limit=request.limit,
            parameter_count=len(parameters),
            parameter_logical_types=tuple(parameter_types),
            sql_template=sql_template,
            query_hash=query_hash,
            provenance_refs=model.provenance_refs,
        )
        try:
            trusted_sql = render_semantic_query_sql(model, query_plan)
        except SemanticQueryRenderError as exc:
            raise SemanticLayerError(f"INTERNAL_SEMANTIC_RENDER_REJECTED: {exc}") from exc
        if trusted_sql != sql_template:
            raise SemanticLayerError("INTERNAL_SEMANTIC_RENDER_MISMATCH: compiler output is not structurally renderable")
        return SemanticQueryCompilation(query_plan=query_plan, parameters=tuple(parameters))

    def resolve_query(self, model: SemanticModel, request: SemanticQueryRequest, **kwargs: object) -> SemanticQueryCompilation:
        return self.compile_query(model, request, **kwargs)

    @staticmethod
    def _value_matches_type(value: SemanticValue, logical_type: str) -> bool:
        logical = logical_type.upper()
        if logical in {"STRING", "STRING_LIST"}:
            return isinstance(value, str)
        if logical in {"INTEGER", "BIGINT"}:
            return isinstance(value, int) and not isinstance(value, bool)
        if logical in {"DECIMAL", "FLOAT"}:
            return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
        if logical == "BOOLEAN":
            return isinstance(value, bool)
        if logical == "DATE":
            return isinstance(value, (date, datetime))
        if logical in {"DATETIME", "TIMESTAMP"}:
            return isinstance(value, datetime)
        return False

    @staticmethod
    def validation_result(
        model: SemanticModel,
        checks: Sequence[tuple[str, bool, str]],
    ) -> object:
        """Return a project-owned validation artifact without adding a checkpoint."""

        from dirty_data_to_olap.domain.contracts.semantic import (
            SemanticValidationCheck,
            SemanticValidationResult,
            SemanticValidationStatus,
            semantic_validation_id,
        )

        items = tuple(SemanticValidationCheck(
            check_id=_slug(name),
            name=name,
            status=SemanticValidationStatus.PASS if passed else SemanticValidationStatus.FAIL,
            details=details,
            provenance_refs=model.provenance_refs,
        ) for name, passed, details in checks)
        payload = {"model": model.content_hash, "checks": [item.model_dump(mode="json") for item in items]}
        return SemanticValidationResult(
            validation_id=semantic_validation_id(payload),
            semantic_model_id=model.semantic_model_id,
            semantic_model_content_hash=model.content_hash,
            status=SemanticValidationStatus.PASS if all(item.status is SemanticValidationStatus.PASS for item in items) else SemanticValidationStatus.FAIL,
            checks=items,
            comparison_scope="same-materialized-target-semantic-validation-only",
            provenance_refs=model.provenance_refs,
        )
