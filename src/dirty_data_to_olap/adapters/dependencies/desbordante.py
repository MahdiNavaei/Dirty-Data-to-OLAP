"""Replaceable Desbordante adapter over immutable staged evidence.

Desbordante is intentionally an optional runtime capability.  Importing this
module never imports it; a Windows run therefore reports an explicit
capability failure instead of silently substituting a home-grown engine.
"""

from __future__ import annotations

import csv
import math
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from dirty_data_to_olap.adapters.dependencies.staged import DependencyInputIntegrityError, DependencyStagedReader
from dirty_data_to_olap.domain.contracts.dependency import (
    DependencyCapability,
    DependencyCapabilityStatus,
    DependencyEvidenceState,
    DependencyFailure,
    DependencyFailureKind,
    DependencyKind,
    DependencyObservationScope,
    DependencyProvenance,
    DependencyRequest,
    DependencyResult,
    DependencySearchStats,
    DependencyStageStatus,
    DependencyViolation,
    FunctionalDependencyEvidence,
    InclusionDependencyEvidence,
    KeyCandidate,
    NullPolicy,
    RelationshipCandidate,
    dependency_config_hash,
    dependency_id,
)
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SourceCatalog, SourceSnapshotResult, TableDescriptor, TableObservationStatus


class DependencyEngine(Protocol):
    name: str
    version: str

    def discover_ucc(self, path: Path) -> Sequence[Any]: ...
    def discover_fd(self, path: Path, *, approximate: bool, max_error: float, max_lhs: int) -> Sequence[Any]: ...
    def discover_ind(self, paths: Sequence[Path], *, approximate: bool, max_error: float, max_arity: int) -> Sequence[Any]: ...


class DesbordantePythonEngine:
    """Thin native-binding call boundary; native objects do not leave it."""

    name = "desbordante"

    def __init__(self, module: Any) -> None:
        self._module = module
        self.version = str(getattr(module, "__version__", "unknown"))

    @classmethod
    def try_create(cls) -> "DesbordantePythonEngine | None":
        try:
            import desbordante  # type: ignore[import-not-found]
        except Exception:
            return None
        return cls(desbordante)

    @staticmethod
    def _table(path: Path) -> tuple[str, str, bool]:
        return (str(path), ",", True)

    def discover_ucc(self, path: Path) -> Sequence[Any]:
        algo = self._module.ucc.algorithms.Default()
        algo.load_data(table=self._table(path))
        algo.execute()
        return tuple(algo.get_uccs())

    def discover_fd(self, path: Path, *, approximate: bool, max_error: float, max_lhs: int) -> Sequence[Any]:
        algo = self._module.afd.algorithms.Tane() if approximate else self._module.fd.algorithms.HyFD()
        algo.load_data(table=self._table(path))
        if approximate:
            algo.execute(error=max_error, max_lhs=max_lhs)
        else:
            algo.execute(max_lhs=max_lhs)
        return tuple(algo.get_fds())

    def discover_ind(self, paths: Sequence[Path], *, approximate: bool, max_error: float, max_arity: int) -> Sequence[Any]:
        module = self._module.aind if approximate else self._module.ind
        algo = module.algorithms.Mind() if approximate else module.algorithms.Spider()
        algo.load_data(tables=[self._table(path) for path in paths])
        if approximate:
            algo.execute(error=max_error, max_arity=max_arity)
        else:
            algo.execute()
        return tuple(algo.get_inds())


