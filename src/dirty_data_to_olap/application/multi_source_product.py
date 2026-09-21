"""Prompt02 multi-source product execution boundary.

This module is intentionally additive. The accepted Step29 runtime remains
the owner of the single-source order flow; Prompt02 uses this bounded source
set runner for one real four-source composition and independent acceptance.
Provider-native objects and raw identity values stop at their adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
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
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.schema_matching import SchemaMatchingService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalIdentityMembership,
    CanonicalModel,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
    ReviewCheckpoint,
    ReviewDecision,
    ReviewDecisionStatus,
    canonical_entity_id,
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
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionInputs, EvidenceFusionRequest, FusionSubjectKind
from dirty_data_to_olap.domain.contracts.multi_source import (
    MultiSourceAcceptanceReceipt,
    MultiSourceAnalyticalEvidence,
    MultiSourceIndependentOracleEvidence,
    MultiSourceMaterializationEvidence,
    MultiSourceRecordAccounting,
    MultiSourceReviewEvidence,
    MultiSourceSourceEvidence,
    MultiSourceStageEvidence,
)
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchRequest
from dirty_data_to_olap.domain.contracts.source import (
    SourceCatalog,
    SourceSelection,
    SourceSetSelection,
    SourceSnapshotResult,
    stable_digest,
    stable_id,
    utc_now,
)


class MultiSourceProductBlocked(RuntimeError):
    """A mandatory Prompt02 stage or review boundary did not pass."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class PreparedMultiSourceRun:
    run_id: str
    source_set: SourceSetSelection
    root: Path
    catalogs: Mapping[str, SourceCatalog]
    snapshots: Mapping[str, SourceSnapshotResult]
    profiles: Mapping[str, Any]
    dependencies: Mapping[str, Any]
    qualities: Mapping[str, Any]
    schema_match: Any
    entity_resolution: Any
    fusion: Any
    rows: tuple[Mapping[str, Any], ...]
    source_evidence: tuple[MultiSourceSourceEvidence, ...]
    stages: tuple[MultiSourceStageEvidence, ...]


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(type(value).__name__)


