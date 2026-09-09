from __future__ import annotations

from dirty_data_to_olap.domain.contracts.database import (
    BoundedSampleObservation,
    BoundedSampleRequest,
    ConnectionProfileReference,
    DatabaseEngine,
    DatabaseIdentifier,
    SamplingPolicy,
)


def test_connection_profile_round_trip_preserves_schema_version() -> None:
    profile = ConnectionProfileReference(
        profile_id="contract-fixture",
        database_engine=DatabaseEngine.SQLITE,
        database_name="workspace/tests/contract.sqlite",
    )
    restored = ConnectionProfileReference.model_validate_json(profile.model_dump_json())
    assert restored == profile
    assert restored.schema_version == "1.0"


def test_bounded_observation_carries_scope_and_never_claims_randomness() -> None:
    request = BoundedSampleRequest(
        table=DatabaseIdentifier(name="items"),
        columns=("item_id", "label"),
        sampling_policy=SamplingPolicy(max_rows=2),
    )
    observation = BoundedSampleObservation(
        table=request.table,
        columns=request.columns,
        rows=((1, "a"),),
        rows_observed=1,
        sampling_method=request.sampling_policy.method,
        requested_max_rows=request.sampling_policy.max_rows,
    )
    assert observation.observation_scope == "sample"
    assert observation.bounded is True
    assert observation.representative is False
    assert observation.requested_max_rows == 2
