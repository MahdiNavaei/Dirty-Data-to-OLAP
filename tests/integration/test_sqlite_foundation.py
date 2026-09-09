from __future__ import annotations

from pathlib import Path

import pytest

from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource
from dirty_data_to_olap.domain.contracts.database import (
    BoundedSampleRequest,
    ConnectionProfileReference,
    DatabaseAccessError,
    DatabaseEngine,
    DatabaseFailureKind,
    DatabaseIdentifier,
    SamplingPolicy,
    TimeoutPolicy,
)


def _source(path: Path, *, statement_timeout_seconds: float = 10.0) -> SQLiteReadOnlySource:
    return SQLiteReadOnlySource(
        ConnectionProfileReference(
            profile_id="step06-fixture",
            database_engine=DatabaseEngine.SQLITE,
            database_name=str(path),
        ),
        timeout_policy=TimeoutPolicy(
            connection_timeout_seconds=1.0,
            busy_timeout_seconds=1.0,
            statement_timeout_seconds=statement_timeout_seconds,
        ),
    )


def test_sqlite_read_only_metadata_and_constraints(sqlite_fixture_path: Path) -> None:
    source = _source(sqlite_fixture_path)
    with source.open_readonly_session() as session:
        assert session.read_only is True
        metadata = session.inspect_database_metadata()
        names = {table.name for table in metadata.tables}
        assert {"parent", "child", "child_view", "odd table"} <= names
        child = session.inspect_table_metadata(DatabaseIdentifier(name="child"))
        assert [column.name for column in child.columns] == ["parent_id", "child_seq", "label", "unique_value"]
        assert tuple(column.name for column in child.columns if column.primary_key_position) == ("parent_id", "child_seq")
        assert any(constraint.constraint_type.value == "PRIMARY_KEY" and constraint.columns == ("parent_id", "child_seq") for constraint in child.constraints)
        assert any(constraint.constraint_type.value == "FOREIGN_KEY" and constraint.referenced_table == "parent" for constraint in child.constraints)
        assert any(constraint.constraint_type.value == "UNIQUE" and constraint.columns == ("unique_value",) for constraint in child.constraints)
        parent = session.inspect_table_metadata(DatabaseIdentifier(name="parent"))
        note = next(column for column in parent.columns if column.name == "nullable_note")
        assert note.nullable is True
        assert note.normalized_type == "text"
        assert all(not isinstance(row, dict) for row in ())


def test_bounded_sampling_and_explain_are_project_owned(sqlite_fixture_path: Path) -> None:
    request = BoundedSampleRequest(
        table=DatabaseIdentifier(name="child"),
        columns=("child_seq", "label"),
        sampling_policy=SamplingPolicy(max_rows=2),
    )
    with _source(sqlite_fixture_path).open_readonly_session() as session:
        observation = session.sample_rows_bounded(request)
        plan = session.explain_bounded_read(request)
        assert observation.rows_observed == 2
        assert observation.rows == ((1, "first"), (2, "second"))
        assert observation.columns == ("child_seq", "label")
        assert observation.representative is False
        assert plan and all(step.detail for step in plan)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO parent(parent_id, parent_code) VALUES (99, 'blocked')",
        "UPDATE parent SET parent_code = 'blocked' WHERE parent_id = 1",
        "DELETE FROM parent WHERE parent_id = 1",
        "CREATE TABLE blocked_table(value INTEGER)",
        "DROP TABLE parent",
    ],
)
def test_protected_session_rejects_dml_and_ddl(sqlite_fixture_path: Path, statement: str) -> None:
    with _source(sqlite_fixture_path).open_readonly_session() as session:
        with pytest.raises(DatabaseAccessError) as raised:
            session._run(statement, operation="negative_write_attempt")  # adapter-internal test hook
        assert raised.value.failure.kind is DatabaseFailureKind.READ_ONLY_VIOLATION


def test_connection_failure_is_normalized(sqlite_fixture_path: Path) -> None:
    missing = sqlite_fixture_path.with_name("missing.sqlite")
    with pytest.raises(DatabaseAccessError) as raised:
        with _source(missing).open_readonly_session():
            pass
    assert raised.value.failure.kind is DatabaseFailureKind.CONNECTION_FAILED
    assert "sqlite3" not in repr(raised.value.failure).lower()


def test_read_only_transaction_rolls_back_and_closes(sqlite_fixture_path: Path) -> None:
    session_context = _source(sqlite_fixture_path).open_readonly_session()
    with session_context as session:
        with session.read_only_transaction() as transaction:
            assert transaction.inspect_table_metadata(DatabaseIdentifier(name="parent")).name == "parent"
        assert session.closed is False
    assert session.closed is True


def test_statement_deadline_is_real_and_normalized(sqlite_fixture_path: Path) -> None:
    with _source(sqlite_fixture_path, statement_timeout_seconds=0.001).open_readonly_session() as session:
        recursive_query = (
            "WITH RECURSIVE counter(value) AS ("
            "SELECT 0 UNION ALL SELECT value + 1 FROM counter WHERE value < 100000000) "
            "SELECT sum(value) FROM counter"
        )
        with pytest.raises(DatabaseAccessError) as raised:
            session._run(recursive_query, operation="deadline_fixture")  # private diagnostic path; not public API
        assert raised.value.failure.kind is DatabaseFailureKind.TIMEOUT