class DesbordanteDependencyAdapter:
    """Read staged rows, call the engine, then emit only project contracts."""

    name = "desbordante-adapter"
    adapter_reference = AdapterReference(name=name, version="1.0", config_fingerprint="dependency-adapter-v1")

    def __init__(self, *, project_root: Path, engine: DependencyEngine | None = None, reader: DependencyStagedReader | None = None) -> None:
        self.project_root = project_root.resolve()
        self.engine = engine if engine is not None else DesbordantePythonEngine.try_create()
        self.reader = reader or DependencyStagedReader()

    def capability(self, request: DependencyRequest) -> DependencyCapability:
        if self.engine is None:
            return DependencyCapability(capability_id="dependencies.adapter", status=DependencyCapabilityStatus.UNAVAILABLE, engine="desbordante", requested_kinds=request.requested_kinds, detail="Desbordante Python binding is unavailable in the current environment")
        return DependencyCapability(capability_id="dependencies.adapter", status=DependencyCapabilityStatus.AVAILABLE, engine=self.engine.name, engine_version=self.engine.version, requested_kinds=request.requested_kinds, detail="native engine binding is importable")

    def discover(
        self,
        request: DependencyRequest,
        catalog: SourceCatalog,
        snapshot_result: SourceSnapshotResult,
        *,
        profiles: Any | None = None,
        artifact_root: Path | None = None,
    ) -> DependencyResult:
        self._validate_entry(request, catalog, snapshot_result)
        selected = tuple(table for table in catalog.tables if table.table_id in request.selected_table_ids)
        columns_by_table = {table.table_id: tuple(column for column in catalog.columns if column.table_id == table.table_id)[: request.search_policy.max_columns_per_table] for table in selected}
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        record_refs_by_table: dict[str, list[str]] = {}
        failures: list[DependencyFailure] = []
        try:
            for table in selected:
                rows = []
                refs = []
                for item in self.reader.iter_table(snapshot_result, catalog, table, [column.physical_name for column in columns_by_table[table.table_id]], project_root=self.project_root):
                    rows.append(item.values)
                    refs.append(item.record_ref)
                rows_by_table[table.table_id] = rows
                record_refs_by_table[table.table_id] = refs
        except DependencyInputIntegrityError as error:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", str(error)), request_id=request.request_id, kind=DependencyFailureKind.STAGED_INPUT_INTEGRITY_FAILED, detail=str(error)))
            return self._failed_result(request, selected, snapshot_result, rows_by_table, failures, self.capability(request))

        scope = self._scope(request, snapshot_result, selected)
        stats = self._search_stats(request, selected, columns_by_table, rows_by_table)
        capability = self.capability(request)
        if stats.candidate_column_pairs > request.search_policy.max_column_pairs:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", "column-pair-budget"), request_id=request.request_id, kind=DependencyFailureKind.BUDGET_EXCEEDED, detail="dependency column-pair search budget would be exceeded; no unbounded IND search was started", retryable=False))
            return self._failed_result(request, selected, snapshot_result, rows_by_table, failures, capability, scope=scope, stats=stats)
        if capability.status is not DependencyCapabilityStatus.AVAILABLE:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", "capability"), request_id=request.request_id, kind=DependencyFailureKind.CAPABILITY_UNAVAILABLE, detail=capability.detail, retryable=True))
            return self._failed_result(request, selected, snapshot_result, rows_by_table, failures, capability, scope=scope, stats=stats)

        temp_root = self._temp_root(request)
        temp_root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        self._deadline = started + request.search_policy.max_runtime_seconds
        try:
            paths = {table.table_id: self._write_engine_input(temp_root, table, columns_by_table[table.table_id], rows_by_table[table.table_id]) for table in selected}
            provenance = lambda algorithm, table_ids: DependencyProvenance(source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=tuple(table_ids), engine=self.engine.name, engine_version=self.engine.version, algorithm=algorithm, algorithm_config_hash=dependency_config_hash(request), adapter=self.adapter_reference, created_at=datetime.now(timezone.utc))
            keys = self._discover_keys(request, selected, columns_by_table, rows_by_table, record_refs_by_table, paths, scope, provenance, failures)
            fds = self._discover_fds(request, selected, columns_by_table, rows_by_table, record_refs_by_table, paths, scope, provenance, failures)
            inds, relationships = self._discover_inds(request, selected, columns_by_table, rows_by_table, record_refs_by_table, paths, scope, provenance, failures)
            emitted = len(keys) + len(fds) + len(inds) + len(relationships)
            runtime_exceeded = time.monotonic() - started > request.search_policy.max_runtime_seconds
            if runtime_exceeded:
                failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", "runtime-budget"), request_id=request.request_id, kind=DependencyFailureKind.BUDGET_EXCEEDED, detail="dependency engine exceeded the configured wall-clock budget", retryable=False))
            truncated = stats.truncated or emitted > request.search_policy.max_output_candidates or runtime_exceeded
            if truncated:
                reasons = list(stats.truncation_reasons) or ["max_output_candidates"]
                if runtime_exceeded and "max_runtime_seconds" not in reasons:
                    reasons.append("max_runtime_seconds")
                stats = stats.model_copy(update={"truncated": True, "truncation_reasons": tuple(reasons)})
            remaining = request.search_policy.max_output_candidates
            capped_keys = tuple(keys[:remaining]); remaining -= len(capped_keys)
            capped_fds = tuple(fds[:remaining]); remaining -= len(capped_fds)
            capped_inds = tuple(inds[:remaining]); remaining -= len(capped_inds)
            capped_relationships = tuple(relationships[:remaining])
            stats = stats.model_copy(update={"emitted_candidates": min(emitted, request.search_policy.max_output_candidates)})
            result = DependencyResult(request=request, observation_scope=scope, key_candidates=capped_keys, functional_dependencies=capped_fds, inclusion_dependencies=capped_inds, relationship_candidates=capped_relationships, failures=tuple(failures), capabilities=(capability,), search_stats=stats, status=DependencyStageStatus.INCOMPLETE if failures or stats.truncated or not all(scope.complete_by_table.values()) else DependencyStageStatus.COMPLETE)
            if artifact_root is not None:
                from .artifacts import DependencyArtifactStore
                result = DependencyArtifactStore(self.project_root).publish(result, run_root=artifact_root)
            return result
        except Exception:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", "engine"), request_id=request.request_id, kind=DependencyFailureKind.ENGINE_FAILED, detail="dependency engine failed before a complete project-owned result was available", retryable=False))
            return self._failed_result(request, selected, snapshot_result, rows_by_table, failures, capability, scope=scope, stats=stats)
        finally:
            self._deadline = None
            shutil.rmtree(temp_root, ignore_errors=True)

    def _discover_keys(self, request, tables, columns_by_table, rows_by_table, refs_by_table, paths, scope, provenance, failures):
        if DependencyKind.UCC not in request.requested_kinds:
            return []
        output = []
        for table in tables:
            if self._runtime_expired(request):
                failures.append(self._budget_failure(request, "ucc-runtime"))
                break
            try:
                native_uccs = self.engine.discover_ucc(paths[table.table_id])
                for ucc in native_uccs:
                    indices = tuple(int(value) for value in getattr(ucc, "indices", ()))
                    cols = tuple(columns_by_table[table.table_id][index].physical_name for index in indices)
                    if not cols:
                        continue
                    rows = rows_by_table[table.table_id]
                    eligible = [row for row in rows if not any(row.get(column) is None for column in cols)]
                    distinct = len({tuple(row.get(column) for column in cols) for row in eligible})
                    uniqueness = distinct / len(eligible) if eligible else 0.0
                    output.append(KeyCandidate(candidate_id=dependency_id("key", [table.table_id, cols]), source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, columns=cols, uniqueness_ratio=uniqueness, physical_missing_ratio=(len(rows) - len(eligible)) / len(rows) if rows else 0.0, duplicate_count=max(len(eligible) - distinct, 0), observation_scope=scope, state=DependencyEvidenceState.OBSERVED, provenance=provenance("UCC.Default", [table.table_id])))
            except Exception:
                failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", ["ucc", table.table_id]), request_id=request.request_id, kind=DependencyFailureKind.ENGINE_FAILED, detail="UCC discovery failed for the selected table", table_id=table.table_id))
        return output

    def _discover_fds(self, request, tables, columns_by_table, rows_by_table, refs_by_table, paths, scope, provenance, failures):
        output = []
        if not ({DependencyKind.FD, DependencyKind.AFD} & set(request.requested_kinds)):
            return output
        for table in tables:
            for approximate, kind in ((False, DependencyKind.FD), (True, DependencyKind.AFD)):
                if kind not in request.requested_kinds:
                    continue
                if self._runtime_expired(request):
                    failures.append(self._budget_failure(request, "fd-runtime"))
                    return output
                try:
                    native_fds = self.engine.discover_fd(paths[table.table_id], approximate=approximate, max_error=request.search_policy.max_error_ratio, max_lhs=request.search_policy.max_determinant_width)
                    columns = columns_by_table[table.table_id]
                    for fd in native_fds:
                        lhs_indices = tuple(int(value) for value in getattr(fd, "lhs_indices", ()))
                        rhs_index = int(getattr(fd, "rhs_index"))
                        determinant = tuple(columns[index].physical_name for index in lhs_indices)
                        dependent = (columns[rhs_index].physical_name,)
                        metrics = _fd_metrics(rows_by_table[table.table_id], refs_by_table[table.table_id], determinant, dependent, request.null_policy)
                        violation = DependencyViolation(violation_id=dependency_id("violation", [table.table_id, determinant, dependent]), kind=kind, record_refs=tuple(metrics["refs"][:20]), count=metrics["violations"], detail_code="FD_DETERMINANT_HAS_MULTIPLE_DEPENDENT_VALUES")
                        output.append(FunctionalDependencyEvidence(evidence_id=dependency_id("fd", [table.table_id, determinant, dependent, approximate]), source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, determinant=determinant, dependent=dependent, approximate=approximate, support_ratio=metrics["support"], error_ratio=metrics["error"], violation_count=metrics["violations"], violations=(violation,) if metrics["violations"] else (), null_policy=request.null_policy, observation_scope=scope, state=DependencyEvidenceState.OBSERVED, provenance=provenance("AFD.Tane" if approximate else "FD.HyFD", [table.table_id])))
                except Exception:
                    failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", ["fd", table.table_id, approximate]), request_id=request.request_id, kind=DependencyFailureKind.ENGINE_FAILED, detail="functional dependency discovery failed for the selected table", table_id=table.table_id))
        return output

    def _discover_inds(self, request, tables, columns_by_table, rows_by_table, refs_by_table, paths, scope, provenance, failures):
        if not ({DependencyKind.IND, DependencyKind.APPROXIMATE_IND} & set(request.requested_kinds)):
            return [], []
        output = []
        relationships = []
        for approximate, kind in ((False, DependencyKind.IND), (True, DependencyKind.APPROXIMATE_IND)):
            if kind not in request.requested_kinds:
                continue
            if self._runtime_expired(request):
                failures.append(self._budget_failure(request, "ind-runtime"))
                break
            try:
                native_inds = self.engine.discover_ind(tuple(paths.values()), approximate=approximate, max_error=request.search_policy.max_error_ratio, max_arity=request.search_policy.max_determinant_width)
                table_list = list(tables)
                for ind in native_inds:
                    lhs = ind.get_lhs()
                    rhs = ind.get_rhs()
                    left_table = table_list[int(lhs.table_index)]
                    right_table = table_list[int(rhs.table_index)]
                    if left_table.table_id == right_table.table_id:
                        continue
                    left_cols = tuple(columns_by_table[left_table.table_id][int(index)].physical_name for index in lhs.column_indices)
                    right_cols = tuple(columns_by_table[right_table.table_id][int(index)].physical_name for index in rhs.column_indices)
                    if len(left_cols) != len(right_cols) or not left_cols:
                        continue
                    metrics = _ind_metrics(rows_by_table[left_table.table_id], refs_by_table[left_table.table_id], rows_by_table[right_table.table_id], left_cols, right_cols, request.null_policy)
                    risk = metrics["right_distinct"] <= request.search_policy.low_cardinality_distinct_limit or (metrics["right_rows"] > 0 and metrics["right_distinct"] / metrics["right_rows"] <= request.search_policy.low_cardinality_distinct_ratio)
                    evidence_id = dependency_id("ind", [left_table.table_id, left_cols, right_table.table_id, right_cols, approximate])
                    violation = DependencyViolation(violation_id=dependency_id("violation", evidence_id), kind=kind, record_refs=tuple(metrics["refs"][:20]), count=metrics["orphan_count"], detail_code="IND_LEFT_VALUE_NOT_IN_RIGHT_DOMAIN")
                    evidence = InclusionDependencyEvidence(evidence_id=evidence_id, source_id=request.source_id, snapshot_id=request.snapshot_id, left_table_id=left_table.table_id, left_columns=left_cols, right_table_id=right_table.table_id, right_columns=right_cols, approximate=approximate, coverage_ratio=metrics["coverage"], violation_ratio=metrics["violation_ratio"], left_distinct_count=metrics["left_distinct"], right_distinct_count=metrics["right_distinct"], orphan_count=metrics["orphan_count"], target_uniqueness_ratio=metrics["target_uniqueness"], type_compatible=_types_compatible(columns_by_table[left_table.table_id], columns_by_table[right_table.table_id], left_cols, right_cols), low_cardinality_risk=risk, violations=(violation,) if metrics["orphan_count"] else (), null_policy=request.null_policy, observation_scope=scope, state=DependencyEvidenceState.OBSERVED, provenance=provenance("AIND.Mind" if approximate else "IND.Spider", [left_table.table_id, right_table.table_id]))
                    output.append(evidence)
                    if not risk:
                        relationships.append(RelationshipCandidate(candidate_id=dependency_id("rel", evidence_id), source_id=request.source_id, snapshot_id=request.snapshot_id, from_table=left_table.table_id, from_columns=left_cols, to_table=right_table.table_id, to_columns=right_cols, source_orphan_ratio=metrics["orphan_ratio"], target_uniqueness_ratio=metrics["target_uniqueness"], type_compatible=evidence.type_compatible, low_cardinality_risk=risk, evidence_refs=(evidence_id,)))
            except Exception:
                failures.append(DependencyFailure(failure_id=dependency_id("dep_failure", ["ind", approximate]), request_id=request.request_id, kind=DependencyFailureKind.ENGINE_FAILED, detail="inclusion dependency discovery failed for the selected table set"))
        return output, relationships

    def _validate_entry(self, request, catalog, snapshot_result):
        if request.source_id != catalog.source_id or request.snapshot_id != snapshot_result.snapshot.snapshot_id:
            raise ValueError("dependency request is not bound to the supplied source snapshot")
        known = {table.table_id for table in catalog.tables}
        if set(request.selected_table_ids) - known:
            raise ValueError("dependency request selects an unknown table")
        if len(request.selected_table_ids) > request.search_policy.max_tables:
            raise ValueError("dependency request exceeds its table search budget")
        if not request.privacy_context.local_only:
            raise ValueError("dependency analysis requires local-only privacy context")

    def _scope(self, request, snapshot_result, tables):
        batches = tuple(batch for batch in snapshot_result.batches if batch.table_id in request.selected_table_ids)
        observations = {item.table_id: item for item in snapshot_result.table_observations}
        rows = {table.table_id: sum(batch.row_count for batch in batches if batch.table_id == table.table_id) for table in tables}
        return DependencyObservationScope(source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=tuple(table.table_id for table in tables), mode=snapshot_result.snapshot.observation_scope.mode, rows_by_table=rows, complete_by_table={table.table_id: observations.get(table.table_id) is not None and observations[table.table_id].status is TableObservationStatus.FULLY_OBSERVED for table in tables}, input_batch_ids=tuple(batch.batch_id for batch in batches), input_batch_hashes=tuple(batch.content_hash for batch in batches), input_record_reference_count=sum(1 for item in snapshot_result.record_references if item.table_id in request.selected_table_ids))

    def _search_stats(self, request, tables, columns_by_table, rows_by_table):
        column_count = sum(len(columns) for columns in columns_by_table.values())
        candidate = sum(len(columns_by_table[left.table_id]) * len(columns_by_table[right.table_id]) for left in tables for right in tables if left.table_id != right.table_id)
        pruned = max(candidate - request.search_policy.max_column_pairs, 0)
        determinant_count = sum(len(columns) * sum(math.comb(len(columns) - 1, width) for width in range(1, min(request.search_policy.max_determinant_width, len(columns) - 1) + 1)) for columns in columns_by_table.values())
        return DependencySearchStats(input_tables=len(tables), input_columns=column_count, candidate_column_pairs=candidate, pruned_column_pairs=pruned, searched_determinants=determinant_count, emitted_candidates=0, truncated=pruned > 0, truncation_reasons=("max_column_pairs",) if pruned else ())

    def _temp_root(self, request):
        root = (self.project_root / request.privacy_context.project_temp_root / request.request_id).resolve()
        root.relative_to(self.project_root)
        return root

    def _runtime_expired(self, request: DependencyRequest) -> bool:
        deadline = getattr(self, "_deadline", None)
        return deadline is not None and time.monotonic() >= deadline

    @staticmethod
    def _budget_failure(request: DependencyRequest, detail: str) -> DependencyFailure:
        return DependencyFailure(failure_id=dependency_id("dep_failure", detail), request_id=request.request_id, kind=DependencyFailureKind.BUDGET_EXCEEDED, detail="dependency search exceeded its configured wall-clock budget", retryable=False)

    @staticmethod
    def _write_engine_input(root, table, columns, rows):
        path = root / f"{table.table_id}.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([column.physical_name for column in columns])
            for row in rows:
                writer.writerow(["__DDO_PHYSICAL_NULL__" if row.get(column.physical_name) is None else row.get(column.physical_name) for column in columns])
        return path

    def _failed_result(self, request, tables, snapshot_result, rows, failures, capability, *, scope=None, stats=None):
        scope = scope or self._scope(request, snapshot_result, tables)
        stats = stats or DependencySearchStats(input_tables=len(tables), input_columns=0, candidate_column_pairs=0, pruned_column_pairs=0, searched_determinants=0, emitted_candidates=0)
        return DependencyResult(request=request, observation_scope=scope, failures=tuple(failures), capabilities=(capability,), search_stats=stats, status=DependencyStageStatus.FAILED)


