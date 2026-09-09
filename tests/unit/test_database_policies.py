from __future__ import annotations

import pytest
from pydantic import ValidationError

from dirty_data_to_olap.domain.contracts.database import (
    CapabilityStatus,
    ConnectionProfileReference,
    DatabaseEngine,
    DatabaseFailure,
    DatabaseFailureKind,
    DatabaseIdentifier,
    PoolPolicy,
    SamplingPolicy,
    TimeoutPolicy,
    capabilities_for,
    quote_identifier,
    quote_qualified_identifier,
)


def test_connection_profile_is_external_secret_reference_only() -> None:
    profile = ConnectionProfileReference(
        profile_id="local-sqlite",
        database_engine=DatabaseEngine.SQLITE,
        database_name="workspace/tests/reference.sqlite",
        credential_reference="secret-store://profile/local-sqlite",
        driver_options={"application_name": "step06-tests"},
    )
    serialized = profile.model_dump_json()
    assert "password" not in serialized.lower()
    assert "secret-value" not in serialized
    assert profile.model_dump()["credential_reference"].startswith("secret-store://")

    with pytest.raises(ValidationError):
        ConnectionProfileReference(
            profile_id="unsafe",
            database_engine=DatabaseEngine.SQLITE,
            database_name="file.db",
            password="fake-secret",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ConnectionProfileReference(
            profile_id="unsafe-dsn",
            database_engine=DatabaseEngine.SQLITE,
            database_name="file.db?password=fake-secret",
        )
    with pytest.raises(ValidationError):
        ConnectionProfileReference(
            profile_id="unsafe-uri",
            database_engine=DatabaseEngine.SQLITE,
            database_name="file://server/source.db",
        )


def test_capability_status_does_not_claim_live_support() -> None:
    sqlite_capabilities = capabilities_for(DatabaseEngine.SQLITE)
    postgres_capabilities = capabilities_for(DatabaseEngine.POSTGRESQL)
    oracle_capabilities = capabilities_for(DatabaseEngine.ORACLE)
    assert sqlite_capabilities.status is CapabilityStatus.REFERENCE_TESTED
    assert sqlite_capabilities.live_verified is False
    assert postgres_capabilities.status is CapabilityStatus.PLANNED_REQUIRED
    assert postgres_capabilities.live_verified is False
    assert oracle_capabilities.status is CapabilityStatus.DEFERRED


def test_policy_validation_requires_positive_explicit_bounds() -> None:
    SamplingPolicy(max_rows=10)
    TimeoutPolicy(connection_timeout_seconds=1, busy_timeout_seconds=1, statement_timeout_seconds=1)
    PoolPolicy(max_pool_size=1, acquire_timeout_seconds=1)
    with pytest.raises(ValidationError):
        SamplingPolicy(max_rows=0)
    with pytest.raises(ValidationError):
        PoolPolicy(max_pool_size=0, acquire_timeout_seconds=1)


def test_identifier_quoting_is_dialect_aware() -> None:
    identifier = DatabaseIdentifier(name='odd"name', schema_name="tenant")
    assert quote_qualified_identifier(identifier, DatabaseEngine.SQLITE) == '"tenant"."odd""name"'
    assert quote_identifier("odd`name", DatabaseEngine.MYSQL) == "`odd``name`"
    assert quote_identifier("odd]name", DatabaseEngine.SQLSERVER) == "[odd]]name]"
    with pytest.raises(ValueError):
        DatabaseIdentifier(name="bad\x00name")


def test_normalized_failure_redacts_connection_material() -> None:
    failure = DatabaseFailure(
        kind=DatabaseFailureKind.CONNECTION_FAILED,
        database_engine=DatabaseEngine.SQLITE,
        operation="connect",
        detail="password=fake-secret token=fake-token",
        retryable=False,
        cause_category="connection",
    )
    assert "fake-secret" not in str(failure)
    assert "fake-token" not in repr(failure)
    assert "fake-secret" not in failure.model_dump_json()
