"""Prompt02 multi-source domain/provider helpers.

The accepted execution lifecycle lives in ``multi_source_runtime`` and the
shared product runtime.  This module contains only reusable source-role,
provider, and entity-resolution preparation helpers; it is not an execution
runner, review store, receipt authority, or materializer.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter
from dirty_data_to_olap.adapters.matching import ValentineSchemaMatchingAdapter
from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.entity_resolution import EntityResolutionService
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.product_policy import OrderProductPolicy
from dirty_data_to_olap.application.profiling import ProfilingService
from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.application.schema_matching import SchemaMatchingService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
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
from dirty_data_to_olap.domain.contracts.quality import (
    QualityRequest,
    QualityRule,
    QualityRuleScope,
    QualityRuleSet,
    QualityRuleType,
    QualitySeverity,
    Repairability,
    quality_profile_fingerprint,
)
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, stable_id


class MultiSourceProductBlocked(RuntimeError):
    """A source-role or provider prerequisite cannot be satisfied."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class MultiSourceProductService:
    """Reusable Prompt02 provider/domain services, without run orchestration."""

    VERSION = "prompt02-multi-source-v2"
    LOGICAL_COLUMN_ALIASES = {
        "order_id": ("order_id", "ticket_id", "sale_key"),
        "customer_id": ("customer_id", "crm_customer_id", "account_no", "account_id", "customer_code", "buyer_ref", "client_code", "customer_ref"),
        "customer_id_ref": ("customer_id_ref", "customer_id", "crm_customer_id", "account_no", "account_id", "customer_code", "buyer_ref", "client_code", "customer_ref"),
        "customer_name": ("customer_name", "full_name", "buyer_name", "client_name", "account_name", "name"),
        "customer_email": ("email", "email_addr", "buyer_email", "customer_email", "client_email"),
        "customer_phone": ("phone", "phone_e164", "buyer_phone", "client_phone"),
        "order_date": ("order_date", "booked_on", "sale_day"),
        "quantity": ("quantity", "units", "qty"),
        "unit_price": ("unit_price", "price_each"),
    }

    def __init__(self, *, project_root: Path, registry, adapters: Mapping[str, Any], graph_root: Path | None = None, product_policy=None) -> None:
        self.project_root = Path(project_root).resolve()
        self.registry = registry
        self.adapters = dict(adapters)
        self.graph_root = Path(graph_root or self.project_root).resolve()
        self.privacy = PrivacyPolicyService(project_root=self.project_root)
        self.discovery = SourceDiscoveryService(registry, self.adapters)
        self.snapshot = SourceSnapshotService(registry, self.adapters)
        self.policy = product_policy or OrderProductPolicy.load(self.graph_root)
        self.profiling = ProfilingService(DataProfilerAdapter(), project_root=self.project_root)
        self.quality = QualityAnalysisService(ParquetQualityStagedReader(), project_root=self.project_root)
        self.dependency = DependencyDiscoveryService(
            DesbordanteDependencyAdapter(project_root=self.project_root, privacy_policy=self.privacy),
            project_root=self.project_root,
            privacy_policy=self.privacy,
        )
        self.matching = SchemaMatchingService(
            ValentineSchemaMatchingAdapter(project_root=self.project_root, privacy_policy=self.privacy),
            project_root=self.project_root,
            privacy_policy=self.privacy,
        )
        self.entity_resolution = EntityResolutionService(project_root=self.project_root, privacy_policy=self.privacy)
        self.fusion = EvidenceFusionService(policy_root=self.graph_root / "policies" / "evidence-fusion")
        self.hypotheses = CanonicalHypothesisService()
        self.identity_proposals = CanonicalIdentityProposalService()
        self.finalization = CanonicalFinalizationService()

    def order_table(self, catalog: SourceCatalog):
        if self.source_role(catalog) == "registry":
            raise MultiSourceProductBlocked("EVENT_TABLE_MISSING", f"source {catalog.source_id} is a customer registry, not an event source")
        for table in catalog.tables:
            names = {column.physical_name.casefold() for column in catalog.columns if column.table_id == table.table_id}
            if names.intersection(self.LOGICAL_COLUMN_ALIASES["order_id"]):
                return table
        raise MultiSourceProductBlocked("EVENT_TABLE_MISSING", f"source {catalog.source_id} has no event-key-bearing table")

    def source_role(self, catalog: SourceCatalog) -> str:
        """Resolve the role through the explicitly bound domain policy."""

        return self.policy.source_role(catalog)

    def identity_table(self, catalog: SourceCatalog):
        if self.source_role(catalog) != "registry":
            raise MultiSourceProductBlocked("IDENTITY_TABLE_MISSING", f"source {catalog.source_id} is not a customer registry")
        for table in catalog.tables:
            if self.column(catalog, table.table_id, self.LOGICAL_COLUMN_ALIASES["customer_id"]) is not None and self.column(catalog, table.table_id, self.LOGICAL_COLUMN_ALIASES["customer_name"]) is not None:
                return table
        raise MultiSourceProductBlocked("IDENTITY_TABLE_MISSING", f"source {catalog.source_id} has no customer registry table")

    def role_table(self, catalog: SourceCatalog):
        if self.policy.product_id == "order":
            return self.identity_table(catalog) if self.source_role(catalog) == "registry" else self.order_table(catalog)
        return self.policy.role_table(catalog)

    @staticmethod
    def column(catalog: SourceCatalog, table_id: str, names: Sequence[str]):
        wanted = {name.casefold() for name in names}
        return next((item for item in catalog.columns if item.table_id == table_id and item.physical_name.casefold() in wanted), None)

    def logical_value(self, catalog: SourceCatalog, table_id: str, values: Mapping[str, Any], logical_name: str) -> Any:
        if self.policy.product_id != "order":
            return self.policy.logical_value(catalog, table_id, values, logical_name)
        column = self.column(catalog, table_id, self.LOGICAL_COLUMN_ALIASES[logical_name])
        return values.get(column.physical_name) if column is not None else None

    def read_rows(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult) -> tuple[Mapping[str, Any], ...]:
        table = self.role_table(catalog)
        columns = tuple(item.physical_name for item in catalog.columns if item.table_id == table.table_id)
        reader = ParquetQualityStagedReader()
        return tuple(
            {
                "source_id": catalog.source_id,
                "snapshot_id": snapshot.snapshot.snapshot_id,
                "table_id": table.table_id,
                "record_ref": row.record_ref,
                "values": dict(row.values),
            }
            for row in reader.iter_table(snapshot, catalog, table, columns, project_root=self.project_root)
        )

    def er_spec(self, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> EntityResolutionSpec:
        fields: list[IdentityFieldSpecification] = []
        for source_id in sorted(catalogs):
            catalog = catalogs[source_id]
            if self.source_role(catalog) != "registry":
                continue
            table = self.identity_table(catalog)
            for field_id, names, role, anchor in (
                ("customer_name", self.LOGICAL_COLUMN_ALIASES["customer_name"], "name", False),
                ("customer_email", self.LOGICAL_COLUMN_ALIASES["customer_email"], "email", True),
                ("customer_phone", self.LOGICAL_COLUMN_ALIASES["customer_phone"], "phone", False),
            ):
                column = self.column(catalog, table.table_id, names)
                if column is None:
                    continue
                fields.append(IdentityFieldSpecification(
                    field_id=field_id,
                    source_id=source_id,
                    snapshot_id=snapshots[source_id].snapshot.snapshot_id,
                    table_id=table.table_id,
                    column_id=column.column_id,
                    physical_name=column.physical_name,
                    semantic_role=role,
                    normalization_rule_id="identity-normalization-v1",
                    nullable=column.schema_nullable is not False,
                    is_anchor=anchor,
                    anchor_group="email" if anchor else None,
                ))
        if not fields or not {"customer_name", "customer_email"}.issubset({item.field_id for item in fields}):
            raise MultiSourceProductBlocked("IDENTITY_SCOPE_INCOMPLETE", "the selected customer registries must expose name and email identity fields")
        source_ids = tuple(sorted(catalogs))
        table_ids = {source_id: (self.role_table(catalogs[source_id]).table_id,) for source_id in source_ids}
        return EntityResolutionSpec(
            spec_id=stable_id("er-spec", {"run_sources": source_ids, "snapshots": {key: snapshots[key].snapshot.snapshot_id for key in source_ids}, "fields": [item.model_dump(mode="json") for item in fields]}),
            entity_family="customer",
            mode=EntityResolutionMode.LINK_ONLY,
            source_ids=source_ids,
            snapshot_ids={key: snapshots[key].snapshot.snapshot_id for key in source_ids},
            table_ids_by_source=table_ids,
            identity_fields=tuple(fields),
            normalization_rules=(EntityResolutionNormalizationRule(rule_id="identity-normalization-v1", version="1", applies_to=("customer_name", "customer_email", "customer_phone")),),
            blocking_rules=(
                ERBlockingRule(rule_id="block-customer-name", version="1", field_ids=("customer_name",), sql_expression="l.customer_name = r.customer_name"),
                ERBlockingRule(rule_id="block-customer-email", version="1", field_ids=("customer_email",), sql_expression="l.customer_email = r.customer_email"),
                ERBlockingRule(rule_id="block-customer-phone", version="1", field_ids=("customer_phone",), sql_expression="l.customer_phone = r.customer_phone"),
            ),
            comparisons=(
                ERComparisonSpecification(comparison_id="compare-customer-name", field_id="customer_name", method="exact"),
                ERComparisonSpecification(comparison_id="compare-customer-email", field_id="customer_email", method="exact"),
                ERComparisonSpecification(comparison_id="compare-customer-phone", field_id="customer_phone", method="exact"),
            ),
            training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-customer-name", "block-customer-email"), max_u_pairs=10_000, max_em_iterations=10),
            threshold_policy=ERThresholdPolicy(policy_id="prompt02-er-threshold-v1", match_probability_threshold=0.95, review_probability_threshold=0.80, match_weight_threshold=-5.0, require_independent_evidence=True),
            clustering_policy=ERClusteringPolicy(threshold_policy_id="prompt02-er-threshold-v1", include_review_edges=False),
            execution_budget=ERExecutionBudget(max_records=100_000, max_candidate_pairs=100_000, max_all_pairs_diagnostic=1_000_000, max_runtime_seconds=300),
        )

    def quality_request(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, profile: Any, run_id: str) -> QualityRequest:
        """Create a role-aware quality request without weakening the quality service."""

        if self.policy.product_id != "order":
            return self.policy.quality_request(catalog, snapshot, profile, run_id)

        table = self.role_table(catalog)
        role = self.source_role(catalog)
        settings = self.policy.data["quality"]
        aliases = self.LOGICAL_COLUMN_ALIASES
        if role == "registry":
            requested = (
                ("customer_id", "REQUIRED_VALUE"),
                ("customer_name", "REQUIRED_VALUE"),
                ("customer_email", "REQUIRED_VALUE"),
                ("customer_id", "UNIQUE_VALUES"),
                ("customer_email", "UNIQUE_VALUES"),
                (None, "EXACT_ROW_DUPLICATION"),
            )
        else:
            requested = (
                ("order_id", "REQUIRED_VALUE"),
                ("customer_id_ref", "REQUIRED_VALUE"),
                ("order_date", "REQUIRED_VALUE"),
                ("quantity", "REQUIRED_VALUE"),
                ("unit_price", "REQUIRED_VALUE"),
                ("order_id", "UNIQUE_VALUES"),
                (None, "EXACT_ROW_DUPLICATION"),
            )
        rules: list[QualityRule] = []
        for index, (logical_name, rule_type) in enumerate(requested):
            column = self.column(catalog, table.table_id, aliases[logical_name]) if logical_name else None
            if logical_name and column is None:
                continue
            column_ids = (column.column_id,) if column is not None else ()
            rules.append(QualityRule(
                rule_id=f"prompt02.{role}.{rule_type.casefold()}.{index}",
                rule_version=str(settings["version"]),
                rule_type=QualityRuleType(rule_type),
                scope=QualityRuleScope.USER_POLICY,
                entity_type="customer" if role == "registry" else "order",
                source_id=catalog.source_id,
                table_id=table.table_id,
                column_ids=column_ids,
                severity=QualitySeverity.HIGH,
                repairability=Repairability.REVIEW_REQUIRED,
                detector_config={},
                provenance=self.policy.provenance,
            ))
        rule_set = QualityRuleSet(
            rule_set_id=f"prompt02-{role}-quality",
            version=str(settings["version"]),
            rules=tuple(rules),
            provenance=self.policy.provenance,
            runtime_default=True,
        )
        return QualityRequest(
            quality_run_id=stable_id("quality-run", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "role": role}),
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            rule_set=rule_set,
            profile_result_fingerprint=quality_profile_fingerprint(profile),
            profile_refs=(profile.profile_request.profile_request_id,),
            batch_ids=tuple(item.batch_id for item in snapshot.batches),
            batch_hashes=tuple(item.content_hash for item in snapshot.batches),
            provenance=self.policy.provenance,
        )


__all__ = ["MultiSourceProductBlocked", "MultiSourceProductService"]
