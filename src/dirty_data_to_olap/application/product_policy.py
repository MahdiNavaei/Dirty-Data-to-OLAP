"""Versioned product policy translated into typed service requests."""

from __future__ import annotations

import json
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalCell,
    AnalyticalColumnBinding,
    AnalyticalInputBinding,
    AnalyticalInputDataset,
    AnalyticalInputRow,
    AnalyticalInputTable,
    AnalyticalPlanningRequest,
    AnalyticalRowBatch,
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
    CanonicalIdentityMembership,
    CanonicalModel,
    CanonicalEntityType,
    CanonicalRelationship,
    CanonicalSourceTable,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
)
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule,
    ERClusteringPolicy,
    ERComparisonSpecification,
    ERExecutionBudget,
    EntityResolutionMode,
    EntityResolutionNormalizationRule,
    EntityResolutionSpec,
    ERThresholdPolicy,
    ERTrainingPolicy,
    IdentityFieldSpecification,
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
from dirty_data_to_olap.domain.contracts.product import ProductDomainPolicy, ProductPolicyBinding


class OrderProductPolicy:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.data, dict) or self.data.get("product_id") != "order":
            raise ValueError("product policy is not the order product policy")
        self.version = str(self.data["version"])
        self.provenance = f"config:{self.path.as_posix()}"
        self.product_id = str(self.data["product_id"])
        self.content_fingerprint = hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def binding(self) -> ProductPolicyBinding:
        return ProductPolicyBinding(product_id=self.product_id, version=self.version, content_fingerprint=self.content_fingerprint, provenance_ref=self.provenance)

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

    @classmethod
    def source_role(cls, catalog: SourceCatalog) -> str:
        source_id = catalog.source_id.casefold()
        if any(token in source_id for token in ("crm", "erp", "customer", "account")):
            return "registry"
        if any(token in source_id for token in ("sales", "legacy", "csv", "event")):
            return "event"
        return "event" if any(cls.column(catalog, table.table_id, ("order_id", "ticket_id", "sale_key")) is not None and cls.column(catalog, table.table_id, ("order_date", "booked_on", "sale_day")) is not None for table in catalog.tables) else "registry"

    @classmethod
    def role_table(cls, catalog: SourceCatalog):
        role = cls.source_role(catalog)
        for table in catalog.tables:
            names = {item.physical_name.casefold() for item in catalog.columns if item.table_id == table.table_id}
            if role == "registry" and {"customer_id", "customer_name"}.issubset(names):
                return table
            if role == "event" and (names & {"order_id", "ticket_id", "sale_key"}):
                return table
        return cls.source_table(catalog)

    def dependency_result(self, result: Any, *, catalog: SourceCatalog) -> Any:
        return result

    def relationship_candidates(self, catalogs: Mapping[str, SourceCatalog]) -> tuple[dict[str, Any], ...]:
        registries = [catalog for catalog in catalogs.values() if self.source_role(catalog) == "registry"]
        events = [catalog for catalog in catalogs.values() if self.source_role(catalog) == "event"]
        if not registries or not events:
            raise ValueError("Prompt02 requires both customer registries and event sources")
        registry = sorted(registries, key=lambda item: item.source_id)[0]
        event = sorted(events, key=lambda item: item.source_id)[0]
        event_table = self.role_table(event)
        registry_table = self.role_table(registry)
        event_customer = self.column(event, event_table.table_id, ("customer_id_ref", "customer_id", "crm_customer_id", "account_no", "account_id", "customer_code", "buyer_ref", "client_code", "customer_ref"))
        registry_customer = self.column(registry, registry_table.table_id, ("customer_id", "crm_customer_id", "account_no", "account_id", "customer_code", "buyer_ref", "client_code", "customer_ref"))
        if event_customer is None or registry_customer is None:
            raise ValueError("Prompt02 relationship candidate lacks an explicit customer key")
        return (
            {"candidate_id": stable_id("relationship-candidate", {"kind": "customer", "event": event.source_id, "registry": registry.source_id}), "from_table": event_table.physical_name, "from_columns": (event_customer.physical_name,), "to_table": registry_table.physical_name, "to_columns": (registry_customer.physical_name,), "proposed_cardinality": "MANY_TO_ONE"},
            {"candidate_id": stable_id("relationship-candidate", {"kind": "source", "event": event.source_id}), "from_table": event_table.physical_name, "from_columns": ("source_id",), "to_table": "source_registry", "to_columns": ("source_id",), "proposed_cardinality": "MANY_TO_ONE"},
        )

    @staticmethod
    def _candidate_subject(item: Mapping[str, Any]) -> str:
        return "rel:" + item["from_table"] + ":" + ",".join(item["from_columns"]) + "->" + item["to_table"] + ":" + ",".join(item["to_columns"])

    def domain_assertions(self, candidates: Sequence[Mapping[str, Any]], catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> tuple[DomainAssertion, ...]:
        source_ids = tuple(sorted(catalogs))
        snapshot_ids = tuple(snapshots[key].snapshot.snapshot_id for key in source_ids)
        return tuple(
            DomainAssertion(
                assertion_id=f"prompt02:{item['candidate_id']}",
                subject_id=self._candidate_subject(item),
                statement="the source-role relationship is declared for review by the versioned order policy",
                status="ACTIVE",
                source_ids=source_ids,
                snapshot_ids=snapshot_ids,
                scope_id=stable_id("prompt02-domain-scope", item["candidate_id"]),
                asserted_by=f"product-policy:{self.version}",
                evidence_refs=(self.provenance, item["candidate_id"]),
            )
            for item in candidates
        )

    def entity_resolution_spec(self, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> EntityResolutionSpec:
        fields: list[IdentityFieldSpecification] = []
        for source_id in sorted(catalogs):
            catalog = catalogs[source_id]
            if self.source_role(catalog) != "registry":
                continue
            table = self.role_table(catalog)
            for field_id, names, role, anchor in (
                ("customer_name", ("customer_name", "full_name", "buyer_name", "client_name", "account_name", "name"), "name", False),
                ("customer_email", ("email", "email_addr", "buyer_email", "customer_email", "client_email"), "email", True),
                ("customer_phone", ("phone", "phone_e164", "buyer_phone", "client_phone"), "phone", False),
            ):
                column = self.column(catalog, table.table_id, names)
                if column is None:
                    continue
                fields.append(IdentityFieldSpecification(field_id=field_id, source_id=source_id, snapshot_id=snapshots[source_id].snapshot.snapshot_id, table_id=table.table_id, column_id=column.column_id, physical_name=column.physical_name, semantic_role=role, normalization_rule_id="identity-normalization-v1", nullable=column.schema_nullable is not False, is_anchor=anchor, anchor_group="email" if anchor else None))
        if not fields or not {"customer_name", "customer_email"}.issubset({item.field_id for item in fields}):
            raise ValueError("the selected customer registries must expose name and email identity fields")
        source_ids = tuple(sorted(catalogs))
        return EntityResolutionSpec(
            spec_id=stable_id("er-spec", {"run_sources": source_ids, "snapshots": {key: snapshots[key].snapshot.snapshot_id for key in source_ids}, "fields": [item.model_dump(mode="json") for item in fields]}),
            entity_family="customer", mode=EntityResolutionMode.LINK_ONLY, source_ids=source_ids,
            snapshot_ids={key: snapshots[key].snapshot.snapshot_id for key in source_ids},
            table_ids_by_source={source_id: (self.role_table(catalogs[source_id]).table_id,) for source_id in source_ids},
            identity_fields=tuple(fields),
            normalization_rules=(EntityResolutionNormalizationRule(rule_id="identity-normalization-v1", version="1", applies_to=("customer_name", "customer_email", "customer_phone")),),
            blocking_rules=(ERBlockingRule(rule_id="block-customer-name", version="1", field_ids=("customer_name",), sql_expression="l.customer_name = r.customer_name"), ERBlockingRule(rule_id="block-customer-email", version="1", field_ids=("customer_email",), sql_expression="l.customer_email = r.customer_email"), ERBlockingRule(rule_id="block-customer-phone", version="1", field_ids=("customer_phone",), sql_expression="l.customer_phone = r.customer_phone")),
            comparisons=(ERComparisonSpecification(comparison_id="compare-customer-name", field_id="customer_name", method="exact"), ERComparisonSpecification(comparison_id="compare-customer-email", field_id="customer_email", method="exact"), ERComparisonSpecification(comparison_id="compare-customer-phone", field_id="customer_phone", method="exact")),
            training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-customer-name", "block-customer-email"), max_u_pairs=10_000, max_em_iterations=10),
            threshold_policy=ERThresholdPolicy(policy_id="prompt02-er-threshold-v1", match_probability_threshold=0.95, review_probability_threshold=0.80, match_weight_threshold=-5.0, require_independent_evidence=True),
            clustering_policy=ERClusteringPolicy(threshold_policy_id="prompt02-er-threshold-v1", include_review_edges=False),
            execution_budget=ERExecutionBudget(max_records=100_000, max_candidate_pairs=100_000, max_all_pairs_diagnostic=1_000_000, max_runtime_seconds=300),
        )

    def entity_resolution_requirements(self, entity_types: Sequence[CanonicalEntityType]) -> Mapping[str, EntityResolutionRequirement]:
        return {item.entity_resolution_family or item.semantic_id: (EntityResolutionRequirement.ER_REQUIRED if item.semantic_id == "customer" else EntityResolutionRequirement.ER_NOT_REQUIRED) for item in entity_types}

    def entity_types(self, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult], relationships: Sequence[Any], domain_refs: tuple[str, ...]) -> tuple[CanonicalEntityType, ...]:
        source_tables = tuple(CanonicalSourceTable(source_id=source_id, snapshot_id=snapshots[source_id].snapshot.snapshot_id, table_id=self.role_table(catalogs[source_id]).table_id, schema_fingerprint=catalogs[source_id].source.schema_fingerprint) for source_id in sorted(catalogs))
        relationship_refs = tuple(item.decision_id for item in relationships)
        return (
            CanonicalEntityType(canonical_entity_type_id="entity_customer", semantic_id="customer", business_name="Customer", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="customer", identity_strategy="REVIEWED_SPLINK_LINKAGE", identity_attribute_ids=("customer_name", "customer_email"), source_table_refs=tuple(item for item in source_tables if self.source_role(catalogs[item.source_id]) == "registry"), canonical_attribute_ids=("customer_name", "customer_email"), relationship_refs=relationship_refs, domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.provenance,)),
            CanonicalEntityType(canonical_entity_type_id="entity_order", semantic_id="order", business_name="Order event", kind=CanonicalEntityKind.EVENT, entity_resolution_family="order_event", identity_strategy="SOURCE_LOCAL_EVENT_KEY", identity_attribute_ids=("order_id",), source_table_refs=tuple(item for item in source_tables if self.source_role(catalogs[item.source_id]) == "event"), canonical_attribute_ids=("order_id",), relationship_refs=relationship_refs, domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.provenance,)),
            CanonicalEntityType(canonical_entity_type_id="entity_source", semantic_id="source", business_name="Source system", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="source_system", identity_strategy="REGISTERED_SOURCE_ID", identity_attribute_ids=("source_id",), source_table_refs=(), canonical_attribute_ids=("source_id",), relationship_refs=relationship_refs, domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.provenance,)),
        )

    def canonical_relationships(self, relationships: Sequence[Any], review_decision_id_by_subject: Mapping[str, str]) -> tuple[CanonicalRelationship, ...]:
        output = []
        for item in relationships:
            review_ref = review_decision_id_by_subject.get(item.decision_id)
            if review_ref is None:
                raise ValueError(f"order relationship {item.decision_id} lacks an accepted evidence review")
            output.append(CanonicalRelationship(relationship_id=item.decision_id, from_entity_type_id="entity_order", to_entity_type_id="entity_source" if "source_id" in item.from_columns else "entity_customer", cardinality=item.proposed_cardinality, upstream_decision_ref=item.decision_id, review_decision_ref=review_ref, conflict_refs=tuple(item.conflict_refs), provenance_refs=(item.decision_id, self.provenance)))
        return tuple(output)

    def identity_memberships(self, *, hypothesis, snapshots: Mapping[str, SourceSnapshotResult], catalogs: Mapping[str, SourceCatalog], policy_ref: str, entity_resolution_result: Any | None = None, entity_resolution_ref: str | None = None, domain_assertions: Sequence[Any] = ()) -> tuple[CanonicalIdentityMembership, ...]:
        if entity_resolution_result is None:
            raise ValueError("order identity memberships require the authorized entity-resolution result")
        if not entity_resolution_ref:
            raise ValueError("order identity memberships require the persisted entity-resolution artifact reference")
        assertion_refs = tuple(item.assertion_id for item in domain_assertions if item.assertion_id in set(hypothesis.domain_assertion_refs))
        if not assertion_refs:
            raise ValueError("order identity memberships require durable domain assertions")
        memberships = []
        for cluster in entity_resolution_result.clusters:
            if cluster.decision == "CANDIDATE_CLUSTER":
                if len(cluster.record_refs) == 1:
                    memberships.append(CanonicalIdentityMembership(membership_group_id=cluster.cluster_id, canonical_entity_type_id="entity_customer", entity_resolution_family="customer", source_record_refs=cluster.record_refs, derivation_basis=IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW, actor=domain_assertions[0].asserted_by, actor_source="DOMAIN_ASSERTION", domain_assertion_refs=assertion_refs, cluster_evidence_refs=cluster.diagnostic_refs, evidence_refs=cluster.diagnostic_refs, policy_refs=(policy_ref,), rationale="no authorized linkage edge was observed; the registry row remains a reviewed singleton customer identity", provenance_refs=(hypothesis.artifact_id, entity_resolution_ref)))
                else:
                    memberships.append(CanonicalIdentityMembership(membership_group_id=cluster.cluster_id, canonical_entity_type_id="entity_customer", entity_resolution_family="customer", source_record_refs=cluster.record_refs, derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE, authorized_edge_refs=cluster.edge_refs, cluster_evidence_refs=cluster.diagnostic_refs, evidence_refs=tuple(sorted(set(cluster.edge_refs) | set(cluster.diagnostic_refs))), policy_refs=(policy_ref,), rationale="customer membership is derived from the authorized entity-resolution candidate cluster and remains review-gated", provenance_refs=(hypothesis.artifact_id, entity_resolution_ref)))
        for source_id, catalog in sorted(catalogs.items()):
            if self.source_role(catalog) != "event":
                continue
            for row in snapshots[source_id].record_references:
                memberships.append(CanonicalIdentityMembership(membership_group_id=stable_id("event-membership", {"source": source_id, "record": row.record_ref}), canonical_entity_type_id="entity_order", entity_resolution_family="order_event", source_record_refs=(row.record_ref,), derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY, evidence_refs=(row.record_ref, snapshots[source_id].snapshot.snapshot_id), policy_refs=(policy_ref,), rationale="event identity is the source-local event key; no cross-source identity is inferred", provenance_refs=(hypothesis.artifact_id, snapshots[source_id].snapshot.snapshot_id)))
        return tuple(memberships)

    def validate_identity_memberships(self, memberships: Sequence[CanonicalIdentityMembership]) -> None:
        for membership in memberships:
            if len(membership.source_record_refs) > 1 and (membership.derivation_basis is not IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE or not membership.authorized_edge_refs):
                raise ValueError("order multi-record identity membership requires authorized entity-resolution evidence")

    @staticmethod
    def _typed_value(value: Any, logical_type: str) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        if logical_type == "DATE":
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        if logical_type == "INTEGER":
            numeric = Decimal(str(value))
            if numeric != numeric.to_integral_value():
                raise ValueError(f"non-integral value for INTEGER field: {value!r}")
            return int(numeric)
        if logical_type == "DECIMAL":
            return value if isinstance(value, Decimal) else Decimal(str(value))
        return str(value) if logical_type == "STRING" else value

    def build_multi_source_dataset_and_request(self, *, run_id: str, canonical: CanonicalModel, service, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult], relationships: Sequence[Any]):
        maps = {item.record_ref: item for item in canonical.source_record_maps}
        customer_rows: dict[str, dict[str, Any]] = {}
        event_rows: list[dict[str, Any]] = []
        registry_keys: dict[str, str] = {}
        source_record_refs_by_source: dict[str, list[str]] = {}
        for source_id, catalog in sorted(catalogs.items()):
            role, table, snapshot = self.source_role(catalog), self.role_table(catalog), snapshots[source_id]
            source_record_refs_by_source[source_id] = []
            for row in service.read_rows(catalog, snapshot):
                values, record_ref, mapping = row["values"], row["record_ref"], maps.get(row["record_ref"])
                if mapping is None:
                    raise ValueError(f"canonical model does not map source record {record_ref}")
                source_record_refs_by_source[source_id].append(record_ref)
                if role == "registry":
                    customer_value = self.logical_value(catalog, table.table_id, values, "customer_id")
                    if customer_value is not None:
                        registry_keys[str(customer_value)] = mapping.canonical_entity_id
                        current = customer_rows.setdefault(mapping.canonical_entity_id, {"canonical_reference": mapping.canonical_entity_id, "customer_id": str(customer_value), "customer_name": self.logical_value(catalog, table.table_id, values, "customer_name"), "email": self.logical_value(catalog, table.table_id, values, "customer_email"), "phone": self.logical_value(catalog, table.table_id, values, "customer_phone"), "source_count": 0, "source_record_refs": ()})
                        current["source_count"] += 1
                        current["source_record_refs"] = tuple((*current["source_record_refs"], record_ref))
                else:
                    order_value = self.logical_value(catalog, table.table_id, values, "order_id")
                    customer_value = self.logical_value(catalog, table.table_id, values, "customer_id_ref")
                    event_rows.append({"canonical_reference": mapping.canonical_entity_id, "order_id": None if order_value is None else str(order_value), "customer_id": None if customer_value is None else str(customer_value), "canonical_customer_id": None if customer_value is None else registry_keys.get(str(customer_value)), "order_date": self.logical_value(catalog, table.table_id, values, "order_date"), "source_id": source_id, "quantity": self.logical_value(catalog, table.table_id, values, "quantity"), "unit_price": self.logical_value(catalog, table.table_id, values, "unit_price"), "source_record_refs": (record_ref,)})
        for row in event_rows:
            if row["customer_id"] is not None:
                row["canonical_customer_id"] = registry_keys.get(row["customer_id"])
        source_refs = tuple(sorted(catalogs))
        source_rows = [{"canonical_reference": source_id, "source_id": source_id, "source_role": self.source_role(catalogs[source_id]), "source_record_refs": tuple(source_record_refs_by_source[source_id])} for source_id in source_refs]

        def make_table(table_id, concept, rows, columns):
            bindings = tuple(AnalyticalColumnBinding(column_id=stable_id("analytical-column", {"table": table_id, "column": name}), column_name=name, logical_type=logical, nullable=nullable, lineage_refs=source_refs) for name, logical, nullable in columns)
            analytical_rows = tuple(AnalyticalInputRow(row_ref=stable_id("ainput-row", {"run": run_id, "table": table_id, "index": index, "reference": row["canonical_reference"]}), canonical_reference=str(row["canonical_reference"]), values=tuple(AnalyticalCell(column_name=name, value=self._typed_value(row.get(name), logical)) for name, logical, _nullable in columns), source_record_refs=tuple(row["source_record_refs"]), lineage_refs=source_refs) for index, row in enumerate(rows))
            batch = AnalyticalRowBatch(batch_id=stable_id("analytical-batch", {"run": run_id, "table": table_id}), table_id=table_id, rows=analytical_rows, source_batch_refs=source_refs, source_snapshot_fingerprints={key: snapshots[key].snapshot.source_fingerprint or snapshots[key].snapshot.schema_fingerprint for key in snapshots}, lineage_refs=source_refs)
            return AnalyticalInputTable(table_id=table_id, canonical_concept_ref=concept, columns=bindings, batches=(batch,), source_table_refs=source_refs, lineage_refs=source_refs)

        customer_table = make_table("customer_input", "customer", tuple(customer_rows.values()), (("customer_id", "STRING", False), ("customer_name", "STRING", False), ("email", "STRING", True), ("phone", "STRING", True), ("source_count", "INTEGER", False)))
        source_table = make_table("source_input", "source", tuple(source_rows), (("source_id", "STRING", False), ("source_role", "STRING", False)))
        event_table = make_table("event_input", "order", tuple(event_rows), (("order_id", "STRING", False), ("customer_id", "STRING", True), ("canonical_customer_id", "STRING", True), ("order_date", "DATE", False), ("source_id", "STRING", False), ("quantity", "INTEGER", False), ("unit_price", "DECIMAL", True)))
        dataset = AnalyticalInputDataset(dataset_id=stable_id("analytical-dataset", {"run": run_id, "canonical": canonical.model_id, "rows": [row.row_ref for table in (customer_table, source_table, event_table) for row in table.rows]}), canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, tables=(customer_table, source_table, event_table), source_schema_fingerprints={key: catalogs[key].source.schema_fingerprint for key in catalogs}, source_snapshot_fingerprints={key: snapshots[key].snapshot.source_fingerprint or snapshots[key].snapshot.schema_fingerprint for key in snapshots}, allow_literal_sql=False, provenance_refs=(run_id, canonical.model_id, *source_refs, self.provenance))
        binding = AnalyticalInputBinding(binding_id=stable_id("input-binding", {"run": run_id, "dataset": dataset.dataset_id}), canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, dataset_id=dataset.dataset_id, dataset_content_hash=dataset.content_hash, source_schema_fingerprints=dict(dataset.source_schema_fingerprints), source_snapshot_fingerprints=dict(dataset.source_snapshot_fingerprints), row_counts=dataset.row_counts, provenance_refs=(dataset.dataset_id, canonical.model_id, self.provenance))
        customer_rel = next(item for item in relationships if "source_id" not in item.from_columns)
        source_rel = next(item for item in relationships if "source_id" in item.from_columns)
        lineage = (canonical.model_id, run_id, *source_refs, self.provenance)
        customer_dimension = DimensionSpec(dimension_id="dim_customer", table_name="dim_customer", input_table_id="customer_input", canonical_entity_type_id="entity_customer", canonical_entity_refs=tuple(sorted(customer_rows)), role=DimensionRole.CONFORMED, eligibility_reason="reviewed cross-source customer registry identity", surrogate_key=WarehouseKeySpec(key_name="customer_key", namespace="prompt02.customer"), alternate_key_columns=("customer_id",), attributes=(DimensionAttributeSpec(attribute_id="customer_id", column_name="customer_id", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="customer_name", column_name="customer_name", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="customer_email", column_name="email", input_column_name="email", logical_type="STRING", nullable=True, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="customer_phone", column_name="phone", input_column_name="phone", logical_type="STRING", nullable=True, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="pinned source snapshots provide no historical validity contract"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unresolved customer references are quarantined"), provenance_refs=lineage)
        source_dimension = DimensionSpec(dimension_id="dim_source", table_name="dim_source", input_table_id="source_input", canonical_entity_type_id="entity_source", canonical_entity_refs=source_refs, role=DimensionRole.CONFORMED, eligibility_reason="registered source identity is explicit and stable for lineage", surrogate_key=WarehouseKeySpec(key_name="source_key", namespace="prompt02.source"), alternate_key_columns=("source_id",), attributes=(DimensionAttributeSpec(attribute_id="source_id", column_name="source_id", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="source_role", column_name="source_role", logical_type="STRING", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="registered source roles are immutable during a run"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unregistered source IDs cannot be materialized"), provenance_refs=lineage)
        date_dimension = DimensionSpec(dimension_id="dim_date", table_name="dim_date", canonical_entity_type_id="cet_date", canonical_entity_refs=("date",), role=DimensionRole.DATE, eligibility_reason="Gregorian date is deterministically generated from observed events", surrogate_key=WarehouseKeySpec(key_name="date_key", namespace="prompt02.date"), alternate_key_columns=("full_date",), attributes=(DimensionAttributeSpec(attribute_id="date_full", column_name="full_date", logical_type="DATE", derivation="FULL_DATE", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_year", column_name="year", logical_type="INTEGER", derivation="YEAR", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_month", column_name="month", logical_type="INTEGER", derivation="MONTH", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_day", column_name="day", logical_type="INTEGER", derivation="DAY", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="calendar has no historical validity contract"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER, rationale="date role has an explicit unknown member", unknown_member_key=-1), calendar_policy="GREGORIAN_V1", provenance_refs=lineage)
        time_rel = stable_id("time_role", {"run": run_id})
        fact = FactSpec(fact_id="fact_order", table_name="fact_order", input_table_id="event_input", fact_type=FactType.TRANSACTION, canonical_event_type_id="entity_order", canonical_event_refs=tuple(row.canonical_reference for row in event_table.rows), grain_spec_id="grain_order", dimension_foreign_keys=(FactForeignKeySpec(relationship_ref=customer_rel.decision_id, dimension_id="dim_customer", fact_column="customer_key", dimension_key_column="customer_key", canonical_entity_type_id="entity_customer", input_reference_column="canonical_customer_id"), FactForeignKeySpec(relationship_ref=source_rel.decision_id, dimension_id="dim_source", fact_column="source_key", dimension_key_column="source_key", canonical_entity_type_id="entity_source", input_reference_column="source_id"), FactForeignKeySpec(relationship_ref=time_rel, relationship_scope=FactRelationshipScope.ANALYTICAL_TIME_ROLE, dimension_id="dim_date", fact_column="date_key", dimension_key_column="date_key", canonical_entity_type_id="cet_date", input_reference_column="order_date")), degenerate_dimension_columns=("source_id", "customer_id"), measure_ids=("measure_quantity",), date_role_columns=("order_date",), relationship_refs=(customer_rel.decision_id, source_rel.decision_id, time_rel), provenance_refs=lineage)
        grain = GrainSpec(grain_id="grain_order", fact_id="fact_order", human_readable_grain="one row per source and source-local order key", key_columns=("source_id", "order_id"), null_policy=GrainNullPolicy.REJECT_NULLS, observed_row_count=0, duplicate_key_count=0, evidence_refs=source_refs, provenance_refs=lineage)
        measure = MeasureSpec(measure_id="measure_quantity", fact_id=fact.fact_id, field_name="quantity", semantic_name="Order quantity", aggregation_class=AggregationClass.ADDITIVE, aggregation_rule="SUM(quantity)", unit_semantics="source quantity units", currency_semantics="not applicable; unit price is retained as a non-aggregated attribute", logical_type="INTEGER", nullable=False, domain_assertion_refs=("prompt02:quantity",), provenance_refs=lineage)
        planning = AnalyticalPlanningRequest(request_id=stable_id("analytical-request", {"run": run_id, "canonical": canonical.model_id}), dimensions=(customer_dimension, date_dimension, source_dimension), facts=(fact,), grains=(grain,), measures=(measure,), accepted_relationship_refs=(customer_rel.decision_id, source_rel.decision_id), domain_assertion_refs=("prompt02:source-roles", "prompt02:quantity"), provenance_refs=lineage)
        return dataset, binding, planning

    def source_records(self, *, service, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> tuple[Mapping[str, Any], ...]:
        records = []
        for source_id, catalog in sorted(catalogs.items()):
            role = self.source_role(catalog)
            table = self.role_table(catalog)
            snapshot = snapshots[source_id]
            ordinals = {item.record_ref: item.extraction_ordinal for item in snapshot.record_references}
            for row in service.read_rows(catalog, snapshot):
                values = row["values"]
                records.append({"source_id": source_id, "snapshot_id": snapshot.snapshot.snapshot_id, "table_id": table.table_id, "record_ref": row["record_ref"], "role": role, "extraction_ordinal": ordinals.get(row["record_ref"], 0), "logical_values": {"customer_id": self.logical_value(catalog, table.table_id, values, "customer_id" if role == "registry" else "customer_id_ref"), "customer_name": self.logical_value(catalog, table.table_id, values, "customer_name"), "customer_email": self.logical_value(catalog, table.table_id, values, "customer_email"), "customer_phone": self.logical_value(catalog, table.table_id, values, "customer_phone"), "order_id": self.logical_value(catalog, table.table_id, values, "order_id"), "order_date": self.logical_value(catalog, table.table_id, values, "order_date"), "quantity": self.logical_value(catalog, table.table_id, values, "quantity"), "unit_price": self.logical_value(catalog, table.table_id, values, "unit_price")}})
        return tuple(records)

    def record_accounting_ref(self, run_id: str) -> str:
        return stable_id("prompt02-accounting", run_id)

    @classmethod
    def logical_value(cls, catalog: SourceCatalog, table_id: str, values: Mapping[str, Any], logical_name: str) -> Any:
        aliases = {
            "customer_id": ("customer_id", "crm_customer_id", "account_no", "account_id", "customer_code", "buyer_ref", "client_code", "customer_ref"),
            "customer_id_ref": ("customer_id_ref", "customer_id", "crm_customer_id", "account_no", "account_id", "customer_code", "buyer_ref", "client_code", "customer_ref"),
            "customer_name": ("customer_name", "full_name", "buyer_name", "client_name", "account_name", "name"),
            "customer_email": ("email", "email_addr", "buyer_email", "customer_email", "client_email"),
            "customer_phone": ("phone", "phone_e164", "buyer_phone", "client_phone"),
            "order_id": ("order_id", "ticket_id", "sale_key"),
            "order_date": ("order_date", "booked_on", "sale_day"),
            "quantity": ("quantity", "units", "qty"),
            "unit_price": ("unit_price", "price_each"),
        }
        column = cls.column(catalog, table_id, aliases[logical_name])
        return None if column is None else values.get(column.physical_name)

    def build_dataset_and_request(self, *, run_id: str, catalog: SourceCatalog, snapshot: SourceSnapshotResult, canonical, project_root: Path, table_name: str, column_types: Mapping[str, str]):
        from dirty_data_to_olap.application.product_input import build_order_dataset
        dataset, binding = build_order_dataset(run_id=run_id, catalog=catalog, snapshot=snapshot, canonical=canonical, project_root=project_root, table_name=table_name, column_types=column_types)
        return dataset, binding

    def build_truth_and_accounting(self, **kwargs):
        if "source_records" in kwargs:
            from dirty_data_to_olap.application.product_truth import build_multi_source_truth_and_accounting
            return build_multi_source_truth_and_accounting(policy=self, **kwargs)
        from dirty_data_to_olap.application.product_truth import build_order_truth_and_accounting
        return build_order_truth_and_accounting(policy=self, **kwargs)

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


class ProductPolicyRegistry:
    """Bounded resolver for explicitly selected, repository-owned policies."""

    POLICY_FILES = {
        "order": "order_v1.json",
        "telemetry": "telemetry_v1.json",
    }
    POLICY_FACTORIES = {
        "order": OrderProductPolicy,
        "telemetry": lambda path: __import__("dirty_data_to_olap.application.telemetry_policy", fromlist=["TelemetryProductPolicy"]).TelemetryProductPolicy(path),
    }

    def __init__(self, root: Path, *, additional_policies: Mapping[str, ProductDomainPolicy] | None = None) -> None:
        self.root = Path(root).resolve()
        self.additional_policies = dict(additional_policies or {})

    def resolve(self, *, product_id: str, version: str, fingerprint: str | None = None):
        additional = self.additional_policies.get(product_id)
        if additional is not None:
            if additional.version != version:
                raise ValueError(f"product policy version is not registered: {product_id}:{version}")
            if fingerprint is not None and fingerprint != additional.content_fingerprint:
                raise ValueError(f"product policy fingerprint is incompatible with {product_id}:{version}")
            return additional
        if product_id not in self.POLICY_FILES:
            raise ValueError(f"unknown product policy: {product_id}")
        path = self.root / "config" / "product" / self.POLICY_FILES[product_id]
        if not path.is_file():
            raise ValueError(f"registered product policy is unavailable: {product_id}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if fingerprint is not None and fingerprint != actual:
            raise ValueError(f"product policy fingerprint is incompatible with {product_id}:{version}")
        policy = self.POLICY_FACTORIES[product_id](path)
        if policy.version != version:
            raise ValueError(f"product policy version is not registered: {product_id}:{version}")
        if policy.content_fingerprint != actual:
            raise ValueError("product policy fingerprint could not be reproduced")
        return policy


__all__ = ["OrderProductPolicy", "ProductPolicyRegistry"]
