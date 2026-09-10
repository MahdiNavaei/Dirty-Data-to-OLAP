"""Bounded official-Splink entity-resolution adapter.

Only project-owned contracts cross this boundary. Raw identity values are
restricted to the private in-process/DataFrame/DuckDB lifetime and are never
placed in returned contracts, logs or artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from dirty_data_to_olap.adapters.dependencies.staged import DependencyInputIntegrityError, DependencyStagedReader
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERComparisonSpecification,
    EntityCluster,
    EntityClusterDiagnostic,
    EntityMatchEdge,
    EntityResolutionAuthorization,
    EntityResolutionArtifactReference,
    EntityResolutionCapability,
    EntityResolutionCapabilityStatus,
    EntityResolutionEngineReference,
    EntityResolutionFailure,
    EntityResolutionFailureKind,
    EntityResolutionMode,
    EntityResolutionModelEvidence,
    EntityResolutionObservationScope,
    EntityResolutionResult,
    EntityResolutionRunMetrics,
    EntityResolutionSpec,
    EntityResolutionStatus,
    entity_edge_id,
)
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SourceCatalog, SourceSnapshotResult


class SplinkEntityResolutionAdapter:
    name = "splink-entity-resolution-adapter"
    runtime_dependency = "splink==4.0.17"
    adapter_reference = AdapterReference(name=name, version="1.0", config_fingerprint="entity-resolution-splink-v1")

    def __init__(self, *, project_root: Path, reader: DependencyStagedReader | None = None, privacy_policy: Any | None = None) -> None:
        self.project_root = project_root.resolve()
        self.reader = reader or DependencyStagedReader()
        self.privacy_policy = privacy_policy

    def capability(self, spec: EntityResolutionSpec) -> EntityResolutionCapability:
        try:
            import splink  # type: ignore[import-not-found]
            version = str(getattr(splink, "__version__", "unknown"))
            if version != "4.0.17":
                return EntityResolutionCapability(capability_id="entity-resolution.adapter", status=EntityResolutionCapabilityStatus.INCOMPATIBLE, engine="splink", engine_version=version, detail="runtime is not the pinned stable project dependency")
            return EntityResolutionCapability(capability_id="entity-resolution.adapter", status=EntityResolutionCapabilityStatus.AVAILABLE, engine="splink", engine_version=version, detail="official Splink runtime available; native objects remain adapter-local")
        except Exception as error:
            return EntityResolutionCapability(capability_id="entity-resolution.adapter", status=EntityResolutionCapabilityStatus.UNAVAILABLE, engine="splink", engine_version="unknown", detail=f"official Splink runtime unavailable: {error.__class__.__name__}")

    def run(self, spec: EntityResolutionSpec, catalogs: Mapping[str, SourceCatalog], snapshots: Mapping[str, SourceSnapshotResult], *, authorization: EntityResolutionAuthorization | None, artifact_root: Path | None = None) -> EntityResolutionResult:
        started = time.monotonic()
        capability = self.capability(spec)
        failures: list[EntityResolutionFailure] = []
        private_root = self._private_root(spec)
        scope = self._empty_scope(spec, snapshots)
        empty_metrics = EntityResolutionRunMetrics(available_staged_records=0, records_read=0, records_sampled=0, all_pairs=0, candidate_pairs=0, pairs_rejected_by_budget=0, predictions_emitted=0, clusters_emitted=0, incomplete=True)
        engine = EntityResolutionEngineReference(engine="splink", engine_version=capability.engine_version, adapter=self.adapter_reference, runtime_dependency=self.runtime_dependency, source_revision="official-splink-v4.0.17", native_objects_isolated=True)
        if capability.status is not EntityResolutionCapabilityStatus.AVAILABLE:
            failures.append(self._failure("capability", EntityResolutionFailureKind.CAPABILITY_UNAVAILABLE, capability.detail, "capability"))
            return EntityResolutionResult(spec=spec, observation_scope=scope, engine=engine, failures=tuple(failures), capabilities=(capability,), metrics=empty_metrics, status=EntityResolutionStatus.FAILED)
        try:
            self._validate_entry(spec, catalogs, snapshots, authorization)
            records, scope = self._load_records(spec, catalogs, snapshots, private_root)
            all_pairs = self._all_pairs(spec, records)
            if len(records) > spec.execution_budget.max_records:
                raise _ERBudget("max_records")
            if all_pairs > spec.execution_budget.max_all_pairs_diagnostic:
                failures.append(self._failure("all-pairs", EntityResolutionFailureKind.BUDGET_EXCEEDED, "all-pairs diagnostic bound exceeded; candidate blocking remains bounded and no Cartesian fallback is used", "blocking"))
            import pandas as pd
            frame = pd.DataFrame(records)
            if frame.empty:
                raise ValueError("no staged records contain the selected identity fields")
            candidate_pairs_by_rule = self._candidate_counts(frame, spec)
            candidate_pairs = sum(candidate_pairs_by_rule.values())
            if candidate_pairs > spec.execution_budget.max_candidate_pairs:
                raise _ERBudget("max_candidate_pairs")
            edges, model, clusters, diagnostics, predicted = self._execute_splink(frame, spec, private_root, started)
            metrics = EntityResolutionRunMetrics(available_staged_records=sum(scope.available_staged_rows_by_table.values()), records_read=scope.records_read, records_sampled=scope.records_sampled, all_pairs=all_pairs, candidate_pairs=candidate_pairs, candidate_pairs_by_rule=candidate_pairs_by_rule, pairs_rejected_by_budget=0, predictions_emitted=predicted, clusters_emitted=len(clusters), incomplete=bool(failures))
            status = EntityResolutionStatus.INCOMPLETE if failures else EntityResolutionStatus.COMPLETE
            result = EntityResolutionResult(spec=spec, observation_scope=scope, engine=engine, model=model, edges=tuple(edges), clusters=tuple(clusters), diagnostics=tuple(diagnostics), failures=tuple(failures), capabilities=(capability,), metrics=metrics, status=status)
            if artifact_root is not None:
                result = self._publish(result, artifact_root)
            return result
        except PermissionError as error:
            failures.append(self._failure("authorization", EntityResolutionFailureKind.AUTHORIZATION_INVALID, str(error), "authorization"))
        except DependencyInputIntegrityError as error:
            failures.append(self._failure("staged", EntityResolutionFailureKind.STAGED_INPUT_INTEGRITY_FAILED, str(error), "input"))
        except _ERBudget as error:
            failures.append(self._failure("budget", EntityResolutionFailureKind.BUDGET_EXCEEDED, str(error), "budget"))
        except _ERTraining as error:
            failures.append(self._failure("training", EntityResolutionFailureKind.TRAINING_FAILED, str(error), "training"))
        except Exception as error:
            failures.append(self._failure("run", EntityResolutionFailureKind.PREDICTION_FAILED, f"entity resolution failed: {error.__class__.__name__}", "execution", retryable=True))
        finally:
            self._cleanup_private_root(private_root)
        return EntityResolutionResult(spec=spec, observation_scope=scope, engine=engine, failures=tuple(failures), capabilities=(capability,), metrics=empty_metrics, status=EntityResolutionStatus.FAILED)

    def _validate_entry(self, spec, catalogs, snapshots, authorization) -> None:
        if self.privacy_policy is None or authorization is None:
            raise PermissionError("entity resolution requires policy-issued exact-scope authorization")
        batch_ids = tuple(batch.batch_id for source_id in spec.source_ids for batch in snapshots[source_id].batches if batch.table_id in spec.table_ids_by_source[source_id])
        column_ids = tuple(field.column_id for field in spec.identity_fields)
        if not self.privacy_policy.verify_entity_resolution_authorization(authorization, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=column_ids, batch_ids=batch_ids):
            raise PermissionError("entity resolution authorization does not exactly bind the requested spec and staged scope")
        if set(catalogs) != set(spec.source_ids) or set(snapshots) != set(spec.source_ids):
            raise ValueError("entity resolution inputs must cover every selected source")
        for source_id in spec.source_ids:
            if catalogs[source_id].source_id != source_id or snapshots[source_id].snapshot.snapshot_id != spec.snapshot_ids[source_id]:
                raise ValueError("catalog or snapshot binding mismatch")
        for field in spec.identity_fields:
            if field.source_id not in spec.source_ids or field.snapshot_id != spec.snapshot_ids[field.source_id] or field.table_id not in spec.table_ids_by_source[field.source_id]:
                raise ValueError("identity field is outside the authorized spec scope")

    def _load_records(self, spec, catalogs, snapshots, private_root):
        fields_by_table: dict[tuple[str, str], tuple[Any, ...]] = {}
        for source_id in spec.source_ids:
            for table_id in spec.table_ids_by_source[source_id]:
                fields_by_table[(source_id, table_id)] = tuple(field for field in spec.identity_fields if field.source_id == source_id and field.table_id == table_id)
        records: list[dict[str, Any]] = []
        available: dict[str, int] = {}
        batches: list[str] = []
        hashes: list[str] = []
        by_ref: dict[str, Any] = {}
        for source_id in spec.source_ids:
            snapshot_result = snapshots[source_id]
            catalog = catalogs[source_id]
            for table_id in spec.table_ids_by_source[source_id]:
                fields = fields_by_table[(source_id, table_id)]
                observation = next((item for item in snapshot_result.table_observations if item.table_id == table_id), None)
                available[table_id] = observation.rows_observed if observation is not None else 0
                table = next(item for item in catalog.tables if item.table_id == table_id)
                if not fields:
                    continue
                physical = [field.physical_name for field in fields]
                for batch in snapshot_result.batches:
                    if batch.table_id == table_id:
                        batches.append(batch.batch_id); hashes.append(batch.content_hash)
                for row in self.reader.iter_table(snapshot_result, catalog, table, physical, project_root=self.project_root):
                    record = {"unique_id": f"{source_id}::{row.record_ref}", "source_dataset": source_id, "_record_ref": row.record_ref, "_source_id": source_id, "_snapshot_id": snapshot_result.snapshot.snapshot_id}
                    for field in fields:
                        record[self._field_column(field.field_id)] = self._normalize(row.values.get(field.physical_name), field, spec)
                    by_ref[row.record_ref] = record
        records = list(by_ref.values())
        sample_count = len(records)
        scope = EntityResolutionObservationScope(source_ids=spec.source_ids, snapshot_ids=dict(spec.snapshot_ids), table_ids_by_source={key: tuple(value) for key, value in spec.table_ids_by_source.items()}, input_batch_ids=tuple(dict.fromkeys(batches)), input_batch_hashes=tuple(dict.fromkeys(hashes)), available_staged_rows_by_table=available, records_read=len(records), records_sampled=sample_count, sample_seed=spec.training_policy.random_seed, sample_algorithm_version="full_authorized_staged_rows_v1", raw_values_local_only=True)
        return records, scope

    def _execute_splink(self, frame, spec, private_root, started):
        import splink.internals.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator
        private_root.mkdir(parents=True, exist_ok=True)
        db_path = private_root / "private.duckdb"
        comparisons = []
        field_map = {field.field_id: field for field in spec.identity_fields}
        for item in spec.comparisons:
            column = self._field_column(item.field_id)
            if item.method == "jaro_winkler":
                comparisons.append(cl.JaroWinklerAtThresholds(column, item.thresholds or (0.95, 0.85)))
            elif item.method == "levenshtein":
                comparisons.append(cl.LevenshteinAtThresholds(column, item.thresholds or (1, 2)))
            else:
                comparisons.append(cl.ExactMatch(column))
        blocking_sql = [self._safe_blocking_sql(rule, field_map) for rule in spec.blocking_rules]
        settings = SettingsCreator(link_type={EntityResolutionMode.LINK_ONLY: "link_only", EntityResolutionMode.DEDUPE_ONLY: "dedupe_only", EntityResolutionMode.LINK_AND_DEDUPE: "link_and_dedupe"}[spec.mode], comparisons=comparisons, blocking_rules_to_generate_predictions=blocking_sql, probability_two_random_records_match=0.01, max_iterations=spec.training_policy.max_em_iterations, retain_matching_columns=True, retain_intermediate_calculation_columns=True)
        linker = Linker(frame.drop(columns=["_record_ref", "_source_id", "_snapshot_id"]), settings, DuckDBAPI(connection=str(db_path)), input_table_aliases="entity_records", set_up_basic_logging=False)
        try:
            linker.training.estimate_u_using_random_sampling(max_pairs=spec.training_policy.max_u_pairs, seed=spec.training_policy.random_seed)
            for rule_id in spec.training_policy.em_blocking_rule_ids:
                rule = next(item for item in spec.blocking_rules if item.rule_id == rule_id)
                linker.training.estimate_parameters_using_expectation_maximisation(self._safe_blocking_sql(rule, field_map))
            if not bool(getattr(linker._settings_obj, "_is_fully_trained", False)):
                raise _ERTraining("Splink completed without estimates for every comparison; default parameters are not accepted")
        except Exception as error:
            raise _ERTraining(f"Splink u/m training failed: {error.__class__.__name__}") from None
        predictions = linker.inference.predict()
        predicted_frame = predictions.as_pandas_dataframe()
        clusters_frame = linker.clustering.cluster_pairwise_predictions_at_threshold(predictions, spec.threshold_policy.review_probability_threshold).as_pandas_dataframe()
        model_id = "er-model-" + hashlib.sha256(f"{spec.spec_id}:{spec.fingerprint}:{self.runtime_dependency}".encode()).hexdigest()[:24]
        model = EntityResolutionModelEvidence(model_id=model_id, training_policy_id=spec.training_policy.policy_id, u_training_method="splink.estimate_u_using_random_sampling", m_training_method="splink.estimate_parameters_using_expectation_maximisation", random_seed=spec.training_policy.random_seed, training_blocking_rule_ids=spec.training_policy.em_blocking_rule_ids, trained=True, training_provenance=f"splink:{self.runtime_dependency};spec:{spec.spec_id};config:{spec.fingerprint}")
        record_map = {row["unique_id"]: row for row in frame.to_dict(orient="records")}
        edges: list[EntityMatchEdge] = []
        graph: dict[str, set[str]] = defaultdict(set)
        for row in predicted_frame.to_dict(orient="records"):
            left_id, right_id = row.get("unique_id_l"), row.get("unique_id_r")
            if not left_id or not right_id or left_id not in record_map or right_id not in record_map:
                continue
            left, right = record_map[left_id], record_map[right_id]
            evidence = self._independent_evidence(left, right, spec)
            probability = float(row.get("match_probability", 0.0) or 0.0)
            weight = float(row.get("match_weight", 0.0) or 0.0)
            decision = "MATCH" if probability >= spec.threshold_policy.match_probability_threshold else "REVIEW" if probability >= spec.threshold_policy.review_probability_threshold else "NON_MATCH"
            risk: list[str] = []
            if spec.threshold_policy.require_independent_evidence and decision == "MATCH" and len(evidence) < 2:
                decision = "REVIEW"; risk.append("insufficient_independent_evidence")
            if not evidence:
                risk.append("no_non_placeholder_agreement")
            edge = EntityMatchEdge(edge_id=entity_edge_id(left["_record_ref"], right["_record_ref"], model_id), left_record_ref=left["_record_ref"], right_record_ref=right["_record_ref"], left_source_id=left["_source_id"], right_source_id=right["_source_id"], left_snapshot_id=left["_snapshot_id"], right_snapshot_id=right["_snapshot_id"], match_weight=weight, match_probability=probability, decision=decision, comparison_evidence_refs=tuple(evidence), blocking_rule_ids=tuple(rule.rule_id for rule in spec.blocking_rules), model_evidence_ref=model.model_id, independent_evidence_refs=tuple(evidence), risk_flags=tuple(risk))
            edges.append(edge)
            if decision in {"MATCH", "REVIEW"}:
                graph[left_id].add(right_id); graph[right_id].add(left_id)
        clusters, diagnostics = self._clusters(clusters_frame, graph, record_map, edges, spec, model_id)
        return edges, model, clusters, diagnostics, len(predicted_frame)

    def _clusters(self, clusters_frame, graph, record_map, edges, spec, model_id):
        components: list[set[str]] = []
        seen: set[str] = set()
        for root in sorted(graph):
            if root in seen: continue
            todo = [root]; component = set()
            while todo:
                current = todo.pop()
                if current in seen: continue
                seen.add(current); component.add(current); todo.extend(graph[current] - seen)
            components.append(component)
        edge_by_pair = {frozenset((f"{item.left_source_id}::{item.left_record_ref}", f"{item.right_source_id}::{item.right_record_ref}")): item for item in edges}
        clusters: list[EntityCluster] = []; diagnostics: list[EntityClusterDiagnostic] = []
        for index, component in enumerate(sorted(components, key=lambda item: (len(item), sorted(item)))):
            refs = tuple(sorted(record_map[item]["_record_ref"] for item in component))
            cluster_id = "entity_cluster_" + hashlib.sha256("|".join(refs).encode()).hexdigest()[:24]
            internal = sum(1 for left in component for right in component if left < right and frozenset((left, right)) in edge_by_pair)
            unsafe_bridge = len(component) >= 3 and internal < len(component) * (len(component) - 1) // 2
            internal_edges = [edge_by_pair[pair] for pair in edge_by_pair if all(endpoint in component for endpoint in pair)]
            evidence_count = sum(len(edge.independent_evidence_refs) for edge in internal_edges)
            placeholder_only = bool(internal_edges) and all(not edge.independent_evidence_refs for edge in internal_edges)
            conflicting_anchors = False
            flags = tuple(flag for flag, active in (("unsafe_bridge", unsafe_bridge), ("placeholder_only", placeholder_only), ("conflicting_anchors", conflicting_anchors)) if active)
            diagnostic_id = "entity_diag_" + hashlib.sha256(cluster_id.encode()).hexdigest()[:24]
            diagnostics.append(EntityClusterDiagnostic(diagnostic_id=diagnostic_id, cluster_id=cluster_id, connected_component_size=len(component), independent_evidence_count=evidence_count, placeholder_only=placeholder_only, conflicting_anchors=conflicting_anchors, unsafe_bridge=unsafe_bridge, largest_cluster_guard=len(component) > spec.clustering_policy.max_cluster_size, detail="candidate cluster diagnostic; placeholder/null, bridge and size guards are evidence flags only; no canonical entity asserted"))
            clusters.append(EntityCluster(cluster_id=cluster_id, record_refs=refs, edge_refs=tuple(edge.edge_id for edge in edges if edge.left_record_ref in refs and edge.right_record_ref in refs), decision="REVIEW_CLUSTER" if unsafe_bridge or len(component) > spec.clustering_policy.max_cluster_size else "CANDIDATE_CLUSTER", diagnostic_refs=(diagnostic_id,), risk_flags=flags))
        return clusters, diagnostics

    @staticmethod
    def _candidate_counts(frame, spec):
        output = {}
        for rule in spec.blocking_rules:
            columns = [SplinkEntityResolutionAdapter._field_column(field_id) for field_id in rule.field_ids]
            count = 0
            values = frame[columns].where(frame[columns].notna(), None).to_dict(orient="records")
            for left_index in range(len(values)):
                for right_index in range(left_index + 1, len(values)):
                    if spec.mode is EntityResolutionMode.LINK_ONLY and frame.iloc[left_index]["source_dataset"] == frame.iloc[right_index]["source_dataset"]:
                        continue
                    if all(values[left_index].get(column) is not None and values[left_index].get(column) == values[right_index].get(column) for column in columns):
                        if values[left_index].get(columns[0]) is not None:
                            count += 1
            output[rule.rule_id] = count
        return output

    @staticmethod
    def _independent_evidence(left, right, spec):
        refs = []
        for field in spec.identity_fields:
            column = SplinkEntityResolutionAdapter._field_column(field.field_id)
            if left.get(column) is not None and right.get(column) is not None and left.get(column) == right.get(column):
                refs.append(f"identity-agreement:{field.field_id}")
        return refs

    @staticmethod
    def _safe_blocking_sql(rule, field_map):
        expression = rule.sql_expression
        for field_id, field in field_map.items():
            expression = re.sub(rf"\b{re.escape(field.physical_name)}\b", SplinkEntityResolutionAdapter._field_column(field_id), expression)
            expression = re.sub(rf"\b{re.escape(field_id)}\b", SplinkEntityResolutionAdapter._field_column(field_id), expression)
        return expression

    @staticmethod
    def _field_column(field_id):
        return "f_" + hashlib.sha256(field_id.encode()).hexdigest()[:16]

    @staticmethod
    def _normalize(value, field, spec):
        if value is None:
            return None
        text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
        text = re.sub(r"\s+", " ", text)
        if not text or text in {"unknown", "n/a", "na", "none", "null", "-", "not provided", "not available"} or (text.isdigit() and len(text) >= 4 and len(set(text)) == 1):
            return None
        rule = next(item for item in spec.normalization_rules if item.rule_id == field.normalization_rule_id)
        if "DIGITS_ONLY" in rule.operations:
            if field.semantic_role.lower() == "phone" and not rule.country_context:
                raise ValueError("phone digit normalization requires explicit country/context policy")
            text = re.sub(r"\D", "", text)
        return text or None

    def _private_root(self, spec):
        root = (self.project_root / spec.privacy_context.project_temp_root).resolve()
        root.relative_to(self.project_root)
        return root / ("run-" + hashlib.sha256(f"{spec.spec_id}:{spec.fingerprint}".encode()).hexdigest()[:20])

    def _cleanup_private_root(self, path):
        if path.exists() and path.is_dir():
            shutil.rmtree(path)

    def _publish(self, result, artifact_root):
        root = artifact_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        location = root / "entity_resolution_result.json"
        temporary = root / ".entity_resolution_result.json.tmp"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(location)
        reference = EntityResolutionArtifactReference(artifact_id="entity-resolution-artifact-" + digest[:24], artifact_location=str(location.relative_to(self.project_root).as_posix()), content_hash=digest)
        return result.model_copy(update={"artifacts": (reference,)})

    @staticmethod
    def _all_pairs(spec, records):
        if spec.mode is EntityResolutionMode.DEDUPE_ONLY:
            return len(records) * (len(records) - 1) // 2
        if spec.mode is EntityResolutionMode.LINK_ONLY:
            counts: dict[str, int] = defaultdict(int)
            for record in records:
                counts[record["source_dataset"]] += 1
            return sum(left * right for index, left in enumerate(counts.values()) for right in list(counts.values())[index + 1:])
        total = len(records) * (len(records) - 1) // 2
        return total

    @staticmethod
    def _empty_scope(spec, snapshots):
        return EntityResolutionObservationScope(source_ids=spec.source_ids, snapshot_ids=dict(spec.snapshot_ids), table_ids_by_source={key: tuple(value) for key, value in spec.table_ids_by_source.items()}, input_batch_ids=(), input_batch_hashes=(), available_staged_rows_by_table={}, records_read=0, records_sampled=0, sample_seed=spec.training_policy.random_seed, sample_algorithm_version="full_authorized_staged_rows_v1", raw_values_local_only=True)

    @staticmethod
    def _failure(failure_id, kind, detail, stage, retryable=False):
        return EntityResolutionFailure(failure_id=f"entity-resolution-{failure_id}", kind=kind, detail=detail, stage=stage, retryable=retryable)


class _ERBudget(RuntimeError):
    pass


class _ERTraining(RuntimeError):
    pass