def _fd_metrics(rows, refs, determinant, dependent, null_policy):
    groups: dict[tuple[Any, ...], set[tuple[Any, ...]]] = defaultdict(set)
    group_refs: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    eligible = 0
    for index, row in enumerate(rows):
        lhs = tuple(row.get(column) for column in determinant)
        rhs = tuple(row.get(column) for column in dependent)
        if null_policy is NullPolicy.EXCLUDE_PHYSICAL_NULL and any(value is None for value in lhs + rhs):
            continue
        eligible += 1
        groups[lhs].add(rhs)
        group_refs[lhs].append(refs[index])
    violations = sum(max(len(values) - 1, 0) for values in groups.values())
    bad_refs = [ref for key, values in groups.items() if len(values) > 1 for ref in group_refs[key]]
    error = violations / eligible if eligible else 0.0
    return {"support": 1.0 - error, "error": error, "violations": violations, "refs": bad_refs}


def _ind_metrics(left_rows, left_refs, right_rows, left_cols, right_cols, null_policy):
    def eligible(row, cols):
        values = tuple(row.get(column) for column in cols)
        if null_policy is NullPolicy.EXCLUDE_PHYSICAL_NULL and any(value is None for value in values):
            return None
        return values
    right_values = {value for row in right_rows if (value := eligible(row, right_cols)) is not None}
    left_values = [eligible(row, left_cols) for row in left_rows]
    left_values = [value for value in left_values if value is not None]
    orphans = [index for index, value in enumerate(left_values) if value not in right_values]
    left_distinct = len(set(left_values))
    right_distinct = len(right_values)
    right_eligible_rows = sum(1 for row in right_rows if eligible(row, right_cols) is not None)
    coverage = len(set(left_values) & right_values) / left_distinct if left_distinct else 1.0
    return {"coverage": coverage, "violation_ratio": len(orphans) / len(left_values) if left_values else 0.0, "orphan_count": len(orphans), "orphan_ratio": len(orphans) / len(left_values) if left_values else 0.0, "left_distinct": left_distinct, "right_distinct": right_distinct, "right_rows": right_eligible_rows, "target_uniqueness": right_distinct / right_eligible_rows if right_eligible_rows else 0.0, "refs": [left_refs[index] for index in orphans if index < len(left_refs)]}


def _types_compatible(left_columns, right_columns, left_names, right_names):
    for left, right in zip(left_names, right_names):
        left_type = next(column.normalized_physical_type for column in left_columns if column.physical_name == left)
        right_type = next(column.normalized_physical_type for column in right_columns if column.physical_name == right)
        if left_type != right_type and {left_type, right_type} not in ({"integer", "numeric"}, {"string", "unknown"}):
            return False
    return True
