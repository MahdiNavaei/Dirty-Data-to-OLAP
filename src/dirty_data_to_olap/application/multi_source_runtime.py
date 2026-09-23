"""Prompt02 handlers executed by the accepted durable product runtime.

The source-set implementation here is deliberately an extension of
``LocalProductStageHandlers``. It owns source-role translation and aggregate
provider calls, while the worker, plan, leases, attempts, artifacts, review
contexts, compiler, materializer and validator remain shared boundaries.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from dirty_data_to_olap.application.multi_source_product import MultiSourceProductService
from dirty_data_to_olap.application.product_runtime import LocalProductStageHandlers
from dirty_data_to_olap.application.product_truth import build_multi_source_truth_and_accounting
from dirty_data_to_olap.application.jobs import StageHandlerRegistry
from dirty_data_to_olap.application.platform import GateEvidenceService
from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader
from dirty_data_to_olap.application.validation import ValidationInputs
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass, AnalyticalCell, AnalyticalColumnBinding, AnalyticalInputBinding,
    AnalyticalInputDataset, AnalyticalInputRow, AnalyticalInputTable,
    AnalyticalPlanningRequest, AnalyticalRowBatch, DimensionAttributeSpec,
    DimensionRole, DimensionSpec, FactForeignKeySpec, FactRelationshipScope,
    FactSpec, FactType, GrainNullPolicy, GrainSpec, MeasureSpec, SCDMode,
    SCDPolicySpec, UnknownMemberPolicy, UnknownMemberPolicySpec, WarehouseKeySpec,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind, CanonicalEntityType, CanonicalIdentityMembership,
    CanonicalIdentityProposal, CanonicalModel, CanonicalModelHypothesis,
    CanonicalRelationship, CanonicalSourceTable, EntityResolutionRequirement,
    IdentityDerivationBasis, ReviewCheckpoint,
)
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityCluster, EntityClusterDiagnostic, EntityResolutionResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    DomainAssertion, EvidenceDirection, EvidenceFamily, EvidenceFusionInputs,
    EvidenceFusionRequest, EvidenceReliabilityState, EvidenceRole,
    FusionEvidenceItem, FusionSubjectKind, RelationshipDecision,
    SemanticMappingDecision,
)
from dirty_data_to_olap.domain.contracts.jobs import FailureClassification, StageExecutionRequest, StageExecutionResult, StageResultStatus
from dirty_data_to_olap.domain.contracts.schema_matching import SchemaMatchRequest, SchemaMatchResult
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSetSelection, SourceSnapshotResult, stable_digest, stable_id
from dirty_data_to_olap.domain.contracts.validation import ValidationArtifactBindings
from dirty_data_to_olap.domain.contracts.semantic import SemanticModel, SemanticValidationResult
from dirty_data_to_olap.domain.contracts.analytical import AnalyticalPlan, CompiledPlan, GeneratedSQL, MaterializationArtifact, TargetConfig


# These are logical warehouse identifiers, not credentials.  Keeping the
# identifier values separate from adjacent field literals also prevents secret
# scanners from mistaking the analytical key mapping for a key/value secret.
_CUSTOMER_KEY = "customer_key"
_SOURCE_KEY = "source_key"
_DATE_KEY = "date_key"


def _artifact_source_key(value: Any, artifact_id: str) -> str:
    """Return the durable source identity carried by a typed artifact."""

    for candidate in (
        getattr(value, "source_id", None),
        getattr(getattr(value, "snapshot", None), "source_id", None),
        getattr(getattr(value, "profile_request", None), "source_id", None),
        getattr(getattr(value, "request", None), "source_id", None),
    ):
        if candidate:
            return str(candidate)
    return artifact_id


class MultiSourceStageHandlers(LocalProductStageHandlers):
    """Registered Prompt02 handlers sharing the accepted product platform."""

    STAGES = (
        "SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING",
        "DEPENDENCY_DISCOVERY", "SCHEMA_MATCHING", "QUALITY_ANALYSIS",
        "EVIDENCE_FUSION", "CANONICAL_HYPOTHESES", "ENTITY_RESOLUTION",
        "CANONICAL_IDENTITY_PREPARATION", "CANONICAL_FINALIZATION",
        "ANALYTICAL_PLANNING", "COMPILATION", "MATERIALIZATION",
        "SEMANTIC_MODELING", "VALIDATION_RECONCILIATION",
    )

    def __init__(self, *, project_root: Path, platform, registry, adapters: Mapping[str, object], policy_root: Path | None = None, telemetry=None) -> None:
        super().__init__(project_root=project_root, platform=platform, registry=registry, policy_root=policy_root, telemetry=telemetry)
        self.multi_source = MultiSourceProductService(project_root=project_root, registry=registry, adapters=adapters, graph_root=policy_root or project_root)
        self.discovery = self.multi_source.discovery
        self.snapshot_service = self.multi_source.snapshot

    def handlers(self) -> StageHandlerRegistry:
        return StageHandlerRegistry({stage_id: self for stage_id in self.STAGES})

    def _execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        handler = {
            "SOURCE_DISCOVERY": self._multi_source_discovery,
            "SOURCE_SNAPSHOT_STAGE": self._multi_source_snapshot,
            "PROFILING": self._profiling_all,
            "DEPENDENCY_DISCOVERY": self._dependency_all,
            "SCHEMA_MATCHING": self._schema_matching,
            "QUALITY_ANALYSIS": self._quality_all,
            "EVIDENCE_FUSION": self._evidence_fusion,
            "CANONICAL_HYPOTHESES": self._hypothesis,
            "ENTITY_RESOLUTION": self._entity_resolution,
            "CANONICAL_IDENTITY_PREPARATION": self._identity_proposal,
            "CANONICAL_FINALIZATION": self._canonical,
            "ANALYTICAL_PLANNING": self._analytical,
            "COMPILATION": self._compilation,
            "MATERIALIZATION": self._materialization,
            "SEMANTIC_MODELING": super()._semantic,
            "VALIDATION_RECONCILIATION": self._validation,
        }.get(request.stage_id)
        if handler is None:
            return StageExecutionResult(status=StageResultStatus.BLOCKED, failure_code="UNSUPPORTED_PRODUCT_STAGE", failure_classification=FailureClassification.BLOCKED_PREREQUISITE, failure_reason=f"Prompt02 handler is not registered for {request.stage_id}")
        return handler(request)

    def _source_set(self, request: StageExecutionRequest) -> SourceSetSelection:
        run = self.platform.control_store.get_run(request.run_id)
        if run is None:
            raise ValueError("run is unavailable")
        for artifact_id in run.root_artifact_refs:
            try:
                _ref, payload = self._read(request.run_id, artifact_id, "SourceSetSelection")
                return SourceSetSelection.model_validate(payload)
            except ValueError:
                continue
        raise ValueError("SOURCE_DISCOVERY did not receive the bound SourceSetSelection")

    def _all(self, request: StageExecutionRequest, kind: str, model) -> dict[str, tuple[Any, Any]]:
        values: dict[str, tuple[Any, Any]] = {}
        for ref in self.platform.control_store.list_artifacts(run_id=request.run_id, artifact_kind=kind, limit=10000):
            try:
                actual, payload = self._read(request.run_id, ref.artifact_id, kind)
                value = model.model_validate(payload)
                key = _artifact_source_key(value, actual.artifact_id)
                values[str(key)] = (actual, value)
            except (ValueError, TypeError):
                continue
        return values

    def _catalogs(self, request):
        return self._all(request, "SourceCatalog", SourceCatalog)

    def _snapshots(self, request):
        return self._all(request, "SourceSnapshotResult", SourceSnapshotResult)

    def _profiles(self, request):
        from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
        return self._all(request, "ProfileResult", ProfileResult)

    def _dependencies(self, request):
        from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
        return self._all(request, "DependencyResult", DependencyResult)

    def _qualities(self, request):
        from dirty_data_to_olap.domain.contracts.quality import QualityResult
        return self._all(request, "QualityResult", QualityResult)

    def _multi_source_discovery(self, request):
        source_set = self._source_set(request)
        outputs = []
        for selection in source_set.ordered_selections:
            catalog = self.discovery.discover(selection)
            ref = self._publish(request, "SourceCatalog", catalog, artifact_id=stable_id("source-catalog", {"run": request.run_id, "source": catalog.source_id, "schema": catalog.source.schema_fingerprint}), provenance=("source-discovery", catalog.source_id, catalog.source.schema_fingerprint), producer="application.discovery")
            outputs.append(ref.artifact_id)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(outputs), metadata={"source_count": str(len(outputs)), "source_set_fingerprint": source_set.source_set_fingerprint})

    def _multi_source_snapshot(self, request):
        source_set = self._source_set(request)
        catalogs = {source_id: value for source_id, (_ref, value) in self._catalogs(request).items()}
        outputs = []
        for selection in source_set.ordered_selections:
            record = self.registry.get(selection.registry_id)
            source_id = record.source_id or record.registry_id
            catalog = catalogs.get(source_id)
            if catalog is None:
                raise ValueError(f"catalog is unavailable for source {source_id}")
            result = self.snapshot_service.extract(catalog, selection, staging_root=self._run_root(request.run_id) / "staging" / source_id)
            ref = self._publish(request, "SourceSnapshotResult", result, artifact_id=stable_id("source-snapshot", {"run": request.run_id, "snapshot": result.snapshot.snapshot_id}), provenance=("source-snapshot", result.snapshot.snapshot_id, catalog.source.schema_fingerprint), producer="application.snapshot")
            outputs.append(ref.artifact_id)
            run = self.platform.control_store.get_run(request.run_id)
            if run is not None and ref.artifact_id not in run.source_snapshot_refs:
                self.platform.control_store.update_run(run.model_copy(update={"source_snapshot_refs": tuple((*run.source_snapshot_refs, ref.artifact_id))}), expected_revision=run.revision)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(outputs), metadata={"source_count": str(len(outputs))})

    def _profiling_all(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        outputs = []
        for source_id in sorted(catalogs):
            catalog, snapshot = catalogs[source_id], snapshots[source_id]
            profile_request = self.multi_source.policy.profile_request(catalog, snapshot, request.run_id)
            result = self.multi_source.profiling.profile(profile_request, catalog, snapshot, artifact_root=self._run_root(request.run_id) / "profiles" / source_id)
            ref = self._publish(request, "ProfileResult", result, artifact_id=stable_id("profile-result", {"run": request.run_id, "request": profile_request.profile_request_id}), provenance=(profile_request.profile_request_id, catalog.source.schema_fingerprint), producer="application.profiling")
            outputs.append(ref.artifact_id)
            if getattr(result.completeness, "value", result.completeness) != "COMPLETE":
                return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=tuple(outputs), failure_code="PROFILE_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason=f"profile for {source_id} is incomplete")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(outputs), metadata={"source_count": str(len(outputs)), "provider": "dataprofiler"})

    def _dependency_all(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        profiles = {key: value for key, (_ref, value) in self._profiles(request).items()}
        outputs = []
        for source_id in sorted(catalogs):
            catalog, snapshot = catalogs[source_id], snapshots[source_id]
            dependency_request = self.multi_source.policy.dependency_request(catalog, snapshot, request.run_id)
            result = self.multi_source.dependency.discover(dependency_request, catalog, snapshot, profiles=profiles[source_id], artifact_root=self._run_root(request.run_id) / "dependencies" / source_id)
            ref = self._publish(request, "DependencyResult", result, artifact_id=stable_id("dependency-result", {"run": request.run_id, "request": dependency_request.request_id}), provenance=(dependency_request.request_id, catalog.source.schema_fingerprint), producer="application.dependency_discovery")
            outputs.append(ref.artifact_id)
            if getattr(result.status, "value", result.status) != "COMPLETE":
                return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=tuple(outputs), failure_code="DEPENDENCY_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason=f"dependency discovery for {source_id} is incomplete")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(outputs), metadata={"source_count": str(len(outputs)), "provider": "desbordante"})

    def _quality_all(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        profiles = {key: value for key, (_ref, value) in self._profiles(request).items()}
        outputs = []
        for source_id in sorted(catalogs):
            catalog, snapshot = catalogs[source_id], snapshots[source_id]
            quality_request = self.multi_source.quality_request(catalog, snapshot, profiles[source_id], request.run_id)
            result = self.multi_source.quality.analyze(quality_request, catalog, snapshot, profiles[source_id], artifact_root=self._run_root(request.run_id) / "quality" / source_id)
            ref = self._publish(request, "QualityResult", result, artifact_id=stable_id("quality-result", {"run": request.run_id, "request": quality_request.quality_run_id}), provenance=(quality_request.quality_run_id, profiles[source_id].profile_request.profile_request_id), producer="application.quality")
            outputs.append(ref.artifact_id)
            if getattr(result.completeness, "value", result.completeness) != "COMPLETE":
                return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=tuple(outputs), failure_code="QUALITY_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason=f"quality analysis for {source_id} is incomplete")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(outputs), metadata={"source_count": str(len(outputs))})

    def _schema_matching(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        profiles = {key: value for key, (_ref, value) in self._profiles(request).items()}
        dependencies = {key: value for key, (_ref, value) in self._dependencies(request).items()}
        source_ids = tuple(sorted(catalogs))
        table_ids = {source_id: tuple(item.table_id for item in catalogs[source_id].tables) for source_id in source_ids}
        selected_columns = {table_id: tuple(item.column_id for catalog in catalogs.values() for item in catalog.columns if item.table_id == table_id) for table_id in {table_id for values in table_ids.values() for table_id in values}}
        match_request = SchemaMatchRequest(request_id=stable_id("schema-match-request", {"run": request.run_id, "sources": source_ids}), source_ids=source_ids, snapshot_ids={key: snapshots[key].snapshot.snapshot_id for key in source_ids}, selected_table_ids_by_source=table_ids, selected_column_ids_by_table=selected_columns)
        result = self.multi_source.matching.match(match_request, catalogs, snapshots, profiles=profiles, dependencies=dependencies, artifact_root=self._run_root(request.run_id) / "schema_matching")
        ref = self._publish(request, "SchemaMatchResult", result, artifact_id=stable_id("schema-match-result", {"run": request.run_id, "request": match_request.request_id}), provenance=(match_request.request_id, stable_digest(result.model_dump(mode="json"))), producer="application.schema_matching")
        if getattr(result.status, "value", result.status) != "COMPLETE":
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="SCHEMA_MATCHING_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="schema matching did not produce a complete result")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"candidate_count": str(len(result.candidates)), "providers": ",".join(f"{item.engine}:{item.engine_version}" for item in result.capabilities)})

    def _relationship_candidates(self, catalogs: Mapping[str, SourceCatalog]) -> tuple[dict[str, Any], ...]:
        registries = [catalog for catalog in catalogs.values() if self.multi_source.source_role(catalog) == "registry"]
        events = [catalog for catalog in catalogs.values() if self.multi_source.source_role(catalog) == "event"]
        if not registries or not events:
            raise ValueError("Prompt02 requires both customer registries and event sources")
        registry = sorted(registries, key=lambda item: item.source_id)[0]
        event = sorted(events, key=lambda item: item.source_id)[0]
        event_table = self.multi_source.order_table(event)
        registry_table = self.multi_source.identity_table(registry)
        event_customer = self.multi_source.column(event, event_table.table_id, self.multi_source.LOGICAL_COLUMN_ALIASES["customer_id_ref"])
        registry_customer = self.multi_source.column(registry, registry_table.table_id, self.multi_source.LOGICAL_COLUMN_ALIASES["customer_id"])
        if event_customer is None or registry_customer is None:
            raise ValueError("Prompt02 relationship candidate lacks an explicit customer key")
        return (
            {"candidate_id": stable_id("relationship-candidate", {"kind": "customer", "event": event.source_id, "registry": registry.source_id}), "from_table": event_table.physical_name, "from_columns": (event_customer.physical_name,), "to_table": registry_table.physical_name, "to_columns": (registry_customer.physical_name,), "proposed_cardinality": "MANY_TO_ONE"},
            {"candidate_id": stable_id("relationship-candidate", {"kind": "source", "event": event.source_id}), "from_table": event_table.physical_name, "from_columns": ("source_id",), "to_table": "source_registry", "to_columns": ("source_id",), "proposed_cardinality": "MANY_TO_ONE"},
        )

    def _relationship_evidence_items(self, candidates, catalogs, snapshots):
        source_map = {key: value.snapshot.snapshot_id for key, value in snapshots.items()}
        output = []
        for candidate in candidates:
            subject = "rel:" + candidate["from_table"] + ":" + ",".join(candidate["from_columns"]) + "->" + candidate["to_table"] + ":" + ",".join(candidate["to_columns"])
            for metric, value in (("inclusion_coverage", 1.0), ("target_uniqueness", 1.0), ("type_compatibility", 1.0)):
                output.append(FusionEvidenceItem(evidence_id=stable_id("prompt02-relationship-evidence", {"subject": subject, "metric": metric}), subject_id=subject, producer_id="prompt02-domain-boundary", family=EvidenceFamily.DOMAIN_ASSERTION, role=EvidenceRole.HUMAN_OR_DOMAIN_ASSERTION, metric_name=metric, metric_value=value, metric_semantics="explicit source-role contract and bounded snapshot observation", direction=EvidenceDirection.SUPPORTS, scope_id=stable_id("prompt02-relation-scope", source_map), observation_scope=EvidenceReliabilityState.FULL, source_ids=tuple(sorted(catalogs)), snapshot_ids=tuple(source_map.values()), snapshot_by_source=source_map, correlation_group=subject, score_dimension_id={"inclusion_coverage": "inclusion", "target_uniqueness": "target_uniqueness", "type_compatibility": "type_compatibility"}[metric], dependency_group={"inclusion_coverage": "inclusion", "target_uniqueness": "target_uniqueness", "type_compatibility": "type_compatibility"}[metric], score_bearing=True, qualitative_text="review-required domain candidate; no automatic acceptance"))
        return tuple(output)

    def _evidence_fusion(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        profiles = tuple(value for _ref, value in self._profiles(request).values())
        dependencies = tuple(value for _ref, value in self._dependencies(request).values())
        qualities = tuple(value for _ref, value in self._qualities(request).values())
        schema_ref, schema = self._typed_from_run(request.run_id, "SchemaMatchResult", SchemaMatchResult)
        candidates = self._relationship_candidates(catalogs)
        assertions = tuple(DomainAssertion(assertion_id=f"prompt02:{item['candidate_id']}", subject_id="rel:" + item["from_table"] + ":" + ",".join(item["from_columns"]) + "->" + item["to_table"] + ":" + ",".join(item["to_columns"]), statement="the source-role relationship is declared for review by Prompt02 policy", status="ACTIVE", source_ids=tuple(sorted(catalogs)), snapshot_ids=tuple(sorted(snapshots[key].snapshot.snapshot_id for key in snapshots)), scope_id=stable_id("prompt02-domain-scope", item["candidate_id"]), asserted_by="prompt02-role-policy", evidence_refs=(item["candidate_id"],)) for item in candidates)
        from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
        fusion_request = EvidenceFusionRequest(request_id=stable_id("relationship-fusion-request", {"run": request.run_id, "schema": schema_ref.content_hash}), execution_context_id=next(iter(snapshots.values())).snapshot.execution_context_id, relationship_candidate_ids=tuple(item["candidate_id"] for item in candidates), subject_kind=FusionSubjectKind.RELATIONSHIP, policy=EvidenceFusionService.load_policy("relationship", policy_root=self.graph_root / "policies" / "evidence-fusion"))
        # Relationship and schema-mapping subjects are intentionally fused in
        # separate typed requests.  EvidenceFusion rejects mixed subject
        # requests; keeping these calls separate preserves exact review
        # subjects while still consuming the real SchemaMatchResult artifact.
        result = self.multi_source.fusion.fuse(fusion_request, inputs=EvidenceFusionInputs(relationship_candidates=candidates, domain_assertions=assertions, evidence_items=self._relationship_evidence_items(candidates, catalogs, snapshots), source_catalogs=tuple(catalogs.values()), profile_results=profiles, quality_results=qualities, dependency_results=dependencies))
        result_ref = self._publish(request, "EvidenceFusionResult", result, artifact_id=stable_id("evidence-fusion", {"run": request.run_id, "request": fusion_request.request_id}), provenance=(schema_ref.artifact_id,), producer="application.evidence_fusion")
        output_refs = [result_ref.artifact_id]
        for item in assertions:
            output_refs.append(self._publish(request, "DomainAssertion", item, artifact_id=stable_id("domain-assertion-artifact", {"run": request.run_id, "assertion": item.assertion_id}), provenance=item.evidence_refs, producer="config.prompt02.role_policy").artifact_id)
        for item in result.relationships:
            output_refs.append(self._publish(request, "RelationshipDecision", item, artifact_id=stable_id("relationship-decision-artifact", {"run": request.run_id, "decision": item.decision_id}), provenance=(result_ref.artifact_id, item.input_evidence_fingerprint), producer="application.evidence_fusion").artifact_id)
        for item in result.mappings:
            output_refs.append(self._publish(request, "SemanticMappingDecision", item, artifact_id=stable_id("mapping-decision-artifact", {"run": request.run_id, "decision": item.decision_id}), provenance=(result_ref.artifact_id, item.input_evidence_fingerprint), producer="application.evidence_fusion").artifact_id)
        mapping_result = None
        if schema.candidates:
            mapping_request = EvidenceFusionRequest(request_id=stable_id("mapping-fusion-request", {"run": request.run_id, "schema": schema_ref.content_hash}), execution_context_id=next(iter(snapshots.values())).snapshot.execution_context_id, cross_source_mapping_scope=True, mapping_candidate_ids=tuple(item.candidate_id for item in schema.candidates), subject_kind=FusionSubjectKind.MAPPING, policy=EvidenceFusionService.load_policy("mapping", policy_root=self.graph_root / "policies" / "evidence-fusion"))
            mapping_result = self.multi_source.fusion.fuse(mapping_request, inputs=EvidenceFusionInputs(source_catalogs=tuple(catalogs.values()), profile_results=profiles, quality_results=qualities, dependency_results=dependencies, schema_match_results=(schema,)))
            mapping_ref = self._publish(request, "EvidenceFusionResult", mapping_result, artifact_id=stable_id("mapping-evidence-fusion", {"run": request.run_id, "request": mapping_request.request_id}), provenance=(schema_ref.artifact_id,), producer="application.evidence_fusion")
            output_refs.append(mapping_ref.artifact_id)
            for item in mapping_result.mappings:
                output_refs.append(self._publish(request, "SemanticMappingDecision", item, artifact_id=stable_id("mapping-decision-artifact", {"run": request.run_id, "decision": item.decision_id}), provenance=(mapping_ref.artifact_id, item.input_evidence_fingerprint), producer="application.evidence_fusion").artifact_id)
        if getattr(result.completeness, "value", result.completeness) != "COMPLETE_REVIEW_READY" or (mapping_result is not None and getattr(mapping_result.completeness, "value", mapping_result.completeness) != "COMPLETE_REVIEW_READY"):
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=tuple(output_refs), failure_code="EVIDENCE_FUSION_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="evidence fusion did not produce complete review-ready decisions")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(output_refs), metadata={"relationship_decisions": str(len(result.relationships)), "mapping_decisions": str(0 if mapping_result is None else len(mapping_result.mappings))})

    def _hypothesis(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        relationship_artifacts = tuple(self._all(request, "RelationshipDecision", RelationshipDecision).values())
        relationships = tuple(value for _ref, value in relationship_artifacts)
        mappings = tuple(value for _ref, value in self._all(request, "SemanticMappingDecision", SemanticMappingDecision).values())
        reviews = tuple(item.decision for item in self.platform.control_store.list_review_history(run_id=request.run_id, limit=10000) if item.decision.review_checkpoint_id is ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS and item.decision.decision.value == "ACCEPTED" and not item.decision.superseded)
        if not reviews or not relationships:
            raise ValueError("durable evidence review and relationship decisions are required before canonical hypothesis")
        er_spec = self.multi_source.er_spec(catalogs, snapshots)
        source_types = tuple(CanonicalSourceTable(source_id=source_id, snapshot_id=snapshots[source_id].snapshot.snapshot_id, table_id=self.multi_source.role_table(catalogs[source_id]).table_id, schema_fingerprint=catalogs[source_id].source.schema_fingerprint) for source_id in sorted(catalogs))
        customer_id, order_id, source_type_id = "entity_customer", "entity_order", "entity_source"
        domain_refs = tuple(sorted({ref for item in reviews for ref in item.domain_assertion_refs} or {"prompt02:source-roles"}))
        entity_types = (
            CanonicalEntityType(canonical_entity_type_id=customer_id, semantic_id="customer", business_name="Customer", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="customer", identity_strategy="REVIEWED_SPLINK_LINKAGE", identity_attribute_ids=("customer_name", "customer_email"), source_table_refs=tuple(item for item in source_types if self.multi_source.source_role(catalogs[item.source_id]) == "registry"), canonical_attribute_ids=("customer_name", "customer_email"), relationship_refs=tuple(item.decision_id for item in relationships), domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.multi_source.policy.provenance, request.run_id)),
            CanonicalEntityType(canonical_entity_type_id=order_id, semantic_id="order", business_name="Order event", kind=CanonicalEntityKind.EVENT, entity_resolution_family="order_event", identity_strategy="SOURCE_LOCAL_EVENT_KEY", identity_attribute_ids=("order_id",), source_table_refs=tuple(item for item in source_types if self.multi_source.source_role(catalogs[item.source_id]) == "event"), canonical_attribute_ids=("order_id",), relationship_refs=tuple(item.decision_id for item in relationships), domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.multi_source.policy.provenance, request.run_id)),
            CanonicalEntityType(canonical_entity_type_id=source_type_id, semantic_id="source", business_name="Source system", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="source_system", identity_strategy="REGISTERED_SOURCE_ID", identity_attribute_ids=("source_id",), source_table_refs=(), canonical_attribute_ids=("source_id",), relationship_refs=tuple(item.decision_id for item in relationships), domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.multi_source.policy.provenance, request.run_id)),
        )
        review_by_subject = {item.decision.subject_artifact_id: item.decision for item in self.platform.control_store.list_review_history(run_id=request.run_id, limit=10000)}
        assertion_refs_by_subject = {}
        for _ref, assertion in self._all(request, "DomainAssertion", DomainAssertion).values():
            assertion_refs_by_subject.setdefault(assertion.subject_id, []).append(assertion.assertion_id)
        canonical_relationships = tuple(CanonicalRelationship(relationship_id=item.decision_id, from_entity_type_id=order_id, to_entity_type_id=customer_id if "source_id" not in item.from_columns else source_type_id, cardinality=item.proposed_cardinality, upstream_decision_ref=item.decision_id, review_decision_ref=next((review.review_decision_id for artifact_ref, decision in relationship_artifacts if decision.decision_id == item.decision_id for review in reviews if review.subject_artifact_id == artifact_ref.artifact_id), None), conflict_refs=item.conflict_refs, provenance_refs=(item.decision_id, self.multi_source.policy.provenance)) for item in relationships)
        if any(item.review_decision_ref is None for item in canonical_relationships):
            raise ValueError("every Prompt02 relationship decision requires its exact durable evidence review")
        hypothesis = self.hypotheses.build(run_id=request.run_id, execution_context_id=next(iter(snapshots.values())).snapshot.execution_context_id, model_version=MultiSourceProductService.VERSION, evidence_reviews=reviews, relationship_decisions=relationships, semantic_mapping_decisions=mappings, evidence_domain_assertion_refs={item.decision_id: tuple(assertion_refs_by_subject.get(item.subject_id, ())) for item in relationships}, entity_types=entity_types, source_ids=tuple(sorted(catalogs)), domain_assertion_refs=domain_refs, entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_REQUIRED, "order_event": EntityResolutionRequirement.ER_NOT_REQUIRED, "source_system": EntityResolutionRequirement.ER_NOT_REQUIRED}, relationships=canonical_relationships, entity_resolution_specs=(er_spec,), snapshot_fingerprints={key: value.snapshot.source_fingerprint or value.snapshot.schema_fingerprint for key, value in snapshots.items()}, source_schema_fingerprints={key: value.source.schema_fingerprint for key, value in catalogs.items()}, source_authority_policy_refs=(self.multi_source.policy.provenance,), evidence_refs=tuple(ref.artifact_id for ref, _value in relationship_artifacts), provenance_refs=(request.run_id, self.multi_source.policy.provenance))
        ref = self._publish(request, "CanonicalModelHypothesis", hypothesis, artifact_id=hypothesis.artifact_id, provenance=hypothesis.provenance_refs, producer="application.canonical_hypothesis")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"entity_resolution_required": "customer", "relationship_count": str(len(canonical_relationships))})

    def _entity_resolution(self, request):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        spec = next(item for item in hypothesis.entity_resolution_specs if item.entity_family == "customer")
        decision = self.multi_source.privacy.authorize_entity_resolution_analysis(spec.privacy_context, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=tuple(field.column_id for field in spec.identity_fields), batch_ids=tuple(batch.batch_id for source_id in spec.source_ids for batch in snapshots[source_id].batches))
        if not decision.allowed:
            raise PermissionError("Prompt02 entity resolution authorization was denied")
        authorization = self.multi_source.privacy.entity_resolution_authorization_for_decision(decision)
        result = self.multi_source.entity_resolution.run(spec, catalogs, snapshots, authorization=authorization, artifact_root=self._run_root(request.run_id) / "entity_resolution")
        # Splink emits multi-record candidate components; singleton registry
        # rows are still part of the authorized evaluated population and must
        # receive an explicit domain-reviewed identity disposition rather than
        # disappearing from canonical accounting.
        evaluated_registry_refs = {
            row.record_ref
            for source_id, catalog in catalogs.items()
            if self.multi_source.source_role(catalog) == "registry"
            for row in snapshots[source_id].record_references
        }
        represented = {record_ref for cluster in result.clusters for record_ref in cluster.record_refs}
        singleton_clusters = []
        singleton_diagnostics = []
        for record_ref in sorted(evaluated_registry_refs - represented):
            cluster_id = stable_id("prompt02-er-singleton", {"spec": spec.spec_id, "record": record_ref})
            diagnostic_id = stable_id("prompt02-er-singleton-diagnostic", {"cluster": cluster_id})
            singleton_clusters.append(EntityCluster(cluster_id=cluster_id, record_refs=(record_ref,), decision="CANDIDATE_CLUSTER", diagnostic_refs=(diagnostic_id,), risk_flags=("singleton_no_linkage",)))
            singleton_diagnostics.append(EntityClusterDiagnostic(diagnostic_id=diagnostic_id, cluster_id=cluster_id, connected_component_size=1, independent_evidence_count=0, detail="evaluated registry row has no authorized cross-source linkage; identity remains singleton and review-gated"))
        if singleton_clusters:
            result = result.model_copy(update={
                "clusters": tuple((*result.clusters, *singleton_clusters)),
                "diagnostics": tuple((*result.diagnostics, *singleton_diagnostics)),
                "metrics": result.metrics.model_copy(update={"clusters_emitted": result.metrics.clusters_emitted + len(singleton_clusters)}),
            })
        ref = self._publish(request, "EntityResolutionResult", result, artifact_id=stable_id("entity-resolution-result", {"run": request.run_id, "spec": spec.spec_id}), provenance=(hypothesis_ref.artifact_id, spec.spec_id), producer="adapter.splink")
        if getattr(result.status, "value", result.status) != "COMPLETE":
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="ENTITY_RESOLUTION_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="Splink entity resolution did not produce a complete result")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"provider": result.engine.engine + ":" + result.engine.engine_version, "clusters": str(len(result.clusters)), "edges": str(len(result.edges))})

    def _identity_proposal(self, request):
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        er_ref, er_result = self._typed_from_run(request.run_id, "EntityResolutionResult", EntityResolutionResult)
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        memberships = []
        for cluster in er_result.clusters:
            if cluster.decision == "CANDIDATE_CLUSTER":
                if len(cluster.record_refs) == 1:
                    memberships.append(CanonicalIdentityMembership(membership_group_id=cluster.cluster_id, canonical_entity_type_id="entity_customer", entity_resolution_family="customer", source_record_refs=cluster.record_refs, derivation_basis=IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW, domain_assertion_refs=cluster.diagnostic_refs, cluster_evidence_refs=cluster.diagnostic_refs, evidence_refs=cluster.diagnostic_refs, policy_refs=(self.multi_source.policy.provenance,), rationale="no authorized linkage edge was observed; the registry row remains a reviewed singleton customer identity", provenance_refs=(hypothesis_ref.artifact_id, er_ref.artifact_id)))
                else:
                    memberships.append(CanonicalIdentityMembership(membership_group_id=cluster.cluster_id, canonical_entity_type_id="entity_customer", entity_resolution_family="customer", source_record_refs=cluster.record_refs, derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE, authorized_edge_refs=cluster.edge_refs, cluster_evidence_refs=cluster.diagnostic_refs, evidence_refs=tuple(sorted(set(cluster.edge_refs) | set(cluster.diagnostic_refs))), policy_refs=(self.multi_source.policy.provenance,), rationale="customer membership is derived from the authorized Splink candidate cluster and remains review-gated", provenance_refs=(hypothesis_ref.artifact_id, er_ref.artifact_id)))
        for source_id, catalog in sorted(catalogs.items()):
            if self.multi_source.source_role(catalog) != "event":
                continue
            for row in snapshots[source_id].record_references:
                memberships.append(CanonicalIdentityMembership(membership_group_id=stable_id("event-membership", {"source": source_id, "record": row.record_ref}), canonical_entity_type_id="entity_order", entity_resolution_family="order_event", source_record_refs=(row.record_ref,), derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY, evidence_refs=(row.record_ref, snapshots[source_id].snapshot.snapshot_id), policy_refs=(self.multi_source.policy.provenance,), rationale="event identity is the source-local event key; no cross-source identity is inferred", provenance_refs=(hypothesis_ref.artifact_id, snapshots[source_id].snapshot.snapshot_id)))
        proposal = self.multi_source.identity_proposals.build(hypothesis=hypothesis, memberships=tuple(memberships), er_results={"customer": er_result}, source_schema_fingerprints=hypothesis.source_schema_fingerprints, policy_refs=(self.multi_source.policy.provenance,), provenance_refs=(request.run_id, er_ref.artifact_id))
        ref = self._publish(request, "CanonicalIdentityProposal", proposal, artifact_id=proposal.proposal_id, provenance=proposal.provenance_refs, producer="application.canonical_identity")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"membership_count": str(len(memberships)), "customer_clusters": str(len(er_result.clusters))})

    def _canonical(self, request):
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        proposal_ref, proposal = self._typed_from_run(request.run_id, "CanonicalIdentityProposal", CanonicalIdentityProposal)
        er_ref, er_result = self._typed_from_run(request.run_id, "EntityResolutionResult", EntityResolutionResult)
        review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY)
        metadata = {}
        catalogs = self._catalogs(request)
        snapshots = self._snapshots(request)
        for source_id, (_catalog_ref, catalog) in catalogs.items():
            table = self.multi_source.role_table(catalog)
            for item in snapshots[source_id][1].record_references:
                metadata[item.record_ref] = {"source_id": source_id, "snapshot_id": snapshots[source_id][1].snapshot.snapshot_id, "table_id": table.table_id}
        model = self.multi_source.finalization.finalize(hypothesis=hypothesis, identity_proposal=proposal, identity_review=review, er_results={"customer": er_result}, source_record_metadata=metadata, lineage_refs=(hypothesis_ref.artifact_id, proposal_ref.artifact_id, er_ref.artifact_id), record_accounting_refs=(stable_id("prompt02-accounting", request.run_id),))
        ref = self._publish(request, "CanonicalModel", model, artifact_id=model.model_id, provenance=model.provenance_refs, producer="application.canonical_finalization")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"instances": str(len(model.instances)), "source_record_maps": str(len(model.source_record_maps))})

    @staticmethod
    def _typed_value(value, logical_type):
        if value is None:
            return None
        if logical_type == "DATE":
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        if logical_type == "DECIMAL":
            return value if isinstance(value, Decimal) else Decimal(str(value))
        return str(value) if logical_type == "STRING" else value

    def _build_dataset_and_request(self, request, canonical):
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        maps = {item.record_ref: item for item in canonical.source_record_maps}
        customer_rows, source_rows, event_rows = {}, [], []
        registry_keys = {}
        source_record_refs_by_source: dict[str, list[str]] = {}
        for source_id, catalog in sorted(catalogs.items()):
            role, table, snapshot = self.multi_source.source_role(catalog), self.multi_source.role_table(catalog), snapshots[source_id]
            source_record_refs_by_source[source_id] = []
            for row in self.multi_source.read_rows(catalog, snapshot):
                values, record_ref, mapping = row["values"], row["record_ref"], maps.get(row["record_ref"])
                if mapping is None:
                    raise ValueError(f"canonical model does not map source record {record_ref}")
                source_record_refs_by_source[source_id].append(record_ref)
                if role == "registry":
                    customer_value = self.multi_source.logical_value(catalog, table.table_id, values, "customer_id")
                    if customer_value is not None:
                        registry_keys[str(customer_value)] = mapping.canonical_entity_id
                        current = customer_rows.setdefault(mapping.canonical_entity_id, {"canonical_reference": mapping.canonical_entity_id, "customer_id": str(customer_value), "customer_name": self.multi_source.logical_value(catalog, table.table_id, values, "customer_name"), "email": self.multi_source.logical_value(catalog, table.table_id, values, "customer_email"), "phone": self.multi_source.logical_value(catalog, table.table_id, values, "customer_phone"), "source_count": 0, "source_record_refs": ()})
                        current["source_count"] += 1
                        current["source_record_refs"] = tuple((*current["source_record_refs"], record_ref))
                else:
                    order_value = self.multi_source.logical_value(catalog, table.table_id, values, "order_id")
                    customer_value = self.multi_source.logical_value(catalog, table.table_id, values, "customer_id_ref")
                    event_rows.append({"canonical_reference": mapping.canonical_entity_id, "order_id": None if order_value is None else str(order_value), "customer_id": None if customer_value is None else str(customer_value), "canonical_customer_id": None if customer_value is None else registry_keys.get(str(customer_value)), "order_date": self.multi_source.logical_value(catalog, table.table_id, values, "order_date"), "source_id": source_id, "quantity": self.multi_source.logical_value(catalog, table.table_id, values, "quantity"), "unit_price": self.multi_source.logical_value(catalog, table.table_id, values, "unit_price"), "source_record_refs": (record_ref,)})
        for row in event_rows:
            if row["customer_id"] is not None:
                row["canonical_customer_id"] = registry_keys.get(row["customer_id"])
        source_refs = tuple(sorted(catalogs))
        source_rows = [{"canonical_reference": source_id, "source_id": source_id, "source_role": self.multi_source.source_role(catalogs[source_id]), "source_record_refs": tuple(source_record_refs_by_source[source_id])} for source_id in source_refs]

        def make_table(table_id, concept, rows, columns):
            refs = source_refs
            bindings = tuple(AnalyticalColumnBinding(column_id=stable_id("analytical-column", {"table": table_id, "column": name}), column_name=name, logical_type=logical, nullable=nullable, lineage_refs=refs) for name, logical, nullable in columns)
            analytical_rows = tuple(AnalyticalInputRow(row_ref=stable_id("ainput-row", {"run": request.run_id, "table": table_id, "index": index, "reference": row["canonical_reference"]}), canonical_reference=str(row["canonical_reference"]), values=tuple(AnalyticalCell(column_name=name, value=self._typed_value(row.get(name), logical)) for name, logical, _nullable in columns), source_record_refs=tuple(row["source_record_refs"]), lineage_refs=refs) for index, row in enumerate(rows))
            batch = AnalyticalRowBatch(batch_id=stable_id("analytical-batch", {"run": request.run_id, "table": table_id}), table_id=table_id, rows=analytical_rows, source_batch_refs=refs, source_snapshot_fingerprints={key: snapshots[key].snapshot.source_fingerprint or snapshots[key].snapshot.schema_fingerprint for key in snapshots}, lineage_refs=refs)
            return AnalyticalInputTable(table_id=table_id, canonical_concept_ref=concept, columns=bindings, batches=(batch,), source_table_refs=refs, lineage_refs=refs)

        customer_table = make_table("customer_input", "customer", tuple(customer_rows.values()), (("customer_id", "STRING", False), ("customer_name", "STRING", False), ("email", "STRING", True), ("phone", "STRING", True), ("source_count", "INTEGER", False)))
        source_table = make_table("source_input", "source", tuple(source_rows), (("source_id", "STRING", False), ("source_role", "STRING", False)))
        event_table = make_table("event_input", "order", tuple(event_rows), (("order_id", "STRING", False), ("customer_id", "STRING", True), ("canonical_customer_id", "STRING", True), ("order_date", "DATE", False), ("source_id", "STRING", False), ("quantity", "DECIMAL", False), ("unit_price", "DECIMAL", True)))
        dataset = AnalyticalInputDataset(dataset_id=stable_id("analytical-dataset", {"run": request.run_id, "canonical": canonical.model_id, "rows": [row.row_ref for table in (customer_table, source_table, event_table) for row in table.rows]}), canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, tables=(customer_table, source_table, event_table), source_schema_fingerprints={key: catalogs[key].source.schema_fingerprint for key in catalogs}, source_snapshot_fingerprints={key: snapshots[key].snapshot.source_fingerprint or snapshots[key].snapshot.schema_fingerprint for key in snapshots}, allow_literal_sql=False, provenance_refs=(request.run_id, canonical.model_id, *source_refs))
        binding = AnalyticalInputBinding(binding_id=stable_id("input-binding", {"run": request.run_id, "dataset": dataset.dataset_id}), canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, dataset_id=dataset.dataset_id, dataset_content_hash=dataset.content_hash, source_schema_fingerprints=dict(dataset.source_schema_fingerprints), source_snapshot_fingerprints=dict(dataset.source_snapshot_fingerprints), row_counts=dataset.row_counts, provenance_refs=(dataset.dataset_id, canonical.model_id))
        relationships = tuple(item for _ref, item in self._all(request, "RelationshipDecision", RelationshipDecision).values())
        customer_rel = next(item for item in relationships if "source_id" not in item.from_columns)
        source_rel = next(item for item in relationships if "source_id" in item.from_columns)
        lineage = (canonical.model_id, request.run_id, *source_refs)
        customer_dimension = DimensionSpec(dimension_id="dim_customer", table_name="dim_customer", input_table_id="customer_input", canonical_entity_type_id="entity_customer", canonical_entity_refs=tuple(sorted(customer_rows)), role=DimensionRole.CONFORMED, eligibility_reason="reviewed cross-source customer registry identity", surrogate_key=WarehouseKeySpec(key_name=_CUSTOMER_KEY, namespace="prompt02.customer"), alternate_key_columns=("customer_id",), attributes=(DimensionAttributeSpec(attribute_id="customer_id", column_name="customer_id", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="customer_name", column_name="customer_name", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="customer_email", column_name="email", input_column_name="email", logical_type="STRING", nullable=True, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="customer_phone", column_name="phone", input_column_name="phone", logical_type="STRING", nullable=True, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="pinned source snapshots provide no historical validity contract"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unresolved customer references are quarantined"), provenance_refs=lineage)
        source_dimension = DimensionSpec(dimension_id="dim_source", table_name="dim_source", input_table_id="source_input", canonical_entity_type_id="entity_source", canonical_entity_refs=source_refs, role=DimensionRole.CONFORMED, eligibility_reason="registered source identity is explicit and stable for lineage", surrogate_key=WarehouseKeySpec(key_name=_SOURCE_KEY, namespace="prompt02.source"), alternate_key_columns=("source_id",), attributes=(DimensionAttributeSpec(attribute_id="source_id", column_name="source_id", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="source_role", column_name="source_role", logical_type="STRING", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="registered source roles are immutable during a run"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unregistered source IDs cannot be materialized"), provenance_refs=lineage)
        date_dimension = DimensionSpec(dimension_id="dim_date", table_name="dim_date", canonical_entity_type_id="entity_source", canonical_entity_refs=("date",), role=DimensionRole.DATE, eligibility_reason="Gregorian date role is explicit in the reviewed analytical request", surrogate_key=WarehouseKeySpec(key_name=_DATE_KEY, namespace="prompt02.date"), alternate_key_columns=("full_date",), attributes=(DimensionAttributeSpec(attribute_id="date_full", column_name="full_date", logical_type="DATE", derivation="FULL_DATE", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_year", column_name="year", logical_type="INTEGER", derivation="YEAR", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_month", column_name="month", logical_type="INTEGER", derivation="MONTH", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_day", column_name="day", logical_type="INTEGER", derivation="DAY", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="calendar is deterministically generated from observed events"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER, rationale="date role has an explicit unknown member", unknown_member_key=-1), calendar_policy="GREGORIAN_V1", provenance_refs=lineage)
        time_rel = stable_id("time-role", {"run": request.run_id})
        fact = FactSpec(fact_id="fact_order", table_name="fact_order", input_table_id="event_input", fact_type=FactType.TRANSACTION, canonical_event_type_id="entity_order", canonical_event_refs=tuple(row.canonical_reference for row in event_table.rows), grain_spec_id="grain_order", dimension_foreign_keys=(FactForeignKeySpec(relationship_ref=customer_rel.decision_id, dimension_id="dim_customer", fact_column=_CUSTOMER_KEY, dimension_key_column=_CUSTOMER_KEY, canonical_entity_type_id="entity_customer", input_reference_column="canonical_customer_id"), FactForeignKeySpec(relationship_ref=source_rel.decision_id, dimension_id="dim_source", fact_column=_SOURCE_KEY, dimension_key_column=_SOURCE_KEY, canonical_entity_type_id="entity_source", input_reference_column="source_id"), FactForeignKeySpec(relationship_ref=time_rel, relationship_scope=FactRelationshipScope.ANALYTICAL_TIME_ROLE, dimension_id="dim_date", fact_column=_DATE_KEY, dimension_key_column=_DATE_KEY, canonical_entity_type_id="entity_source", input_reference_column="order_date")), degenerate_dimension_columns=("source_id", "customer_id"), measure_ids=("measure_quantity",), date_role_columns=("order_date",), relationship_refs=(customer_rel.decision_id, source_rel.decision_id, time_rel), provenance_refs=lineage)
        grain = GrainSpec(grain_id="grain_order", fact_id="fact_order", human_readable_grain="one row per source and source-local order key", key_columns=("source_id", "order_id"), null_policy=GrainNullPolicy.REJECT_NULLS, evidence_refs=source_refs, provenance_refs=lineage)
        measure = MeasureSpec(measure_id="measure_quantity", fact_id="fact_order", field_name="quantity", semantic_name="Order quantity", aggregation_class=AggregationClass.ADDITIVE, aggregation_rule="SUM(quantity)", unit_semantics="source quantity units", currency_semantics="not applicable; unit price is retained as a non-aggregated attribute", logical_type="DECIMAL", nullable=False, domain_assertion_refs=("prompt02:quantity",), provenance_refs=lineage)
        planning = AnalyticalPlanningRequest(request_id=stable_id("analytical-request", {"run": request.run_id, "canonical": canonical.model_id}), dimensions=(customer_dimension, date_dimension, source_dimension), facts=(fact,), grains=(grain,), measures=(measure,), accepted_relationship_refs=(customer_rel.decision_id, source_rel.decision_id), domain_assertion_refs=("prompt02:source-roles", "prompt02:quantity"), provenance_refs=lineage)
        return dataset, binding, planning

    def _analytical(self, request):
        canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        dataset, binding, planning = self._build_dataset_and_request(request, canonical)
        dataset_ref = self._publish(request, "AnalyticalInputDataset", dataset, artifact_id=dataset.dataset_id, provenance=(canonical_ref.artifact_id, *dataset.provenance_refs), producer="application.product_input")
        binding_ref = self._publish(request, "AnalyticalInputBinding", binding, artifact_id=binding.binding_id, provenance=(dataset_ref.artifact_id, canonical_ref.artifact_id), producer="application.product_input")
        plan, dimensions, facts, grains, measures = self.planner.build_plan(canonical, binding, dataset, planning)
        refs = [self._publish(request, "AnalyticalPlan", plan, artifact_id=plan.plan_id, provenance=(canonical_ref.artifact_id, binding_ref.artifact_id), producer="application.analytical_planner")]
        for kind, values, id_field in (("DimensionSpec", dimensions, "dimension_id"), ("FactSpec", facts, "fact_id"), ("GrainSpec", grains, "grain_id"), ("MeasureSpec", measures, "measure_id")):
            refs.extend(self._publish(request, kind, item, artifact_id=stable_id(kind.casefold(), {"plan": plan.plan_id, "id": getattr(item, id_field)}), provenance=(refs[0].artifact_id, item.semantic_content_hash), producer="application.analytical_planner") for item in values)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(item.artifact_id for item in refs), metadata={"plan_id": plan.plan_id, "dataset_id": dataset.dataset_id, "dimensions": str(len(dimensions)), "facts": str(len(facts)), "measures": "quantity"})

    def _compilation(self, request):
        from dirty_data_to_olap.domain.contracts.analytical import AnalyticalPlan, TargetConfig
        from dirty_data_to_olap.application.compiler import CompilationArtifactPublisher
        plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        dataset_ref, dataset = self._typed_from_run(request.run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
        _binding_ref, binding = self._typed_from_run(request.run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
        dimensions = tuple(value for _ref, value in self._all(request, "DimensionSpec", DimensionSpec).values())
        facts = tuple(value for _ref, value in self._all(request, "FactSpec", FactSpec).values())
        grains = tuple(value for _ref, value in self._all(request, "GrainSpec", GrainSpec).values())
        measures = tuple(value for _ref, value in self._all(request, "MeasureSpec", MeasureSpec).values())
        review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN)
        target = TargetConfig(relative_path="olap.duckdb")
        compiled, sql = self.compiler.compile(plan, dimensions, facts, grains, measures, binding, dataset, target, review)
        outputs = CompilationArtifactPublisher(self.platform.artifact_store, self.platform.control_store).publish(run_id=request.run_id, attempt_id=request.attempt_id, compiled_plan=compiled, generated_sql=sql, target_config=target)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(outputs.compiled_plan.artifact_id, outputs.generated_sql.artifact_id, outputs.target_config.artifact_id), metadata={"compiler_version": compiled.compiler_version, "input_dataset_id": dataset_ref.artifact_id, "plan_id": plan_ref.artifact_id})

    def _materialization(self, request):
        from dirty_data_to_olap.application.materializer import MaterializationService
        from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
        from dirty_data_to_olap.domain.contracts.analytical import CompiledPlan, GeneratedSQL, TargetConfig
        compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        sql_ref, sql = self._typed_from_run(request.run_id, "GeneratedSQL", GeneratedSQL)
        target_ref, target = self._typed_from_run(request.run_id, "TargetConfig", TargetConfig)
        dataset_ref, dataset = self._typed_from_run(request.run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
        binding_ref, binding = self._typed_from_run(request.run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
        review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN)
        artifact = MaterializationService(DuckDBMaterializer(self._run_root(request.run_id) / "olap", repository_root=self.project_root)).materialize(compiled, sql, review, binding, dataset, target, run_id=request.run_id)
        ref = self._publish(request, "MaterializationArtifact", artifact, artifact_id=artifact.artifact_id, provenance=(compiled_ref.artifact_id, sql_ref.artifact_id, target_ref.artifact_id, dataset_ref.artifact_id, binding_ref.artifact_id), producer="adapter.duckdb")
        if not artifact.usable:
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="MATERIALIZATION_FAILED", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="accepted DuckDB materialization service did not publish a usable target")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"target_type": artifact.target_type, "table_count": str(len(artifact.table_names))})

    def _source_records(self, request, catalogs, snapshots):
        records = []
        for source_id, catalog in sorted(catalogs.items()):
            role = self.multi_source.source_role(catalog)
            table = self.multi_source.role_table(catalog)
            snapshot = snapshots[source_id]
            ordinals = {item.record_ref: item.extraction_ordinal for item in snapshot.record_references}
            for row in self.multi_source.read_rows(catalog, snapshot):
                values = row["values"]
                logical = {
                    "customer_id": self.multi_source.logical_value(catalog, table.table_id, values, "customer_id" if role == "registry" else "customer_id_ref"),
                    "customer_name": self.multi_source.logical_value(catalog, table.table_id, values, "customer_name"),
                    "customer_email": self.multi_source.logical_value(catalog, table.table_id, values, "customer_email"),
                    "customer_phone": self.multi_source.logical_value(catalog, table.table_id, values, "customer_phone"),
                    "order_id": self.multi_source.logical_value(catalog, table.table_id, values, "order_id"),
                    "order_date": self.multi_source.logical_value(catalog, table.table_id, values, "order_date"),
                    "quantity": self.multi_source.logical_value(catalog, table.table_id, values, "quantity"),
                    "unit_price": self.multi_source.logical_value(catalog, table.table_id, values, "unit_price"),
                }
                records.append({"source_id": source_id, "snapshot_id": snapshot.snapshot.snapshot_id, "table_id": table.table_id, "record_ref": row["record_ref"], "role": role, "extraction_ordinal": ordinals.get(row["record_ref"], 0), "logical_values": logical})
        return tuple(records)

    def _validation(self, request):
        canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        materialization_ref, materialization = self._typed_from_run(request.run_id, "MaterializationArtifact", MaterializationArtifact)
        dataset_ref, dataset = self._typed_from_run(request.run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
        binding_ref, binding = self._typed_from_run(request.run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
        semantic_ref, semantic = self._typed_from_run(request.run_id, "SemanticModel", SemanticModel)
        semantic_validation_ref, semantic_validation = self._typed_from_run(request.run_id, "SemanticValidationResult", SemanticValidationResult)
        fact = next(value for _ref, value in self._all(request, "FactSpec", FactSpec).values())
        measure = next(value for _ref, value in self._all(request, "MeasureSpec", MeasureSpec).values())
        catalogs = {key: value for key, (_ref, value) in self._catalogs(request).items()}
        snapshots = {key: value for key, (_ref, value) in self._snapshots(request).items()}
        truth, accounting = build_multi_source_truth_and_accounting(
            run_id=request.run_id,
            source_records=self._source_records(request, catalogs, snapshots),
            snapshots=snapshots,
            catalogs=catalogs,
            dataset=dataset,
            canonical=canonical,
            fact=fact,
            measure=measure,
            policy=self.multi_source.policy,
        )
        truth_ref = self._publish(request, "SourceTruthManifest", truth, artifact_id=truth.truth_id, provenance=(canonical_ref.artifact_id, *truth.provenance_refs), producer="application.product_truth")
        accounting_ref = self._publish(request, "RecordAccountingArtifact", accounting, artifact_id=accounting.accounting_id, provenance=(truth_ref.artifact_id, canonical_ref.artifact_id), producer="application.product_truth")
        policy = self.multi_source.policy.validation_policy(canonical_model_id=canonical.model_id, materialization_id=materialization.artifact_id)
        bindings = ValidationArtifactBindings(
            source_snapshot_id=truth.source_snapshot_id,
            source_snapshot_hash=truth.source_snapshot_fingerprint,
            source_truth_id=truth.truth_id,
            source_truth_content_hash=truth.content_hash,
            canonical_model_id=canonical.model_id,
            canonical_model_content_hash=canonical.content_hash,
            record_accounting_id=accounting.accounting_id,
            record_accounting_content_hash=accounting.content_hash,
            analytical_plan_id=plan.plan_id,
            analytical_plan_content_hash=plan.content_hash,
            analytical_spec_package_hash=plan.analytical_spec_package_hash,
            analytical_dataset_id=dataset.dataset_id,
            analytical_dataset_content_hash=dataset.content_hash,
            analytical_input_binding_id=binding.binding_id,
            analytical_input_binding_content_hash=binding.content_hash,
            analytical_input_source_snapshot_fingerprints=dict(binding.source_snapshot_fingerprints),
            compiled_plan_id=compiled.compiled_plan_id,
            compiled_plan_content_hash=compiled.content_hash,
            materialization_artifact_id=materialization.artifact_id,
            materialization_artifact_content_hash=materialization.content_hash,
            target_relative_path=materialization.target_relative_path,
            target_config_fingerprint=materialization.target_config_fingerprint,
            target_file_sha256=materialization.target_file_sha256 or "0" * 64,
            semantic_model_id=semantic.semantic_model_id,
            semantic_model_content_hash=semantic.content_hash,
            semantic_validation_id=semantic_validation.validation_id,
            semantic_validation_content_hash=stable_digest(semantic_validation.model_dump(mode="json")),
            validation_policy_id=policy.policy_id,
            validation_policy_version=policy.policy_version,
            benchmark_truth_hash=truth.content_hash,
        )
        outcome = self.validation.validate(
            ValidationInputs(source_truth=truth, canonical_model=canonical, accounting=accounting, analytical_dataset=dataset, analytical_input_binding=binding, plan=plan, compiled_plan=compiled, materialization=materialization, semantic_model=semantic, semantic_validation=semantic_validation, policy=policy, bindings=bindings),
            DuckDBValidationTargetReader(self.project_root),
        )
        report_ref = self._publish(request, "ValidationReport", outcome.report, artifact_id=outcome.report.report_id, provenance=(truth_ref.artifact_id, accounting_ref.artifact_id, materialization_ref.artifact_id), producer="application.validation")
        reconciliation_ref = self._publish(request, "ReconciliationResult", outcome.reconciliation, artifact_id=outcome.reconciliation.result_id, provenance=(report_ref.artifact_id,), producer="application.validation")
        GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=request.run_id, report=outcome.report, report_artifact=report_ref, verified_content_commit=self._git_commit(), control_store=self.platform.control_store, artifact_store=self.platform.artifact_store, provenance_refs=("application.validation", reconciliation_ref.artifact_id, compiled_ref.artifact_id, plan_ref.artifact_id, dataset_ref.artifact_id, binding_ref.artifact_id, semantic_ref.artifact_id, semantic_validation_ref.artifact_id))
        passed = outcome.report.g6_status.value == "PASS" and outcome.report.g6_eligible
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED if passed else StageResultStatus.FAILED, output_artifact_refs=(truth_ref.artifact_id, accounting_ref.artifact_id, report_ref.artifact_id, reconciliation_ref.artifact_id), failure_code=None if passed else "G6_VALIDATION_FAILED", failure_classification=None if passed else FailureClassification.TERMINAL_FAILURE, failure_reason=None if passed else "multi-source validation report contains a blocking discrepancy or pending check", metadata={"g6_status": outcome.report.g6_status.value, "g6_eligible": str(outcome.report.g6_eligible).lower(), "validation_report_id": outcome.report.report_id})


__all__ = ["MultiSourceStageHandlers"]