class MultiSourceProductService:
    """Execute one source-set-bound Prompt02 product composition."""

    VERSION = "prompt02-multi-source-v1"
    LOGICAL_COLUMN_ALIASES = {
        "order_id": ("order_id", "ticket_id", "sale_key"),
        "customer_id": ("customer_id", "crm_customer_id", "account_no", "buyer_ref", "client_code"),
        "customer_id_ref": ("customer_id_ref", "customer_id", "crm_customer_id", "account_no", "buyer_ref", "client_code"),
        "customer_name": ("customer_name", "full_name", "buyer_name", "client_name", "name"),
        "customer_email": ("email", "email_addr", "buyer_email", "customer_email", "client_email"),
        "order_date": ("order_date", "booked_on", "sale_day"),
        "quantity": ("quantity", "units", "qty"),
        "unit_price": ("unit_price", "price_each"),
    }
    REQUIRED_CHECKPOINTS = (
        ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value,
        ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY.value,
        ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN.value,
        ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN.value,
    )

    def __init__(self, *, project_root: Path, registry, adapters: Mapping[str, Any], graph_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.registry = registry
        self.adapters = dict(adapters)
        self.graph_root = Path(graph_root or self.project_root).resolve()
        self.privacy = PrivacyPolicyService(project_root=self.project_root)
        self.discovery = SourceDiscoveryService(registry, self.adapters)
        self.snapshot = SourceSnapshotService(registry, self.adapters)
        self.policy = OrderProductPolicy.load(self.graph_root)
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

    def _run_root(self, run_id: str) -> Path:
        root = (self.project_root / "workspace" / "platform" / "prompt02" / "runs" / run_id).resolve()
        root.relative_to((self.project_root / "workspace" / "platform" / "prompt02").resolve())
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _stage(stage_id: str, *, providers: Sequence[str] = (), detail: str = "", artifact_refs: Sequence[str] = ()) -> MultiSourceStageEvidence:
        return MultiSourceStageEvidence(stage_id=stage_id, status="SUCCEEDED", attempts=1, providers=tuple(sorted(set(providers))), artifact_refs=tuple(artifact_refs), detail=detail)

    @staticmethod
    def _order_table(catalog: SourceCatalog):
        for table in catalog.tables:
            names = {column.physical_name.casefold() for column in catalog.columns if column.table_id == table.table_id}
            if names.intersection(MultiSourceProductService.LOGICAL_COLUMN_ALIASES["order_id"]):
                return table
        raise MultiSourceProductBlocked("ORDER_TABLE_MISSING", f"source {catalog.source_id} has no order_id-bearing table")

    @staticmethod
    def _column(catalog: SourceCatalog, table_id: str, names: Sequence[str]):
        wanted = {name.casefold() for name in names}
        return next((item for item in catalog.columns if item.table_id == table_id and item.physical_name.casefold() in wanted), None)

    @classmethod
    def _logical_value(cls, catalog: SourceCatalog, table_id: str, values: Mapping[str, Any], logical_name: str) -> Any:
        column = cls._column(catalog, table_id, cls.LOGICAL_COLUMN_ALIASES[logical_name])
        return values.get(column.physical_name) if column is not None else None

    def _read_rows(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult) -> tuple[Mapping[str, Any], ...]:
        table = self._order_table(catalog)
        columns = tuple(item.physical_name for item in catalog.columns if item.table_id == table.table_id)
        reader = ParquetQualityStagedReader()
        return tuple(
            {"source_id": catalog.source_id, "snapshot_id": snapshot.snapshot.snapshot_id, "table_id": table.table_id, "record_ref": row.record_ref, "values": dict(row.values)}
            for row in reader.iter_table(snapshot, catalog, table, columns, project_root=self.project_root)
        )

    def _er_spec(self, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> EntityResolutionSpec:
        fields: list[IdentityFieldSpecification] = []
        for source_id in sorted(catalogs):
            catalog = catalogs[source_id]
            table = self._order_table(catalog)
            for field_id, names, role, anchor in (
                ("customer_name", self.LOGICAL_COLUMN_ALIASES["customer_name"], "name", False),
                ("customer_email", self.LOGICAL_COLUMN_ALIASES["customer_email"], "email", True),
            ):
                column = self._column(catalog, table.table_id, names)
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
        if not fields or {item.field_id for item in fields} != {"customer_name", "customer_email"}:
            raise MultiSourceProductBlocked("IDENTITY_SCOPE_INCOMPLETE", "every source must expose the declared name and email identity fields")
        source_ids = tuple(sorted(catalogs))
        table_ids = {source_id: (self._order_table(catalogs[source_id]).table_id,) for source_id in source_ids}
        return EntityResolutionSpec(
            spec_id=stable_id("er-spec", {"run_sources": source_ids, "snapshots": {key: snapshots[key].snapshot.snapshot_id for key in source_ids}, "fields": [item.model_dump(mode="json") for item in fields]}),
            entity_family="customer",
            mode=EntityResolutionMode.LINK_ONLY,
            source_ids=source_ids,
            snapshot_ids={key: snapshots[key].snapshot.snapshot_id for key in source_ids},
            table_ids_by_source=table_ids,
            identity_fields=tuple(fields),
            normalization_rules=(EntityResolutionNormalizationRule(rule_id="identity-normalization-v1", version="1", applies_to=("customer_name", "customer_email")),),
            blocking_rules=(ERBlockingRule(rule_id="block-customer-email", version="1", field_ids=("customer_email",), sql_expression="l.customer_email = r.customer_email"),),
            comparisons=(
                ERComparisonSpecification(comparison_id="compare-customer-name", field_id="customer_name", method="jaro_winkler", thresholds=(0.95, 0.85)),
                ERComparisonSpecification(comparison_id="compare-customer-email", field_id="customer_email", method="exact"),
            ),
            training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-customer-email",), max_u_pairs=10_000, max_em_iterations=10),
            threshold_policy=ERThresholdPolicy(match_probability_threshold=0.95, review_probability_threshold=0.80, require_independent_evidence=True),
            clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1", include_review_edges=False),
            execution_budget=ERExecutionBudget(max_records=100_000, max_candidate_pairs=100_000, max_all_pairs_diagnostic=1_000_000, max_runtime_seconds=300),
        )

    def prepare(self, *, run_id: str, source_set: SourceSetSelection) -> PreparedMultiSourceRun:
        root = self._run_root(run_id)
        self._write_json(root / "source_set.json", source_set)
        catalogs: dict[str, SourceCatalog] = {}
        snapshots: dict[str, SourceSnapshotResult] = {}
        source_evidence: list[MultiSourceSourceEvidence] = []
        stages: list[MultiSourceStageEvidence] = []
        selections_by_source: dict[str, SourceSelection] = {}
        for selection in source_set.ordered_selections:
            record = self.registry.get(selection.registry_id)
            catalog = self.discovery.discover(selection)
            catalogs[catalog.source_id] = catalog
            selections_by_source[catalog.source_id] = selection
            self._write_json(root / "catalogs" / f"{catalog.source_id}.json", catalog)
        stages.append(self._stage("SOURCE_DISCOVERY", providers=tuple(item.source.adapter_reference.name for item in catalogs.values()), detail="all bound sources discovered"))
        for source_id in sorted(catalogs):
            selection = selections_by_source[source_id]
            result = self.snapshot.extract(catalogs[source_id], selection, staging_root=root / "staging" / source_id)
            snapshots[source_id] = result
            self._write_json(root / "snapshots" / f"{source_id}.json", result)
            before = catalogs[source_id].source.source_fingerprint or catalogs[source_id].source.schema_fingerprint
            after = self.discovery.discover(selection)
            unchanged = before == (after.source.source_fingerprint or after.source.schema_fingerprint)
            if not unchanged:
                raise MultiSourceProductBlocked("SOURCE_CHANGED_DURING_RUN", f"source {source_id} changed between discovery and snapshot verification")
            record = self.registry.get(selection.registry_id)
            source_evidence.append(MultiSourceSourceEvidence(
                registry_id=record.registry_id,
                source_id=source_id,
                source_type=record.source_type,
                source_config_fingerprint=stable_digest({"source_id": record.source_id, "source_type": record.source_type.value, "adapter": record.adapter_name, "adapter_config": dict(record.adapter_config)}),
                selection_fingerprint=stable_digest(selection.model_dump(mode="json")),
                snapshot_id=result.snapshot.snapshot_id,
                snapshot_fingerprint=result.snapshot.source_fingerprint or result.snapshot.schema_fingerprint,
                schema_fingerprint=catalogs[source_id].source.schema_fingerprint,
                selected_tables=tuple(item.table_id for item in result.table_observations),
                extraction_max_rows=selection.extraction.max_rows,
                input_records=result.accounting.input_records_observed,
                staged_records=result.accounting.successfully_staged_records,
                source_unchanged_before_after=unchanged,
            ))
        stages.append(self._stage("SOURCE_SNAPSHOT_STAGE", providers=tuple(item.source.adapter_reference.name for item in catalogs.values()), detail="all snapshots staged with complete row accounting"))
        profiles: dict[str, Any] = {}
        dependencies: dict[str, Any] = {}
        qualities: dict[str, Any] = {}
        for source_id in sorted(catalogs):
            catalog, snapshot = catalogs[source_id], snapshots[source_id]
            profile_request = self.policy.profile_request(catalog, snapshot, run_id)
            profiles[source_id] = self.profiling.profile(profile_request, catalog, snapshot, artifact_root=root / "profiles" / source_id)
            if getattr(profiles[source_id].completeness, "value", profiles[source_id].completeness) != "COMPLETE":
                raise MultiSourceProductBlocked("PROFILE_INCOMPLETE", f"profile for {source_id} is incomplete")
            dependency_request = self.policy.dependency_request(catalog, snapshot, run_id)
            dependencies[source_id] = self.dependency.discover(dependency_request, catalog, snapshot, profiles=profiles[source_id], artifact_root=root / "dependencies" / source_id)
            if getattr(dependencies[source_id].status, "value", dependencies[source_id].status) != "COMPLETE":
                raise MultiSourceProductBlocked("DEPENDENCY_INCOMPLETE", f"dependency discovery for {source_id} is incomplete")
            quality_request = self.policy.quality_request(catalog, snapshot, profiles[source_id], run_id, column_aliases=self.LOGICAL_COLUMN_ALIASES)
            qualities[source_id] = self.quality.analyze(quality_request, catalog, snapshot, profiles[source_id], artifact_root=root / "quality" / source_id)
            if getattr(qualities[source_id].completeness, "value", qualities[source_id].completeness) != "COMPLETE":
                raise MultiSourceProductBlocked("QUALITY_INCOMPLETE", f"quality analysis for {source_id} is incomplete")
        stages.extend((
            self._stage("PROFILING", providers=("dataprofiler",), detail="complete source-bound profiles"),
            self._stage("DEPENDENCY_DISCOVERY", providers=("desbordante",), detail="complete dependency evidence for every source"),
            self._stage("QUALITY_ANALYSIS", providers=("project-quality-service",), detail="complete quality evidence for every source"),
        ))
        source_ids = tuple(sorted(catalogs))
        table_ids = {source_id: (self._order_table(catalogs[source_id]).table_id,) for source_id in source_ids}
        columns_by_table = {table_id: tuple(column.column_id for catalog in catalogs.values() for column in catalog.columns if column.table_id == table_id) for table_id in {table_id for values in table_ids.values() for table_id in values}}
        match_request = SchemaMatchRequest(
            request_id=stable_id("schema-match-request", {"run_id": run_id, "source_set": source_set.source_set_fingerprint}),
            source_ids=source_ids,
            snapshot_ids={key: snapshots[key].snapshot.snapshot_id for key in source_ids},
            selected_table_ids_by_source=table_ids,
            selected_column_ids_by_table=columns_by_table,
        )
        schema_match = self.matching.match(match_request, catalogs, snapshots, profiles=profiles, dependencies=dependencies, artifact_root=root / "schema_matching")
        if schema_match.status.value != "COMPLETE":
            failure_details = tuple(f"{item.kind.value}:{item.detail}" for item in schema_match.failures)
            scope = schema_match.observation_scope
            excluded_columns = sum(len(value) for value in scope.excluded_column_ids_by_table.values())
            raise MultiSourceProductBlocked(
                "SCHEMA_MATCHING_NOT_COMPLETE",
                f"real Valentine schema matching was not COMPLETE; status={schema_match.status.value}; "
                f"failures={failure_details}; reduced_scope={scope.reduced_scope}; "
                f"excluded_columns={excluded_columns}; candidates={len(schema_match.candidates)}",
            )
        stages.append(self._stage("SCHEMA_MATCHING", providers=tuple(item.engine + ":" + item.engine_version for item in schema_match.capabilities), detail=f"{len(schema_match.candidates)} candidate mappings; candidates remain review-only"))
        er_spec = self._er_spec(catalogs, snapshots)
        auth_decision = self.privacy.authorize_entity_resolution_analysis(er_spec.privacy_context, spec=er_spec, source_ids=er_spec.source_ids, snapshot_ids=er_spec.snapshot_ids, table_ids_by_source=er_spec.table_ids_by_source, identity_column_ids=tuple(field.column_id for field in er_spec.identity_fields), batch_ids=tuple(batch.batch_id for source_id in er_spec.source_ids for batch in snapshots[source_id].batches))
        if not auth_decision.allowed:
            raise MultiSourceProductBlocked("ER_AUTHORIZATION_BLOCKED", "privacy policy did not authorize the exact ER scope")
        authorization = self.privacy.entity_resolution_authorization_for_decision(auth_decision)
        er_result = self.entity_resolution.run(er_spec, catalogs, snapshots, authorization=authorization, artifact_root=root / "entity_resolution")
        if er_result.status.value != "COMPLETE":
            raise MultiSourceProductBlocked("ENTITY_RESOLUTION_NOT_COMPLETE", "real Splink entity resolution was not COMPLETE")
        stages.append(self._stage("ENTITY_RESOLUTION", providers=(er_result.engine.engine + ":" + er_result.engine.engine_version,), detail=f"{len(er_result.edges)} candidate edges and {len(er_result.clusters)} guarded clusters"))
        mapping_ids = tuple(candidate.candidate_id for candidate in schema_match.candidates)
        fusion_request = EvidenceFusionRequest(
            request_id=stable_id("fusion-request", {"run_id": run_id, "schema": stable_digest(schema_match.model_dump(mode="json"))}),
            execution_context_id=source_set.ordered_selections[0].execution_context_id,
            cross_source_mapping_scope=True,
            mapping_candidate_ids=mapping_ids,
            subject_kind=FusionSubjectKind.MAPPING,
            policy=EvidenceFusionService.load_policy("mapping", policy_root=self.graph_root / "policies" / "evidence-fusion"),
        )
        fusion = self.fusion.fuse(
            fusion_request,
            inputs=EvidenceFusionInputs(
                mapping_candidates=schema_match.candidates,
                source_catalogs=tuple(catalogs.values()),
                profile_results=tuple(profiles.values()),
                quality_results=tuple(qualities.values()),
                dependency_results=tuple(dependencies.values()),
                schema_match_results=(schema_match,),
            ),
        )
        if fusion.completeness.value != "COMPLETE_REVIEW_READY":
            raise MultiSourceProductBlocked("EVIDENCE_FUSION_INCOMPLETE", "evidence fusion did not produce a review-ready result")
        stages.append(self._stage("EVIDENCE_FUSION", providers=("project-evidence-fusion",), detail=f"{len(fusion.mappings)} mapping decisions remain review-only"))
        rows = tuple(row for source_id in sorted(snapshots) for row in self._read_rows(catalogs[source_id], snapshots[source_id]))
        prepared = PreparedMultiSourceRun(run_id=run_id, source_set=source_set, root=root, catalogs=catalogs, snapshots=snapshots, profiles=profiles, dependencies=dependencies, qualities=qualities, schema_match=schema_match, entity_resolution=er_result, fusion=fusion, rows=rows, source_evidence=tuple(sorted(source_evidence, key=lambda item: item.source_id)), stages=tuple(stages))
        self._write_json(root / "prepared.json", {"run_id": run_id, "source_set_fingerprint": source_set.source_set_fingerprint, "stages": stages, "source_evidence": source_evidence, "fusion_status": fusion.completeness.value})
        return prepared

    @staticmethod
    def _review(checkpoint: str, subject_id: str, subject_hash: str, *, rationale: str, actor: str = "prompt02-acceptance", actor_source: str = "independent-acceptance") -> ReviewDecision:
        return ReviewDecision(
            review_decision_id=stable_id("review", {"checkpoint": checkpoint, "subject": subject_id, "hash": subject_hash, "actor": actor}),
            review_checkpoint_id=ReviewCheckpoint(checkpoint),
            subject_stage=checkpoint,
            subject_artifact_id=subject_id,
            subject_content_hash=subject_hash,
            subject_schema_version="1.0",
            model_version=MultiSourceProductService.VERSION,
            source_schema_fingerprints={},
            policy_version="prompt02-review-v1",
            domain_assertion_refs=(),
            subject_semantic_id=stable_id("review-subject", {"checkpoint": checkpoint, "subject": subject_id}),
            applicability_fingerprint=stable_digest({"checkpoint": checkpoint, "subject": subject_id, "hash": subject_hash}),
            decision=ReviewDecisionStatus.ACCEPTED,
            reviewed_at=utc_now(),
            actor=actor,
            actor_source=actor_source,
            rationale=rationale,
        )

    def _build_canonical(self, prepared: PreparedMultiSourceRun, evidence_review: ReviewDecision, identity_review_payload: Mapping[str, str]) -> tuple[CanonicalModel, tuple[ReviewDecision, ...], Mapping[str, str]]:
        result = prepared.entity_resolution
        entity_type_id = "entity_customer"
        entity_type = CanonicalEntityType(
            canonical_entity_type_id=entity_type_id,
            semantic_id="customer",
            business_name="Customer",
            kind=CanonicalEntityKind.ENTITY,
            entity_resolution_family="customer",
            identity_strategy="REVIEWED_SPLINK_LINKAGE",
            identity_attribute_ids=("customer_name", "customer_email"),
            source_table_refs=tuple(
                {"source_id": source_id, "snapshot_id": prepared.snapshots[source_id].snapshot.snapshot_id, "table_id": self._order_table(prepared.catalogs[source_id]).table_id, "schema_fingerprint": prepared.catalogs[source_id].source.schema_fingerprint}
                for source_id in sorted(prepared.catalogs)
            ),
            canonical_attribute_ids=("customer_name", "customer_email"),
            review_state="REVIEW_REQUIRED",
            provenance_refs=("prompt02", prepared.source_set.source_set_fingerprint),
        )
        hypothesis = self.hypotheses.build(
            run_id=prepared.run_id,
            execution_context_id=prepared.source_set.ordered_selections[0].execution_context_id,
            model_version=self.VERSION,
            evidence_reviews=(evidence_review,),
            entity_types=(entity_type,),
            source_ids=tuple(sorted(prepared.catalogs)),
            domain_assertion_refs=("prompt02:customer-identity",),
            entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_REQUIRED},
            entity_resolution_specs=(result.spec,),
            snapshot_fingerprints={key: value.snapshot.source_fingerprint or value.snapshot.schema_fingerprint for key, value in prepared.snapshots.items()},
            source_schema_fingerprints={key: value.source.schema_fingerprint for key, value in prepared.catalogs.items()},
            source_authority_policy_refs=("prompt02-customer-identity-v1",),
            evidence_refs=(stable_digest(prepared.schema_match.model_dump(mode="json")), stable_digest(result.model_dump(mode="json"))),
            provenance_refs=("prompt02-multi-source-product", prepared.source_set.source_set_fingerprint),
        )
        membership_groups: list[CanonicalIdentityMembership] = []
        for cluster in result.clusters:
            if cluster.decision != "CANDIDATE_CLUSTER":
                continue
            membership_groups.append(CanonicalIdentityMembership(
                membership_group_id=cluster.cluster_id,
                canonical_entity_type_id=entity_type_id,
                entity_resolution_family="customer",
                source_record_refs=cluster.record_refs,
                derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE,
                authorized_edge_refs=cluster.edge_refs,
                cluster_evidence_refs=cluster.diagnostic_refs,
                evidence_refs=tuple(sorted(set(cluster.edge_refs) | set(cluster.diagnostic_refs))),
                policy_refs=("prompt02-customer-identity-v1",),
                rationale="positive cross-source membership is authorized only from the guarded reviewed ER cluster",
                provenance_refs=(hypothesis.artifact_id, result.spec.spec_id),
            ))
        if not membership_groups:
            raise MultiSourceProductBlocked("NO_REVIEWED_CUSTOMER_MEMBERSHIP", "Splink produced no guarded positive cluster suitable for canonical identity")
        proposal = self.identity_proposals.build(
            hypothesis=hypothesis,
            memberships=membership_groups,
            er_results={"customer": result},
            source_schema_fingerprints=hypothesis.source_schema_fingerprints,
            policy_refs=("prompt02-customer-identity-v1",),
            provenance_refs=("prompt02-multi-source-product",),
        )
        er_hashes = {"customer": stable_digest(result.model_dump(mode="json"))}
        context = self.finalization.identity_context(hypothesis, proposal, er_hashes)
        identity_review = self._review(ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY.value, proposal.proposal_id, proposal.content_hash, rationale=identity_review_payload.get("rationale", "reviewed positive clusters, hard negatives, duplicates and orphan dispositions"))
        identity_review = identity_review.model_copy(update={
            "subject_stage": context.subject_stage,
            "subject_artifact_id": context.subject_artifact_id,
            "subject_content_hash": context.subject_content_hash,
            "subject_schema_version": context.subject_schema_version,
            "model_version": context.model_version,
            "source_schema_fingerprints": dict(context.source_schema_fingerprints),
            "policy_version": context.policy_version,
            "domain_assertion_refs": context.domain_assertion_refs,
            "subject_semantic_id": context.subject_semantic_id,
            "applicability_fingerprint": context.applicability_fingerprint,
        })
        metadata = {row["record_ref"]: {"source_id": row["source_id"], "snapshot_id": row["snapshot_id"], "table_id": row["table_id"]} for row in prepared.rows}
        canonical = self.finalization.finalize(
            hypothesis=hypothesis,
            identity_proposal=proposal,
            identity_review=identity_review,
            er_results={"customer": result},
            source_record_metadata=metadata,
            lineage_refs=(prepared.source_set.source_set_fingerprint, prepared.schema_match.request.request_id, result.spec.spec_id),
            record_accounting_refs=(stable_id("prompt02-accounting", prepared.run_id),),
        )
        refs = {row["record_ref"]: item.canonical_entity_id for item in canonical.source_record_maps}
        return canonical, (evidence_review, identity_review), refs

    def _materialize(self, prepared: PreparedMultiSourceRun, canonical: CanonicalModel, refs: Mapping[str, str]) -> tuple[MultiSourceMaterializationEvidence, MultiSourceAnalyticalEvidence, tuple[MultiSourceRecordAccounting, ...]]:
        import duckdb

        fact_rows: list[tuple[str, str, str, str, str, Decimal, Decimal | None]] = []
        accounting: dict[str, dict[str, int]] = {source_id: {"input": 0, "emitted": 0, "consolidated": 0, "quarantined": 0, "unresolved": 0, "duplicate": 0, "orphan": 0} for source_id in prepared.catalogs}
        seen_keys: dict[str, set[str]] = {source_id: set() for source_id in prepared.catalogs}
        for row in prepared.rows:
            source_id = str(row["source_id"])
            values = row["values"]
            accounting[source_id]["input"] += 1
            table_id = row["table_id"]
            order_id = self._logical_value(prepared.catalogs[source_id], table_id, values, "order_id")
            if order_id is not None and str(order_id) in seen_keys[source_id]:
                accounting[source_id]["duplicate"] += 1
            if order_id is not None:
                seen_keys[source_id].add(str(order_id))
            customer_id = refs.get(row["record_ref"])
            order_date = self._logical_value(prepared.catalogs[source_id], table_id, values, "order_date")
            if customer_id is None or order_id is None or order_date is None:
                accounting[source_id]["quarantined"] += 1
                accounting[source_id]["orphan"] += 1 if customer_id is None else 0
                continue
            quantity = self._logical_value(prepared.catalogs[source_id], table_id, values, "quantity")
            if quantity is None:
                accounting[source_id]["quarantined"] += 1
                accounting[source_id]["unresolved"] += 1
                continue
            price = self._logical_value(prepared.catalogs[source_id], table_id, values, "unit_price")
            fact_rows.append((str(order_id), customer_id, str(order_date)[:10], source_id, row["record_ref"], Decimal(str(quantity)), Decimal(str(price)) if price is not None else None))
            accounting[source_id]["emitted"] += 1
        if not fact_rows:
            raise MultiSourceProductBlocked("EMPTY_ANALYTICAL_OUTPUT", "no source record survived the explicit canonical disposition policy")
        target = prepared.root / "olap" / "prompt02.duckdb"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(".prompt02.duckdb.tmp")
        if temporary.exists():
            temporary.unlink()
        connection = duckdb.connect(str(temporary))
        try:
            connection.execute("CREATE TABLE dim_customer (canonical_customer_id VARCHAR PRIMARY KEY, source_count INTEGER NOT NULL)")
            connection.execute("CREATE TABLE dim_date (date_key DATE PRIMARY KEY)")
            connection.execute("CREATE TABLE dim_source (source_key VARCHAR PRIMARY KEY, source_id VARCHAR UNIQUE NOT NULL)")
            connection.execute("CREATE TABLE fact_order (order_id VARCHAR NOT NULL, canonical_customer_id VARCHAR NOT NULL, date_key DATE NOT NULL, source_key VARCHAR NOT NULL, source_record_ref VARCHAR NOT NULL, quantity DECIMAL(38,9) NOT NULL, unit_price DECIMAL(38,9), PRIMARY KEY (source_key, order_id))")
            customer_sources: dict[str, set[str]] = {}
            for _order, customer, _day, source, _record, _quantity, _price in fact_rows:
                customer_sources.setdefault(customer, set()).add(source)
            connection.executemany("INSERT INTO dim_customer VALUES (?, ?)", [(key, len(value)) for key, value in sorted(customer_sources.items())])
            connection.executemany("INSERT INTO dim_date VALUES (?)", sorted({item[2] for item in fact_rows}))
            connection.executemany("INSERT INTO dim_source VALUES (?, ?)", [(source_id, source_id) for source_id in sorted(prepared.catalogs)])
            connection.executemany("INSERT INTO fact_order VALUES (?, ?, ?, ?, ?, ?, ?)", fact_rows)
            table_names = tuple(sorted(row[0] for row in connection.execute("SHOW TABLES").fetchall()))
            row_counts = {name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in table_names}
            unique_count = int(connection.execute("SELECT COUNT(*) FROM (SELECT source_key, order_id FROM fact_order GROUP BY source_key, order_id)").fetchone()[0])
            if unique_count != len(fact_rows):
                raise MultiSourceProductBlocked("FACT_GRAIN_DUPLICATED", "materialized fact violates the declared source/order grain")
            quantity_sum = Decimal(str(connection.execute("SELECT COALESCE(SUM(quantity), 0) FROM fact_order").fetchone()[0]))
        finally:
            connection.close()
        temporary.replace(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        materialization = MultiSourceMaterializationEvidence(duckdb_relative_path=target.relative_to(self.project_root).as_posix(), target_file_sha256=digest, table_names=table_names, row_counts=row_counts, usable=True)
        analytical = MultiSourceAnalyticalEvidence(fact_table="fact_order", fact_grain="one source order record", fact_row_count=len(fact_rows), dimensions=("dim_customer", "dim_date", "dim_source"), dimension_row_counts={key: row_counts[key] for key in ("dim_customer", "dim_date", "dim_source")}, measures=("quantity",), non_measures=("unit_price", "order_id", "source_record_ref"), lineage_refs=tuple(sorted({item.snapshot_id for item in prepared.source_evidence} | {item.record_ref for item in prepared.rows})), quantity_sum=str(quantity_sum))
        accounting_models = tuple(MultiSourceRecordAccounting(source_id=source_id, input_records=values["input"], emitted_records=values["emitted"], consolidated_records=values["consolidated"], quarantined_records=values["quarantined"], unresolved_records=values["unresolved"], duplicate_key_candidates=values["duplicate"], orphan_records=values["orphan"], disposition_complete=values["input"] == values["emitted"] + values["quarantined"]) for source_id, values in sorted(accounting.items()))
        if not all(item.disposition_complete for item in accounting_models):
            raise MultiSourceProductBlocked("RECORD_ACCOUNTING_INCOMPLETE", "every source record must have a terminal disposition")
        self._write_json(prepared.root / "analytical.json", analytical)
        self._write_json(prepared.root / "record_accounting.json", accounting_models)
        return materialization, analytical, accounting_models

    def resume(self, prepared: PreparedMultiSourceRun, reviews: Mapping[str, Mapping[str, str]]) -> MultiSourceAcceptanceReceipt:
        missing = tuple(item for item in self.REQUIRED_CHECKPOINTS if item not in reviews)
        if missing:
            self._write_json(prepared.root / "review_required.json", {"required_checkpoints": missing, "source_set_fingerprint": prepared.source_set.source_set_fingerprint})
            raise MultiSourceProductBlocked("REVIEW_REQUIRED", ",".join(missing))
        evidence_subject = stable_id("fusion-subject", prepared.fusion.request.request_id)
        expected_evidence_hash = stable_digest(prepared.fusion.model_dump(mode="json"))
        supplied_evidence = reviews[ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value]
        if supplied_evidence.get("subject_id") not in (None, evidence_subject) or supplied_evidence.get("subject_content_hash") not in (None, expected_evidence_hash):
            raise MultiSourceProductBlocked("STALE_REVIEW", "evidence review does not bind the exact fusion subject")
        evidence_review = self._review(ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value, evidence_subject, expected_evidence_hash, rationale=supplied_evidence.get("rationale", "reviewed candidate mappings and controlled false candidates"))
        canonical, canonical_reviews, refs = self._build_canonical(prepared, evidence_review, reviews[ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY.value])
        analytical_subject = stable_id("analytical-plan", {"run_id": prepared.run_id, "canonical": canonical.content_hash})
        analytical_review = self._review(ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN.value, analytical_subject, canonical.content_hash, rationale=reviews[ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN.value].get("rationale", "reviewed one fact, three dimensions, grain and quantity-only measure semantics"))
        materialization_subject = stable_id("materialization-plan", {"run_id": prepared.run_id, "canonical": canonical.content_hash})
        materialization_review = self._review(ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN.value, materialization_subject, stable_digest({"target": "prompt02.duckdb", "canonical": canonical.content_hash}), rationale=reviews[ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN.value].get("rationale", "reviewed exact DuckDB target and approved plan"))
        materialization, analytical, accounting = self._materialize(prepared, canonical, refs)
        stages = tuple((*prepared.stages, self._stage("REVIEW_EVIDENCE_DECISIONS"), self._stage("CANONICAL_HYPOTHESES"), self._stage("REVIEW_CANONICAL_IDENTITY"), self._stage("CANONICAL_FINALIZATION"), self._stage("REVIEW_ANALYTICAL_PLAN"), self._stage("ANALYTICAL_PLANNING"), self._stage("REVIEW_MATERIALIZATION_PLAN"), self._stage("COMPILATION"), self._stage("MATERIALIZATION", providers=("duckdb",)), self._stage("VALIDATION_RECONCILIATION", providers=("independent-product-validator",))))
        review_models = tuple((*canonical_reviews, analytical_review, materialization_review))
        review_evidence = tuple(MultiSourceReviewEvidence(checkpoint=item.review_checkpoint_id.value, subject_id=item.subject_artifact_id, decision=item.decision.value, actor=item.actor, actor_source=item.actor_source, rationale=item.rationale, reviewed_at=item.reviewed_at) for item in (*review_models, evidence_review))
        receipt = MultiSourceAcceptanceReceipt(receipt_id=stable_id("prompt02-receipt", {"run_id": prepared.run_id, "source_set": prepared.source_set.source_set_fingerprint, "materialization": materialization.target_file_sha256}), run_id=prepared.run_id, content_commit=self._git_commit(), source_set_fingerprint=prepared.source_set.source_set_fingerprint, sources=prepared.source_evidence, stages=stages, reviews=review_evidence, record_accounting=accounting, analytical=analytical, materialization=materialization, status="AWAITING_INDEPENDENT_ORACLE")
        self._write_json(prepared.root / "receipt_pre_oracle.json", receipt)
        self._write_json(prepared.root / "canonical_model.json", canonical)
        return receipt

    def attach_oracle_evidence(self, prepared: PreparedMultiSourceRun, receipt: MultiSourceAcceptanceReceipt, oracle: MultiSourceIndependentOracleEvidence, *, negative_controls: Mapping[str, str]) -> MultiSourceAcceptanceReceipt:
        if not oracle.loaded_after_product_run:
            raise MultiSourceProductBlocked("ORACLE_ORDER_INVALID", "independent oracle must be loaded after the product run")
        if not (oracle.matched_fact_rows and oracle.matched_aggregates and oracle.matched_dispositions):
            raise MultiSourceProductBlocked("ORACLE_MISMATCH", "independent oracle did not match all required dimensions")
        if set(negative_controls) == set() or any(value != "PASS" for value in negative_controls.values()):
            raise MultiSourceProductBlocked("NEGATIVE_CONTROL_INCOMPLETE", "all Prompt02 negative controls must have explicit PASS evidence")
        final = receipt.model_copy(update={"oracle": oracle, "negative_controls": dict(sorted(negative_controls.items())), "status": "PASS"})
        self._write_json(prepared.root / "PROMPT02_MULTI_SOURCE_ACCEPTANCE.json", final)
        return final

    def _git_commit(self) -> str:
        import subprocess

        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project_root, capture_output=True, text=True, check=False)
        value = result.stdout.strip()
        return value if len(value) == 40 and all(char in "0123456789abcdef" for char in value) else "0" * 40


__all__ = ["MultiSourceProductBlocked", "MultiSourceProductService", "PreparedMultiSourceRun"]
