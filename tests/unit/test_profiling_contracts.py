from __future__ import annotations

import pytest

from dirty_data_to_olap.domain.contracts.profiling import (
    ExpensiveStatisticsPolicy,
    NullMarkerPolicy,
    ProfileMode,
    ProfileObservationScope,
    ProfileRequest,
    ProfileObservationStatus,
    TableObservationStatus,
)


def _request(**updates):
    values = dict(
        profile_request_id="request-1",
        source_id="source-1",
        snapshot_id="snapshot-1",
        selected_table_ids=("table-1",),
        mode=ProfileMode.FULL,
    )
    values.update(updates)
    return ProfileRequest(**values)


def test_sampling_requires_explicit_limit_and_seed():
    with pytest.raises(ValueError):
        _request(mode=ProfileMode.SAMPLE)
    request = _request(mode=ProfileMode.SAMPLE, sample_limit=10, seed=42)
    assert request.sample_limit == 10 and request.seed == 42


def test_expensive_metrics_require_a_bound():
    with pytest.raises(ValueError):
        ExpensiveStatisticsPolicy(correlation_enabled=True)
    assert ExpensiveStatisticsPolicy(correlation_enabled=True, max_columns_for_expensive_stats=2)


def test_sample_scope_can_cover_all_available_rows_but_stays_sample_mode():
    scope = ProfileObservationScope(
        source_snapshot_mode="complete",
        source_snapshot_was_bounded=False,
        source_table_observation_status=TableObservationStatus.FULLY_OBSERVED,
        source_rows_observed=3,
        rows_available_in_snapshot=3,
        rows_profiled=3,
        profiling_mode=ProfileMode.SAMPLE,
        sample_method="deterministic_reservoir_v1",
        seed=7,
        sample_limit=3,
        sample_record_refs=("r1", "r2", "r3"),
        sample_identity="sample-1",
        all_available_staged_rows_covered=True,
        completeness=ProfileObservationStatus.FULLY_OBSERVED,
    )
    assert scope.profiling_mode is ProfileMode.SAMPLE


def test_null_markers_are_explicit_and_unique():
    assert NullMarkerPolicy(configured_markers=("NULL",)).configured_markers == ("NULL",)
    with pytest.raises(ValueError):
        NullMarkerPolicy(configured_markers=("NULL", "NULL"))
