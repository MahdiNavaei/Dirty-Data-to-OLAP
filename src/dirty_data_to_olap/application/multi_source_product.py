"""Prompt02 multi-source domain/provider helpers.

The accepted execution lifecycle lives in ``multi_source_runtime`` and the
shared product runtime.  This module contains only reusable source-role,
provider, and entity-resolution preparation helpers; it is not an execution
runner, review store, receipt authority, or materializer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.matching import ValentineSchemaMatchingAdapter
from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.entity_resolution import EntityResolutionService
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
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
    def __init__(self, *, project_root: Path, registry, adapters: Mapping[str, Any], graph_root: Path | None = None, product_policy=None) -> None:
        if product_policy is None:
            raise ValueError("MultiSourceProductService requires an explicitly bound domain policy")
        self.project_root = Path(project_root).resolve()
        self.registry = registry
        self.adapters = dict(adapters)
        self.graph_root = Path(graph_root or self.project_root).resolve()
        self.privacy = PrivacyPolicyService(project_root=self.project_root)
        self.discovery = SourceDiscoveryService(registry, self.adapters)
        self.snapshot = SourceSnapshotService(registry, self.adapters)
        self.policy = product_policy
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

    def source_role(self, catalog: SourceCatalog) -> str:
        """Resolve the role through the explicitly bound domain policy."""
        return self.policy.source_role(catalog)

    def role_table(self, catalog: SourceCatalog):
        return self.policy.role_table(catalog)

    @staticmethod
    def column(catalog: SourceCatalog, table_id: str, names: Sequence[str]):
        wanted = {name.casefold() for name in names}
        return next((item for item in catalog.columns if item.table_id == table_id and item.physical_name.casefold() in wanted), None)

    def logical_value(self, catalog: SourceCatalog, table_id: str, values: Mapping[str, Any], logical_name: str) -> Any:
        return self.policy.logical_value(catalog, table_id, values, logical_name)

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
        spec = self.policy.entity_resolution_spec(catalogs, snapshots)
        if spec is None:
            raise MultiSourceProductBlocked("ER_NOT_APPLICABLE", "the selected product policy does not require entity resolution")
        return spec

    def quality_request(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, profile: Any, run_id: str) -> QualityRequest:
        """Create a role-aware quality request without weakening the quality service."""
        return self.policy.quality_request(catalog, snapshot, profile, run_id)


__all__ = ["MultiSourceProductBlocked", "MultiSourceProductService"]
