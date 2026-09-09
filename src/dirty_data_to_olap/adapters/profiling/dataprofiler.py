"""DataProfiler-backed bounded profiling over COMPLETE staged Parquet batches."""

from __future__ import annotations

import hashlib
import logging
import math
import random
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from dirty_data_to_olap.application.profiling_adapter import AdapterTableProfileResult, ProfilingAdapter
from dirty_data_to_olap.domain.contracts.profiling import (
    ColumnProfile,
    DateTimeSummary,
    LengthSummary,
    NumericSummary,
    NullMarkerPolicy,
    PatternType,
    ProfileCompleteness,
    ProfileFailure,
    ProfileFailureKind,
    ProfileMode,
    ProfileObservationScope,
    ProfileObservationStatus,
    ProfileProvenance,
    ProfileRequest,
    UniquenessSemantics,
    ValuePatternSummary,
    pattern_id_for,
    profile_config_hash,
    profile_id_for,
)
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    BatchReference,
    ColumnDescriptor,
    PublicationState,
    SourceCatalog,
    SourceSnapshotResult,
    TableDescriptor,
    TableObservationStatus,
    TableSnapshotObservation,
    stable_digest,
    utc_now,
)


_PATTERNS: tuple[tuple[PatternType, re.Pattern[str]], ...] = (
    (PatternType.EMAIL_LIKE, re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    (PatternType.PHONE_LIKE, re.compile(r"^\+?[0-9][0-9()\-\s]{6,}$")),
    (PatternType.UUID_LIKE, re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")),
    (PatternType.INTEGER_STRING, re.compile(r"^[+-]?\d+$")),
    (PatternType.DECIMAL_STRING, re.compile(r"^[+-]?\d+\.\d+$")),
    (PatternType.DATE_STRING, re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")),
    (PatternType.URL_LIKE, re.compile(r"^https?://[^\s]+$", re.IGNORECASE)),
)


class _BatchIntegrityError(ValueError):
    """The staged input no longer matches the COMPLETE batch contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _primitive(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    if isinstance(value, (datetime, date)):
        return "datetime"
    if isinstance(value, str):
        return "string"
    return "nested"


def _value_key(value: Any) -> str:
    return stable_digest({"type": _primitive(value), "value": value})


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class _ColumnAccumulator:
    def __init__(self, markers: NullMarkerPolicy, *, distinct_limit: int = 100_000) -> None:
        self.markers = set(markers.configured_markers)
        self.distinct_limit = distinct_limit
        self.rows = 0
        self.physical_nulls = 0
        self.marker_count = 0
        self.primitive = Counter()
        self.distinct: set[str] = set()
        self.distinct_overflow = False
        self.lengths: list[int] = []
        self.empty_strings = 0
        self.numeric_count = 0
        self.numeric_mean = 0.0
        self.numeric_m2 = 0.0
        self.numeric_min: float | None = None
        self.numeric_max: float | None = None
        self.numeric_zeros = 0
        self.numeric_negatives = 0
        self.numeric_quantiles: list[float] = []
        self.datetime_count = 0
        self.datetime_success = 0
        self.datetime_min: datetime | None = None
        self.datetime_max: datetime | None = None
        self.patterns = Counter()
        self.category_counts: Counter[str] = Counter()
        self.category_overflow = False

    def add(self, value: Any) -> None:
        self.rows += 1
        kind = _primitive(value)
        self.primitive[kind] += 1
        if value is None:
            self.physical_nulls += 1
        elif isinstance(value, str) and value in self.markers:
            self.marker_count += 1
        key = _value_key(value)
        if not self.distinct_overflow:
            self.distinct.add(key)
            if len(self.distinct) > self.distinct_limit:
                self.distinct_overflow = True
                self.distinct.clear()
        if not self.category_overflow or key in self.category_counts:
            self.category_counts[key] += 1
            if len(self.category_counts) > self.distinct_limit:
                self.category_overflow = True
                self.category_counts.clear()
        if isinstance(value, str):
            self.lengths.append(len(value))
            self.empty_strings += value == ""
            for pattern, regex in _PATTERNS:
                if regex.fullmatch(value):
                    self.patterns[pattern] += 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if math.isfinite(numeric):
                self.numeric_count += 1
                delta = numeric - self.numeric_mean
                self.numeric_mean += delta / self.numeric_count
                self.numeric_m2 += delta * (numeric - self.numeric_mean)
                self.numeric_min = numeric if self.numeric_min is None else min(self.numeric_min, numeric)
                self.numeric_max = numeric if self.numeric_max is None else max(self.numeric_max, numeric)
                self.numeric_zeros += numeric == 0
                self.numeric_negatives += numeric < 0
                if len(self.numeric_quantiles) < self.distinct_limit:
                    self.numeric_quantiles.append(numeric)
        parsed = _parse_datetime(value)
        if value is not None and (isinstance(value, (str, datetime, date))):
            self.datetime_count += 1
            if parsed is not None:
                self.datetime_success += 1
                self.datetime_min = parsed if self.datetime_min is None else min(self.datetime_min, parsed)
                self.datetime_max = parsed if self.datetime_max is None else max(self.datetime_max, parsed)

    def column_profile(
        self,
        *,
        request: ProfileRequest,
        catalog: SourceCatalog,
        table: TableDescriptor,
        column: ColumnDescriptor,
        scope: ProfileObservationScope,
        provenance: ProfileProvenance,
    ) -> tuple[ColumnProfile, tuple[ValuePatternSummary, ...]]:
        if self.distinct_overflow:
            distinct_count = None
            distinct_ratio = None
            uniqueness = UniquenessSemantics.NOT_COMPUTED_BOUNDED
        else:
            distinct_count = len(self.distinct)
            distinct_ratio = distinct_count / self.rows if self.rows else None
            uniqueness = UniquenessSemantics.EXACT_ON_FULL_SCOPE if request.mode is ProfileMode.FULL else UniquenessSemantics.SAMPLE_OBSERVATION
        length_summary = None
        if self.lengths:
            length_summary = LengthSummary(count=len(self.lengths), minimum=min(self.lengths), maximum=max(self.lengths), mean=sum(self.lengths) / len(self.lengths), empty_string_count=self.empty_strings)
        numeric_summary = None
        if self.numeric_count:
            values = sorted(self.numeric_quantiles)
            quantiles = {}
            for name, fraction in (("p25", 0.25), ("p50", 0.5), ("p75", 0.75)):
                quantiles[name] = values[min(len(values) - 1, int(fraction * (len(values) - 1)))]
            variance = self.numeric_m2 / (self.numeric_count - 1) if self.numeric_count > 1 else 0.0
            numeric_summary = NumericSummary(count=self.numeric_count, minimum=self.numeric_min, maximum=self.numeric_max, mean=self.numeric_mean, variance=variance, standard_deviation=math.sqrt(variance), zero_count=self.numeric_zeros, negative_count=self.numeric_negatives, quantiles=quantiles)
        datetime_summary = None
        if self.datetime_count:
            datetime_summary = DateTimeSummary(observed_count=self.datetime_count, parse_success_count=self.datetime_success, minimum=self.datetime_min.isoformat() if self.datetime_min else None, maximum=self.datetime_max.isoformat() if self.datetime_max else None)
        patterns = []
        pattern_refs = []
        for pattern, _ in _PATTERNS:
            match_count = self.patterns[pattern]
            if not match_count:
                continue
            pattern_id = pattern_id_for(request.source_id, request.snapshot_id, table.table_id, column.column_id, pattern, request)
            pattern_refs.append(pattern_id)
            patterns.append(ValuePatternSummary(pattern_id=pattern_id, source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, column_id=column.column_id, pattern_type=pattern, match_count=match_count, observed_rows=self.rows, support_ratio=match_count / self.rows if self.rows else 0.0, observation_scope=scope, method="anchored_project_regex", method_version="1.0", provenance=provenance))
        categorical = {"distinct_count": distinct_count, "gini_impurity": (1.0 - sum((count / self.rows) ** 2 for count in self.category_counts.values())) if self.rows and not self.category_overflow else None}
        profile = ColumnProfile(profile_id=profile_id_for(request.source_id, request.snapshot_id, table.table_id, column.column_id, request), source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, column_id=column.column_id, physical_type=column.native_physical_type, primitive_type_observations=dict(self.primitive), physical_null_count=self.physical_nulls, configured_null_marker_count=self.marker_count, unknown_missing_count=0, rows_observed=self.rows, observed_distinct_count=distinct_count, observed_distinct_ratio=distinct_ratio, uniqueness_semantics=uniqueness, length_summary=length_summary, numeric_summary=numeric_summary, datetime_summary=datetime_summary, categorical_summary=categorical, pattern_summary_refs=tuple(pattern_refs), observation_scope=scope, provenance=provenance, status=ProfileCompleteness.COMPLETE)
        return profile, tuple(patterns)


class DataProfilerAdapter(ProfilingAdapter):
    name = "dataprofiler"

    def __init__(self, *, distinct_limit: int = 100_000) -> None:
        import dataprofiler

        logging.getLogger("DataProfiler").setLevel(logging.WARNING)
        self.version = str(dataprofiler.__version__)
        self.distinct_limit = distinct_limit

    def _adapter_reference(self, request: ProfileRequest) -> AdapterReference:
        return AdapterReference(name=self.name, version=self.version, config_fingerprint=stable_digest({"profile_config_hash": profile_config_hash(request), "labeler": request.semantic_label_policy.enabled, "correlation": request.expensive_statistics.correlation_enabled, "chi_square": request.expensive_statistics.chi_square_enabled}))

    def _path_for_batch(self, batch: BatchReference, project_root: Path) -> Path:
        if batch.publication_state is not PublicationState.COMPLETE:
            raise _BatchIntegrityError("profiling accepts only COMPLETE staged batches")
        path = (project_root / batch.artifact_location).resolve()
        try:
            path.relative_to(project_root.resolve())
        except ValueError:
            raise _BatchIntegrityError("profile batch artifact escapes the project root") from None
        if not path.is_file() or _sha256(path) != batch.content_hash:
            raise _BatchIntegrityError("profile batch content hash verification failed")
        return path

    def _iter_rows(self, batch: BatchReference, columns: Sequence[ColumnDescriptor], project_root: Path) -> Iterator[dict[str, Any]]:
        import pyarrow.parquet as pq

        path = self._path_for_batch(batch, project_root)
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != batch.row_count:
            raise _BatchIntegrityError("profile batch row count verification failed")
        names = [column.physical_name for column in columns]
        yielded = 0
        for arrow_batch in parquet.iter_batches(batch_size=max(1, min(batch.row_count or 1, 10_000)), columns=names):
            for row in arrow_batch.to_pylist():
                yielded += 1
                yield row
        if yielded != batch.row_count:
            raise _BatchIntegrityError("profile batch iteration row count verification failed")

    def _new_engine(self) -> Any:
        from dataprofiler.profilers.profile_builder import StructuredProfiler
        from dataprofiler.profilers.profiler_options import StructuredOptions

        options = StructuredOptions(null_values={})
        options.data_labeler.is_enabled = False
        options.multiprocess.is_enabled = False
        options.correlation.is_enabled = False
        options.chi2_homogeneity.is_enabled = False
        options.null_replication_metrics.is_enabled = False
        options.row_statistics.is_enabled = False
        return StructuredProfiler(None, samples_per_update=None, min_true_samples=0, options=options)

    def _update_engine(self, profiler: Any, rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> None:
        import pandas as pd

        if rows:
            dataframe = pd.DataFrame(rows, columns=list(names))
            profiler.update_profile(dataframe, sample_size=len(dataframe), min_true_samples=0)

    @staticmethod
    def _finish_engine(profiler: Any) -> None:
        if profiler is not None:
            profiler.report({"output_format": "serializable", "remove_disabled_flag": True})

    def profile_table(self, request: ProfileRequest, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, table: TableDescriptor, columns: Sequence[ColumnDescriptor], batches: Sequence[BatchReference], table_observation: TableSnapshotObservation, *, project_root: Path) -> AdapterTableProfileResult:
        adapter_reference = self._adapter_reference(request)
        batch_ids = tuple(batch.batch_id for batch in batches)
        batch_hashes = tuple(batch.content_hash for batch in batches)
        provenance = ProfileProvenance(execution_context_id=snapshot_result.snapshot.execution_context_id, source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, schema_fingerprint=catalog.source.schema_fingerprint, input_batch_ids=batch_ids, input_batch_hashes=batch_hashes, profiling_adapter=adapter_reference, dataprofiler_version=self.version, profile_config_hash=profile_config_hash(request), created_at=utc_now())
        if table_observation.status is TableObservationStatus.NOT_OBSERVED:
            scope = self._scope(request, snapshot_result, table_observation, 0, 0, ())
            failure = ProfileFailure(failure_id=f"failure_{table.table_id}_not_observed", profile_request_id=request.profile_request_id, source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, kind=ProfileFailureKind.TABLE_NOT_OBSERVED, detail="table was not observed because the source-wide extraction bound was exhausted; it is not known to be empty", engine=self.name, config_hash=profile_config_hash(request), retryable=False)
            return AdapterTableProfileResult(columns=(), patterns=(), failures=(failure,), observation_scope=scope, duplicate_row_count=None, duplicate_observation_complete=False, provenance=provenance)
        selected_columns = tuple(column for column in columns if not request.selected_column_ids or column.column_id in request.selected_column_ids)
        accumulators = {column.column_id: _ColumnAccumulator(request.null_marker_policy, distinct_limit=self.distinct_limit) for column in selected_columns}
        rows_available = 0
        duplicate_hashes: set[str] = set()
        duplicate_count = 0
        duplicate_complete = True
        engine_profiler = None
        selected_names = [column.physical_name for column in selected_columns]
        sample_rows: list[dict[str, Any]] = []
        sample_refs: list[str] = []
        reservoir_seen = 0
        rng = random.Random(request.seed) if request.mode is ProfileMode.SAMPLE else None
        ref_by_batch_ordinal = {(reference.batch_id, reference.extraction_ordinal): reference.record_ref for reference in snapshot_result.record_references}
        try:
            for batch in batches:
                batch_ordinal = batch.first_extraction_ordinal or 0
                batch_rows: list[dict[str, Any]] = []
                for row in self._iter_rows(batch, selected_columns, project_root):
                    reference = ref_by_batch_ordinal.get((batch.batch_id, batch_ordinal))
                    rows_available += 1
                    batch_ordinal += 1
                    if request.mode is ProfileMode.SAMPLE:
                        assert rng is not None and request.sample_limit is not None
                        if len(sample_rows) < request.sample_limit:
                            sample_rows.append(row)
                            sample_refs.append(reference or f"ordinal:{rows_available - 1}")
                        else:
                            replacement = rng.randrange(rows_available)
                            if replacement < request.sample_limit:
                                sample_rows[replacement] = row
                                sample_refs[replacement] = reference or f"ordinal:{rows_available - 1}"
                    else:
                        self._add_row(row, accumulators, selected_columns, duplicate_hashes, duplicate_count, duplicate_complete)
                        row_hash = stable_digest(row)
                        if duplicate_complete:
                            if row_hash in duplicate_hashes:
                                duplicate_count += 1
                            elif len(duplicate_hashes) < self.distinct_limit:
                                duplicate_hashes.add(row_hash)
                            else:
                                duplicate_complete = False
                                duplicate_hashes.clear()
                        batch_rows.append(row)
                if request.mode is ProfileMode.FULL and batch_rows:
                    if engine_profiler is None:
                        engine_profiler = self._new_engine()
                    self._update_engine(engine_profiler, batch_rows, selected_names)
            if request.mode is ProfileMode.SAMPLE:
                duplicate_hashes = set()
                duplicate_count = 0
                for row in sample_rows:
                    self._add_row(row, accumulators, selected_columns, duplicate_hashes, duplicate_count, duplicate_complete)
                    row_hash = stable_digest(row)
                    if duplicate_complete:
                        if row_hash in duplicate_hashes:
                            duplicate_count += 1
                        elif len(duplicate_hashes) < self.distinct_limit:
                            duplicate_hashes.add(row_hash)
                        else:
                            duplicate_complete = False
                            duplicate_hashes.clear()
                if sample_rows:
                    engine_profiler = self._new_engine()
                    self._update_engine(engine_profiler, sample_rows, selected_names)
            self._finish_engine(engine_profiler)
        except _BatchIntegrityError as error:
            failure = ProfileFailure(failure_id=f"failure_{table.table_id}_batch_integrity", profile_request_id=request.profile_request_id, source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, kind=ProfileFailureKind.BATCH_INTEGRITY_FAILED, detail=str(error), engine=self.name, config_hash=profile_config_hash(request), rows_observed=rows_available, retryable=False)
            return AdapterTableProfileResult(columns=(), patterns=(), failures=(failure,), observation_scope=self._scope(request, snapshot_result, table_observation, rows_available, 0, tuple(sample_refs), known_source_row_count=table.row_count), duplicate_row_count=None, duplicate_observation_complete=False, provenance=provenance)
        except Exception:
            failure = ProfileFailure(failure_id=f"failure_{table.table_id}_engine", profile_request_id=request.profile_request_id, source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, kind=ProfileFailureKind.ENGINE_FAILED, detail="DataProfiler execution or staged batch validation failed", engine=self.name, config_hash=profile_config_hash(request), rows_observed=rows_available, retryable=False)
            return AdapterTableProfileResult(columns=(), patterns=(), failures=(failure,), observation_scope=self._scope(request, snapshot_result, table_observation, rows_available, 0, tuple(sample_refs), known_source_row_count=table.row_count), duplicate_row_count=None, duplicate_observation_complete=False, provenance=provenance)
        profiled_rows = len(sample_rows) if request.mode is ProfileMode.SAMPLE else rows_available
        scope = self._scope(request, snapshot_result, table_observation, rows_available, profiled_rows, tuple(sample_refs), known_source_row_count=table.row_count)
        profiles = []
        patterns = []
        failures = []
        for column in selected_columns:
            try:
                column_provenance = provenance.model_copy(update={"column_id": column.column_id})
                profile, column_patterns = self._build_column_profile(accumulators[column.column_id], request=request, catalog=catalog, table=table, column=column, scope=scope, provenance=column_provenance)
                profiles.append(profile)
                patterns.extend(column_patterns)
            except Exception:
                failures.append(ProfileFailure(failure_id=f"failure_{table.table_id}_{column.column_id}", profile_request_id=request.profile_request_id, source_id=request.source_id, snapshot_id=request.snapshot_id, table_id=table.table_id, column_id=column.column_id, kind=ProfileFailureKind.COLUMN_FAILED, detail="column observation failed and was retained as an explicit item failure", engine=self.name, config_hash=profile_config_hash(request), rows_observed=profiled_rows, retryable=False))
        return AdapterTableProfileResult(columns=tuple(profiles), patterns=tuple(patterns), failures=tuple(failures), observation_scope=scope, duplicate_row_count=duplicate_count if duplicate_complete else None, duplicate_observation_complete=duplicate_complete, provenance=provenance)

    @staticmethod
    def _build_column_profile(accumulator: _ColumnAccumulator, **kwargs: Any) -> tuple[ColumnProfile, tuple[ValuePatternSummary, ...]]:
        return accumulator.column_profile(**kwargs)

    @staticmethod
    def _add_row(row: Mapping[str, Any], accumulators: Mapping[str, _ColumnAccumulator], columns: Sequence[ColumnDescriptor], *_: Any) -> None:
        for column in columns:
            accumulators[column.column_id].add(row.get(column.physical_name))

    def _scope(self, request: ProfileRequest, snapshot_result: SourceSnapshotResult, table_observation: TableSnapshotObservation, rows_available: int, rows_profiled: int, sample_refs: tuple[str, ...], *, known_source_row_count: int | None = None) -> ProfileObservationScope:
        sample = request.mode is ProfileMode.SAMPLE
        all_covered = rows_profiled == rows_available and table_observation.status is not TableObservationStatus.NOT_OBSERVED
        sample_identity = stable_digest(sample_refs) if sample else None
        completeness = ProfileObservationStatus.NOT_OBSERVED if table_observation.status is TableObservationStatus.NOT_OBSERVED else (ProfileObservationStatus.FULLY_OBSERVED if all_covered and not sample else (ProfileObservationStatus.PARTIALLY_OBSERVED if rows_profiled else ProfileObservationStatus.NOT_OBSERVED))
        return ProfileObservationScope(source_snapshot_mode=snapshot_result.snapshot.observation_scope.mode.value, source_snapshot_was_bounded=snapshot_result.snapshot.observation_scope.mode.value == "bounded", source_table_observation_status=table_observation.status, source_rows_observed=table_observation.rows_observed, rows_available_in_snapshot=rows_available, rows_profiled=rows_profiled, profiling_mode=request.mode, sample_method="deterministic_reservoir_v1" if sample else "none", seed=request.seed if sample else None, sample_limit=request.sample_limit if sample else None, sample_record_refs=sample_refs if sample else (), sample_identity=sample_identity, all_available_staged_rows_covered=all_covered, known_source_row_count=known_source_row_count, completeness=completeness)
