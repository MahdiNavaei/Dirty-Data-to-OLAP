"""Valentine boundary for bounded cross-source schema evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
import unicodedata
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirty_data_to_olap.adapters.dependencies.staged import DependencyInputIntegrityError, DependencyStagedReader
from dirty_data_to_olap.domain.contracts.profiling import ColumnProfile, ProfileResult
from dirty_data_to_olap.domain.contracts.schema_matching import (
    MatchingAuthorization, SchemaMatchArtifactReference, SchemaMatchCapability,
    SchemaMatchCapabilityStatus, SchemaMatchCandidate, SchemaMatchFailure,
    SchemaMatchFailureKind, SchemaMatchMode, SchemaMatchObservationScope,
    SchemaMatchPruningSummary, SchemaMatchRequest, SchemaMatchResult,
    SchemaMatchScore, SchemaMatchSearchPolicy, SchemaMatchSignal,
    SchemaMatchSignalFamily, SchemaMatchStatus, SchemaMatcherReference,
    schema_match_candidate_id, schema_match_config_hash, schema_match_score_id,
    schema_match_signal_id,
)
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SourceCatalog, SourceSnapshotResult, TableObservationStatus


class ValentineSchemaMatchingAdapter:
    """Use only the official Valentine package behind project-owned contracts."""

    name = "valentine-adapter"
    adapter_reference = AdapterReference(name=name, version="1.0", config_fingerprint="schema-matching-adapter-v1")

    def __init__(self, *, project_root: Path, reader: DependencyStagedReader | None = None, privacy_policy: Any | None = None) -> None:
        self.project_root = project_root.resolve()
        self.reader = reader or DependencyStagedReader()
        self.privacy_policy = privacy_policy

    def capability(self, request: SchemaMatchRequest) -> SchemaMatchCapability:
        try:
            import valentine  # type: ignore[import-not-found]
            version = str(getattr(valentine, "__version__", "1.0.0"))
        except Exception as error:
            return SchemaMatchCapability(capability_id="matching.adapter", status=SchemaMatchCapabilityStatus.UNAVAILABLE, engine="valentine", engine_version="unknown", requested_matchers=tuple(item.matcher_id for item in request.matcher_references), detail=f"official Valentine runtime unavailable: {error.__class__.__name__}")
        return SchemaMatchCapability(capability_id="matching.adapter", status=SchemaMatchCapabilityStatus.AVAILABLE, engine="valentine", engine_version=version, requested_matchers=tuple(item.matcher_id for item in request.matcher_references), detail="official PyPI Valentine runtime available; native objects remain adapter-local")

    def discover(
        self,
        request: SchemaMatchRequest,
        catalogs: Mapping[str, SourceCatalog],
        snapshots: Mapping[str, SourceSnapshotResult],
        *,
        profiles: Mapping[str, ProfileResult | Sequence[ColumnProfile]] | ProfileResult | None = None,
        dependencies: Mapping[str, Any] | None = None,
        artifact_root: Path | None = None,
        authorization: MatchingAuthorization | None = None,
    ) -> SchemaMatchResult:
        started = time.monotonic()
        capability = self.capability(request)
        failures: list[SchemaMatchFailure] = []
        config_hash = schema_match_config_hash(request)
        try:
            self._validate_entry(request, catalogs, snapshots, authorization)
            frames, metadata, scope = self._load_frames(request, catalogs, snapshots)
        except (ValueError, DependencyInputIntegrityError) as error:
            failures.append(SchemaMatchFailure(failure_id=self._failure_id(request, "input"), request_id=request.request_id, kind=SchemaMatchFailureKind.STAGED_INPUT_INTEGRITY_FAILED if isinstance(error, DependencyInputIntegrityError) else SchemaMatchFailureKind.PRIVACY_BLOCKED if isinstance(error, PermissionError) else SchemaMatchFailureKind.INPUT_INVALID, detail=str(error)))
            return self._result(request, catalogs, snapshots, capability, failures, scope=self._empty_scope(request, catalogs, snapshots), config_hash=config_hash, status=SchemaMatchStatus.FAILED)
        if capability.status is not SchemaMatchCapabilityStatus.AVAILABLE:
            failures.append(SchemaMatchFailure(failure_id=self._failure_id(request, "capability"), request_id=request.request_id, kind=SchemaMatchFailureKind.CAPABILITY_UNAVAILABLE, detail=capability.detail, retryable=True))
            return self._result(request, catalogs, snapshots, capability, failures, scope=scope, config_hash=config_hash, status=SchemaMatchStatus.FAILED)

        policy = request.search_policy
        source_pairs = list(combinations(request.source_ids, 2))
        pairs_before = len(source_pairs)
        if len(source_pairs) > policy.max_source_pairs:
            source_pairs = source_pairs[:policy.max_source_pairs]
            failures.append(self._budget_failure(request, "max_source_pairs"))
        table_pairs: list[tuple[str, str, str, str]] = []
        table_pairs_before = 0
        for left_source, right_source in source_pairs:
            left_tables = request.selected_table_ids_by_source[left_source][:policy.max_tables_per_source]
            right_tables = request.selected_table_ids_by_source[right_source][:policy.max_tables_per_source]
            table_pairs_before += len(left_tables) * len(right_tables)
            table_pairs.extend((left_source, left_table, right_source, right_table) for left_table in left_tables for right_table in right_tables)
        if len(table_pairs) > policy.max_table_pairs:
            table_pairs = table_pairs[:policy.max_table_pairs]
            failures.append(self._budget_failure(request, "max_table_pairs"))

        matcher_refs = request.matcher_references[:policy.max_matchers]
        native_results: dict[str, Mapping[Any, float]] = {}
        matcher_details: dict[str, Mapping[Any, Mapping[str, float]]] = {}
        evaluated_by_matcher: dict[str, int] = {}
        returned_by_matcher: dict[str, int] = {}
        matcher_calls = 0
        runtime_limited = False
        try:
            import valentine
            from valentine.algorithms import Coma, Cupid, DistributionBased
        except Exception:
            # Capability has already caught this; this branch is defensive.
            failures.append(SchemaMatchFailure(failure_id=self._failure_id(request, "runtime-import"), request_id=request.request_id, kind=SchemaMatchFailureKind.CAPABILITY_UNAVAILABLE, detail="official Valentine import failed after capability discovery", retryable=True))
            return self._result(request, catalogs, snapshots, capability, failures, scope=scope, config_hash=config_hash, status=SchemaMatchStatus.FAILED)

        selected_frames = {name: frame for name, frame in frames.items()}
        metadata_by_name = {name: item for name, item in metadata.items()}
        eligible_budget = sum(
            1
            for left_source, left_table, right_source, right_table in table_pairs
            for left in metadata_by_name[f"{left_source}::{left_table}"]["columns"].values()
            for right in metadata_by_name[f"{right_source}::{right_table}"]["columns"].values()
            if self._eligible_types(left, right, policy)
        )
        if eligible_budget > policy.max_column_pairs:
            failures.append(self._budget_failure(request, "max_column_pairs"))
            matcher_refs = ()
        for reference in matcher_refs:
            if time.monotonic() - started > policy.max_runtime_seconds:
                runtime_limited = True
                failures.append(self._budget_failure(request, "max_runtime_seconds"))
                break
            try:
                matcher = self._build_matcher(reference, request.mode, Coma, Cupid, DistributionBased)
                result = valentine.valentine_match(list(selected_frames.values()), matcher, df_names=list(selected_frames), instance_sample_size=policy.max_instance_rows_per_table)
                native_results[reference.matcher_id] = result
                matcher_details[reference.matcher_id] = {pair: result.get_details(pair) or {} for pair in result}
                matcher_calls += 1
                evaluated_by_matcher[reference.matcher_id] = len(self._eligible_pair_keys(metadata_by_name, policy))
                returned_by_matcher[reference.matcher_id] = len(result)
            except Exception as error:
                failures.append(SchemaMatchFailure(failure_id=self._failure_id(request, reference.matcher_id), request_id=request.request_id, kind=SchemaMatchFailureKind.MATCHER_FAILED, matcher_id=reference.matcher_id, detail=f"Valentine matcher failed: {error.__class__.__name__}", retryable=True))

        scores: list[SchemaMatchScore] = []
        signal_map: dict[str, SchemaMatchSignal] = {}
        candidate_scores: dict[str, list[str]] = {}
        candidate_meta: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        candidate_signal_refs: dict[str, tuple[str, ...]] = {}
        for reference in matcher_refs:
            result = native_results.get(reference.matcher_id)
            if result is None:
                continue
            directional = []
            for pair, raw in result.items():
                left = metadata_by_name.get(pair.source_table)
                right = metadata_by_name.get(pair.target_table)
                if left is None or right is None or left["source_id"] == right["source_id"]:
                    continue
                if pair.source_column not in left["columns"] or pair.target_column not in right["columns"]:
                    continue
                left_col = left["columns"][pair.source_column]
                right_col = right["columns"][pair.target_column]
                if not self._eligible_types(left_col, right_col, policy):
                    continue
                directional.append((left_col, right_col, float(raw)))
            directional.sort(key=lambda item: (-item[2], item[0]["column_id"], item[1]["column_id"]))
            ranks: dict[str, int] = {}
            for left_col, right_col, raw in directional:
                ranks[left_col["column_id"]] = ranks.get(left_col["column_id"], 0) + 1
                rank = ranks[left_col["column_id"]]
                if rank > policy.top_k_per_left_column:
                    continue
                candidate_id = schema_match_candidate_id(self._endpoint(left_col), self._endpoint(right_col))
                score_id = schema_match_score_id(reference.matcher_id, left_col["column_id"], right_col["column_id"], rank)
                score = SchemaMatchScore(score_id=score_id, matcher=reference, source_column_id=left_col["column_id"], target_column_id=right_col["column_id"], raw_native_score=raw, native_score_name=reference.score_name, native_score_semantics=reference.score_semantics, rank=rank, config_hash=config_hash, mode=request.mode, observation_scope=scope, provenance=self.adapter_reference)
                scores.append(score)
                candidate_scores.setdefault(candidate_id, []).append(score_id)
                candidate_meta[candidate_id] = (left_col, right_col)

        for candidate_id, (left_col, right_col) in candidate_meta.items():
            left_endpoint, right_endpoint = sorted((left_col, right_col), key=lambda item: (item["source_id"], item["table_id"], item["column_id"]))
            signal_refs = self._signals_for(left_endpoint, right_endpoint, profiles, dependencies, request, signal_map)
            candidate_scores[candidate_id] = tuple(candidate_scores[candidate_id])
            candidate_meta[candidate_id] = (left_endpoint, right_endpoint)
            candidate_signal_refs[candidate_id] = signal_refs

        ordered_candidates = sorted(candidate_meta.items(), key=lambda item: (-max((score.raw_native_score for score in scores if score.score_id in candidate_scores[item[0]]), default=0.0), item[0]))
        output_truncated = len(ordered_candidates) > policy.max_output_candidates
        if output_truncated:
            failures.append(self._budget_failure(request, "max_output_candidates"))
        ordered_candidates = ordered_candidates[:policy.max_output_candidates]
        candidates = tuple(self._candidate(candidate_id, pair[0], pair[1], candidate_scores[candidate_id], candidate_signal_refs[candidate_id], scope) for candidate_id, pair in ordered_candidates)
        complete = bool(native_results) and not failures and not runtime_limited and not output_truncated and len(native_results) == len(matcher_refs)
        pruning = self._pruning(request, catalogs, source_pairs, table_pairs, metadata_by_name, policy, scores, evaluated_by_matcher, returned_by_matcher, matcher_calls, output_truncated, failures)
        status = SchemaMatchStatus.COMPLETE if complete else SchemaMatchStatus.INCOMPLETE if native_results else SchemaMatchStatus.FAILED
        result = SchemaMatchResult(request=request, observation_scope=scope, candidates=candidates, scores=tuple(scores), signals=tuple(signal_map.values()), failures=tuple(failures), capabilities=(capability,), pruning=pruning, status=status)
        if artifact_root is not None:
            result = SchemaMatchingArtifactStore(self.project_root).publish(result, run_root=artifact_root)
        return result

    def _validate_entry(self, request, catalogs, snapshots, authorization):
        if set(catalogs) != set(request.source_ids) or set(snapshots) != set(request.source_ids):
            raise ValueError("matching inputs must cover every selected source")
        for source_id in request.source_ids:
            if catalogs[source_id].source_id != source_id or snapshots[source_id].snapshot.source_id != source_id or snapshots[source_id].snapshot.snapshot_id != request.snapshot_ids[source_id]:
                raise ValueError("catalog and snapshot bindings do not match the request")
        if request.mode is SchemaMatchMode.INSTANCE_AWARE:
            if self.privacy_policy is None or not authorization or not self.privacy_policy.verify_schema_matching_authorization(authorization, source_ids=request.source_ids, snapshot_ids=request.snapshot_ids, table_ids_by_source=request.selected_table_ids_by_source, column_ids_by_table=request.selected_column_ids_by_table, artifact_ids=tuple(batch.batch_id for snapshot in snapshots.values() for batch in snapshot.batches)):
                raise PermissionError("instance-aware matching requires policy-issued exact-scope authorization")

    def _load_frames(self, request, catalogs, snapshots):
        frames: dict[str, Any] = {}
        metadata: dict[str, dict[str, Any]] = {}
        staged_rows: dict[str, int] = {}
        sampled_rows: dict[str, int] = {}
        complete: dict[str, bool] = {}
        modes: dict[str, Any] = {}
        sampled_refs: list[tuple[str, str, str]] = []
        for source_id in request.source_ids:
            catalog = catalogs[source_id]
            snapshot = snapshots[source_id]
            modes[source_id] = snapshot.snapshot.observation_scope.mode
            selected_tables = tuple(table for table in catalog.tables if table.table_id in request.selected_table_ids_by_source[source_id])[:request.search_policy.max_tables_per_source]
            for table in selected_tables:
                columns = tuple(column for column in catalog.columns if column.table_id == table.table_id)
                requested = request.selected_column_ids_by_table.get(table.table_id)
                if requested:
                    columns = tuple(column for column in columns if column.column_id in requested)
                remaining = request.search_policy.max_columns_per_source - sum(len(item["columns"]) for item in metadata.values() if item["source_id"] == source_id)
                columns = columns[:max(remaining, 0)]
                rows = []
                for item in self.reader.iter_table(snapshot, catalog, table, [column.physical_name for column in columns], project_root=self.project_root):
                    rows.append(item)
                selected = self._sample_rows(rows, request.search_policy.max_instance_rows_per_table, request.sample_seed)
                sampled_refs.extend((source_id, table.table_id, item.record_ref) for item in selected)
                table_key = f"{source_id}::{table.table_id}"
                import pandas as pd
                frames[table_key] = pd.DataFrame([item.values for item in selected], columns=[column.physical_name for column in columns])
                metadata[table_key] = {"source_id": source_id, "snapshot_id": request.snapshot_ids[source_id], "table_id": table.table_id, "columns": {column.physical_name: {"source_id": source_id, "snapshot_id": request.snapshot_ids[source_id], "table_id": table.table_id, "column_id": column.column_id, "physical_name": column.physical_name, "normalized_type": column.normalized_physical_type, "descriptor": column} for column in columns}, "profiles": {}}
                table_id = table.table_id
                staged_rows[table_id] = len(rows)
                sampled_rows[table_id] = len(selected)
                observation = next((item for item in snapshot.table_observations if item.table_id == table_id), None)
                complete[table_id] = observation is not None and observation.status is TableObservationStatus.FULLY_OBSERVED
        sample_identity = hashlib.sha256(json.dumps(sorted(sampled_refs), separators=(",", ":")).encode()).hexdigest()[:32]
        scope = SchemaMatchObservationScope(source_ids=request.source_ids, snapshot_ids=dict(request.snapshot_ids), table_ids_by_source={key: tuple(value) for key, value in request.selected_table_ids_by_source.items()}, column_ids_by_table={key: tuple(value) for key, value in request.selected_column_ids_by_table.items()}, source_snapshot_modes=modes, complete_by_table=complete, staged_rows_by_table=staged_rows, sampled_rows_by_table=sampled_rows, sample_seed=request.sample_seed, sample_mode=request.sample_mode, sample_algorithm_version=request.sample_algorithm_version, sample_identity=sample_identity, reduced_scope=any(value < staged_rows.get(key, 0) for key, value in sampled_rows.items()))
        return frames, metadata, scope

    @staticmethod
    def _sample_rows(rows, limit, seed):
        if len(rows) <= limit:
            return rows
        ranked = sorted(enumerate(rows), key=lambda item: hashlib.sha256(f"{seed}:{item[1].record_ref}".encode()).hexdigest())
        return [rows[index] for index, _ in sorted(ranked[:limit])]

    @staticmethod
    def _build_matcher(reference, mode, Coma, Cupid, DistributionBased):
        config = dict(reference.configuration)
        if reference.name == "Coma":
            return Coma(**config)
        if reference.name == "Cupid":
            return Cupid(**config)
        if reference.name == "DistributionBased":
            return DistributionBased(**config)
        raise ValueError(f"unsupported official Valentine matcher: {reference.name}")

    @staticmethod
    def _endpoint(column):
        return (column["source_id"], column["table_id"], column["column_id"])

    @staticmethod
    def _eligible_types(left, right, policy):
        if not policy.reject_type_incompatible:
            return True
        def family(value):
            value = (value or "unknown").lower()
            if any(token in value for token in ("int", "float", "decimal", "numeric", "number")): return "numeric"
            if any(token in value for token in ("date", "time")): return "datetime"
            if any(token in value for token in ("bool",)): return "bool"
            if any(token in value for token in ("char", "text", "string", "object")): return "text"
            return "unknown"
        left_family, right_family = family(left["normalized_type"]), family(right["normalized_type"])
        return "unknown" in (left_family, right_family) or left_family == right_family

    def _signals_for(self, left, right, profiles, dependencies, request, signal_map):
        refs = []
        name_value = SequenceMatcher(None, self._normalize(left["physical_name"]), self._normalize(right["physical_name"])).ratio()
        name = SchemaMatchSignal(signal_id=schema_match_signal_id(SchemaMatchSignalFamily.NAME_LEXICAL, left["column_id"], right["column_id"], round(name_value, 8)), family=SchemaMatchSignalFamily.NAME_LEXICAL, value=name_value, semantics="deterministic Unicode/case/separator/camel-token lexical similarity; physical names retained only at the adapter boundary")
        signal_map[name.signal_id] = name; refs.append(name.signal_id)
        structural_value = 1.0 if self._eligible_types(left, right, request.search_policy) else 0.0
        structural = SchemaMatchSignal(signal_id=schema_match_signal_id(SchemaMatchSignalFamily.SCHEMA_STRUCTURAL, left["column_id"], right["column_id"], structural_value), family=SchemaMatchSignalFamily.SCHEMA_STRUCTURAL, value=structural_value, semantics="conservative normalized physical-type compatibility; not a semantic assertion")
        signal_map[structural.signal_id] = structural; refs.append(structural.signal_id)
        profile_map = self._profile_map(profiles)
        lp, rp = profile_map.get(left["column_id"]), profile_map.get(right["column_id"])
        if lp and rp and lp.observed_distinct_ratio is not None and rp.observed_distinct_ratio is not None:
            value = 1.0 - min(abs(lp.observed_distinct_ratio - rp.observed_distinct_ratio), 1.0)
            signal = SchemaMatchSignal(signal_id=schema_match_signal_id(SchemaMatchSignalFamily.PROFILE, left["column_id"], right["column_id"], round(value, 8)), family=SchemaMatchSignalFamily.PROFILE, value=value, semantics="profile distinct-ratio compatibility over the profile observation scopes", evidence_refs=(lp.profile_id, rp.profile_id))
            signal_map[signal.signal_id] = signal; refs.append(signal.signal_id)
        if request.mode is SchemaMatchMode.INSTANCE_AWARE:
            signal = SchemaMatchSignal(signal_id=schema_match_signal_id(SchemaMatchSignalFamily.INSTANCE, left["column_id"], right["column_id"], "local"), family=SchemaMatchSignalFamily.INSTANCE, value=None, semantics="official Valentine instance-aware native score; raw values remained local and are absent from artifacts")
            signal_map[signal.signal_id] = signal; refs.append(signal.signal_id)
        for assertion in request.domain_assertions:
            if {assertion.get("left_column_id"), assertion.get("right_column_id")} == {left["column_id"], right["column_id"]}:
                assertion_id = str(assertion.get("assertion_id", "unidentified-domain-assertion"))
                assertion_version = str(assertion.get("version", "unversioned"))
                signal = SchemaMatchSignal(signal_id=schema_match_signal_id(SchemaMatchSignalFamily.DOMAIN_ASSERTION, left["column_id"], right["column_id"], assertion_id), family=SchemaMatchSignalFamily.DOMAIN_ASSERTION, value=None, semantics=f"explicit domain assertion {assertion_id} version {assertion_version}; assertion changes evidence only and never the native matcher score", evidence_refs=(assertion_id,), assertion_bound=True)
                signal_map[signal.signal_id] = signal; refs.append(signal.signal_id)
        dependency_refs = self._dependency_refs(left, right, dependencies)
        if dependency_refs:
            signal = SchemaMatchSignal(signal_id=schema_match_signal_id(SchemaMatchSignalFamily.DEPENDENCY, left["column_id"], right["column_id"], dependency_refs), family=SchemaMatchSignalFamily.DEPENDENCY, value=None, semantics="structural dependency evidence only; does not assert a key, foreign key or accepted mapping", evidence_refs=tuple(dependency_refs))
            signal_map[signal.signal_id] = signal; refs.append(signal.signal_id)
        return tuple(refs)

    @staticmethod
    def _profile_map(profiles):
        if profiles is None: return {}
        if isinstance(profiles, ProfileResult): return {item.column_id: item for item in profiles.columns}
        output = {}
        for value in profiles.values() if isinstance(profiles, Mapping) else (profiles,):
            items = value.columns if isinstance(value, ProfileResult) else value
            for item in items: output[item.column_id] = item
        return output

    @staticmethod
    def _dependency_refs(left, right, dependencies):
        refs = []
        for result in (dependencies or {}).values():
            for item in getattr(result, "inclusion_dependencies", ()):
                if left["column_id"] in item.left_columns and right["column_id"] in item.right_columns:
                    refs.append(item.evidence_id)
                if right["column_id"] in item.left_columns and left["column_id"] in item.right_columns:
                    refs.append(item.evidence_id)
        return tuple(dict.fromkeys(refs))

    @staticmethod
    def _normalize(value):
        value = unicodedata.normalize("NFKC", value)
        value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
        return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))

    @staticmethod
    def _candidate(candidate_id, left, right, score_refs, signals, scope):
        return SchemaMatchCandidate(candidate_id=candidate_id, source_id=left["source_id"], source_snapshot_id=left["snapshot_id"], source_table_id=left["table_id"], source_column_id=left["column_id"], source_column_name=left["physical_name"], target_source_id=right["source_id"], target_snapshot_id=right["snapshot_id"], target_table_id=right["table_id"], target_column_id=right["column_id"], target_column_name=right["physical_name"], score_refs=tuple(score_refs), signal_refs=tuple(signals), observation_scope=scope, risk_flags=("candidate_only", "native_score_not_probability"))

    def _pruning(self, request, catalogs, source_pairs, table_pairs, metadata, policy, scores, evaluated_by_matcher, returned_by_matcher, matcher_calls, output_truncated, failures):
        all_columns = sum(len(tuple(column for column in catalog.columns if column.table_id in request.selected_table_ids_by_source[source_id])) for source_id, catalog in catalogs.items())
        before = sum(len(metadata[f"{left_source}::{left_table}"]["columns"]) * len(metadata[f"{right_source}::{right_table}"]["columns"]) for left_source, left_table, right_source, right_table in table_pairs)
        type_pruned = sum(1 for left_source, left_table, right_source, right_table in table_pairs for left in metadata[f"{left_source}::{left_table}"]["columns"].values() for right in metadata[f"{right_source}::{right_table}"]["columns"].values() if not self._eligible_types(left, right, policy))
        evaluated = max(before - type_pruned, 0)
        return SchemaMatchPruningSummary(source_pairs_before_bound=len(request.source_ids) * (len(request.source_ids) - 1) // 2, source_pairs_evaluated=len(source_pairs), tables_before_bound=sum(len(request.selected_table_ids_by_source[source_id]) for source_id in request.source_ids), tables_evaluated=sum(len(request.selected_table_ids_by_source[source_id][:policy.max_tables_per_source]) for source_id in request.source_ids), table_pairs_before_bound=sum(len(request.selected_table_ids_by_source[left_source][:policy.max_tables_per_source]) * len(request.selected_table_ids_by_source[right_source][:policy.max_tables_per_source]) for left_source, right_source in source_pairs), table_pairs_evaluated=len(table_pairs), column_pairs_before_pruning=before, column_pairs_pruned_by_type=type_pruned, column_pairs_pruned_by_scope=0, column_pairs_pruned_by_context=0, column_pairs_pruned_by_budget=max(evaluated - policy.max_column_pairs, 0), column_pairs_evaluated=min(evaluated, policy.max_column_pairs), matcher_calls=matcher_calls, evaluated_by_matcher=evaluated_by_matcher, returned_by_matcher=returned_by_matcher, output_candidates_emitted=min(len({score.score_id for score in scores}), policy.max_output_candidates), output_truncated=output_truncated, truncation_reasons=tuple(failure.detail for failure in failures if failure.kind is SchemaMatchFailureKind.BUDGET_EXCEEDED))

    def _result(self, request, catalogs, snapshots, capability, failures, *, scope, config_hash, status):
        empty = SchemaMatchPruningSummary(source_pairs_before_bound=0, source_pairs_evaluated=0, tables_before_bound=0, tables_evaluated=0, table_pairs_before_bound=0, table_pairs_evaluated=0, column_pairs_before_pruning=0, column_pairs_pruned_by_type=0, column_pairs_pruned_by_scope=0, column_pairs_pruned_by_context=0, column_pairs_pruned_by_budget=0, column_pairs_evaluated=0, matcher_calls=0, evaluated_by_matcher={}, returned_by_matcher={}, output_candidates_emitted=0, output_truncated=False)
        return SchemaMatchResult(request=request, observation_scope=scope, failures=tuple(failures), capabilities=(capability,), pruning=empty, status=status)

    @staticmethod
    def _empty_scope(request, catalogs, snapshots):
        return SchemaMatchObservationScope(source_ids=request.source_ids, snapshot_ids=dict(request.snapshot_ids), table_ids_by_source={key: tuple(value) for key, value in request.selected_table_ids_by_source.items()}, column_ids_by_table=dict(request.selected_column_ids_by_table), source_snapshot_modes={key: snapshots[key].snapshot.observation_scope.mode for key in request.source_ids}, complete_by_table={}, staged_rows_by_table={}, sampled_rows_by_table={}, sample_seed=request.sample_seed, sample_mode=request.sample_mode, sample_algorithm_version=request.sample_algorithm_version, sample_identity="empty")

    @staticmethod
    def _failure_id(request, value): return "schema_failure_" + hashlib.sha256(f"{request.request_id}:{value}".encode()).hexdigest()[:32]

    @staticmethod
    def _budget_failure(request, value): return SchemaMatchFailure(failure_id=ValentineSchemaMatchingAdapter._failure_id(request, value), request_id=request.request_id, kind=SchemaMatchFailureKind.BUDGET_EXCEEDED, detail=f"schema matching bound reached: {value}")

    @staticmethod
    def _eligible_pair_keys(metadata, policy):
        return tuple(metadata)


class SchemaMatchingArtifactStore:
    """Atomic aggregate-only JSON publication under the execution context."""

    def __init__(self, project_root: Path): self.project_root = project_root.resolve()

    def publish(self, result: SchemaMatchResult, *, run_root: Path) -> SchemaMatchResult:
        root = run_root.resolve()
        root.relative_to(self.project_root)
        output: list[SchemaMatchArtifactReference] = []
        items = [("candidates", "schema_match_candidate", item.candidate_id, item) for item in result.candidates] + [("scores", "schema_match_score", item.score_id, item) for item in result.scores] + [("signals", "schema_match_signal", item.signal_id, item) for item in result.signals] + [("failures", "schema_match_failure", item.failure_id, item) for item in result.failures] + [("capabilities", "schema_match_capability", item.capability_id, item) for item in result.capabilities] + [("evaluations", "schema_match_evaluation", item.evaluation_id, item) for item in result.evaluations] + [("summary", "schema_match_summary", f"summary-{result.request.request_id}", result.pruning), ("scope", "schema_match_observation_scope", f"scope-{result.request.request_id}", result.observation_scope)]
        try:
            for folder, kind, item_id, item in items:
                path = root / "schema_matching" / folder / f"{item_id}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                temporary = path.with_suffix(".json.writing")
                temporary.write_text(payload, encoding="utf-8")
                temporary.replace(path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                output.append(SchemaMatchArtifactReference(artifact_id=item_id, artifact_type=kind, artifact_location=path.relative_to(self.project_root).as_posix(), content_hash=digest))
        except Exception as error:
            raise ValueError(f"schema matching artifact publication failed: {error.__class__.__name__}") from None
        return result.model_copy(update={"artifacts": tuple(output)})
