"""Versioned product policy translated into typed service requests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalPlanningRequest,
    DimensionAttributeSpec,
    DimensionRole,
    DimensionSpec,
    FactForeignKeySpec,
    FactRelationshipScope,
    FactSpec,
    FactType,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    SCDMode,
    SCDPolicySpec,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
    WarehouseKeySpec,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalRelationship,
    CanonicalSourceTable,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
)
from dirty_data_to_olap.domain.contracts.dependency import DependencyPrivacyContext, DependencyRequest
from dirty_data_to_olap.domain.contracts.evidence_fusion import DomainAssertion
from dirty_data_to_olap.domain.contracts.profiling import NullMarkerPolicy, ProfileMode, ProfileRequest
from dirty_data_to_olap.domain.contracts.quality import (
    QualityRequest,
    QualityRule,
    QualityRuleScope,
    QualityRuleSet,
    QualityRuleType,
    QualitySeverity,
    Repairability,
)
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, stable_id


class OrderProductPolicy:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.data, dict) or self.data.get("product_id") != "order":
            raise ValueError("product policy is not the order product policy")
        self.version = str(self.data["version"])
        self.provenance = f"config:{self.path.as_posix()}"

    @classmethod
    def load(cls, root: Path) -> "OrderProductPolicy":
        return cls(root / "config" / "product" / "order_v1.json")

    def source_table(self, catalog: SourceCatalog):
        name = str(self.data["source_table"])
        configured = next((item for item in catalog.tables if item.physical_name == name), None)
        if configured is not None:
            return configured
        if len(catalog.tables) == 1:
            return catalog.tables[0]
        raise ValueError("the product policy source table is not present in the discovered catalog")

    @staticmethod
    def column(catalog: SourceCatalog, table_id: str, names: str | Sequence[str]):
        wanted = (names,) if isinstance(names, str) else tuple(names)
        normalized = {name.casefold() for name in wanted}
        return next((item for item in catalog.columns if item.table_id == table_id and item.physical_name.casefold() in normalized), None)

    def profile_request(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, run_id: str) -> ProfileRequest:
        settings = self.data["profiling"]
        return ProfileRequest(
            profile_request_id=stable_id("profile-request", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "policy": self.version}),
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            selected_table_ids=tuple(item.table_id for item in catalog.tables),
            selected_column_ids=(),
            mode=ProfileMode.FULL,
            metric_policy_version=str(settings["metric_policy_version"]),
            null_marker_policy=NullMarkerPolicy(configured_markers=tuple(settings.get("null_markers", ()))),
            profile_config_version=str(settings["profile_config_version"]),
        )

    def quality_request(
        self,
        catalog: SourceCatalog,
        snapshot: SourceSnapshotResult,
        profile,
        run_id: str,
        *,
        column_aliases: Mapping[str, Sequence[str]] | None = None,
    ) -> QualityRequest:
        table = self.source_table(catalog)
        rules = []
        settings = self.data["quality"]
        for raw in settings["rules"]:
            names = tuple(str(item) for item in raw.get("column_names", ()))
            ids = []
            for name in names:
                aliases = tuple(column_aliases.get(name, (name,))) if column_aliases is not None else (name,)
                column = self.column(catalog, table.table_id, aliases)
                if column is None:
                    raise ValueError(f"quality policy column is not present in the discovered catalog: {name}")
                ids.append(column.column_id)
            rules.append(QualityRule(
                rule_id=str(raw["rule_id"]),
                rule_version=str(settings["version"]),
                rule_type=QualityRuleType(str(raw["rule_type"])),
                scope=QualityRuleScope.USER_POLICY,
                entity_type=self.data["entity"]["semantic_id"],
                source_id=catalog.source_id,
                table_id=table.table_id,
                column_ids=tuple(ids),
                severity=QualitySeverity.HIGH,
                repairability=Repairability.REVIEW_REQUIRED,
                detector_config={},
                provenance=self.provenance,
            ))
        rule_set = QualityRuleSet(rule_set_id=str(settings["rule_set_id"]), version=str(settings["version"]), rules=tuple(rules), provenance=self.provenance, runtime_default=True)
        return QualityRequest(
            quality_run_id=stable_id("quality-run", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "policy": rule_set.version}),
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            rule_set=rule_set,
            profile_result_fingerprint=__import__("dirty_data_to_olap.domain.contracts.quality", fromlist=["quality_profile_fingerprint"]).quality_profile_fingerprint(profile),
            profile_refs=(profile.profile_request.profile_request_id,),
            batch_ids=tuple(item.batch_id for item in snapshot.batches),
            batch_hashes=tuple(item.content_hash for item in snapshot.batches),
            provenance=self.provenance,
        )

    def dependency_request(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, run_id: str) -> DependencyRequest:
        return DependencyRequest(
            request_id=stable_id("dependency-request", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "policy": self.version}),
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            selected_table_ids=tuple(item.table_id for item in catalog.tables),
            privacy_context=DependencyPrivacyContext(
                purpose="dependency_discovery_structural_analysis",
                exposure_context="DEPENDENCY_LOCAL_ANALYSIS",
                raw_staging_allowed=True,
                local_only=True,
                external_processing_allowed=False,
                network_allowed=False,
                llm_allowed=False,
                raw_values_in_results=False,
                raw_values_in_logs=False,
                project_temp_root="privacy_ephemeral/dependency_discovery",
                cleanup_required=True,
            ),
        )

    def domain_assertions(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, candidates) -> tuple[DomainAssertion, ...]:
        assertions = self.data["entity"]["domain_assertion_refs"]
        output = []
        for candidate in candidates:
            subject_id = "rel:" + candidate.from_table + ":" + ",".join(candidate.from_columns) + "->" + candidate.to_table + ":" + ",".join(candidate.to_columns)
            for assertion_ref in assertions:
                output.append(DomainAssertion(
                    assertion_id=str(assertion_ref),
                    subject_id=subject_id,
                    statement=f"{assertion_ref} is explicitly declared by the versioned order product policy",
                    status="ACTIVE",
                    source_ids=(catalog.source_id,),
                    snapshot_ids=(snapshot.snapshot.snapshot_id,),
                    scope_id=stable_id("domain-scope", {"candidate": candidate.candidate_id, "assertion": assertion_ref}),
                    asserted_by=f"product-policy:{self.version}",
                    evidence_refs=(self.provenance, candidate.candidate_id),
                ))
        return tuple(output)

    def entity_type(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, decision_id: str, domain_assertion_refs: tuple[str, ...] | None = None) -> CanonicalEntityType:
        config = self.data["entity"]
        table = self.source_table(catalog)
        return CanonicalEntityType(
            canonical_entity_type_id=str(config["canonical_entity_type_id"]),
            semantic_id=str(config["semantic_id"]),
            business_name=str(config["business_name"]),
            kind=CanonicalEntityKind.EVENT,
            entity_resolution_family=str(config["entity_resolution_family"]),
            identity_strategy=str(config["identity_strategy"]),
            identity_attribute_ids=(str(config["identity_column"]),),
            source_table_refs=(CanonicalSourceTable(source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_id=table.table_id, schema_fingerprint=catalog.source.schema_fingerprint),),
            relationship_refs=(decision_id,),
            domain_assertion_refs=tuple(domain_assertion_refs or config["domain_assertion_refs"]),
            review_state="ACCEPTED_BY_EVIDENCE_REVIEW",
            provenance_refs=(catalog.source.schema_fingerprint, snapshot.snapshot.snapshot_id, decision_id, self.provenance),
        )

    def relationship(self, decision, entity_type_id: str, review_decision_ref: str) -> CanonicalRelationship:
        return CanonicalRelationship(
            relationship_id=decision.decision_id,
            from_entity_type_id=entity_type_id,
            to_entity_type_id=entity_type_id,
            cardinality=str(decision.proposed_cardinality).replace("_CANDIDATE", ""),
            upstream_decision_ref=decision.decision_id,
            review_decision_ref=review_decision_ref,
            conflict_refs=tuple(decision.conflict_refs),
            provenance_refs=(decision.decision_id, self.provenance),
        )

    def analytical_request(self, *, catalog: SourceCatalog, snapshot: SourceSnapshotResult, canonical, decision) -> AnalyticalPlanningRequest:
        settings = self.data["analytical"]
        table = self.source_table(catalog)
        config_types = {str(k): str(v) for k, v in settings["column_types"].items()}
        lineage = (canonical.model_id, snapshot.snapshot.snapshot_id, self.provenance)
        order_dimension = DimensionSpec(
            dimension_id=str(settings["dimension"]["dimension_id"]),
            table_name=str(settings["dimension"]["table_name"]),
            input_table_id=table.table_id,
            canonical_reference_column="canonical_entity_id",
            canonical_entity_type_id=self.data["entity"]["canonical_entity_type_id"],
            canonical_entity_refs=tuple(item.canonical_entity_id for item in canonical.instances),
            role=DimensionRole.CONFORMED,
            eligibility_reason="explicit source-local event identity policy and accepted dependency evidence",
            surrogate_key=WarehouseKeySpec(key_name="order_key", namespace="order-product-v1.order"),
            alternate_key_columns=("order_id",),
            attributes=tuple(
                DimensionAttributeSpec(attribute_id=f"order_{name}", column_name=name, input_column_name=name, logical_type=config_types[name], nullable=False, lineage_refs=lineage)
                for name in ("order_id", "customer_id", "customer_id_ref")
            ),
            scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="immutable bounded source snapshot has no history contract"),
            unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unresolved order identity is not silently allocated"),
            provenance_refs=lineage,
        )
        date_dimension = DimensionSpec(
            dimension_id=str(settings["date_dimension"]["dimension_id"]),
            table_name=str(settings["date_dimension"]["table_name"]),
            canonical_entity_type_id="cet_date",
            canonical_entity_refs=("date",),
            role=DimensionRole.DATE,
            eligibility_reason="Gregorian date role is explicit in the product policy",
            surrogate_key=WarehouseKeySpec(key_name="date_key", namespace="order-product-v1.date"),
            alternate_key_columns=("full_date",),
            attributes=(
                DimensionAttributeSpec(attribute_id="date_full", column_name="full_date", logical_type="DATE", derivation="FULL_DATE", nullable=False, lineage_refs=lineage),
                DimensionAttributeSpec(attribute_id="date_year", column_name="year", logical_type="INTEGER", derivation="YEAR", nullable=False, lineage_refs=lineage),
                DimensionAttributeSpec(attribute_id="date_month", column_name="month", logical_type="INTEGER", derivation="MONTH", nullable=False, lineage_refs=lineage),
                DimensionAttributeSpec(attribute_id="date_day", column_name="day", logical_type="INTEGER", derivation="DAY", nullable=False, lineage_refs=lineage),
            ),
            scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="calendar dimension is deterministically generated for the observed dates"),
            unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER, rationale="date role uses an explicit unknown member policy", unknown_member_key=-1),
            calendar_policy=str(settings["date_dimension"]["calendar_policy"]),
            provenance_refs=lineage,
        )
        time_relation = stable_id("time_role", {"fact": settings["fact"]["fact_id"], "column": "order_date"})
        foreign_keys = (
            FactForeignKeySpec(relationship_ref=decision.decision_id, relationship_scope=FactRelationshipScope.CANONICAL_ACCEPTED, dimension_id=order_dimension.dimension_id, fact_column="order_key", dimension_key_column="order_key", canonical_entity_type_id=order_dimension.canonical_entity_type_id, input_reference_column="canonical_entity_id"),
            FactForeignKeySpec(relationship_ref=time_relation, relationship_scope=FactRelationshipScope.ANALYTICAL_TIME_ROLE, dimension_id=date_dimension.dimension_id, fact_column="order_date_key", dimension_key_column="date_key", canonical_entity_type_id=date_dimension.canonical_entity_type_id, input_reference_column="order_date"),
        )
        fact = FactSpec(
            fact_id=str(settings["fact"]["fact_id"]),
            table_name=str(settings["fact"]["table_name"]),
            input_table_id=table.table_id,
            fact_type=FactType.TRANSACTION,
            canonical_event_type_id=self.data["entity"]["canonical_entity_type_id"],
            canonical_event_refs=tuple(item.canonical_entity_id for item in canonical.instances),
            grain_spec_id=str(settings["fact"]["grain_id"]),
            dimension_foreign_keys=foreign_keys,
            degenerate_dimension_columns=("customer_id", "customer_id_ref"),
            measure_ids=(str(settings["measure"]["measure_id"]),),
            date_role_columns=("order_date",),
            relationship_refs=(decision.decision_id, time_relation),
            provenance_refs=lineage,
        )
        grain = GrainSpec(grain_id=fact.grain_spec_id, fact_id=fact.fact_id, human_readable_grain="one row per source order_id", key_columns=("order_id",), null_policy=GrainNullPolicy.REJECT_NULLS, validated=False, observed_row_count=0, duplicate_key_count=0, evidence_refs=(snapshot.snapshot.snapshot_id,), provenance_refs=lineage)
        measure_config = settings["measure"]
        measure = MeasureSpec(measure_id=str(measure_config["measure_id"]), fact_id=fact.fact_id, field_name=str(measure_config["field_name"]), semantic_name=str(measure_config["semantic_name"]), aggregation_class=AggregationClass.ADDITIVE, aggregation_rule=str(measure_config["aggregation_rule"]), unit_semantics=str(measure_config["unit_semantics"]), currency_semantics=str(measure_config["currency_semantics"]), logical_type="DECIMAL", nullable=False, domain_assertion_refs=tuple(settings["domain_assertion_refs"]), provenance_refs=lineage)
        return AnalyticalPlanningRequest(request_id=stable_id("analytical-request", {"snapshot": snapshot.snapshot.snapshot_id, "canonical": canonical.model_id, "decision": decision.decision_id, "policy": self.version}), dimensions=(order_dimension, date_dimension), facts=(fact,), grains=(grain,), measures=(measure,), accepted_relationship_refs=(decision.decision_id,), deferred_concept_refs=tuple(settings["deferred_concepts"]), deferred_concept_reasons=dict(settings["deferred_concepts"]), domain_assertion_refs=tuple(settings["domain_assertion_refs"]), provenance_refs=lineage)

    def validation_policy(self, *, canonical_model_id: str, materialization_id: str):
        from dirty_data_to_olap.domain.contracts.validation import ValidationPolicy, ValidationStatus
        settings = self.data["validation"]
        return ValidationPolicy(
            policy_id=stable_id("validation-policy", {"product": self.version, "canonical": canonical_model_id, "materialization": materialization_id}),
            policy_version=str(settings["policy_version"]),
            required_check_ids=tuple(settings["required_check_ids"]),
            allowed_terminal_dispositions=(__import__("dirty_data_to_olap.domain.contracts.canonical", fromlist=["RecordDisposition"]).RecordDisposition.EMITTED_DIRECT,),
            orphan_policy=dict(settings["orphan_policy"]),
            require_bidirectional_lineage=True,
            exact_numeric_comparison=True,
            monetary_status=ValidationStatus(str(settings["monetary_status"])),
            monetary_reason=str(settings["monetary_reason"]),
            deferred_concepts=dict(self.data["analytical"]["deferred_concepts"]),
            provenance_refs=(self.provenance, materialization_id),
        )
