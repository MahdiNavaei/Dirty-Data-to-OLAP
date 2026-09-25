"""Telemetry domain policy used by the shared multi-source product path.

This module contains domain translation only.  It does not own workers,
reviews, artifact persistence, SQL compilation, or target materialization.
Those boundaries remain in the accepted product runtime.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    CanonicalEntityType,
    CanonicalSourceTable,
    RecordDisposition,
    CanonicalModel,
    CanonicalIdentityMembership,
    IdentityDerivationBasis,
    EntityResolutionRequirement,
)
from dirty_data_to_olap.domain.contracts.dependency import DependencyPrivacyContext, DependencyRequest
from dirty_data_to_olap.domain.contracts.evidence_fusion import DomainAssertion
from dirty_data_to_olap.domain.contracts.profiling import NullMarkerPolicy, ProfileMode, ProfileRequest
from dirty_data_to_olap.domain.contracts.product import ProductPolicyBinding
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    RecordAccountingArtifact,
    RecordAccountingEntry,
    RecordAccountingScope,
    SourceTruthAccountingExpectation,
    SourceTruthAggregateExpectation,
    SourceTruthEntity,
    SourceTruthFact,
    SourceTruthManifest,
    SourceTruthRecord,
    SourceTruthRelationship,
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


class TelemetryProductPolicy:
    """Versioned telemetry/device domain adapter for the shared runtime."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.data, dict) or self.data.get("product_id") != "telemetry":
            raise ValueError("product policy is not the telemetry product policy")
        self.product_id = "telemetry"
        self.version = str(self.data["version"])
        self.provenance = f"config:{self.path.as_posix()}"
        self.content_fingerprint = hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def binding(self) -> ProductPolicyBinding:
        return ProductPolicyBinding(product_id=self.product_id, version=self.version, content_fingerprint=self.content_fingerprint, provenance_ref=self.provenance)

    @staticmethod
    def column(catalog: SourceCatalog, table_id: str, names: str | Sequence[str]):
        wanted = {str(name).casefold() for name in ((names,) if isinstance(names, str) else names)}
        return next((item for item in catalog.columns if item.table_id == table_id and item.physical_name.casefold() in wanted), None)

    @classmethod
    def source_role(cls, catalog: SourceCatalog) -> str:
        for table in catalog.tables:
            names = {item.physical_name.casefold() for item in catalog.columns if item.table_id == table.table_id}
            if {"device_id", "device_name"}.issubset(names):
                return "device_registry"
            if {"location_id", "location_name"}.issubset(names):
                return "location_registry"
            if {"reading_id", "device_ref", "location_ref", "observed_on", "temperature"}.issubset(names):
                return "reading_event"
        raise ValueError(f"telemetry source {catalog.source_id} does not match a registered telemetry role")

    @classmethod
    def role_table(cls, catalog: SourceCatalog):
        role = cls.source_role(catalog)
        required = {
            "device_registry": ("device_id", "device_name"),
            "location_registry": ("location_id", "location_name"),
            "reading_event": ("reading_id", "temperature"),
        }[role]
        for table in catalog.tables:
            if all(cls.column(catalog, table.table_id, name) is not None for name in required):
                return table
        raise ValueError(f"telemetry role table is unavailable for {catalog.source_id}")

    @classmethod
    def identity_column(cls, catalog: SourceCatalog) -> str:
        return {"device_registry": "device_id", "location_registry": "location_id", "reading_event": "reading_id"}[cls.source_role(catalog)]

    @classmethod
    def logical_value(cls, catalog: SourceCatalog, table_id: str, values: Mapping[str, Any], logical_name: str) -> Any:
        aliases = {
            "device_id": ("device_id", "device_ref"),
            "device_name": ("device_name", "name"),
            "location_id": ("location_id", "location_ref"),
            "location_name": ("location_name", "name"),
            "reading_id": ("reading_id", "event_id"),
            "device_ref": ("device_ref", "device_id"),
            "location_ref": ("location_ref", "location_id"),
            "observed_on": ("observed_on", "observed_at", "reading_date"),
            "temperature": ("temperature", "temperature_c", "temp_c"),
        }
        column = cls.column(catalog, table_id, aliases[logical_name])
        return None if column is None else values.get(column.physical_name)

    def profile_request(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, run_id: str) -> ProfileRequest:
        return ProfileRequest(
            profile_request_id=stable_id("profile-request", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "policy": self.version}),
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            selected_table_ids=tuple(item.table_id for item in catalog.tables),
            selected_column_ids=(),
            mode=ProfileMode.FULL,
            metric_policy_version="telemetry-profile-metrics-v1",
            null_marker_policy=NullMarkerPolicy(configured_markers=("", "NA", "NULL")),
            profile_config_version="telemetry-profile-v1",
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

    def dependency_result(self, result: Any, *, catalog: SourceCatalog) -> Any:
        """Bind telemetry's source-local identity candidate at the policy edge."""

        from dirty_data_to_olap.application.product_dependency import bind_source_local_identity_candidate

        return bind_source_local_identity_candidate(
            result,
            catalog=catalog,
            table_name=self.role_table(catalog).physical_name,
            column_name=self.identity_column(catalog),
        )

    def quality_request(self, catalog: SourceCatalog, snapshot: SourceSnapshotResult, profile: Any, run_id: str) -> QualityRequest:
        table = self.role_table(catalog)
        role = self.source_role(catalog)
        logical_rules = {
            "device_registry": (("device_id", "REQUIRED_VALUE"), ("device_name", "REQUIRED_VALUE"), ("device_id", "UNIQUE_VALUES")),
            "location_registry": (("location_id", "REQUIRED_VALUE"), ("location_name", "REQUIRED_VALUE"), ("location_id", "UNIQUE_VALUES")),
            "reading_event": (("reading_id", "REQUIRED_VALUE"), ("device_ref", "REQUIRED_VALUE"), ("location_ref", "REQUIRED_VALUE"), ("observed_on", "REQUIRED_VALUE"), ("temperature", "REQUIRED_VALUE"), ("reading_id", "UNIQUE_VALUES")),
        }[role]
        rules = []
        for index, (logical_name, rule_type) in enumerate(logical_rules):
            column = self.column(catalog, table.table_id, logical_name)
            if column is None:
                continue
            rules.append(QualityRule(
                rule_id=f"telemetry.{role}.{rule_type.casefold()}.{index}",
                rule_version="telemetry-quality-v1",
                rule_type=QualityRuleType(rule_type),
                scope=QualityRuleScope.USER_POLICY,
                entity_type=role,
                source_id=catalog.source_id,
                table_id=table.table_id,
                column_ids=(column.column_id,),
                severity=QualitySeverity.HIGH,
                repairability=Repairability.REVIEW_REQUIRED,
                detector_config={},
                provenance=self.provenance,
            ))
        rule_set = QualityRuleSet(rule_set_id=f"telemetry-{role}-quality", version="telemetry-quality-v1", rules=tuple(rules), provenance=self.provenance, runtime_default=True)
        return QualityRequest(
            quality_run_id=stable_id("quality-run", {"run": run_id, "snapshot": snapshot.snapshot.snapshot_id, "role": role}),
            source_id=catalog.source_id,
            snapshot_id=snapshot.snapshot.snapshot_id,
            rule_set=rule_set,
            profile_result_fingerprint=quality_profile_fingerprint(profile),
            profile_refs=(profile.profile_request.profile_request_id,),
            batch_ids=tuple(item.batch_id for item in snapshot.batches),
            batch_hashes=tuple(item.content_hash for item in snapshot.batches),
            provenance=self.provenance,
        )

    def relationship_candidates(self, catalogs: Mapping[str, SourceCatalog]) -> tuple[dict[str, Any], ...]:
        readings = [item for item in catalogs.values() if self.source_role(item) == "reading_event"]
        devices = [item for item in catalogs.values() if self.source_role(item) == "device_registry"]
        locations = [item for item in catalogs.values() if self.source_role(item) == "location_registry"]
        if len(readings) != 1 or len(devices) != 1 or len(locations) != 1:
            raise ValueError("telemetry requires exactly one reading, device, and location source")
        reading, device, location = readings[0], devices[0], locations[0]
        rt, dt, lt = self.role_table(reading), self.role_table(device), self.role_table(location)
        return (
            {"candidate_id": stable_id("relationship-candidate", {"kind": "device", "event": reading.source_id, "registry": device.source_id}), "from_table": rt.physical_name, "from_columns": (self.column(reading, rt.table_id, "device_ref").physical_name,), "to_table": dt.physical_name, "to_columns": (self.column(device, dt.table_id, "device_id").physical_name,), "proposed_cardinality": "MANY_TO_ONE"},
            {"candidate_id": stable_id("relationship-candidate", {"kind": "location", "event": reading.source_id, "registry": location.source_id}), "from_table": rt.physical_name, "from_columns": (self.column(reading, rt.table_id, "location_ref").physical_name,), "to_table": lt.physical_name, "to_columns": (self.column(location, lt.table_id, "location_id").physical_name,), "proposed_cardinality": "MANY_TO_ONE"},
        )

    def domain_assertions(self, candidates: Sequence[Mapping[str, Any]], catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> tuple[DomainAssertion, ...]:
        source_ids = tuple(sorted(catalogs))
        snapshot_ids = tuple(snapshots[key].snapshot.snapshot_id for key in source_ids)
        assertions: list[DomainAssertion] = []
        def candidate_subject(item: Mapping[str, Any]) -> str:
            return "rel:" + item["from_table"] + ":" + ",".join(item["from_columns"]) + "->" + item["to_table"] + ":" + ",".join(item["to_columns"])

        relationship_subjects = {
            kind: next(
                (
                    candidate_subject(item)
                    for item in candidates
                    if kind in item["from_columns"] or f"{kind}_ref" in item["from_columns"]
                ),
                None,
            )
            for kind in ("device", "location")
        }
        if any(value is None for value in relationship_subjects.values()):
            raise ValueError("telemetry domain assertions require selected device and location relationship candidates")
        for item in candidates:
            subject_id = candidate_subject(item)
            kind = "device" if "device" in item["from_columns"] or "device_ref" in item["from_columns"] else "location"
            assertions.append(DomainAssertion(assertion_id=f"telemetry:{kind}-relationship", subject_id=subject_id, statement=f"reading_event.{kind}_ref references the reviewed {kind}_registry.{kind}_id relationship", status="ACTIVE", source_ids=source_ids, snapshot_ids=snapshot_ids, scope_id=stable_id("telemetry-domain-scope", item["candidate_id"]), asserted_by=f"product-policy:{self.version}", evidence_refs=(self.provenance, item["candidate_id"])))
        # These are explicit policy records, not inferred merges.  The source
        # fixture contains two same-name devices with different IDs and one
        # reading with an unknown device reference; both remain reviewable.
        assertions.extend((
            DomainAssertion(assertion_id="telemetry:hard-negative-same-name-device", subject_id=relationship_subjects["device"], statement="same-name devices with different device_id values are not merged by this source-local identity policy", status="ACTIVE", source_ids=source_ids, snapshot_ids=snapshot_ids, scope_id=stable_id("telemetry-domain-scope", "hard-negative"), asserted_by=f"product-policy:{self.version}", evidence_refs=(self.provenance, "telemetry:device-hard-negative")),
            DomainAssertion(assertion_id="telemetry:missing-device-reference", subject_id=relationship_subjects["device"], statement="a reading with no matching device_id is quarantined at the reviewed fact boundary", status="ACTIVE", source_ids=source_ids, snapshot_ids=snapshot_ids, scope_id=stable_id("telemetry-domain-scope", "missing-device"), asserted_by=f"product-policy:{self.version}", evidence_refs=(self.provenance, "telemetry:missing-device-reference")),
        ))
        return tuple(assertions)

    def entity_types(self, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult], relationships: Sequence[Any], domain_refs: tuple[str, ...]) -> tuple[CanonicalEntityType, ...]:
        source_tables = tuple(CanonicalSourceTable(source_id=source_id, snapshot_id=snapshots[source_id].snapshot.snapshot_id, table_id=self.role_table(catalogs[source_id]).table_id, schema_fingerprint=catalogs[source_id].source.schema_fingerprint) for source_id in sorted(catalogs))
        relationship_refs = tuple(item.decision_id for item in relationships)
        return (
            CanonicalEntityType(canonical_entity_type_id="entity_device", semantic_id="device", business_name="Telemetry device", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="device", identity_strategy="SOURCE_LOCAL_DEVICE_ID", identity_attribute_ids=("device_id",), source_table_refs=tuple(item for item in source_tables if self.source_role(catalogs[item.source_id]) == "device_registry"), canonical_attribute_ids=("device_name",), relationship_refs=relationship_refs, domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.provenance,)),
            CanonicalEntityType(canonical_entity_type_id="entity_location", semantic_id="location", business_name="Telemetry location", kind=CanonicalEntityKind.IDENTITY, entity_resolution_family="location", identity_strategy="SOURCE_LOCAL_LOCATION_ID", identity_attribute_ids=("location_id",), source_table_refs=tuple(item for item in source_tables if self.source_role(catalogs[item.source_id]) == "location_registry"), canonical_attribute_ids=("location_name",), relationship_refs=relationship_refs, domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.provenance,)),
            CanonicalEntityType(canonical_entity_type_id="entity_reading", semantic_id="reading", business_name="Telemetry reading", kind=CanonicalEntityKind.EVENT, entity_resolution_family="reading_event", identity_strategy="SOURCE_LOCAL_READING_ID", identity_attribute_ids=("reading_id",), source_table_refs=tuple(item for item in source_tables if self.source_role(catalogs[item.source_id]) == "reading_event"), canonical_attribute_ids=("observed_on", "temperature"), relationship_refs=relationship_refs, domain_assertion_refs=domain_refs, review_state="REVIEW_REQUIRED", provenance_refs=(self.provenance,)),
        )

    def canonical_relationships(self, relationships: Sequence[Any], review_decision_id_by_subject: Mapping[str, str]) -> tuple[Any, ...]:
        from dirty_data_to_olap.domain.contracts.canonical import CanonicalRelationship
        output = []
        for item in relationships:
            target = "entity_device" if "device" in item.subject_id or "device_ref" in item.subject_id else "entity_location"
            review_ref = review_decision_id_by_subject.get(item.decision_id)
            if review_ref is None:
                raise ValueError(f"telemetry relationship {item.decision_id} lacks an accepted evidence review")
            output.append(CanonicalRelationship(relationship_id=item.decision_id, from_entity_type_id="entity_reading", to_entity_type_id=target, cardinality=item.proposed_cardinality, upstream_decision_ref=item.decision_id, review_decision_ref=review_ref, conflict_refs=tuple(item.conflict_refs), provenance_refs=(item.decision_id, self.provenance)))
        return tuple(output)

    def entity_resolution_spec(self, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> None:
        return None

    def entity_resolution_requirements(self, entity_types: Sequence[CanonicalEntityType]) -> Mapping[str, EntityResolutionRequirement]:
        return {item.entity_resolution_family or item.semantic_id: EntityResolutionRequirement.ER_NOT_REQUIRED for item in entity_types}

    def identity_memberships(self, *, hypothesis, snapshots: Mapping[str, SourceSnapshotResult], catalogs: Mapping[str, SourceCatalog], policy_ref: str, entity_resolution_result: Any | None = None, entity_resolution_ref: str | None = None, domain_assertions: Sequence[Any] = ()) -> tuple[CanonicalIdentityMembership, ...]:
        type_by_role = {"device_registry": ("entity_device", "device"), "location_registry": ("entity_location", "location"), "reading_event": ("entity_reading", "reading")}
        memberships = []
        for source_id, catalog in sorted(catalogs.items()):
            entity_type, semantic = type_by_role[self.source_role(catalog)]
            for row in snapshots[source_id].record_references:
                memberships.append(CanonicalIdentityMembership(membership_group_id=stable_id("telemetry-membership", {"source": source_id, "record": row.record_ref}), canonical_entity_type_id=entity_type, entity_resolution_family=semantic, source_record_refs=(row.record_ref,), derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY, evidence_refs=(hypothesis.artifact_id, row.record_ref), policy_refs=(policy_ref,), rationale=f"the versioned telemetry policy preserves source-local {semantic} identity; same-name records are not merged", provenance_refs=(hypothesis.artifact_id, snapshots[source_id].snapshot.snapshot_id, row.record_ref)))
        return tuple(memberships)

    def validate_identity_memberships(self, memberships: Sequence[CanonicalIdentityMembership]) -> None:
        """Reject an injected cross-record device merge without authorization."""

        for membership in memberships:
            if membership.canonical_entity_type_id == "entity_device" and len(membership.source_record_refs) > 1:
                raise ValueError("telemetry same-name device hard-negative requires explicit identity evidence and authorization")

    @staticmethod
    def _typed_value(value: Any, logical_type: str) -> Any:
        if value is None:
            return None
        if logical_type == "DATE":
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        if logical_type == "DECIMAL":
            return value if isinstance(value, Decimal) else Decimal(str(value))
        return str(value) if logical_type == "STRING" else value

    def build_dataset_and_request(self, *, run_id: str, canonical: CanonicalModel, service, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]):
        maps = {item.record_ref: item for item in canonical.source_record_maps}
        devices: list[dict[str, Any]] = []
        locations: list[dict[str, Any]] = []
        readings: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        device_ids: dict[str, str] = {}
        location_ids: dict[str, str] = {}
        refs_by_source: dict[str, list[str]] = {}
        source_rows_by_role = []
        for source_id, catalog in sorted(catalogs.items()):
            role = self.source_role(catalog)
            table = self.role_table(catalog)
            refs_by_source[source_id] = []
            rows = tuple(service.read_rows(catalog, snapshots[source_id]))
            source_rows_by_role.append((source_id, catalog, role, table, rows))
            for row in rows:
                record_ref = row["record_ref"]
                mapping = maps.get(record_ref)
                if mapping is None:
                    raise ValueError(f"canonical model does not map telemetry source record {record_ref}")
                refs_by_source[source_id].append(record_ref)
                values = row["values"]
                if role == "device_registry":
                    device_id = str(self.logical_value(catalog, table.table_id, values, "device_id"))
                    device_ids[device_id] = mapping.canonical_entity_id
                    devices.append({"canonical_reference": mapping.canonical_entity_id, "device_id": device_id, "device_name": str(self.logical_value(catalog, table.table_id, values, "device_name")), "source_record_refs": (record_ref,)})
                elif role == "location_registry":
                    location_id = str(self.logical_value(catalog, table.table_id, values, "location_id"))
                    location_ids[location_id] = mapping.canonical_entity_id
                    locations.append({"canonical_reference": mapping.canonical_entity_id, "location_id": location_id, "location_name": str(self.logical_value(catalog, table.table_id, values, "location_name")), "source_record_refs": (record_ref,)})
        for source_id, catalog, role, table, rows in source_rows_by_role:
            if role != "reading_event":
                continue
            for row in rows:
                record_ref = row["record_ref"]
                mapping = maps[record_ref]
                values = row["values"]
                raw_device = self.logical_value(catalog, table.table_id, values, "device_ref")
                raw_location = self.logical_value(catalog, table.table_id, values, "location_ref")
                readings.append({"canonical_reference": mapping.canonical_entity_id, "reading_id": str(self.logical_value(catalog, table.table_id, values, "reading_id")), "device_id": device_ids.get(str(raw_device)) if raw_device is not None else None, "location_id": location_ids.get(str(raw_location)) if raw_location is not None else None, "observed_on": self.logical_value(catalog, table.table_id, values, "observed_on"), "temperature": self.logical_value(catalog, table.table_id, values, "temperature"), "source_record_refs": (record_ref,)})
        source_rows = [{"canonical_reference": source_id, "source_id": source_id, "source_role": self.source_role(catalogs[source_id]), "source_record_refs": tuple(refs_by_source[source_id])} for source_id in sorted(catalogs)]

        def make_table(table_id: str, concept: str, rows: Sequence[Mapping[str, Any]], columns: Sequence[tuple[str, str, bool]]) -> AnalyticalInputTable:
            lineage = tuple(sorted(catalogs))
            bindings = tuple(AnalyticalColumnBinding(column_id=stable_id("analytical-column", {"table": table_id, "column": name}), column_name=name, logical_type=logical, nullable=nullable, lineage_refs=lineage) for name, logical, nullable in columns)
            analytical_rows = tuple(AnalyticalInputRow(row_ref=stable_id("ainput-row", {"run": run_id, "table": table_id, "index": index, "reference": row["canonical_reference"]}), canonical_reference=str(row["canonical_reference"]), values=tuple(AnalyticalCell(column_name=name, value=self._typed_value(row.get(name), logical)) for name, logical, _nullable in columns), source_record_refs=tuple(row["source_record_refs"]), lineage_refs=lineage) for index, row in enumerate(rows))
            batch = AnalyticalRowBatch(batch_id=stable_id("analytical-batch", {"run": run_id, "table": table_id}), table_id=table_id, rows=analytical_rows, source_batch_refs=lineage, source_snapshot_fingerprints={key: snapshots[key].snapshot.source_fingerprint or snapshots[key].snapshot.schema_fingerprint for key in snapshots}, lineage_refs=lineage)
            return AnalyticalInputTable(table_id=table_id, canonical_concept_ref=concept, columns=bindings, batches=(batch,), source_table_refs=lineage, lineage_refs=lineage)

        device_input = make_table("device_input", "device", devices, (("device_id", "STRING", False), ("device_name", "STRING", False)))
        location_input = make_table("location_input", "location", locations, (("location_id", "STRING", False), ("location_name", "STRING", False)))
        source_input = make_table("source_input", "source", source_rows, (("source_id", "STRING", False), ("source_role", "STRING", False)))
        reading_input = make_table("reading_input", "reading", readings, (("reading_id", "STRING", False), ("device_id", "STRING", True), ("location_id", "STRING", True), ("observed_on", "DATE", False), ("temperature", "DECIMAL", False)))
        source_schema = {key: catalogs[key].source.schema_fingerprint for key in catalogs}
        source_snapshots = {key: snapshots[key].snapshot.source_fingerprint or snapshots[key].snapshot.schema_fingerprint for key in snapshots}
        dataset = AnalyticalInputDataset(dataset_id=stable_id("analytical-dataset", {"run": run_id, "canonical": canonical.model_id, "rows": [row.row_ref for table in (device_input, location_input, source_input, reading_input) for row in table.rows]}), canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, tables=(device_input, location_input, source_input, reading_input), source_schema_fingerprints=source_schema, source_snapshot_fingerprints=source_snapshots, allow_literal_sql=False, provenance_refs=(run_id, canonical.model_id, self.provenance, *sorted(catalogs)))
        binding = AnalyticalInputBinding(binding_id=stable_id("input-binding", {"run": run_id, "dataset": dataset.dataset_id}), canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, dataset_id=dataset.dataset_id, dataset_content_hash=dataset.content_hash, source_schema_fingerprints=source_schema, source_snapshot_fingerprints=source_snapshots, row_counts=dataset.row_counts, provenance_refs=(dataset.dataset_id, canonical.model_id, self.provenance))
        relationships = tuple(item for item in canonical.relationships if item.from_entity_type_id == "entity_reading")
        device_rel = next(item for item in relationships if item.to_entity_type_id == "entity_device")
        location_rel = next(item for item in relationships if item.to_entity_type_id == "entity_location")
        time_rel = stable_id("time_role", {"run": run_id, "policy": self.version})
        lineage = (run_id, canonical.model_id, self.provenance, *sorted(catalogs))
        device_dim = DimensionSpec(dimension_id="dim_device", table_name="dim_device", input_table_id="device_input", canonical_entity_type_id="entity_device", canonical_entity_refs=tuple(sorted(row["canonical_reference"] for row in devices)), role=DimensionRole.CONFORMED, eligibility_reason="source-local device_id identity is explicit; same-name devices remain separate", surrogate_key=WarehouseKeySpec(key_name="device_key", namespace="telemetry-v1.device"), alternate_key_columns=("device_id",), attributes=(DimensionAttributeSpec(attribute_id="device_id", column_name="device_id", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="device_name", column_name="device_name", logical_type="STRING", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="pinned telemetry snapshot has no history contract"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unknown device references are quarantined; no silent allocation"), provenance_refs=lineage)
        location_dim = DimensionSpec(dimension_id="dim_location", table_name="dim_location", input_table_id="location_input", canonical_entity_type_id="entity_location", canonical_entity_refs=tuple(sorted(row["canonical_reference"] for row in locations)), role=DimensionRole.CONFORMED, eligibility_reason="source-local location_id identity is explicit", surrogate_key=WarehouseKeySpec(key_name="location_key", namespace="telemetry-v1.location"), alternate_key_columns=("location_id",), attributes=(DimensionAttributeSpec(attribute_id="location_id", column_name="location_id", logical_type="STRING", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="location_name", column_name="location_name", logical_type="STRING", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="pinned telemetry snapshot has no history contract"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unknown location references are quarantined; no silent allocation"), provenance_refs=lineage)
        date_dim = DimensionSpec(dimension_id="dim_date", table_name="dim_date", canonical_entity_type_id="cet_date", canonical_entity_refs=("date",), role=DimensionRole.DATE, eligibility_reason="observed_on is an explicit Gregorian date role", surrogate_key=WarehouseKeySpec(key_name="date_key", namespace="telemetry-v1.date"), alternate_key_columns=("full_date",), attributes=(DimensionAttributeSpec(attribute_id="date_full", column_name="full_date", logical_type="DATE", derivation="FULL_DATE", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_year", column_name="year", logical_type="INTEGER", derivation="YEAR", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_month", column_name="month", logical_type="INTEGER", derivation="MONTH", nullable=False, lineage_refs=lineage), DimensionAttributeSpec(attribute_id="date_day", column_name="day", logical_type="INTEGER", derivation="DAY", nullable=False, lineage_refs=lineage)), scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="calendar is generated from observed_on"), unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.EXPLICIT_UNKNOWN_MEMBER, rationale="date role has an explicit unknown member", unknown_member_key=-1), calendar_policy="GREGORIAN_V1", provenance_refs=lineage)
        fact = FactSpec(fact_id="fact_device_reading", table_name="fact_device_reading", input_table_id="reading_input", fact_type=FactType.SNAPSHOT, canonical_event_type_id="entity_reading", canonical_event_refs=tuple(row["canonical_reference"] for row in readings), grain_spec_id="grain_reading", dimension_foreign_keys=(FactForeignKeySpec(relationship_ref=device_rel.relationship_id, dimension_id="dim_device", fact_column="device_key", dimension_key_column="device_key", canonical_entity_type_id="entity_device", input_reference_column="device_id"), FactForeignKeySpec(relationship_ref=location_rel.relationship_id, dimension_id="dim_location", fact_column="location_key", dimension_key_column="location_key", canonical_entity_type_id="entity_location", input_reference_column="location_id"), FactForeignKeySpec(relationship_ref=time_rel, relationship_scope=FactRelationshipScope.ANALYTICAL_TIME_ROLE, dimension_id="dim_date", fact_column="date_key", dimension_key_column="date_key", canonical_entity_type_id="cet_date", input_reference_column="observed_on")), degenerate_dimension_columns=("reading_id",), measure_ids=("measure_temperature",), date_role_columns=("observed_on",), relationship_refs=(device_rel.relationship_id, location_rel.relationship_id, time_rel), provenance_refs=lineage)
        grain = GrainSpec(grain_id="grain_reading", fact_id=fact.fact_id, human_readable_grain="one row per reading_id", key_columns=("reading_id",), null_policy=GrainNullPolicy.REJECT_NULLS, observed_row_count=0, duplicate_key_count=0, evidence_refs=tuple(sorted(catalogs)), provenance_refs=lineage)
        measure_config = self.data["measure"]
        measure = MeasureSpec(measure_id=measure_config["measure_id"], fact_id=fact.fact_id, field_name=measure_config["field_name"], semantic_name=measure_config["semantic_name"], aggregation_class=AggregationClass.SEMI_ADDITIVE, aggregation_rule=measure_config["aggregation_rule"], unit_semantics=measure_config["unit_semantics"], currency_semantics=measure_config["currency_semantics"], logical_type=measure_config["logical_type"], nullable=False, domain_assertion_refs=("telemetry:temperature-semantics",), provenance_refs=lineage)
        planning = AnalyticalPlanningRequest(request_id=stable_id("analytical-request", {"run": run_id, "canonical": canonical.model_id, "policy": self.version}), dimensions=(device_dim, location_dim, date_dim), facts=(fact,), grains=(grain,), measures=(measure,), accepted_relationship_refs=(device_rel.relationship_id, location_rel.relationship_id), deferred_concept_refs=tuple(self.data["deferred_concepts"]), deferred_concept_reasons=dict(self.data["deferred_concepts"]), domain_assertion_refs=("telemetry:temperature-semantics", "telemetry:hard-negative-same-name-device", "telemetry:missing-device-reference"), provenance_refs=lineage)
        return dataset, binding, planning

    def build_multi_source_dataset_and_request(self, *, run_id: str, canonical: CanonicalModel, service, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult], relationships: Sequence[Any]):
        return self.build_dataset_and_request(run_id=run_id, canonical=canonical, service=service, catalogs=catalogs, snapshots=snapshots)

    def source_records(self, *, service, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult]) -> tuple[Mapping[str, Any], ...]:
        output = []
        for source_id, catalog in sorted(catalogs.items()):
            role = self.source_role(catalog)
            table = self.role_table(catalog)
            ordinals = {item.record_ref: item.extraction_ordinal for item in snapshots[source_id].record_references}
            for row in service.read_rows(catalog, snapshots[source_id]):
                values = row["values"]
                logical = {name: self.logical_value(catalog, table.table_id, values, name) for name in ("device_id", "device_name", "location_id", "location_name", "reading_id", "device_ref", "location_ref", "observed_on", "temperature")}
                output.append({"source_id": source_id, "snapshot_id": snapshots[source_id].snapshot.snapshot_id, "table_id": table.table_id, "record_ref": row["record_ref"], "role": role, "subject_type": {"device_registry": "device", "location_registry": "location", "reading_event": "reading"}[role], "extraction_ordinal": ordinals.get(row["record_ref"], 0), "logical_values": logical})
        return tuple(output)

    def build_truth_and_accounting(self, *, run_id: str, source_records: Sequence[Mapping[str, Any]], snapshots: Mapping[str, SourceSnapshotResult], catalogs: Mapping[str, SourceCatalog], dataset: AnalyticalInputDataset, canonical: CanonicalModel, fact: FactSpec, measure: MeasureSpec, policy=None):
        maps = {item.record_ref: item for item in canonical.source_record_maps}
        types = {item.canonical_entity_type_id: item.semantic_id for item in canonical.entity_types}
        ordinals = {item.record_ref: item.extraction_ordinal for snapshot in snapshots.values() for item in snapshot.record_references}
        records = []
        source_entries = []
        source_expectations = []
        entities = []
        facts = []
        relationships = []
        valid_readings = []
        # Raw references are intentionally resolved only from the policy-owned
        # device/location rows; a missing reference is not guessed.
        device_by_key = {}
        location_by_key = {}
        for item in source_records:
            mapping = maps[item["record_ref"]]
            values = dict(item["logical_values"])
            if item["subject_type"] == "device" and values.get("device_id") is not None:
                device_by_key[str(values["device_id"])] = mapping.canonical_entity_id
            if item["subject_type"] == "location" and values.get("location_id") is not None:
                location_by_key[str(values["location_id"])] = mapping.canonical_entity_id
        for item in sorted(source_records, key=lambda value: (str(value["source_id"]), int(value.get("extraction_ordinal", 0)), str(value["record_ref"]))):
            mapping = maps[item["record_ref"]]
            subject_type = str(item["subject_type"])
            records.append(SourceTruthRecord(record_ref=item["record_ref"], source_id=item["source_id"], snapshot_id=item["snapshot_id"], table_id=item["table_id"], extraction_ordinal=int(item.get("extraction_ordinal", ordinals.get(item["record_ref"], 0))), subject_type=subject_type, canonical_entity_id=mapping.canonical_entity_id, terminal_disposition=RecordDisposition.EMITTED_DIRECT, output_reference=mapping.canonical_entity_id, values=dict(item["logical_values"]), provenance_refs=(item["snapshot_id"], item["record_ref"], self.provenance)))
            source_entries.append(RecordAccountingEntry(input_record_ref=item["record_ref"], disposition=RecordDisposition.EMITTED_DIRECT, output_or_group_ref=mapping.canonical_entity_id, transformation_or_policy_ref=self.provenance, reason=f"source-local {subject_type} identity is emitted directly", provenance_refs=(item["record_ref"], mapping.canonical_entity_id)))
            source_expectations.append(SourceTruthAccountingExpectation(boundary=AccountingBoundary.SOURCE_TO_CANONICAL, input_record_ref=item["record_ref"], expected_disposition=RecordDisposition.EMITTED_DIRECT, expected_output_or_group_ref=mapping.canonical_entity_id, reason_contains="emitted", provenance_refs=(item["record_ref"],)))
            entities.append(SourceTruthEntity(entity_type=subject_type, canonical_entity_id=mapping.canonical_entity_id, source_record_refs=(item["record_ref"],), expected_attributes={}, provenance_refs=(item["record_ref"], mapping.canonical_entity_id)))
            if subject_type == "reading":
                values = item["logical_values"]
                device = device_by_key.get(str(values.get("device_ref"))) if values.get("device_ref") is not None else None
                location = location_by_key.get(str(values.get("location_ref"))) if values.get("location_ref") is not None else None
                if device is not None and location is not None:
                    fact_ref = stable_id("truth-fact", {"run": run_id, "record": item["record_ref"]})
                    temperature = values["temperature"] if isinstance(values["temperature"], Decimal) else Decimal(str(values["temperature"]))
                    valid_readings.append((mapping.canonical_entity_id, fact_ref))
                    facts.append(SourceTruthFact(fact_ref=fact_ref, fact_id=fact.fact_id, source_record_refs=(item["record_ref"],), canonical_event_id=mapping.canonical_entity_id, grain_values={"reading_id": str(values["reading_id"])}, dimension_entity_ids={"dim_device": device, "dim_location": location}, measure_values={measure.field_name: temperature}, date_value=str(values["observed_on"]), provenance_refs=(item["record_ref"], fact.fact_id)))
                    relationships.extend((SourceTruthRelationship(relationship_ref=rel.relationship_id, from_record_ref=item["record_ref"], to_canonical_entity_id=target, required=True, provenance_refs=(item["record_ref"], rel.relationship_id)) for rel, target in ((next(rel for rel in canonical.relationships if rel.to_entity_type_id == "entity_device"), device), (next(rel for rel in canonical.relationships if rel.to_entity_type_id == "entity_location"), location))))
        valid_by_entity = dict(valid_readings)
        canonical_entries = []
        canonical_expectations = []
        for entity in entities:
            is_reading = entity.entity_type == "reading"
            fact_ref = valid_by_entity.get(entity.canonical_entity_id)
            disposition = RecordDisposition.EMITTED_DIRECT if not is_reading or fact_ref else RecordDisposition.QUARANTINED
            output_ref = f"dimension:{entity.entity_type}:{entity.canonical_entity_id}" if not is_reading else fact_ref
            reason = "canonical entity is materialized at the reviewed analytical boundary" if disposition is RecordDisposition.EMITTED_DIRECT else "canonical reading is quarantined because a device or location relationship is unresolved"
            canonical_entries.append(RecordAccountingEntry(input_record_ref=entity.canonical_entity_id, disposition=disposition, output_or_group_ref=output_ref, transformation_or_policy_ref=self.provenance, reason=reason, provenance_refs=(entity.canonical_entity_id,)))
            canonical_expectations.append(SourceTruthAccountingExpectation(boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL, input_record_ref=entity.canonical_entity_id, expected_disposition=disposition, expected_output_or_group_ref=output_ref, reason_contains="materialized" if disposition is RecordDisposition.EMITTED_DIRECT else "quarantined", provenance_refs=(entity.canonical_entity_id,)))
        aggregate_values = [(str(item.date_value), next(iter(item.measure_values.values()))) for item in facts]
        by_date: dict[str, Decimal] = {}
        for key, value in aggregate_values:
            by_date[key] = max(by_date.get(key, value), value)
        total = max(by_date.values()) if by_date else Decimal("0")
        aggregate_expectations = (SourceTruthAggregateExpectation(aggregate_id="temperature_global_max", fact_id=fact.fact_id, measure_field=measure.field_name, semantic_measure_ref=measure.measure_id, operation="MAX", expected=total, unit_semantics=measure.unit_semantics, provenance_refs=(fact.fact_id, measure.measure_id)), SourceTruthAggregateExpectation(aggregate_id="temperature_by_date_max", fact_id=fact.fact_id, measure_field=measure.field_name, semantic_measure_ref=measure.measure_id, operation="MAX", group_by=("dim_date",), expected=total, expected_by_key=by_date, unit_semantics=measure.unit_semantics, provenance_refs=(fact.fact_id, measure.measure_id)))
        source_snapshot_ids = {source_id: snapshots[source_id].snapshot.snapshot_id for source_id in sorted(snapshots)}
        source_snapshot_id = stable_id("telemetry-snapshot-set", source_snapshot_ids)
        truth = SourceTruthManifest(truth_id=stable_id("truth", {"run": run_id, "source_snapshot_ids": source_snapshot_ids, "records": [item.record_ref for item in records]}), truth_version=self.version, source_snapshot_id=source_snapshot_id, source_snapshot_ids=source_snapshot_ids, source_schema_fingerprints={source_id: catalogs[source_id].source.schema_fingerprint for source_id in sorted(catalogs)}, records=tuple(records), entities=tuple(entities), relationships=tuple(relationships), facts=tuple(facts), accounting_expectations=tuple((*source_expectations, *canonical_expectations)), aggregate_expectations=aggregate_expectations, expected_aggregates={"aggregates": [item.model_dump(mode="json") for item in aggregate_expectations]}, provenance_refs=("application.telemetry_policy", source_snapshot_id, self.provenance, *source_snapshot_ids.values()))
        accounting = RecordAccountingArtifact(accounting_id=stable_id("accounting", {"run": run_id, "source_snapshot_ids": source_snapshot_ids}), run_id=run_id, scopes=(RecordAccountingScope(scope_id=stable_id("accounting-scope", {"run": run_id, "boundary": AccountingBoundary.SOURCE_TO_CANONICAL.value}), boundary=AccountingBoundary.SOURCE_TO_CANONICAL, input_object_ref=source_snapshot_id, input_record_refs=tuple(item.record_ref for item in records), entries=tuple(source_entries), policy_version=self.version, provenance_refs=(truth.truth_id, source_snapshot_id)), RecordAccountingScope(scope_id=stable_id("accounting-scope", {"run": run_id, "boundary": AccountingBoundary.CANONICAL_TO_ANALYTICAL.value}), boundary=AccountingBoundary.CANONICAL_TO_ANALYTICAL, input_object_ref=canonical.model_id, input_record_refs=tuple(item.canonical_entity_id for item in entities), entries=tuple(canonical_entries), policy_version=self.version, provenance_refs=(truth.truth_id, canonical.model_id))), policy_version=self.version, provenance_refs=(truth.truth_id, canonical.model_id, self.provenance))
        return truth, accounting

    def record_accounting_ref(self, run_id: str) -> str:
        return stable_id("accounting", run_id)

    def validation_policy(self, *, canonical_model_id: str, materialization_id: str):
        from dirty_data_to_olap.domain.contracts.validation import ValidationPolicy, ValidationStatus
        settings = self.data["validation"]
        return ValidationPolicy(policy_id=stable_id("validation-policy", {"product": self.version, "canonical": canonical_model_id, "materialization": materialization_id}), policy_version=settings["policy_version"], required_check_ids=tuple(settings["required_check_ids"]), allowed_terminal_dispositions=(RecordDisposition.EMITTED_DIRECT, RecordDisposition.QUARANTINED), orphan_policy=dict(settings["orphan_policy"]), require_bidirectional_lineage=True, exact_numeric_comparison=True, monetary_status=ValidationStatus(settings["monetary_status"]), monetary_reason=settings["monetary_reason"], deferred_concepts=dict(self.data["deferred_concepts"]), provenance_refs=(self.provenance, materialization_id))


__all__ = ["TelemetryProductPolicy"]
