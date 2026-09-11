"""Review-gated, data-driven analytical planning over a canonical graph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalPlan,
    AnalyticalPlanningRequest,
    AnalyticalReviewState,
    DimensionRole,
    DimensionSpec,
    FactSpec,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    as_analytical_dataset,
    analytical_plan_id,
)
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModel
from dirty_data_to_olap.domain.contracts.source import stable_digest


class AnalyticalPlanningError(ValueError):
    """The canonical graph, input binding or explicit model request is unsafe."""


class AnalyticalPlannerService:
    """Create a bounded V1 plan from explicit reviewed modeling semantics.

    The service validates and packages supplied candidates. It does not infer
    fact, dimension, grain or measure meaning from names.
    """

    PLAN_VERSION = "analytical-plan-v1"
    POLICY_VERSION = "analytical-planning-policy-v1"

    @staticmethod
    def validate_grain(rows: Sequence[AnalyticalInputRow], grain: GrainSpec) -> GrainSpec:
        if not rows:
            raise AnalyticalPlanningError("fact grain cannot be validated on an empty input")
        keys: list[tuple[object, ...]] = []
        null_rows: list[str] = []
        for row in rows:
            try:
                values = tuple(row.value_for(column) for column in grain.key_columns)
            except KeyError as exc:
                raise AnalyticalPlanningError(f"fact grain column is missing: {exc.args[0]}") from exc
            if any(value is None for value in values):
                null_rows.append(row.row_ref)
                if grain.null_policy is GrainNullPolicy.REJECT_NULLS:
                    raise AnalyticalPlanningError("fact grain contains a null key")
                continue
            keys.append(values)
        duplicates = len(keys) - len(set(keys))
        if duplicates:
            raise AnalyticalPlanningError(f"duplicate fact grain keys: {duplicates}")
        fingerprint = stable_digest({
            "fact_id": grain.fact_id,
            "grain_id": grain.grain_id,
            "key_columns": grain.key_columns,
            "keys": sorted((list(key) for key in keys), key=str),
            "null_rows": sorted(null_rows),
            "observed_row_count": len(rows),
        })
        return grain.model_copy(update={
            "validated": True,
            "observed_row_count": len(rows),
            "duplicate_key_count": 0,
            "validation_fingerprint": fingerprint,
        })

    @staticmethod
    def _validate_input_columns(dataset: AnalyticalInputDataset, request: AnalyticalPlanningRequest) -> None:
        dimensions = {item.dimension_id: item for item in request.dimensions}
        for dimension in request.dimensions:
            if dimension.role is DimensionRole.DATE:
                continue
            if not dimension.input_table_id:
                raise AnalyticalPlanningError(f"dimension input table is missing: {dimension.dimension_id}")
            table = dataset.table(dimension.input_table_id)
            columns = table.column_names()
            for attribute in dimension.attributes:
                source_column = attribute.input_column_name or attribute.column_name
                if source_column not in columns:
                    raise AnalyticalPlanningError(f"dimension input column is missing: {dimension.dimension_id}.{source_column}")
            if not set(dimension.alternate_key_columns).issubset({item.column_name for item in dimension.attributes}):
                raise AnalyticalPlanningError(f"dimension alternate key is not represented by attributes: {dimension.dimension_id}")

        for fact in request.facts:
            table = dataset.table(fact.input_table_id)
            columns = table.column_names()
            grain = next((item for item in request.grains if item.grain_id == fact.grain_spec_id), None)
            if grain is None:
                raise AnalyticalPlanningError(f"fact grain specification is missing: {fact.fact_id}")
            for column in grain.key_columns:
                if column not in columns:
                    raise AnalyticalPlanningError(f"fact grain column is missing: {fact.fact_id}.{column}")
            for column in fact.degenerate_dimension_columns + fact.date_role_columns:
                if column not in columns:
                    raise AnalyticalPlanningError(f"fact input column is missing: {fact.fact_id}.{column}")
            for foreign_key in fact.dimension_foreign_keys:
                source_column = foreign_key.input_reference_column or foreign_key.fact_column
                if source_column not in columns:
                    raise AnalyticalPlanningError(f"fact reference column is missing: {fact.fact_id}.{source_column}")
                if foreign_key.dimension_id not in dimensions:
                    raise AnalyticalPlanningError(f"fact references an unplanned dimension: {foreign_key.dimension_id}")
            measure_ids = set(fact.measure_ids)
            for measure in request.measures:
                if measure.measure_id in measure_ids and measure.field_name not in columns:
                    raise AnalyticalPlanningError(f"measure input column is missing: {fact.fact_id}.{measure.field_name}")

    @staticmethod
    def _validate_canonical_refs(canonical_model: CanonicalModel, request: AnalyticalPlanningRequest) -> None:
        entity_ids = {item.canonical_entity_type_id for item in canonical_model.entity_types}
        for dimension in request.dimensions:
            if dimension.role is not DimensionRole.DATE and dimension.canonical_entity_type_id not in entity_ids:
                raise AnalyticalPlanningError(f"dimension canonical entity type is not finalized: {dimension.canonical_entity_type_id}")
        for fact in request.facts:
            if fact.canonical_event_type_id not in entity_ids:
                raise AnalyticalPlanningError(f"fact canonical event type is not finalized: {fact.canonical_event_type_id}")
        relationship_ids = {item.relationship_id for item in canonical_model.relationships}
        requested = set(request.accepted_relationship_refs)
        if not requested.issubset(relationship_ids):
            missing = sorted(requested - relationship_ids)
            raise AnalyticalPlanningError("accepted canonical relationships missing: " + ",".join(missing))
        for fact in request.facts:
            missing = set(fact.relationship_refs) - requested
            if missing:
                raise AnalyticalPlanningError("fact relationship is not accepted: " + ",".join(sorted(missing)))

    def build_plan(
        self,
        canonical_model: CanonicalModel,
        binding: AnalyticalInputBinding,
        input_dataset: AnalyticalInputDataset | object,
        request: AnalyticalPlanningRequest,
        *,
        created_at: datetime | None = None,
    ) -> tuple[AnalyticalPlan, tuple[DimensionSpec, ...], tuple[FactSpec, ...], tuple[GrainSpec, ...], tuple[MeasureSpec, ...]]:
        dataset = as_analytical_dataset(input_dataset)
        if binding.canonical_model_id != canonical_model.model_id or binding.canonical_model_content_hash != canonical_model.content_hash:
            raise AnalyticalPlanningError("analytical input binding is not bound to the exact canonical model")
        if binding.dataset_id is not None:
            if binding.dataset_id != dataset.dataset_id or binding.dataset_content_hash != dataset.content_hash:
                raise AnalyticalPlanningError("analytical dataset does not match its input binding")
        elif binding.fixture_id != dataset.dataset_id:
            raise AnalyticalPlanningError("legacy input binding does not identify the analytical dataset")
        if dataset.canonical_model_id != canonical_model.model_id or dataset.canonical_model_content_hash != canonical_model.content_hash:
            raise AnalyticalPlanningError("analytical dataset is not bound to the exact canonical model")
        if request.review_state is not AnalyticalReviewState.REVIEW_REQUIRED:
            raise AnalyticalPlanningError("analytical planning requires a separate review decision")

        dimensions = tuple(request.dimensions)
        facts = tuple(request.facts)
        measures = tuple(request.measures)
        grains_by_id = {item.grain_id: item for item in request.grains}
        if len(grains_by_id) != len(request.grains):
            raise AnalyticalPlanningError("grain IDs must be unique")
        if len({item.dimension_id for item in dimensions}) != len(dimensions):
            raise AnalyticalPlanningError("dimension IDs must be unique")
        if len({item.fact_id for item in facts}) != len(facts):
            raise AnalyticalPlanningError("fact IDs must be unique")
        if len({item.measure_id for item in measures}) != len(measures):
            raise AnalyticalPlanningError("measure IDs must be unique")
        self._validate_canonical_refs(canonical_model, request)
        self._validate_input_columns(dataset, request)

        validated_grains: list[GrainSpec] = []
        for fact in facts:
            grain = grains_by_id.get(fact.grain_spec_id)
            if grain is None:
                raise AnalyticalPlanningError(f"fact grain specification is missing: {fact.fact_id}")
            validated_grains.append(self.validate_grain(dataset.table(fact.input_table_id).rows, grain))
        grains = tuple(validated_grains)
        grain_map = {item.grain_id: item for item in grains}
        for fact in facts:
            fact_measure_ids = set(fact.measure_ids)
            supplied_measure_ids = {item.measure_id for item in measures if item.fact_id == fact.fact_id}
            if supplied_measure_ids != fact_measure_ids:
                raise AnalyticalPlanningError(f"fact measures do not match the reviewed request: {fact.fact_id}")
            if fact.grain_spec_id not in grain_map:
                raise AnalyticalPlanningError(f"fact grain does not match the reviewed request: {fact.fact_id}")

        accepted_relationships = tuple(sorted(request.accepted_relationship_refs))
        lineage = tuple(sorted({ref for table in dataset.tables for row in table.rows for ref in row.source_record_refs}))
        provenance = tuple(sorted(set(dataset.provenance_refs) | set(request.provenance_refs)))
        dimension_hashes = {item.dimension_id: item.semantic_content_hash for item in dimensions}
        fact_hashes = {item.fact_id: item.semantic_content_hash for item in facts}
        grain_hashes = {item.grain_id: item.semantic_content_hash for item in grains}
        measure_hashes = {item.measure_id: item.semantic_content_hash for item in measures}
        entity_ids = tuple(sorted({item.canonical_entity_type_id for item in dimensions if item.role is not DimensionRole.DATE} | {item.canonical_event_type_id for item in facts}))
        event_ids = tuple(sorted({item.canonical_event_type_id for item in facts}))
        payload = {
            "version": self.PLAN_VERSION,
            "canonical_model_id": canonical_model.model_id,
            "canonical_model_content_hash": canonical_model.content_hash,
            "binding": binding.content_hash,
            "dataset": dataset.content_hash,
            "dimensions": [item.model_dump(mode="json") for item in dimensions],
            "facts": [item.model_dump(mode="json") for item in facts],
            "grains": [item.model_dump(mode="json") for item in grains],
            "measures": [item.model_dump(mode="json") for item in measures],
            "relationships": accepted_relationships,
            "domain_assertions": request.domain_assertion_refs,
        }
        plan = AnalyticalPlan(
            plan_id=analytical_plan_id(payload),
            plan_version=self.PLAN_VERSION,
            canonical_model_id=canonical_model.model_id,
            canonical_model_content_hash=canonical_model.content_hash,
            canonical_model_fingerprint=canonical_model.content_hash,
            input_binding_id=binding.binding_id,
            input_binding_content_hash=binding.content_hash,
            canonical_entity_type_ids=entity_ids or ("analytical:unresolved",),
            canonical_event_type_ids=event_ids,
            accepted_relationship_refs=accepted_relationships or ("analytical:no-relationship",),
            materialized_dimension_ids=tuple(item.dimension_id for item in dimensions) or ("analytical:no-dimension",),
            materialized_fact_ids=tuple(item.fact_id for item in facts),
            grain_spec_ids=tuple(item.grain_id for item in grains),
            measure_spec_ids=tuple(item.measure_id for item in measures),
            dimension_spec_content_hashes=dimension_hashes,
            fact_spec_content_hashes=fact_hashes,
            grain_spec_content_hashes=grain_hashes,
            measure_spec_content_hashes=measure_hashes,
            source_record_lineage_refs=lineage or dataset.provenance_refs,
            deferred_concept_refs=tuple(sorted(request.deferred_concept_refs)),
            deferred_concept_reasons=dict(request.deferred_concept_reasons),
            domain_assertion_refs=tuple(sorted(request.domain_assertion_refs)),
            source_schema_fingerprints=dict(dataset.source_schema_fingerprints),
            policy_version=self.POLICY_VERSION,
            review_state=AnalyticalReviewState.REVIEW_REQUIRED,
            lineage_refs=tuple(sorted(set(lineage) | set(dataset.provenance_refs))),
            provenance_refs=provenance,
            created_at=created_at or datetime.now(timezone.utc),
        )
        return plan, dimensions, facts, grains, measures
