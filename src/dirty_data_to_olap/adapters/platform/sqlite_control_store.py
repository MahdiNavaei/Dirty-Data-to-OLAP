"""SQLite V1 adapter for the typed :class:`ControlStorePort`."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from dirty_data_to_olap.application.platform import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactStorePort,
    ConcurrencyConflictError,
    ControlStorePort,
    PlatformError,
    StaleDependencyError,
    UnsupportedSchemaVersionError,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactDependency,
    ArtifactPublicationState,
    ArtifactRef,
    ArtifactStorageMode,
    CacheEntry,
    CacheEntryStatus,
    CacheKey,
    GateEvidence,
    LocalPlatformConfig,
    ReproducibilityManifest,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
    StagedDatasetManifest,
)
from dirty_data_to_olap.domain.contracts.api import (
    ExecutionCommand,
    IdempotencyRecord,
    ReviewHistoryRecord,
    ReviewRecord,
)
from dirty_data_to_olap.domain.contracts.canonical import ReviewCompatibilityContext, ReviewDecision, review_subject_key
from dirty_data_to_olap.domain.contracts.review_actions import ReviewActionHistoryRecord, ReviewActionRecord, ReviewActionState
from dirty_data_to_olap.domain.contracts.jobs import (
    DeliveryPhase,
    ExecutionPlan,
    FailureClassification,
    JobKind,
    JobRecord,
    JobStatus,
    ReplaySafety,
    StageExecutionResult,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.domain.contracts.source import stable_id


CURRENT_SCHEMA_VERSION = 6


class _ClosingConnection(sqlite3.Connection):
    """Close short-lived connections when their context manager exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _dump(value: object) -> str:
    return json.dumps(value.model_dump(mode="json") if hasattr(value, "model_dump") else value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    return default if value is None else json.loads(value)


def _review_context_json(context: ReviewCompatibilityContext | None, contexts: tuple[ReviewCompatibilityContext, ...] = ()) -> str | None:
    if contexts:
        return _dump({"contexts": [item.model_dump(mode="json") for item in contexts]})
    return _dump(context) if context is not None else None


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteControlStore(ControlStorePort):
    """Transactional metadata repository with deterministic schema versioning."""

    def __init__(self, path: Path, *, project_root: Path | None = None) -> None:
        self.path = Path(path).resolve()
        self.project_root = (project_root or self.path.parent).resolve()
        try:
            self.path.relative_to(self.project_root)
        except ValueError:
            raise PlatformError("control store must remain under the configured project root") from None
        if "quality_unit_artifacts" in {part.lower() for part in self.path.parts}:
            raise PlatformError("protected workspace is outside platform authority")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._schema_version = self._migrate()

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None, check_same_thread=False, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _migrate(self) -> int:
        with self._lock:
            connection = self._connect()
            try:
                has_meta = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'").fetchone() is not None
                if not has_meta:
                    existing = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()
                    if existing:
                        raise PlatformError("control database has tables but no platform schema metadata")
                    connection.execute("BEGIN IMMEDIATE")
                    self._create_schema(connection)
                    connection.execute("INSERT INTO schema_meta(schema_version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
                    connection.execute(
                        "INSERT INTO schema_migrations(version_from, version_to, applied_at, detail) VALUES (?, ?, ?, ?)",
                        (0, CURRENT_SCHEMA_VERSION, datetime.now().astimezone().isoformat(), "initial schema"),
                    )
                    connection.commit()
                    return CURRENT_SCHEMA_VERSION
                version = int(connection.execute("SELECT schema_version FROM schema_meta").fetchone()[0])
                if version > CURRENT_SCHEMA_VERSION:
                    raise UnsupportedSchemaVersionError(f"control database schema {version} is newer than supported {CURRENT_SCHEMA_VERSION}")
                if version < 0:
                    raise UnsupportedSchemaVersionError(f"unsupported control database schema {version}")
                if version == 0:
                    connection.execute("BEGIN IMMEDIATE")
                    self._create_schema_tables(connection)
                    connection.execute("UPDATE schema_meta SET schema_version = 1")
                    connection.execute(
                        "INSERT INTO schema_migrations(version_from, version_to, applied_at, detail) VALUES (?, ?, ?, ?)",
                        (version, 1, datetime.now().astimezone().isoformat(), "forward migration"),
                    )
                    connection.commit()
                    version = 1
                while version < CURRENT_SCHEMA_VERSION:
                    connection.execute("BEGIN IMMEDIATE")
                    if version == 1:
                        self._migrate_v1_to_v2(connection)
                        next_version = 2
                        detail = "run-scoped staged dataset identity"
                    elif version == 2:
                        self._migrate_v2_to_v3(connection)
                        next_version = 3
                        detail = "typed ValidationReport gate provenance"
                    elif version == 3:
                        self._migrate_v3_to_v4(connection)
                        next_version = 4
                        detail = "Step27 control-plane idempotency, review context and command capabilities"
                    elif version == 4:
                        self._migrate_v4_to_v5(connection)
                        next_version = 5
                        detail = "Step28 durable jobs, execution plans, leases and fencing"
                    elif version == 5:
                        self._migrate_v5_to_v6(connection)
                        next_version = 6
                        detail = "Step28 durable delivery phases, replay safety and admission controls"
                    else:
                        connection.rollback()
                        raise UnsupportedSchemaVersionError(f"unsupported control database schema {version}")
                    connection.execute("UPDATE schema_meta SET schema_version = ?", (next_version,))
                    connection.execute(
                        "INSERT INTO schema_migrations(version_from, version_to, applied_at, detail) VALUES (?, ?, ?, ?)",
                        (version, next_version, datetime.now().astimezone().isoformat(), detail),
                    )
                    connection.commit()
                    version = next_version
                # Repeated opens repair a partial capability installation
                # without changing the already-recorded schema version.
                self._ensure_step27_tables(connection)
                self._ensure_step28_tables(connection)
                return version
            finally:
                connection.close()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE schema_meta (schema_version INTEGER NOT NULL)")
        SQLiteControlStore._create_schema_tables(connection)

    @staticmethod
    def _create_schema_tables(connection: sqlite3.Connection) -> None:
        statements = (
            "CREATE TABLE IF NOT EXISTS schema_migrations (migration_id INTEGER PRIMARY KEY AUTOINCREMENT, version_from INTEGER NOT NULL, version_to INTEGER NOT NULL, applied_at TEXT NOT NULL, detail TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, configuration_fingerprint TEXT NOT NULL, git_content_commit TEXT, root_artifact_refs TEXT NOT NULL, source_snapshot_refs TEXT NOT NULL, gate_refs TEXT NOT NULL, latest_stage_refs TEXT NOT NULL, revision INTEGER NOT NULL, metadata TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS stage_attempts (attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), stage_id TEXT NOT NULL, attempt_number INTEGER NOT NULL, status TEXT NOT NULL, started_at TEXT, finished_at TEXT, input_artifact_refs TEXT NOT NULL, output_artifact_refs TEXT NOT NULL, failure_code TEXT, failure_reason TEXT, policy_config_fingerprint TEXT NOT NULL, resource_budget_ref TEXT, delivery_phase TEXT NOT NULL DEFAULT 'ATTEMPT_CREATED', revision INTEGER NOT NULL, UNIQUE(run_id, stage_id, attempt_number))",
            "CREATE TABLE IF NOT EXISTS artifacts (artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), stage_id TEXT NOT NULL, attempt_id TEXT NOT NULL, artifact_kind TEXT NOT NULL, contract_schema_version TEXT NOT NULL, media_type TEXT NOT NULL, content_hash TEXT NOT NULL, byte_size INTEGER NOT NULL, storage_mode TEXT NOT NULL, logical_key TEXT, storage_key TEXT, external_locator TEXT, producer TEXT NOT NULL, retention_class TEXT NOT NULL, sensitivity_ref TEXT, publication_state TEXT NOT NULL, created_at TEXT NOT NULL, provenance_refs TEXT NOT NULL, tombstone_reason TEXT, tombstoned_at TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_artifacts_run_stage_kind ON artifacts(run_id, stage_id, artifact_kind)",
            "CREATE INDEX IF NOT EXISTS idx_artifacts_hash ON artifacts(content_hash)",
            "CREATE TABLE IF NOT EXISTS artifact_dependencies (artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), upstream_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), expected_content_hash TEXT NOT NULL, relationship_kind TEXT NOT NULL, PRIMARY KEY(artifact_id, upstream_artifact_id, relationship_kind))",
            "CREATE INDEX IF NOT EXISTS idx_dependencies_upstream ON artifact_dependencies(upstream_artifact_id)",
            "CREATE TABLE IF NOT EXISTS cache_entries (cache_key_hash TEXT PRIMARY KEY, cache_key TEXT NOT NULL, output_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), output_content_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, invalidated_reason TEXT)",
            "CREATE TABLE IF NOT EXISTS gate_evidence (gate_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id), status TEXT NOT NULL, eligible INTEGER NOT NULL, validation_report_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), validation_report_run_id TEXT NOT NULL, validation_report_id TEXT NOT NULL, validation_report_content_hash TEXT NOT NULL, policy_version TEXT NOT NULL, verified_content_commit TEXT NOT NULL, recorded_at TEXT NOT NULL, provenance_refs TEXT NOT NULL, PRIMARY KEY(gate_id, run_id))",
            "CREATE TABLE IF NOT EXISTS staged_datasets (run_id TEXT NOT NULL REFERENCES runs(run_id), dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL, manifest TEXT NOT NULL, PRIMARY KEY(run_id, dataset_id, dataset_version))",
            "CREATE INDEX IF NOT EXISTS idx_staged_dataset_scope ON staged_datasets(run_id, dataset_id, dataset_version)",
            "CREATE TABLE IF NOT EXISTS audit_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, run_id TEXT, artifact_id TEXT, status TEXT NOT NULL, content_hash TEXT, recorded_at TEXT NOT NULL, detail TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id, recorded_at)",
        )
        for statement in statements:
            connection.execute(statement)
        SQLiteControlStore._ensure_step27_tables(connection)
        SQLiteControlStore._ensure_step28_tables(connection)

    @staticmethod
    def _ensure_step27_tables(connection: sqlite3.Connection) -> None:
        """Ensure the version-4 Step27 control-plane capability is complete."""

        statements = (
            "CREATE TABLE IF NOT EXISTS api_idempotency (scope TEXT NOT NULL, idem_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'COMPLETED', response_status INTEGER NOT NULL, response_body TEXT NOT NULL, resource_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(scope, idem_key))",
            "CREATE TABLE IF NOT EXISTS review_current (run_id TEXT NOT NULL REFERENCES runs(run_id), subject_key TEXT NOT NULL, decision_json TEXT NOT NULL, revision INTEGER NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(run_id, subject_key))",
            "CREATE TABLE IF NOT EXISTS review_history (history_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), subject_key TEXT NOT NULL, decision_json TEXT NOT NULL, revision INTEGER NOT NULL, recorded_at TEXT NOT NULL, UNIQUE(run_id, subject_key, revision))",
            "CREATE INDEX IF NOT EXISTS idx_review_history_subject ON review_history(run_id, subject_key, revision)",
            "CREATE TABLE IF NOT EXISTS review_subject_contexts (run_id TEXT NOT NULL REFERENCES runs(run_id), checkpoint TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), context_json TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(run_id, checkpoint, artifact_id))",
            "CREATE TABLE IF NOT EXISTS review_action_current (run_id TEXT NOT NULL REFERENCES runs(run_id), subject_key TEXT NOT NULL, state_json TEXT NOT NULL, revision INTEGER NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(run_id, subject_key))",
            "CREATE TABLE IF NOT EXISTS review_action_history (history_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), subject_key TEXT NOT NULL, action_json TEXT NOT NULL, revision INTEGER NOT NULL, recorded_at TEXT NOT NULL, UNIQUE(run_id, subject_key, revision))",
            "CREATE INDEX IF NOT EXISTS idx_review_action_history_subject ON review_action_history(run_id, subject_key, revision)",
        )
        for statement in statements:
            connection.execute(statement)
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(api_idempotency)").fetchall()}
        if "state" not in columns:
            connection.execute("ALTER TABLE api_idempotency ADD COLUMN state TEXT NOT NULL DEFAULT 'COMPLETED'")

    @staticmethod
    def _ensure_step28_tables(connection: sqlite3.Connection) -> None:
        """Install only the durable Step28 capability, not an in-memory queue."""

        statements = (
            "CREATE TABLE IF NOT EXISTS execution_plans (plan_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), graph_source TEXT NOT NULL, graph_version TEXT NOT NULL, content_hash TEXT NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id))",
            "CREATE INDEX IF NOT EXISTS idx_execution_plans_run ON execution_plans(run_id)",
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), job_kind TEXT NOT NULL, command_id TEXT UNIQUE, action TEXT, stage_id TEXT, parent_job_id TEXT, plan_id TEXT REFERENCES execution_plans(plan_id), attempt_id TEXT REFERENCES stage_attempts(attempt_id), status TEXT NOT NULL, priority INTEGER NOT NULL, created_at TEXT NOT NULL, available_at TEXT NOT NULL, lease_owner TEXT, lease_generation INTEGER NOT NULL DEFAULT 0, lease_expires_at TEXT, heartbeat_at TEXT, delivery_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0, failure_code TEXT, failure_classification TEXT, failure_reason TEXT, cancellation_requested INTEGER NOT NULL DEFAULT 0, cancellation_requested_at TEXT, result_refs TEXT NOT NULL DEFAULT '[]', review_context_json TEXT, delivery_phase TEXT NOT NULL DEFAULT 'NOT_STARTED', replay_safety TEXT NOT NULL DEFAULT 'RECONCILIATION_REQUIRED_ON_UNKNOWN', source_scope TEXT, durable_result_json TEXT, metadata TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0, UNIQUE(run_id, stage_id))",
            "CREATE INDEX IF NOT EXISTS idx_jobs_claimable ON jobs(status, available_at, priority, created_at, job_id)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_run_status ON jobs(run_id, status, stage_id)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_command ON jobs(command_id)",
        )
        for statement in statements:
            connection.execute(statement)
        attempt_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(stage_attempts)").fetchall()}
        if "delivery_phase" not in attempt_columns:
            connection.execute("ALTER TABLE stage_attempts ADD COLUMN delivery_phase TEXT NOT NULL DEFAULT 'ATTEMPT_CREATED'")
        job_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
        additions = (
            ("delivery_phase", "TEXT NOT NULL DEFAULT 'NOT_STARTED'"),
            ("replay_safety", "TEXT NOT NULL DEFAULT 'RECONCILIATION_REQUIRED_ON_UNKNOWN'"),
            ("source_scope", "TEXT"),
            ("durable_result_json", "TEXT"),
        )
        for column, definition in additions:
            if column not in job_columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]): int(row[5]) for row in connection.execute("PRAGMA table_info(staged_datasets)").fetchall()}
        if not columns:
            SQLiteControlStore._create_schema_tables(connection)
            return
        if columns.get("run_id") == 1:
            return
        connection.execute("ALTER TABLE staged_datasets RENAME TO staged_datasets_legacy")
        connection.execute("CREATE TABLE staged_datasets (run_id TEXT NOT NULL REFERENCES runs(run_id), dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL, manifest TEXT NOT NULL, PRIMARY KEY(run_id, dataset_id, dataset_version))")
        connection.execute("CREATE INDEX idx_staged_dataset_scope ON staged_datasets(run_id, dataset_id, dataset_version)")
        rows = connection.execute("SELECT run_id, dataset_id, dataset_version, manifest FROM staged_datasets_legacy").fetchall()
        for row in rows:
            manifest = json.loads(str(row[3]))
            manifest.setdefault("identity_version", "legacy-v1")
            connection.execute(
                "INSERT INTO staged_datasets(run_id, dataset_id, dataset_version, manifest) VALUES (?, ?, ?, ?)",
                (row[0], row[1], row[2], _dump(manifest)),
            )
        connection.execute("DROP TABLE staged_datasets_legacy")

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(gate_evidence)").fetchall()}
        if "validation_report_run_id" in columns and "validation_report_id" in columns:
            return
        connection.execute("ALTER TABLE gate_evidence ADD COLUMN validation_report_run_id TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE gate_evidence ADD COLUMN validation_report_id TEXT NOT NULL DEFAULT ''")
        rows = connection.execute("SELECT gate_id, run_id, validation_report_artifact_id, provenance_refs FROM gate_evidence").fetchall()
        for row in rows:
            refs = _load_json(row[3], [])
            if "legacy:gate-evidence-unverified" not in refs:
                refs.append("legacy:gate-evidence-unverified")
            artifact = connection.execute("SELECT run_id FROM artifacts WHERE artifact_id = ?", (row[2],)).fetchone()
            report_run_id = "" if artifact is None else str(artifact[0])
            connection.execute(
                "UPDATE gate_evidence SET validation_report_run_id = ?, validation_report_id = ?, provenance_refs = ? WHERE gate_id = ? AND run_id = ?",
                (report_run_id, f"legacy:{row[2]}", _dump(refs), row[0], row[1]),
            )

    @staticmethod
    def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
        """Install Step27 capabilities through the normal migration chain."""

        SQLiteControlStore._ensure_step27_tables(connection)

    @staticmethod
    def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
        """Install Step28 through an explicit forward migration."""

        SQLiteControlStore._ensure_step28_tables(connection)

    @staticmethod
    def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
        """Add only additive fields; existing runs, jobs and attempts remain intact."""

        SQLiteControlStore._ensure_step28_tables(connection)
        for row in connection.execute("SELECT plan_id, plan_json FROM execution_plans").fetchall():
            plan = ExecutionPlan.model_validate(_load_json(str(row["plan_json"]), {}))
            connection.execute(
                "UPDATE execution_plans SET content_hash = ?, plan_json = ? WHERE plan_id = ?",
                (plan.content_hash, _dump(plan), row["plan_id"]),
            )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=str(row["artifact_id"]),
            run_id=str(row["run_id"]),
            stage_id=str(row["stage_id"]),
            attempt_id=str(row["attempt_id"]),
            artifact_kind=str(row["artifact_kind"]),
            schema_version=str(row["contract_schema_version"]),
            media_type=str(row["media_type"]),
            content_hash=str(row["content_hash"]),
            byte_size=int(row["byte_size"]),
            storage_mode=ArtifactStorageMode(str(row["storage_mode"])),
            logical_key=row["logical_key"],
            storage_key=row["storage_key"],
            external_locator=row["external_locator"],
            producer=str(row["producer"]),
            retention_class=str(row["retention_class"]),
            sensitivity_ref=row["sensitivity_ref"],
            publication_state=ArtifactPublicationState(str(row["publication_state"])),
            created_at=_parse_datetime(str(row["created_at"])),
            provenance_refs=tuple(_load_json(row["provenance_refs"], [])),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=str(row["run_id"]),
            project_id=str(row["project_id"]),
            created_at=_parse_datetime(str(row["created_at"])),
            status=str(row["status"]),
            configuration_fingerprint=str(row["configuration_fingerprint"]),
            git_content_commit=row["git_content_commit"],
            root_artifact_refs=tuple(_load_json(row["root_artifact_refs"], [])),
            source_snapshot_refs=tuple(_load_json(row["source_snapshot_refs"], [])),
            gate_refs=tuple(_load_json(row["gate_refs"], [])),
            latest_stage_refs=dict(_load_json(row["latest_stage_refs"], {})),
            revision=int(row["revision"]),
            metadata=dict(_load_json(row["metadata"], {})),
        )

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> StageAttemptRecord:
        return StageAttemptRecord(
            attempt_id=str(row["attempt_id"]),
            run_id=str(row["run_id"]),
            stage_id=str(row["stage_id"]),
            attempt_number=int(row["attempt_number"]),
            status=str(row["status"]),
            started_at=_parse_datetime(row["started_at"]) if row["started_at"] else None,
            finished_at=_parse_datetime(row["finished_at"]) if row["finished_at"] else None,
            input_artifact_refs=tuple(_load_json(row["input_artifact_refs"], [])),
            output_artifact_refs=tuple(_load_json(row["output_artifact_refs"], [])),
            failure_code=row["failure_code"],
            failure_reason=row["failure_reason"],
            policy_config_fingerprint=str(row["policy_config_fingerprint"]),
            resource_budget_ref=row["resource_budget_ref"],
            delivery_phase=str(row["delivery_phase"]) if "delivery_phase" in row.keys() else "ATTEMPT_CREATED",
            revision=int(row["revision"]),
        )

    @staticmethod
    def _insert_run(connection: sqlite3.Connection, run: RunRecord) -> RunRecord:
        existing = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
        if existing:
            stored = SQLiteControlStore._run_from_row(existing)
            if stored.model_copy(update={"revision": run.revision}) != run:
                raise ArtifactConflictError("run identity is already bound to different metadata")
            return stored
        connection.execute(
            "INSERT INTO runs(run_id, project_id, created_at, status, configuration_fingerprint, git_content_commit, root_artifact_refs, source_snapshot_refs, gate_refs, latest_stage_refs, revision, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run.run_id, run.project_id, run.created_at.isoformat(), run.status.value, run.configuration_fingerprint, run.git_content_commit, _dump(run.root_artifact_refs), _dump(run.source_snapshot_refs), _dump(run.gate_refs), _dump(run.latest_stage_refs), run.revision, _dump(run.metadata)),
        )
        SQLiteControlStore._audit(connection, "run_created", run_id=run.run_id, status=run.status.value, detail="run metadata persisted")
        return run

    @staticmethod
    def _idempotency_from_connection(row: sqlite3.Row) -> IdempotencyRecord:
        return IdempotencyRecord(
            scope=str(row["scope"]),
            key=str(row["idem_key"]),
            request_fingerprint=str(row["request_fingerprint"]),
            state=str(row["state"] if "state" in row.keys() else "COMPLETED"),
            response_status=int(row["response_status"]),
            response_body=dict(_load_json(str(row["response_body"]), {})),
            resource_id=row["resource_id"],
            created_at=_parse_datetime(str(row["created_at"])),
        )

    @staticmethod
    def _assert_idempotency_match(existing: IdempotencyRecord, requested: IdempotencyRecord) -> None:
        if existing.request_fingerprint != requested.request_fingerprint:
            raise ArtifactConflictError("idempotency key is already bound to a different semantic request")

    @staticmethod
    def _insert_idempotency(connection: sqlite3.Connection, record: IdempotencyRecord) -> IdempotencyRecord:
        connection.execute(
            "INSERT INTO api_idempotency(scope, idem_key, request_fingerprint, state, response_status, response_body, resource_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record.scope, record.key, record.request_fingerprint, record.state, record.response_status, _dump(record.response_body), record.resource_id, record.created_at.isoformat()),
        )
        SQLiteControlStore._audit(connection, "api_idempotency_recorded", status=record.state, detail=f"scope={record.scope}")
        return record

    def create_run(self, run: RunRecord) -> RunRecord:
        with self._transaction() as connection:
            return self._insert_run(connection, run)

    def create_run_with_idempotency(self, run: RunRecord, idempotency: IdempotencyRecord) -> tuple[RunRecord, bool]:
        if idempotency.state != "COMPLETED":
            raise ValueError("run creation idempotency must be completed")
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (idempotency.scope, idempotency.key)).fetchone()
            if existing_row is not None:
                existing = self._idempotency_from_connection(existing_row)
                self._assert_idempotency_match(existing, idempotency)
                row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (existing.resource_id,)).fetchone()
                if row is None:
                    raise PlatformError("idempotency record does not reference a persisted run")
                return self._run_from_row(row), True
            stored = self._insert_run(connection, run)
            self._insert_idempotency(connection, idempotency.model_copy(update={"resource_id": stored.run_id, "response_body": {"run": stored.model_dump(mode="json")}}))
            return stored, False

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return None if row is None else self._run_from_row(row)

    def list_runs(self, *, project_id: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[RunRecord, ...]:
        if limit < 1 or offset < 0:
            raise ValueError("run list limit must be positive and offset non-negative")
        sql = "SELECT * FROM runs WHERE 1 = 1"
        parameters: list[object] = []
        if project_id is not None:
            sql += " AND project_id = ?"
            parameters.append(project_id)
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status)
        sql += " ORDER BY created_at, run_id LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(self._run_from_row(row) for row in rows)

    def update_run(self, run: RunRecord, *, expected_revision: int) -> RunRecord:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET project_id = ?, status = ?, configuration_fingerprint = ?, git_content_commit = ?, root_artifact_refs = ?, source_snapshot_refs = ?, gate_refs = ?, latest_stage_refs = ?, revision = revision + 1, metadata = ? WHERE run_id = ? AND revision = ?",
                (run.project_id, run.status.value, run.configuration_fingerprint, run.git_content_commit, _dump(run.root_artifact_refs), _dump(run.source_snapshot_refs), _dump(run.gate_refs), _dump(run.latest_stage_refs), _dump(run.metadata), run.run_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("run revision changed before compare-and-swap update")
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
            return self._run_from_row(row)

    def create_stage_attempt(self, attempt: StageAttemptRecord) -> StageAttemptRecord:
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (attempt.run_id,)).fetchone() is None:
                raise PlatformError("stage attempt requires an existing run")
            existing = connection.execute("SELECT * FROM stage_attempts WHERE attempt_id = ?", (attempt.attempt_id,)).fetchone()
            if existing:
                stored = self._attempt_from_row(existing)
                if stored.model_copy(update={"revision": attempt.revision}) != attempt:
                    raise ArtifactConflictError("attempt identity is already bound to different metadata")
                return stored
            connection.execute(
                "INSERT INTO stage_attempts(attempt_id, run_id, stage_id, attempt_number, status, started_at, finished_at, input_artifact_refs, output_artifact_refs, failure_code, failure_reason, policy_config_fingerprint, resource_budget_ref, delivery_phase, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (attempt.attempt_id, attempt.run_id, attempt.stage_id, attempt.attempt_number, attempt.status.value, attempt.started_at.isoformat() if attempt.started_at else None, attempt.finished_at.isoformat() if attempt.finished_at else None, _dump(attempt.input_artifact_refs), _dump(attempt.output_artifact_refs), attempt.failure_code, attempt.failure_reason, attempt.policy_config_fingerprint, attempt.resource_budget_ref, attempt.delivery_phase, attempt.revision),
            )
            self._audit(connection, "stage_attempt_created", run_id=attempt.run_id, status=attempt.status.value, detail=f"attempt {attempt.attempt_id} persisted")
            return attempt

    def get_stage_attempt(self, attempt_id: str) -> StageAttemptRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stage_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            return None if row is None else self._attempt_from_row(row)

    def list_stage_attempts(self, *, run_id: str, stage_id: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[StageAttemptRecord, ...]:
        if limit < 1 or offset < 0:
            raise ValueError("attempt list limit must be positive and offset non-negative")
        sql = "SELECT * FROM stage_attempts WHERE run_id = ?"
        parameters: list[object] = [run_id]
        if stage_id is not None:
            sql += " AND stage_id = ?"
            parameters.append(stage_id)
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status)
        sql += " ORDER BY stage_id, attempt_number, attempt_id LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(self._attempt_from_row(row) for row in rows)

    def update_stage_attempt(self, attempt: StageAttemptRecord, *, expected_revision: int) -> StageAttemptRecord:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE stage_attempts SET status = ?, started_at = ?, finished_at = ?, input_artifact_refs = ?, output_artifact_refs = ?, failure_code = ?, failure_reason = ?, policy_config_fingerprint = ?, resource_budget_ref = ?, delivery_phase = ?, revision = revision + 1 WHERE attempt_id = ? AND revision = ?",
                (attempt.status.value, attempt.started_at.isoformat() if attempt.started_at else None, attempt.finished_at.isoformat() if attempt.finished_at else None, _dump(attempt.input_artifact_refs), _dump(attempt.output_artifact_refs), attempt.failure_code, attempt.failure_reason, attempt.policy_config_fingerprint, attempt.resource_budget_ref, attempt.delivery_phase, attempt.attempt_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("stage attempt revision changed before compare-and-swap update")
            row = connection.execute("SELECT * FROM stage_attempts WHERE attempt_id = ?", (attempt.attempt_id,)).fetchone()
            return self._attempt_from_row(row)

    @staticmethod
    def _artifact_tuple(artifact: ArtifactRef) -> tuple[object, ...]:
        return (
            artifact.artifact_id, artifact.run_id, artifact.stage_id, artifact.attempt_id, artifact.artifact_kind,
            artifact.schema_version, artifact.media_type, artifact.content_hash, artifact.byte_size,
            artifact.storage_mode.value, artifact.logical_key, artifact.storage_key, artifact.external_locator,
            artifact.producer, artifact.retention_class.value, artifact.sensitivity_ref,
            artifact.publication_state.value, artifact.created_at.isoformat(), _dump(artifact.provenance_refs),
        )

    def _insert_artifact(self, connection: sqlite3.Connection, artifact: ArtifactRef) -> ArtifactRef:
        if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (artifact.run_id,)).fetchone() is None:
            raise PlatformError("artifact requires an existing run")
        existing = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,)).fetchone()
        if existing:
            stored = self._artifact_from_row(existing)
            identity_fields = ("run_id", "stage_id", "attempt_id", "artifact_kind", "content_hash", "byte_size", "storage_mode", "storage_key", "external_locator")
            if any(getattr(stored, field) != getattr(artifact, field) for field in identity_fields):
                raise ArtifactConflictError("artifact identity or immutable content conflicts with the existing row")
            if stored.publication_state is ArtifactPublicationState.TOMBSTONED and artifact.publication_state is not ArtifactPublicationState.TOMBSTONED:
                raise ArtifactConflictError("tombstoned artifact identity cannot be republished")
            return stored
        connection.execute(
            "INSERT INTO artifacts(artifact_id, run_id, stage_id, attempt_id, artifact_kind, contract_schema_version, media_type, content_hash, byte_size, storage_mode, logical_key, storage_key, external_locator, producer, retention_class, sensitivity_ref, publication_state, created_at, provenance_refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            self._artifact_tuple(artifact),
        )
        self._audit(connection, "artifact_registered", run_id=artifact.run_id, artifact_id=artifact.artifact_id, status=artifact.publication_state.value, content_hash=artifact.content_hash, detail="artifact metadata persisted")
        return artifact

    def register_artifact(self, artifact: ArtifactRef) -> ArtifactRef:
        with self._transaction() as connection:
            return self._insert_artifact(connection, artifact)

    def register_artifact_with_dependencies(self, artifact: ArtifactRef, dependencies: Sequence[ArtifactDependency]) -> ArtifactRef:
        with self._transaction() as connection:
            stored = self._insert_artifact(connection, artifact)
            for dependency in dependencies:
                if dependency.artifact_id != artifact.artifact_id:
                    raise PlatformError("dependency row must identify its downstream artifact")
                if connection.execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (dependency.upstream_artifact_id,)).fetchone() is None:
                    raise PlatformError("dependency requires a registered upstream artifact")
                existing_dependency = connection.execute(
                    "SELECT expected_content_hash FROM artifact_dependencies WHERE artifact_id = ? AND upstream_artifact_id = ? AND relationship_kind = ?",
                    (dependency.artifact_id, dependency.upstream_artifact_id, dependency.relationship_kind),
                ).fetchone()
                if existing_dependency is not None:
                    if str(existing_dependency["expected_content_hash"]) != dependency.expected_content_hash:
                        raise ArtifactConflictError("dependency identity is already bound to a different expected hash")
                    continue
                connection.execute(
                    "INSERT INTO artifact_dependencies(artifact_id, upstream_artifact_id, expected_content_hash, relationship_kind) VALUES (?, ?, ?, ?)",
                    (dependency.artifact_id, dependency.upstream_artifact_id, dependency.expected_content_hash, dependency.relationship_kind),
                )
            return stored

    def get_artifact(self, artifact_id: str) -> ArtifactRef | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
            return None if row is None else self._artifact_from_row(row)

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None, limit: int | None = None, offset: int = 0) -> tuple[ArtifactRef, ...]:
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("artifact list limit must be positive and offset non-negative")
        sql = "SELECT * FROM artifacts WHERE 1 = 1"
        parameters: list[str] = []
        if run_id is not None:
            sql += " AND run_id = ?"
            parameters.append(run_id)
        if stage_id is not None:
            sql += " AND stage_id = ?"
            parameters.append(stage_id)
        if artifact_kind is not None:
            sql += " AND artifact_kind = ?"
            parameters.append(artifact_kind)
        sql += " ORDER BY artifact_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((limit, offset))
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            parameters.append(offset)
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(self._artifact_from_row(row) for row in rows)

    @staticmethod
    def _review_record_from_row(row: sqlite3.Row) -> ReviewRecord:
        decision = ReviewDecision.model_validate(_load_json(str(row["decision_json"]), {}))
        if str(row["subject_key"]) != review_subject_key(decision):
            raise PlatformError("stored review subject key is not the authoritative canonical key")
        return ReviewRecord(
            run_id=str(row["run_id"]),
            subject_key=str(row["subject_key"]),
            decision=decision,
            revision=int(row["revision"]),
            recorded_at=_parse_datetime(str(row["recorded_at"])),
        )

    @staticmethod
    def _review_history_from_row(row: sqlite3.Row) -> ReviewHistoryRecord:
        decision = ReviewDecision.model_validate(_load_json(str(row["decision_json"]), {}))
        if str(row["subject_key"]) != review_subject_key(decision):
            raise PlatformError("stored review subject key is not the authoritative canonical key")
        return ReviewHistoryRecord(
            history_id=str(row["history_id"]),
            run_id=str(row["run_id"]),
            subject_key=str(row["subject_key"]),
            decision=decision,
            revision=int(row["revision"]),
            recorded_at=_parse_datetime(str(row["recorded_at"])),
        )

    def get_current_review(self, *, run_id: str, subject_key: str) -> ReviewRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM review_current WHERE run_id = ? AND subject_key = ?", (run_id, subject_key)).fetchone()
            return None if row is None else self._review_record_from_row(row)

    def register_review_subject_context(self, *, run_id: str, context: ReviewCompatibilityContext) -> ReviewCompatibilityContext:
        with self._transaction() as connection:
            artifact = connection.execute("SELECT run_id FROM artifacts WHERE artifact_id = ?", (context.subject_artifact_id,)).fetchone()
            if artifact is None or str(artifact["run_id"]) != run_id:
                raise PlatformError("review context must bind an existing artifact in the requested run")
            existing = connection.execute(
                "SELECT context_json FROM review_subject_contexts WHERE run_id = ? AND checkpoint = ? AND artifact_id = ?",
                (run_id, context.review_checkpoint_id.value, context.subject_artifact_id),
            ).fetchone()
            if existing is not None:
                stored = ReviewCompatibilityContext.model_validate(_load_json(str(existing["context_json"]), {}))
                if stored != context:
                    raise ArtifactConflictError("review subject context is already bound to different authoritative semantics")
                return stored
            connection.execute(
                "INSERT INTO review_subject_contexts(run_id, checkpoint, artifact_id, context_json, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, context.review_checkpoint_id.value, context.subject_artifact_id, _dump(context), datetime.now().astimezone().isoformat()),
            )
            self._audit(connection, "review_subject_context_registered", run_id=run_id, artifact_id=context.subject_artifact_id, status=context.review_checkpoint_id.value, content_hash=context.subject_content_hash, detail="trusted review context persisted")
            return context

    def get_review_subject_context(self, *, run_id: str, checkpoint: str, artifact_id: str) -> ReviewCompatibilityContext | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT context_json FROM review_subject_contexts WHERE run_id = ? AND checkpoint = ? AND artifact_id = ?",
                (run_id, checkpoint, artifact_id),
            ).fetchone()
            return None if row is None else ReviewCompatibilityContext.model_validate(_load_json(str(row["context_json"]), {}))

    def list_review_subject_contexts(self, *, run_id: str, checkpoint: str | None = None) -> tuple[ReviewCompatibilityContext, ...]:
        sql = "SELECT context_json FROM review_subject_contexts WHERE run_id = ?"
        parameters: list[object] = [run_id]
        if checkpoint is not None:
            sql += " AND checkpoint = ?"
            parameters.append(checkpoint)
        sql += " ORDER BY checkpoint, artifact_id"
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(ReviewCompatibilityContext.model_validate(_load_json(str(row["context_json"]), {})) for row in rows)

    @staticmethod
    def _record_review_in_connection(connection: sqlite3.Connection, record: ReviewRecord, *, expected_revision: int) -> ReviewRecord:
        if expected_revision < 0 or record.revision != expected_revision + 1:
            raise ConcurrencyConflictError("review revision does not match the expected compare-and-swap revision")
        if record.subject_key != review_subject_key(record.decision):
            raise PlatformError("review subject key must be derived from the complete authoritative context")
        if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (record.run_id,)).fetchone() is None:
            raise PlatformError("review requires an existing run")
        current = connection.execute("SELECT revision FROM review_current WHERE run_id = ? AND subject_key = ?", (record.run_id, record.subject_key)).fetchone()
        current_revision = 0 if current is None else int(current["revision"])
        if current_revision != expected_revision:
            raise ConcurrencyConflictError("review revision changed before compare-and-swap update")
        history_id = f"review-{record.decision.review_decision_id}-{record.revision}"
        connection.execute(
            "INSERT INTO review_history(history_id, run_id, subject_key, decision_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (history_id, record.run_id, record.subject_key, _dump(record.decision), record.revision, record.recorded_at.isoformat()),
        )
        if current is None:
            connection.execute(
                "INSERT INTO review_current(run_id, subject_key, decision_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (record.run_id, record.subject_key, _dump(record.decision), record.revision, record.recorded_at.isoformat()),
            )
        else:
            cursor = connection.execute(
                "UPDATE review_current SET decision_json = ?, revision = ?, recorded_at = ? WHERE run_id = ? AND subject_key = ? AND revision = ?",
                (_dump(record.decision), record.revision, record.recorded_at.isoformat(), record.run_id, record.subject_key, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("review revision changed before compare-and-swap update")
        SQLiteControlStore._audit(connection, "review_recorded", run_id=record.run_id, status=record.decision.decision.value, detail=f"review revision {record.revision} recorded")
        return record

    def record_review(self, record: ReviewRecord, *, expected_revision: int) -> ReviewRecord:
        with self._transaction() as connection:
            return self._record_review_in_connection(connection, record, expected_revision=expected_revision)

    def record_review_with_idempotency(self, record: ReviewRecord, *, expected_revision: int, idempotency: IdempotencyRecord) -> tuple[ReviewRecord, bool]:
        if idempotency.state != "COMPLETED":
            raise ValueError("review idempotency must be completed")
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (idempotency.scope, idempotency.key)).fetchone()
            if existing_row is not None:
                existing = self._idempotency_from_connection(existing_row)
                self._assert_idempotency_match(existing, idempotency)
                body = existing.response_body.get("review")
                if not isinstance(body, dict):
                    raise PlatformError("review idempotency record does not contain a replayable result")
                return ReviewRecord.model_validate(body), True
            stored = self._record_review_in_connection(connection, record, expected_revision=expected_revision)
            self._insert_idempotency(connection, idempotency.model_copy(update={"response_body": {"review": stored.model_dump(mode="json")}, "resource_id": stored.decision.review_decision_id}))
            return stored, False

    def record_review_with_action_state_with_idempotency(
        self,
        record: ReviewRecord,
        state: ReviewActionState,
        *,
        expected_review_revision: int,
        expected_action_revision: int,
        idempotency: IdempotencyRecord,
    ) -> tuple[ReviewRecord, ReviewActionState, bool]:
        if idempotency.state != "COMPLETED":
            raise ValueError("review lifecycle idempotency must be completed")
        if record.revision != expected_review_revision + 1 or state.run_id != record.run_id or state.subject_key != record.subject_key:
            raise ConcurrencyConflictError("legacy review lifecycle revisions are not valid")
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (idempotency.scope, idempotency.key)).fetchone()
            if existing_row is not None:
                existing = self._idempotency_from_connection(existing_row)
                self._assert_idempotency_match(existing, idempotency)
                body = existing.response_body
                return ReviewRecord.model_validate(body["review"]), ReviewActionState.model_validate(body["state"]), True
            stored = self._record_review_in_connection(connection, record, expected_revision=expected_review_revision)
            current = connection.execute("SELECT revision FROM review_action_current WHERE run_id = ? AND subject_key = ?", (state.run_id, state.subject_key)).fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            if current_revision != expected_action_revision:
                raise ConcurrencyConflictError("review action revision changed before legacy review synchronization")
            if current is None:
                if expected_action_revision != 0:
                    raise ConcurrencyConflictError("legacy review action baseline is stale")
                connection.execute(
                    "INSERT INTO review_action_current(run_id, subject_key, state_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (state.run_id, state.subject_key, _dump(state), state.action_revision, state.updated_at.isoformat()),
                )
            else:
                cursor = connection.execute(
                    "UPDATE review_action_current SET state_json = ?, revision = ?, recorded_at = ? WHERE run_id = ? AND subject_key = ? AND revision = ?",
                    (_dump(state), state.action_revision, state.updated_at.isoformat(), state.run_id, state.subject_key, expected_action_revision),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("legacy review action baseline lost its compare-and-swap race")
            self._audit(connection, "review_lifecycle_synchronized", run_id=record.run_id, artifact_id=record.decision.subject_artifact_id, status=record.decision.decision.value, content_hash=record.decision.subject_content_hash, detail=f"legacy review synchronized action revision {state.action_revision}")
            self._insert_idempotency(connection, idempotency.model_copy(update={"response_body": {"review": stored.model_dump(mode="json"), "state": state.model_dump(mode="json")}, "resource_id": stored.decision.review_decision_id}))
            return stored, state, False

    def list_review_history(self, *, run_id: str, subject_key: str | None = None, limit: int = 100, offset: int = 0) -> tuple[ReviewHistoryRecord, ...]:
        if limit < 1 or offset < 0:
            raise ValueError("review history limit must be positive and offset non-negative")
        sql = "SELECT * FROM review_history WHERE run_id = ?"
        parameters: list[object] = [run_id]
        if subject_key is not None:
            sql += " AND subject_key = ?"
            parameters.append(subject_key)
        sql += " ORDER BY subject_key, revision LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(self._review_history_from_row(row) for row in rows)

    @staticmethod
    def _review_action_state_from_row(row: sqlite3.Row) -> ReviewActionState:
        state = ReviewActionState.model_validate(_load_json(str(row["state_json"]), {}))
        if state.run_id != str(row["run_id"]) or state.subject_key != str(row["subject_key"]) or state.action_revision != int(row["revision"]):
            raise PlatformError("stored review action state does not match its durable key")
        return state

    @staticmethod
    def _review_action_history_from_row(row: sqlite3.Row) -> ReviewActionHistoryRecord:
        action = ReviewActionRecord.model_validate(_load_json(str(row["action_json"]), {}))
        if action.run_id != str(row["run_id"]) or action.subject_key != str(row["subject_key"]) or action.resulting_revision != int(row["revision"]):
            raise PlatformError("stored review action history does not match its durable key")
        return ReviewActionHistoryRecord(
            history_id=str(row["history_id"]),
            run_id=str(row["run_id"]),
            subject_key=str(row["subject_key"]),
            action=action,
            revision=int(row["revision"]),
            recorded_at=_parse_datetime(str(row["recorded_at"])),
        )

    def get_review_action_state(self, *, run_id: str, subject_key: str) -> ReviewActionState | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM review_action_current WHERE run_id = ? AND subject_key = ?", (run_id, subject_key)).fetchone()
            return None if row is None else self._review_action_state_from_row(row)

    def record_review_action_with_idempotency(self, record: ReviewActionRecord, state: ReviewActionState, *, expected_revision: int, idempotency: IdempotencyRecord) -> tuple[ReviewActionRecord, ReviewActionState, bool]:
        if idempotency.state != "COMPLETED":
            raise ValueError("review action idempotency must be completed")
        if record.previous_revision != expected_revision or record.resulting_revision != expected_revision + 1:
            raise ConcurrencyConflictError("review action revision does not match the expected compare-and-swap revision")
        if state.run_id != record.run_id or state.subject_key != record.subject_key or state.action_revision != record.resulting_revision:
            raise PlatformError("review action state must match the action revision")
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (idempotency.scope, idempotency.key)).fetchone()
            if existing_row is not None:
                existing = self._idempotency_from_connection(existing_row)
                self._assert_idempotency_match(existing, idempotency)
                action_body = existing.response_body.get("action")
                state_body = existing.response_body.get("state")
                if not isinstance(action_body, dict) or not isinstance(state_body, dict):
                    raise PlatformError("review action idempotency record is not replayable")
                return ReviewActionRecord.model_validate(action_body), ReviewActionState.model_validate(state_body), True
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (record.run_id,)).fetchone() is None:
                raise PlatformError("review action requires an existing run")
            current = connection.execute("SELECT revision FROM review_action_current WHERE run_id = ? AND subject_key = ?", (record.run_id, record.subject_key)).fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            if current_revision != expected_revision:
                raise ConcurrencyConflictError("review action revision changed before compare-and-swap update")
            history_id = f"review-action-{record.action_id}-{record.resulting_revision}"
            connection.execute(
                "INSERT INTO review_action_history(history_id, run_id, subject_key, action_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (history_id, record.run_id, record.subject_key, _dump(record), record.resulting_revision, record.recorded_at.isoformat()),
            )
            if current is None:
                connection.execute(
                    "INSERT INTO review_action_current(run_id, subject_key, state_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (state.run_id, state.subject_key, _dump(state), state.action_revision, state.updated_at.isoformat()),
                )
            else:
                cursor = connection.execute(
                    "UPDATE review_action_current SET state_json = ?, revision = ?, recorded_at = ? WHERE run_id = ? AND subject_key = ? AND revision = ?",
                    (_dump(state), state.action_revision, state.updated_at.isoformat(), state.run_id, state.subject_key, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("review action revision changed before compare-and-swap update")
            self._audit(connection, "review_action_recorded", run_id=record.run_id, artifact_id=record.subject_artifact_id, status=record.action.value, content_hash=record.subject_content_hash, detail=f"review action revision {record.resulting_revision} recorded")
            self._insert_idempotency(connection, idempotency.model_copy(update={"response_body": {"action": record.model_dump(mode="json"), "state": state.model_dump(mode="json")}, "resource_id": record.action_id}))
            return record, state, False

    def _requeue_review_descendants_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        checkpoint: str,
        now: datetime,
        review_contexts: tuple[ReviewCompatibilityContext, ...] | None = None,
    ) -> None:
        plan_row = connection.execute("SELECT * FROM execution_plans WHERE run_id = ?", (run_id,)).fetchone()
        if plan_row is None:
            return
        plan = self._plan_from_row(plan_row)
        stage_index = next((index for index, stage in enumerate(plan.stages) if stage.review_checkpoint is not None and stage.review_checkpoint.value == checkpoint), None)
        if stage_index is None:
            return
        for offset, stage in enumerate(plan.stages[stage_index:]):
            row = connection.execute("SELECT * FROM jobs WHERE run_id = ? AND stage_id = ?", (run_id, stage.stage_id)).fetchone()
            if row is None:
                continue
            if row["status"] == JobStatus.RUNNING.value and row["lease_owner"]:
                raise ConcurrencyConflictError("review lifecycle cannot requeue a leased downstream stage")
            checkpoint_context_json = row["review_context_json"]
            if offset == 0 and review_contexts is not None:
                checkpoint_context_json = _dump({"contexts": [context.model_dump(mode="json") for context in review_contexts]})
            elif offset == 0 and not checkpoint_context_json:
                registered_context_rows = connection.execute(
                    "SELECT context_json FROM review_subject_contexts WHERE run_id = ? AND checkpoint = ? ORDER BY artifact_id",
                    (run_id, checkpoint),
                ).fetchall()
                if registered_context_rows:
                    checkpoint_context_json = _dump({"contexts": [_load_json(str(item["context_json"]), {}) for item in registered_context_rows]})
            status = JobStatus.NEEDS_REVIEW.value if offset == 0 else JobStatus.BLOCKED.value
            failure_code = None if offset == 0 else "REVIEW_REQUEUE_WAITING"
            failure_classification = None if offset == 0 else FailureClassification.BLOCKED_PREREQUISITE.value
            failure_reason = None if offset == 0 else "downstream execution waits for the requeued review checkpoint"
            connection.execute(
                "UPDATE jobs SET status = ?, review_context_json = ?, attempt_id = NULL, available_at = ?, lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, cancellation_requested = 0, cancellation_requested_at = NULL, failure_code = ?, failure_classification = ?, failure_reason = ?, result_refs = ?, delivery_phase = ?, durable_result_json = NULL, revision = revision + 1 WHERE job_id = ?",
                (status, checkpoint_context_json, now.isoformat(), failure_code, failure_classification, failure_reason, _dump(()), DeliveryPhase.ATTEMPT_CREATED.value, str(row["job_id"])),
            )
            self._audit(connection, "review_descendants_requeued", run_id=run_id, status=JobStatus.QUEUED.value, detail=f"checkpoint={checkpoint}; stage={stage.stage_id}; job={row['job_id']}")
        run_row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run_row is not None and run_row["status"] not in {RunStatus.CANCELLED.value, RunStatus.SUCCEEDED.value}:
            connection.execute(
                "UPDATE runs SET status = ?, revision = revision + 1 WHERE run_id = ? AND revision = ?",
                (RunStatus.NEEDS_REVIEW.value, run_id, int(run_row["revision"])),
            )

    def record_review_and_action_with_idempotency(
        self,
        review: ReviewRecord | None,
        action: ReviewActionRecord,
        state: ReviewActionState,
        *,
        expected_review_revision: int,
        expected_action_revision: int,
        idempotency: IdempotencyRecord,
        requeue_checkpoint: str | None = None,
        requeue_review_contexts: tuple[ReviewCompatibilityContext, ...] | None = None,
    ) -> tuple[ReviewRecord | None, ReviewActionRecord, ReviewActionState, bool]:
        if idempotency.state != "COMPLETED":
            raise ValueError("review lifecycle idempotency must be completed")
        if action.previous_revision != expected_action_revision or action.resulting_revision != expected_action_revision + 1:
            raise ConcurrencyConflictError("review action revision does not match the expected compare-and-swap revision")
        if state.run_id != action.run_id or state.subject_key != action.subject_key or state.action_revision != action.resulting_revision:
            raise PlatformError("review action state must match the action revision")
        if review is not None and (review.revision != expected_review_revision + 1 or review.run_id != action.run_id or review.subject_key != action.subject_key):
            raise ConcurrencyConflictError("review decision revision does not match the expected compare-and-swap revision")
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (idempotency.scope, idempotency.key)).fetchone()
            if existing_row is not None:
                existing = self._idempotency_from_connection(existing_row)
                self._assert_idempotency_match(existing, idempotency)
                body = existing.response_body
                action_body = body.get("action")
                state_body = body.get("state")
                if not isinstance(action_body, dict) or not isinstance(state_body, dict):
                    raise PlatformError("review lifecycle idempotency record is not replayable")
                review_body = body.get("review")
                return (None if not isinstance(review_body, dict) else ReviewRecord.model_validate(review_body), ReviewActionRecord.model_validate(action_body), ReviewActionState.model_validate(state_body), True)
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (action.run_id,)).fetchone() is None:
                raise PlatformError("review lifecycle requires an existing run")
            stored_review = None if review is None else self._record_review_in_connection(connection, review, expected_revision=expected_review_revision)
            current = connection.execute("SELECT revision FROM review_action_current WHERE run_id = ? AND subject_key = ?", (action.run_id, action.subject_key)).fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            if current_revision != expected_action_revision:
                raise ConcurrencyConflictError("review action revision changed before compare-and-swap update")
            history_id = f"review-action-{action.action_id}-{action.resulting_revision}"
            connection.execute(
                "INSERT INTO review_action_history(history_id, run_id, subject_key, action_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (history_id, action.run_id, action.subject_key, _dump(action), action.resulting_revision, action.recorded_at.isoformat()),
            )
            if current is None:
                connection.execute(
                    "INSERT INTO review_action_current(run_id, subject_key, state_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (state.run_id, state.subject_key, _dump(state), state.action_revision, state.updated_at.isoformat()),
                )
            else:
                cursor = connection.execute(
                    "UPDATE review_action_current SET state_json = ?, revision = ?, recorded_at = ? WHERE run_id = ? AND subject_key = ? AND revision = ?",
                    (_dump(state), state.action_revision, state.updated_at.isoformat(), state.run_id, state.subject_key, expected_action_revision),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("review action revision changed before compare-and-swap update")
            if requeue_checkpoint is not None:
                self._requeue_review_descendants_in_connection(
                    connection,
                    run_id=action.run_id,
                    checkpoint=requeue_checkpoint,
                    now=action.recorded_at,
                    review_contexts=requeue_review_contexts,
                )
            self._audit(connection, "review_action_recorded", run_id=action.run_id, artifact_id=action.subject_artifact_id, status=action.action.value, content_hash=action.subject_content_hash, detail=f"review action revision {action.resulting_revision} recorded")
            response_body = {"action": action.model_dump(mode="json"), "state": state.model_dump(mode="json")}
            if stored_review is not None:
                response_body["review"] = stored_review.model_dump(mode="json")
            self._insert_idempotency(connection, idempotency.model_copy(update={"response_body": response_body, "resource_id": action.action_id}))
            return stored_review, action, state, False

    def record_review_invalidation_with_lifecycle(
        self,
        review: ReviewRecord,
        state: ReviewActionState,
        *,
        expected_review_revision: int,
        expected_action_revision: int,
        idempotency: IdempotencyRecord,
        requeue_checkpoint: str,
    ) -> tuple[ReviewRecord, ReviewActionState, bool]:
        if idempotency.state != "COMPLETED":
            raise ValueError("review invalidation idempotency must be completed")
        if review.revision != expected_review_revision + 1 or state.action_revision != expected_action_revision + 1:
            raise ConcurrencyConflictError("review invalidation revisions are not a valid compare-and-swap transition")
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (idempotency.scope, idempotency.key)).fetchone()
            if existing_row is not None:
                existing = self._idempotency_from_connection(existing_row)
                self._assert_idempotency_match(existing, idempotency)
                body = existing.response_body
                return ReviewRecord.model_validate(body["review"]), ReviewActionState.model_validate(body["state"]), True
            self._record_review_in_connection(connection, review, expected_revision=expected_review_revision)
            current = connection.execute("SELECT revision FROM review_action_current WHERE run_id = ? AND subject_key = ?", (state.run_id, state.subject_key)).fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            if current_revision != expected_action_revision:
                raise ConcurrencyConflictError("review action revision changed before invalidation")
            if current is None:
                connection.execute(
                    "INSERT INTO review_action_current(run_id, subject_key, state_json, revision, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (state.run_id, state.subject_key, _dump(state), state.action_revision, state.updated_at.isoformat()),
                )
            else:
                cursor = connection.execute(
                    "UPDATE review_action_current SET state_json = ?, revision = ?, recorded_at = ? WHERE run_id = ? AND subject_key = ? AND revision = ?",
                    (_dump(state), state.action_revision, state.updated_at.isoformat(), state.run_id, state.subject_key, expected_action_revision),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("review action revision changed before invalidation")
            self._requeue_review_descendants_in_connection(connection, run_id=review.run_id, checkpoint=requeue_checkpoint, now=review.recorded_at)
            self._audit(connection, "review_invalidated", run_id=review.run_id, artifact_id=review.decision.subject_artifact_id, status=review.decision.decision.value, content_hash=review.decision.subject_content_hash, detail=review.decision.invalidation_reason or "review invalidated")
            self._insert_idempotency(connection, idempotency.model_copy(update={"response_body": {"review": review.model_dump(mode="json"), "state": state.model_dump(mode="json")}, "resource_id": review.decision.review_decision_id}))
            return review, state, False

    def list_review_action_history(self, *, run_id: str, subject_key: str | None = None, limit: int = 100, offset: int = 0) -> tuple[ReviewActionHistoryRecord, ...]:
        if limit < 1 or offset < 0:
            raise ValueError("review action history limit must be positive and offset non-negative")
        sql = "SELECT * FROM review_action_history WHERE run_id = ?"
        parameters: list[object] = [run_id]
        if subject_key is not None:
            sql += " AND subject_key = ?"
            parameters.append(subject_key)
        sql += " ORDER BY subject_key, revision LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(self._review_action_history_from_row(row) for row in rows)

    @staticmethod
    def _idempotency_from_row(row: sqlite3.Row) -> IdempotencyRecord:
        return SQLiteControlStore._idempotency_from_connection(row)

    def get_idempotency(self, *, scope: str, key: str) -> IdempotencyRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (scope, key)).fetchone()
            return None if row is None else self._idempotency_from_row(row)

    def record_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (record.scope, record.key)).fetchone()
            if existing is not None:
                stored = self._idempotency_from_row(existing)
                self._assert_idempotency_match(stored, record)
                return stored
            return self._insert_idempotency(connection, record)

    def reserve_idempotency(self, record: IdempotencyRecord) -> tuple[IdempotencyRecord, bool]:
        if record.state != "RESERVED":
            raise ValueError("idempotency reservation must use RESERVED state")
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (record.scope, record.key)).fetchone()
            if existing is not None:
                stored = self._idempotency_from_row(existing)
                self._assert_idempotency_match(stored, record)
                return stored, False
            return self._insert_idempotency(connection, record), True

    def _update_idempotency(self, record: IdempotencyRecord, *, state: str) -> IdempotencyRecord:
        with self._transaction() as connection:
            existing_row = connection.execute("SELECT * FROM api_idempotency WHERE scope = ? AND idem_key = ?", (record.scope, record.key)).fetchone()
            if existing_row is None:
                raise PlatformError("idempotency record is not reserved")
            existing = self._idempotency_from_row(existing_row)
            self._assert_idempotency_match(existing, record)
            connection.execute(
                "UPDATE api_idempotency SET state = ?, response_status = ?, response_body = ?, resource_id = ? WHERE scope = ? AND idem_key = ?",
                (state, record.response_status, _dump(record.response_body), record.resource_id or existing.resource_id, record.scope, record.key),
            )
            self._audit(connection, "api_idempotency_updated", status=state, detail=f"scope={record.scope}")
            return record.model_copy(update={"state": state, "resource_id": record.resource_id or existing.resource_id})

    def complete_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        return self._update_idempotency(record, state="COMPLETED")

    def mark_idempotency_unknown(self, record: IdempotencyRecord) -> IdempotencyRecord:
        return self._update_idempotency(record, state="UNKNOWN")

    def get_dependents(self, artifact_id: str) -> tuple[ArtifactRef, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT a.* FROM artifacts a JOIN artifact_dependencies d ON d.artifact_id = a.artifact_id WHERE d.upstream_artifact_id = ? ORDER BY a.artifact_id", (artifact_id,)).fetchall()
            return tuple(self._artifact_from_row(row) for row in rows)

    def inspect_dependencies(self, artifact_id: str):
        from dirty_data_to_olap.domain.contracts.platform import DependencyResolution

        with self._connect() as connection:
            rows = connection.execute("SELECT d.*, a.content_hash AS current_hash FROM artifact_dependencies d JOIN artifacts a ON a.artifact_id = d.upstream_artifact_id WHERE d.artifact_id = ? ORDER BY d.upstream_artifact_id", (artifact_id,)).fetchall()
        dependencies = tuple(ArtifactDependency(artifact_id=str(row["artifact_id"]), upstream_artifact_id=str(row["upstream_artifact_id"]), expected_content_hash=str(row["expected_content_hash"]), relationship_kind=str(row["relationship_kind"])) for row in rows)
        stale = tuple(str(row["upstream_artifact_id"]) for row in rows if str(row["expected_content_hash"]) != str(row["current_hash"]))
        return DependencyResolution(artifact_id=artifact_id, dependencies=dependencies, stale_dependency_ids=stale, status="STALE_DEPENDENCY" if stale else "RESOLVED")

    def resolve_dependencies(self, artifact_id: str) -> tuple[ArtifactDependency, ...]:
        result = self.inspect_dependencies(artifact_id)
        if result.stale_dependency_ids:
            raise StaleDependencyError("STALE_DEPENDENCY: " + ",".join(result.stale_dependency_ids))
        return result.dependencies

    def record_cache_entry(self, entry: CacheEntry) -> CacheEntry:
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (entry.output_artifact_id,)).fetchone() is None:
                raise PlatformError("cache entry requires a registered output artifact")
            existing = connection.execute("SELECT * FROM cache_entries WHERE cache_key_hash = ?", (entry.cache_key_hash,)).fetchone()
            if existing:
                stored = CacheEntry(cache_key_hash=str(existing["cache_key_hash"]), cache_key=CacheKey.model_validate(_load_json(existing["cache_key"], {})), output_artifact_id=str(existing["output_artifact_id"]), output_content_hash=str(existing["output_content_hash"]), status=str(existing["status"]), created_at=_parse_datetime(str(existing["created_at"])), invalidated_reason=existing["invalidated_reason"])
                if stored.output_artifact_id != entry.output_artifact_id or stored.output_content_hash != entry.output_content_hash:
                    raise ArtifactConflictError("cache key is already bound to a different immutable output")
                return stored
            connection.execute("INSERT INTO cache_entries(cache_key_hash, cache_key, output_artifact_id, output_content_hash, status, created_at, invalidated_reason) VALUES (?, ?, ?, ?, ?, ?, ?)", (entry.cache_key_hash, _dump(entry.cache_key), entry.output_artifact_id, entry.output_content_hash, entry.status.value, entry.created_at.isoformat(), entry.invalidated_reason))
            self._audit(connection, "cache_entry_recorded", artifact_id=entry.output_artifact_id, status=entry.status.value, content_hash=entry.output_content_hash, detail="cache metadata persisted")
            return entry

    def get_cache_entry(self, cache_key: CacheKey) -> CacheEntry | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM cache_entries WHERE cache_key_hash = ?", (cache_key.key_hash,)).fetchone()
            if row is None:
                return None
            return CacheEntry(cache_key_hash=str(row["cache_key_hash"]), cache_key=CacheKey.model_validate(_load_json(row["cache_key"], {})), output_artifact_id=str(row["output_artifact_id"]), output_content_hash=str(row["output_content_hash"]), status=str(row["status"]), created_at=_parse_datetime(str(row["created_at"])), invalidated_reason=row["invalidated_reason"])

    def invalidate_cache_entry(self, cache_key: CacheKey, *, reason: str) -> None:
        with self._transaction() as connection:
            connection.execute("UPDATE cache_entries SET status = ?, invalidated_reason = ? WHERE cache_key_hash = ?", (CacheEntryStatus.INVALIDATED.value, reason, cache_key.key_hash))
            row = connection.execute("SELECT a.run_id, a.artifact_id, a.content_hash FROM cache_entries c JOIN artifacts a ON a.artifact_id = c.output_artifact_id WHERE c.cache_key_hash = ?", (cache_key.key_hash,)).fetchone()
            self._audit(
                connection,
                "cache_invalidated",
                run_id=None if row is None else str(row["run_id"]),
                artifact_id=None if row is None else str(row["artifact_id"]),
                status=CacheEntryStatus.INVALIDATED.value,
                content_hash=None if row is None else str(row["content_hash"]),
                detail=reason,
            )

    @staticmethod
    def _gate_from_row(row: sqlite3.Row) -> GateEvidence:
        return GateEvidence(
            gate_id=str(row["gate_id"]),
            run_id=str(row["run_id"]),
            status=str(row["status"]),
            eligible=bool(row["eligible"]),
            validation_report_artifact_id=str(row["validation_report_artifact_id"]),
            validation_report_run_id=str(row["validation_report_run_id"]),
            validation_report_id=str(row["validation_report_id"]),
            validation_report_content_hash=str(row["validation_report_content_hash"]),
            policy_version=str(row["policy_version"]),
            verified_content_commit=str(row["verified_content_commit"]),
            recorded_at=_parse_datetime(str(row["recorded_at"])),
            provenance_refs=tuple(_load_json(row["provenance_refs"], [])),
        )

    @staticmethod
    def _decode_registered_report(payload: bytes) -> ValidationReport:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError("registered ValidationReport bytes are not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ArtifactIntegrityError("registered ValidationReport payload must be a JSON object")
        declared_hash = value.pop("content_hash", None)
        try:
            report = ValidationReport.model_validate(value)
        except Exception as exc:
            raise ArtifactIntegrityError("registered ValidationReport does not validate as the project contract") from exc
        if declared_hash is not None and declared_hash != report.content_hash:
            raise ArtifactIntegrityError("registered ValidationReport semantic hash does not match its payload")
        return report

    def _validate_gate_report(self, evidence: GateEvidence, validation_report: ValidationReport, artifact_store: ArtifactStorePort, connection: sqlite3.Connection) -> None:
        if not isinstance(validation_report, ValidationReport):
            raise PlatformError("gate evidence requires the typed ValidationReport contract")
        row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (evidence.validation_report_artifact_id,)).fetchone()
        if row is None:
            raise PlatformError("gate evidence requires a registered ValidationReport artifact")
        artifact = self._artifact_from_row(row)
        if artifact.artifact_kind != "ValidationReport":
            raise PlatformError("gate evidence requires artifact_kind=ValidationReport")
        if artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise PlatformError("gate evidence requires a published ValidationReport")
        if artifact.content_hash != evidence.validation_report_content_hash:
            raise ArtifactConflictError("gate evidence hash does not match its ValidationReport artifact")
        try:
            stored = artifact_store.stat(artifact)
        except (KeyError, PlatformError) as exc:
            raise ArtifactIntegrityError("gate evidence artifact is not present in the configured artifact store") from exc
        if stored != artifact:
            raise ArtifactConflictError("gate evidence artifact metadata differs between control and artifact stores")
        integrity = artifact_store.verify(artifact)
        if integrity.state.value != "VERIFIED":
            raise ArtifactIntegrityError("gate evidence requires verified ValidationReport bytes")
        parsed = self._decode_registered_report(artifact_store.read(artifact))
        if parsed != validation_report:
            raise ArtifactIntegrityError("typed ValidationReport does not correspond to the registered artifact bytes")
        if evidence.gate_id != "G6_DATA_CORRECTNESS":
            raise PlatformError("ValidationReport-backed gate evidence is scoped to G6_DATA_CORRECTNESS")
        if evidence.status.value != parsed.g6_status.value or evidence.eligible != parsed.g6_eligible:
            raise ArtifactConflictError("gate evidence status and eligibility must derive from ValidationReport")
        if evidence.policy_version != parsed.policy.policy_version:
            raise ArtifactConflictError("gate evidence policy version must derive from ValidationReport")
        if evidence.validation_report_run_id != parsed.run_id or evidence.validation_report_id != parsed.report_id:
            raise ArtifactConflictError("gate evidence must bind the ValidationReport run and report identity")

    def record_gate_evidence(self, evidence: GateEvidence, *, validation_report: ValidationReport, artifact_store: ArtifactStorePort) -> GateEvidence:
        with self._transaction() as connection:
            self._validate_gate_report(evidence, validation_report, artifact_store, connection)
            existing = connection.execute("SELECT * FROM gate_evidence WHERE gate_id = ? AND run_id = ?", (evidence.gate_id, evidence.run_id)).fetchone()
            if existing:
                stored = self._gate_from_row(existing)
                stable_stored = stored.model_dump(mode="json", exclude={"recorded_at"})
                stable_requested = evidence.model_dump(mode="json", exclude={"recorded_at"})
                if "legacy:gate-evidence-unverified" in stored.provenance_refs:
                    connection.execute(
                        "UPDATE gate_evidence SET status = ?, eligible = ?, validation_report_artifact_id = ?, validation_report_run_id = ?, validation_report_id = ?, validation_report_content_hash = ?, policy_version = ?, verified_content_commit = ?, recorded_at = ?, provenance_refs = ? WHERE gate_id = ? AND run_id = ?",
                        (evidence.status.value, int(evidence.eligible), evidence.validation_report_artifact_id, evidence.validation_report_run_id, evidence.validation_report_id, evidence.validation_report_content_hash, evidence.policy_version, evidence.verified_content_commit, evidence.recorded_at.isoformat(), _dump(evidence.provenance_refs), evidence.gate_id, evidence.run_id),
                    )
                    return evidence
                if stable_stored != stable_requested:
                    raise ArtifactConflictError("gate evidence identity is already bound to different evidence")
                return stored
            connection.execute("INSERT INTO gate_evidence(gate_id, run_id, status, eligible, validation_report_artifact_id, validation_report_run_id, validation_report_id, validation_report_content_hash, policy_version, verified_content_commit, recorded_at, provenance_refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (evidence.gate_id, evidence.run_id, evidence.status.value, int(evidence.eligible), evidence.validation_report_artifact_id, evidence.validation_report_run_id, evidence.validation_report_id, evidence.validation_report_content_hash, evidence.policy_version, evidence.verified_content_commit, evidence.recorded_at.isoformat(), _dump(evidence.provenance_refs)))
            self._audit(connection, "gate_evidence_recorded", run_id=evidence.run_id, artifact_id=evidence.validation_report_artifact_id, status=evidence.status.value, content_hash=evidence.validation_report_content_hash, detail=f"gate {evidence.gate_id} recorded")
            return evidence

    def get_gate_evidence(self, gate_id: str, *, run_id: str | None = None) -> GateEvidence | None:
        with self._connect() as connection:
            if run_id is None:
                row = connection.execute("SELECT * FROM gate_evidence WHERE gate_id = ? ORDER BY recorded_at DESC LIMIT 1", (gate_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM gate_evidence WHERE gate_id = ? AND run_id = ?", (gate_id, run_id)).fetchone()
            if row is None:
                return None
            return self._gate_from_row(row)

    def record_staged_dataset(self, manifest: StagedDatasetManifest) -> StagedDatasetManifest:
        with self._transaction() as connection:
            manifest = StagedDatasetManifest.model_validate(manifest.model_dump(mode="json"))
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (manifest.run_id,)).fetchone() is None:
                raise PlatformError("staged dataset requires an existing run")
            for part in manifest.parts:
                row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (part.artifact_ref.artifact_id,)).fetchone()
                if row is None or self._artifact_from_row(row) != part.artifact_ref:
                    raise PlatformError("staged manifest part must match a registered artifact")
            existing = connection.execute("SELECT manifest FROM staged_datasets WHERE run_id = ? AND dataset_id = ? AND dataset_version = ?", (manifest.run_id, manifest.dataset_id, manifest.dataset_version)).fetchone()
            if existing:
                stored = StagedDatasetManifest.model_validate(_load_json(existing["manifest"], {}))
                if stored != manifest:
                    raise ArtifactConflictError("staged dataset identity is already bound to a different manifest")
                return stored
            connection.execute("INSERT INTO staged_datasets(run_id, dataset_id, dataset_version, manifest) VALUES (?, ?, ?, ?)", (manifest.run_id, manifest.dataset_id, manifest.dataset_version, _dump(manifest)))
            self._audit(connection, "staged_dataset_registered", run_id=manifest.run_id, status="PUBLISHED", detail=f"dataset {manifest.dataset_id}/{manifest.dataset_version} persisted")
            return manifest

    def get_staged_dataset(self, run_id: str, dataset_id: str, dataset_version: str) -> StagedDatasetManifest | None:
        with self._connect() as connection:
            row = connection.execute("SELECT manifest FROM staged_datasets WHERE run_id = ? AND dataset_id = ? AND dataset_version = ?", (run_id, dataset_id, dataset_version)).fetchone()
            return None if row is None else StagedDatasetManifest.model_validate(_load_json(row["manifest"], {}))

    # ------------------------------------------------------------------
    # Step28 durable job substrate
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_from_row(row: sqlite3.Row) -> ExecutionPlan:
        plan = ExecutionPlan.model_validate(_load_json(str(row["plan_json"]), {}))
        if str(row["content_hash"]) != plan.content_hash:
            raise PlatformError("execution plan content hash is corrupt")
        return plan

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        context = None
        contexts: tuple[ReviewCompatibilityContext, ...] = ()
        if row["review_context_json"]:
            payload = _load_json(str(row["review_context_json"]), {})
            if isinstance(payload, dict) and isinstance(payload.get("contexts"), list):
                contexts = tuple(ReviewCompatibilityContext.model_validate(item) for item in payload["contexts"])
                context = contexts[0] if len(contexts) == 1 else None
            else:
                context = ReviewCompatibilityContext.model_validate(payload)
        durable_result = _load_json(str(row["durable_result_json"]), {}) if "durable_result_json" in row.keys() and row["durable_result_json"] else None
        return JobRecord(
            job_id=str(row["job_id"]),
            run_id=str(row["run_id"]),
            job_kind=JobKind(str(row["job_kind"])),
            command_id=row["command_id"],
            action=row["action"],
            stage_id=row["stage_id"],
            parent_job_id=row["parent_job_id"],
            plan_id=row["plan_id"],
            attempt_id=row["attempt_id"],
            status=JobStatus(str(row["status"])),
            priority=int(row["priority"]),
            created_at=_parse_datetime(str(row["created_at"])),
            available_at=_parse_datetime(str(row["available_at"])),
            lease_owner=row["lease_owner"],
            lease_generation=int(row["lease_generation"]),
            lease_expires_at=_parse_datetime(str(row["lease_expires_at"])) if row["lease_expires_at"] else None,
            heartbeat_at=_parse_datetime(str(row["heartbeat_at"])) if row["heartbeat_at"] else None,
            delivery_count=int(row["delivery_count"]),
            retry_count=int(row["retry_count"]),
            failure_code=row["failure_code"],
            failure_classification=FailureClassification(str(row["failure_classification"])) if row["failure_classification"] else None,
            failure_reason=row["failure_reason"],
            cancellation_requested=bool(row["cancellation_requested"]),
            cancellation_requested_at=_parse_datetime(str(row["cancellation_requested_at"])) if row["cancellation_requested_at"] else None,
            result_refs=tuple(_load_json(str(row["result_refs"]), [])),
            review_context=context,
            review_contexts=contexts,
            delivery_phase=DeliveryPhase(str(row["delivery_phase"])) if "delivery_phase" in row.keys() else DeliveryPhase.NOT_STARTED,
            replay_safety=ReplaySafety(str(row["replay_safety"])) if "replay_safety" in row.keys() else ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN,
            source_scope=row["source_scope"] if "source_scope" in row.keys() else None,
            durable_result=durable_result,
            metadata=dict(_load_json(str(row["metadata"]), {})),
            revision=int(row["revision"]),
        )

    def register_execution_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (plan.run_id,)).fetchone() is None:
                raise PlatformError("execution plan requires an existing run")
            existing = connection.execute("SELECT * FROM execution_plans WHERE run_id = ?", (plan.run_id,)).fetchone()
            if existing is not None:
                stored = self._plan_from_row(existing)
                if stored != plan:
                    raise ArtifactConflictError("run already has a different execution plan")
                return stored
            connection.execute(
                "INSERT INTO execution_plans(plan_id, run_id, graph_source, graph_version, content_hash, plan_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (plan.plan_id, plan.run_id, plan.graph_source, plan.graph_version, plan.content_hash, _dump(plan), plan.created_at.isoformat()),
            )
            self._audit(connection, "execution_plan_registered", run_id=plan.run_id, status="REGISTERED", detail=f"plan={plan.plan_id}; source={plan.graph_source}")
            return plan

    def get_execution_plan(self, run_id: str) -> ExecutionPlan | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM execution_plans WHERE run_id = ?", (run_id,)).fetchone()
            return None if row is None else self._plan_from_row(row)

    def advance_execution_plan(self, plan: ExecutionPlan, *, expected_content_hash: str) -> ExecutionPlan:
        """CAS-update one stable plan identity as runtime truth becomes available."""

        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM execution_plans WHERE run_id = ?", (plan.run_id,)).fetchone()
            if row is None:
                raise PlatformError("execution plan does not exist")
            stored = self._plan_from_row(row)
            if stored.plan_id != plan.plan_id or str(row["content_hash"]) != expected_content_hash:
                raise ConcurrencyConflictError("phased execution plan advancement lost its compare-and-swap race")
            cursor = connection.execute(
                "UPDATE execution_plans SET content_hash = ?, plan_json = ? WHERE run_id = ? AND plan_id = ? AND content_hash = ?",
                (plan.content_hash, _dump(plan), plan.run_id, plan.plan_id, expected_content_hash),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("phased execution plan advancement lost its compare-and-swap race")
            self._audit(
                connection,
                "execution_plan_advanced",
                run_id=plan.run_id,
                status=plan.planning_phase.value,
                detail=f"plan={plan.plan_id}; revision={plan.revision}; pending={','.join(plan.pending_stage_ids)}",
            )
            return plan

    @staticmethod
    def _command_job_id(command: ExecutionCommand) -> str:
        return stable_id("job", {"command_id": command.command_id})

    def enqueue_execution_command(self, command: ExecutionCommand, run: RunRecord) -> tuple[JobRecord, bool]:
        now = command.created_at
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run.run_id,)).fetchone() is None:
                raise PlatformError("execution command requires an existing run")
            existing = connection.execute("SELECT * FROM jobs WHERE command_id = ?", (command.command_id,)).fetchone()
            if existing is not None:
                stored = self._job_from_row(existing)
                if (stored.run_id, stored.action) != (command.run_id, command.action.value) or stored.metadata.get("command_fingerprint") not in {None, command.request_fingerprint}:
                    raise ArtifactConflictError("command_id is already bound to a different command")
                return stored, True
            job = JobRecord(
                job_id=self._command_job_id(command),
                run_id=command.run_id,
                job_kind=JobKind.COMMAND,
                command_id=command.command_id,
                action=command.action.value,
                status=JobStatus.QUEUED,
                created_at=now,
                available_at=now,
                metadata={"principal_source": command.principal_source, "command_fingerprint": command.request_fingerprint},
            )
            connection.execute(
                "INSERT INTO jobs(job_id, run_id, job_kind, command_id, action, stage_id, parent_job_id, plan_id, attempt_id, status, priority, created_at, available_at, lease_owner, lease_generation, lease_expires_at, heartbeat_at, delivery_count, retry_count, failure_code, failure_classification, failure_reason, cancellation_requested, cancellation_requested_at, result_refs, review_context_json, delivery_phase, replay_safety, source_scope, durable_result_json, metadata, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job.job_id, job.run_id, job.job_kind.value, job.command_id, job.action, None, None, None, None, job.status.value, job.priority, job.created_at.isoformat(), job.available_at.isoformat(), None, 0, None, None, 0, 0, None, None, None, 0, None, _dump(job.result_refs), None, DeliveryPhase.NOT_STARTED.value, job.replay_safety.value, None, None, _dump(job.metadata), 0),
            )
            self._audit(connection, "job_enqueued", run_id=job.run_id, status=job.status.value, detail=f"job={job.job_id}; command={command.command_id}")
            return job, False

    def enqueue_stage_job(self, *, run_id: str, plan_id: str, stage_id: str, parent_job_id: str | None = None, available_at: datetime | None = None) -> JobRecord:
        now = available_at or datetime.now(timezone.utc)
        with self._transaction() as connection:
            plan_row = connection.execute("SELECT * FROM execution_plans WHERE plan_id = ? AND run_id = ?", (plan_id, run_id)).fetchone()
            if plan_row is None:
                raise PlatformError("stage job requires a registered run plan")
            plan = self._plan_from_row(plan_row)
            stage = plan.stage(stage_id)
            if stage is None:
                raise PlatformError("stage job is not present in the authoritative execution plan")
            existing = connection.execute("SELECT * FROM jobs WHERE run_id = ? AND stage_id = ?", (run_id, stage_id)).fetchone()
            if existing is not None:
                return self._job_from_row(existing)
            job = JobRecord(
                job_id=stable_id("stage-job", {"run_id": run_id, "plan_id": plan_id, "stage_id": stage_id}),
                run_id=run_id,
                job_kind=JobKind.STAGE,
                stage_id=stage_id,
                plan_id=plan_id,
                parent_job_id=parent_job_id,
                status=JobStatus.QUEUED,
                created_at=now,
                available_at=now,
                delivery_phase=DeliveryPhase.ATTEMPT_CREATED,
                replay_safety=stage.replay_safety,
                source_scope=stage.source_scope,
                metadata=dict(stage.metadata),
            )
            connection.execute(
                "INSERT INTO jobs(job_id, run_id, job_kind, command_id, action, stage_id, parent_job_id, plan_id, attempt_id, status, priority, created_at, available_at, lease_owner, lease_generation, lease_expires_at, heartbeat_at, delivery_count, retry_count, failure_code, failure_classification, failure_reason, cancellation_requested, cancellation_requested_at, result_refs, review_context_json, delivery_phase, replay_safety, source_scope, durable_result_json, metadata, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job.job_id, job.run_id, job.job_kind.value, None, None, job.stage_id, job.parent_job_id, job.plan_id, None, job.status.value, job.priority, job.created_at.isoformat(), job.available_at.isoformat(), None, 0, None, None, 0, 0, None, None, None, 0, None, _dump(job.result_refs), None, job.delivery_phase.value, job.replay_safety.value, job.source_scope, None, _dump(job.metadata), 0),
            )
            self._audit(connection, "stage_job_enqueued", run_id=run_id, status=job.status.value, detail=f"job={job.job_id}; stage={stage_id}")
            return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return None if row is None else self._job_from_row(row)

    def get_stage_job(self, *, run_id: str, stage_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE run_id = ? AND stage_id = ?", (run_id, stage_id)).fetchone()
            return None if row is None else self._job_from_row(row)

    def list_jobs(self, *, run_id: str, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[JobRecord, ...]:
        if limit < 1 or offset < 0:
            raise ValueError("job list limit must be positive and offset non-negative")
        sql = "SELECT * FROM jobs WHERE run_id = ?"
        params: list[object] = [run_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at, job_id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with self._connect() as connection:
            return tuple(self._job_from_row(row) for row in connection.execute(sql, tuple(params)).fetchall())

    def claim_next_job(self, *, worker_id: str, now: datetime, lease_seconds: int = 30, max_active_per_run: int = 1, max_active_per_source: int = 1) -> JobRecord | None:
        if not worker_id or lease_seconds < 1 or max_active_per_run < 1 or max_active_per_source < 1:
            raise ValueError("worker_id and a positive lease are required")
        expires = now + timedelta(seconds=lease_seconds)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE ((status IN ('QUEUED', 'RETRY_WAIT') AND available_at <= ? AND cancellation_requested = 0) OR (status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)) AND (SELECT COUNT(*) FROM jobs AS active_run WHERE active_run.run_id = jobs.run_id AND active_run.status = 'RUNNING' AND active_run.lease_expires_at IS NOT NULL AND active_run.lease_expires_at > ?) < ? AND (jobs.source_scope IS NULL OR (SELECT COUNT(*) FROM jobs AS active_source WHERE active_source.source_scope = jobs.source_scope AND active_source.status = 'RUNNING' AND active_source.lease_expires_at IS NOT NULL AND active_source.lease_expires_at > ?) < ?) ORDER BY priority DESC, available_at, created_at, job_id LIMIT 1",
                (now.isoformat(), now.isoformat(), now.isoformat(), max_active_per_run, now.isoformat(), max_active_per_source),
            ).fetchone()
            if row is None:
                return None
            generation = int(row["lease_generation"]) + 1
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, lease_owner = ?, lease_generation = ?, lease_expires_at = ?, heartbeat_at = ?, delivery_count = delivery_count + 1, revision = revision + 1 WHERE job_id = ? AND ((status IN ('QUEUED', 'RETRY_WAIT') AND available_at <= ? AND cancellation_requested = 0) OR (status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))",
                (JobStatus.RUNNING.value, worker_id, generation, expires.isoformat(), now.isoformat(), row["job_id"], now.isoformat(), now.isoformat()),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("job claim lost its compare-and-swap race")
            claimed = self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone())
            self._audit(connection, "job_claimed", run_id=claimed.run_id, status=claimed.status.value, detail=f"job={claimed.job_id}; worker={worker_id}; generation={generation}")
            return claimed

    def heartbeat_job(self, *, job_id: str, worker_id: str, lease_generation: int, now: datetime, lease_seconds: int = 30) -> JobRecord:
        expires = now + timedelta(seconds=lease_seconds)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET lease_expires_at = ?, heartbeat_at = ?, revision = revision + 1 WHERE job_id = ? AND status = ? AND lease_owner = ? AND lease_generation = ? AND lease_expires_at > ?",
                (expires.isoformat(), now.isoformat(), job_id, JobStatus.RUNNING.value, worker_id, lease_generation, now.isoformat()),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("heartbeat rejected by the current lease fence")
            return self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())

    def ensure_stage_attempt(self, *, job_id: str, worker_id: str, lease_generation: int, now: datetime) -> StageAttemptRecord:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise PlatformError("job was not found")
            if row["job_kind"] != JobKind.STAGE.value or row["status"] != JobStatus.RUNNING.value or row["lease_owner"] != worker_id or int(row["lease_generation"]) != lease_generation:
                raise ConcurrencyConflictError("stage start rejected by the current lease fence")
            if row["lease_expires_at"] is None or _parse_datetime(str(row["lease_expires_at"])) <= now:
                raise ConcurrencyConflictError("stage start rejected after lease expiry")
            if row["attempt_id"]:
                attempt_row = connection.execute("SELECT * FROM stage_attempts WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
                if attempt_row is None:
                    raise PlatformError("job references a missing stage attempt")
                return self._attempt_from_row(attempt_row)
            plan = self._plan_from_row(connection.execute("SELECT * FROM execution_plans WHERE plan_id = ?", (row["plan_id"],)).fetchone())
            stage = plan.stage(str(row["stage_id"]))
            if stage is None:
                raise PlatformError("stage is not in the execution plan")
            last = connection.execute("SELECT COALESCE(MAX(attempt_number), 0) FROM stage_attempts WHERE run_id = ? AND stage_id = ?", (row["run_id"], row["stage_id"])).fetchone()[0]
            input_refs: list[str] = []
            if str(row["stage_id"]) == "SOURCE_DISCOVERY":
                run_row = connection.execute("SELECT root_artifact_refs FROM runs WHERE run_id = ?", (row["run_id"],)).fetchone()
                if run_row is not None:
                    input_refs.extend(str(ref) for ref in _load_json(str(run_row["root_artifact_refs"]), []))
            for dependency_id in stage.dependencies:
                dependency_row = connection.execute("SELECT result_refs FROM jobs WHERE run_id = ? AND stage_id = ?", (row["run_id"], dependency_id)).fetchone()
                if dependency_row is not None:
                    input_refs.extend(str(ref) for ref in _load_json(str(dependency_row["result_refs"]), []))
            attempt = StageAttemptRecord(
                attempt_id=stable_id("stage-attempt", {"job_id": job_id, "stage_id": row["stage_id"], "attempt_number": int(last) + 1}),
                run_id=str(row["run_id"]),
                stage_id=str(row["stage_id"]),
                attempt_number=int(last) + 1,
                status=StageStatus.RUNNING,
                started_at=now,
                input_artifact_refs=tuple(input_refs),
                policy_config_fingerprint=stage.policy_config_fingerprint,
                revision=0,
            )
            connection.execute(
                "INSERT INTO stage_attempts(attempt_id, run_id, stage_id, attempt_number, status, started_at, finished_at, input_artifact_refs, output_artifact_refs, failure_code, failure_reason, policy_config_fingerprint, resource_budget_ref, delivery_phase, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (attempt.attempt_id, attempt.run_id, attempt.stage_id, attempt.attempt_number, attempt.status.value, attempt.started_at.isoformat(), None, _dump(attempt.input_artifact_refs), _dump(attempt.output_artifact_refs), None, None, attempt.policy_config_fingerprint, None, attempt.delivery_phase, 0),
            )
            cursor = connection.execute("UPDATE jobs SET attempt_id = ?, revision = revision + 1 WHERE job_id = ? AND status = ? AND lease_owner = ? AND lease_generation = ? AND attempt_id IS NULL", (attempt.attempt_id, job_id, JobStatus.RUNNING.value, worker_id, lease_generation))
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("stage attempt start lost its compare-and-swap race")
            self._audit(connection, "stage_attempt_started", run_id=attempt.run_id, status=attempt.status.value, detail=f"job={job_id}; attempt={attempt.attempt_id}; generation={lease_generation}")
            return attempt

    def mark_handler_delivery_started(self, *, job_id: str, worker_id: str, lease_generation: int, now: datetime) -> JobRecord:
        with self._transaction() as connection:
            row = self._lease_row(connection, job_id=job_id, worker_id=worker_id, lease_generation=lease_generation, now=now)
            if row["job_kind"] != JobKind.STAGE.value or not row["attempt_id"]:
                raise PlatformError("handler delivery requires a fenced stage attempt")
            attempt_row = connection.execute("SELECT * FROM stage_attempts WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
            if attempt_row is None:
                raise PlatformError("job references a missing stage attempt")
            attempt_revision = int(attempt_row["revision"])
            attempt_cursor = connection.execute(
                "UPDATE stage_attempts SET delivery_phase = ?, revision = revision + 1 WHERE attempt_id = ? AND revision = ?",
                (DeliveryPhase.HANDLER_DELIVERY_STARTED.value, row["attempt_id"], attempt_revision),
            )
            if attempt_cursor.rowcount != 1:
                raise ConcurrencyConflictError("handler delivery marker changed before it was persisted")
            cursor = connection.execute(
                "UPDATE jobs SET delivery_phase = ?, revision = revision + 1 WHERE job_id = ? AND status = ? AND lease_owner = ? AND lease_generation = ?",
                (DeliveryPhase.HANDLER_DELIVERY_STARTED.value, job_id, JobStatus.RUNNING.value, worker_id, lease_generation),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("handler delivery marker lost its lease fence")
            self._audit(connection, "handler_delivery_started", run_id=str(row["run_id"]), status=DeliveryPhase.HANDLER_DELIVERY_STARTED.value, detail=f"job={job_id}; attempt={row['attempt_id']}")
            return self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())

    def record_stage_result(self, *, job_id: str, worker_id: str, lease_generation: int, attempt: StageAttemptRecord, result: StageExecutionResult, now: datetime) -> JobRecord:
        with self._transaction() as connection:
            row = self._lease_row(connection, job_id=job_id, worker_id=worker_id, lease_generation=lease_generation, now=now)
            if row["job_kind"] != JobKind.STAGE.value or row["attempt_id"] != attempt.attempt_id:
                raise ConcurrencyConflictError("stage result does not match the fenced attempt")
            attempt_cursor = connection.execute(
                "UPDATE stage_attempts SET delivery_phase = ?, revision = revision + 1 WHERE attempt_id = ? AND revision = ?",
                (DeliveryPhase.RESULT_RECORDED.value, attempt.attempt_id, attempt.revision),
            )
            if attempt_cursor.rowcount != 1:
                raise ConcurrencyConflictError("stage result changed before durable recording")
            cursor = connection.execute(
                "UPDATE jobs SET delivery_phase = ?, durable_result_json = ?, revision = revision + 1 WHERE job_id = ? AND status = ? AND lease_owner = ? AND lease_generation = ?",
                (DeliveryPhase.RESULT_RECORDED.value, _dump(result), job_id, JobStatus.RUNNING.value, worker_id, lease_generation),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("stage result lost its lease fence")
            self._audit(connection, "stage_result_recorded", run_id=str(row["run_id"]), status=DeliveryPhase.RESULT_RECORDED.value, detail=f"job={job_id}; attempt={attempt.attempt_id}")
            return self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())

    def _lease_row(self, connection: sqlite3.Connection, *, job_id: str, worker_id: str, lease_generation: int, now: datetime | None = None) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise PlatformError("job was not found")
        if row["status"] != JobStatus.RUNNING.value or row["lease_owner"] != worker_id or int(row["lease_generation"]) != lease_generation:
            raise ConcurrencyConflictError("job mutation rejected by the lease fence")
        if now is not None and (row["lease_expires_at"] is None or _parse_datetime(str(row["lease_expires_at"])) <= now):
            raise ConcurrencyConflictError("job mutation rejected after lease expiry")
        return row

    def finalize_stage_job(self, *, job_id: str, worker_id: str, lease_generation: int, attempt: StageAttemptRecord, result: StageExecutionResult, status: str, now: datetime, retry_count: int = 0, available_at: datetime | None = None) -> JobRecord:
        with self._transaction() as connection:
            row = self._lease_row(connection, job_id=job_id, worker_id=worker_id, lease_generation=lease_generation, now=now)
            requested_status = JobStatus(status)
            run_row = connection.execute("SELECT status FROM runs WHERE run_id = ?", (row["run_id"],)).fetchone()
            cancelled = bool(row["cancellation_requested"]) or (run_row is not None and run_row["status"] == "CANCELLED")
            effective_attempt = attempt
            effective_status = requested_status
            failure_code = result.failure_code
            failure_classification = result.failure_classification
            failure_reason = result.failure_reason
            if cancelled and requested_status in {JobStatus.SUCCEEDED, JobStatus.NEEDS_REVIEW, JobStatus.BLOCKED, JobStatus.RETRY_WAIT}:
                effective_status = JobStatus.CANCELLED
                effective_attempt = attempt.model_copy(update={"status": StageStatus.CANCELLED, "finished_at": now, "output_artifact_refs": ()})
                failure_code = "CANCELLATION_REQUESTED"
                failure_classification = FailureClassification.CANCELLED
                failure_reason = "stage completion lost the cancellation race"
            if effective_attempt.attempt_id != row["attempt_id"]:
                raise ConcurrencyConflictError("finalization attempt does not match the fenced job")
            updated_attempt = effective_attempt.model_copy(update={"finished_at": effective_attempt.finished_at or now})
            attempt_cursor = connection.execute(
                "UPDATE stage_attempts SET status = ?, started_at = ?, finished_at = ?, input_artifact_refs = ?, output_artifact_refs = ?, failure_code = ?, failure_reason = ?, policy_config_fingerprint = ?, resource_budget_ref = ?, delivery_phase = ?, revision = revision + 1 WHERE attempt_id = ? AND revision = ?",
                (updated_attempt.status.value, updated_attempt.started_at.isoformat() if updated_attempt.started_at else None, updated_attempt.finished_at.isoformat() if updated_attempt.finished_at else None, _dump(updated_attempt.input_artifact_refs), _dump(updated_attempt.output_artifact_refs), updated_attempt.failure_code, updated_attempt.failure_reason, updated_attempt.policy_config_fingerprint, updated_attempt.resource_budget_ref, DeliveryPhase.FINALIZED.value, updated_attempt.attempt_id, updated_attempt.revision),
            )
            if attempt_cursor.rowcount != 1:
                raise ConcurrencyConflictError("stage attempt finalization changed before its job")
            if effective_status is JobStatus.RETRY_WAIT:
                next_attempt_id = None
                next_phase = DeliveryPhase.ATTEMPT_CREATED.value
                durable_result = None
            else:
                next_attempt_id = updated_attempt.attempt_id
                next_phase = DeliveryPhase.FINALIZED.value
                durable_result = _dump(result)
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, attempt_id = ?, available_at = ?, lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, retry_count = ?, failure_code = ?, failure_classification = ?, failure_reason = ?, result_refs = ?, review_context_json = ?, delivery_phase = ?, durable_result_json = ?, revision = revision + 1 WHERE job_id = ? AND status = ? AND lease_owner = ? AND lease_generation = ?",
                (effective_status.value, next_attempt_id, (available_at or now).isoformat(), retry_count, failure_code, failure_classification.value if isinstance(failure_classification, FailureClassification) else failure_classification, failure_reason, _dump(result.output_artifact_refs if effective_status is not JobStatus.CANCELLED else ()), _review_context_json(result.review_context, result.review_contexts), next_phase, durable_result, job_id, JobStatus.RUNNING.value, worker_id, lease_generation),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("stage finalization lost its compare-and-swap race")
            self._audit(connection, "stage_job_finalized", run_id=str(row["run_id"]), status=effective_status.value, detail=f"job={job_id}; attempt={updated_attempt.attempt_id}; generation={lease_generation}")
            return self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())

    def finalize_command_job(self, *, job_id: str, worker_id: str, lease_generation: int, status: str, now: datetime, detail: str, failure_code: str | None = None, failure_classification: str | None = None) -> JobRecord:
        if len(detail) > 512 or "\n" in detail or "\r" in detail:
            raise ValueError("command result detail must be safe metadata")
        with self._transaction() as connection:
            row = self._lease_row(connection, job_id=job_id, worker_id=worker_id, lease_generation=lease_generation, now=now)
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, failure_code = ?, failure_classification = ?, failure_reason = ?, delivery_phase = ?, revision = revision + 1 WHERE job_id = ? AND status = ? AND lease_owner = ? AND lease_generation = ?",
                (JobStatus(status).value, failure_code, failure_classification, detail, DeliveryPhase.FINALIZED.value, job_id, JobStatus.RUNNING.value, worker_id, lease_generation),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("command finalization lost its lease fence")
            self._audit(connection, "command_job_finalized", run_id=str(row["run_id"]), status=status, detail=f"job={job_id}; {detail}")
            return self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())

    def request_run_cancellation(self, *, run_id: str, now: datetime) -> RunRecord:
        with self._transaction() as connection:
            run_row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run_row is None:
                raise PlatformError("run was not found")
            if run_row["status"] not in {RunStatus.SUCCEEDED.value, RunStatus.CANCELLED.value}:
                connection.execute("UPDATE runs SET status = ?, revision = revision + 1 WHERE run_id = ? AND revision = ?", (RunStatus.CANCELLED.value, run_id, int(run_row["revision"])))
            connection.execute(
                "UPDATE jobs SET status = CASE WHEN status IN ('QUEUED', 'RETRY_WAIT', 'NEEDS_REVIEW', 'BLOCKED') THEN 'CANCELLED' ELSE status END, cancellation_requested = 1, cancellation_requested_at = ?, lease_owner = CASE WHEN status IN ('QUEUED', 'RETRY_WAIT', 'NEEDS_REVIEW', 'BLOCKED') THEN NULL ELSE lease_owner END, lease_expires_at = CASE WHEN status IN ('QUEUED', 'RETRY_WAIT', 'NEEDS_REVIEW', 'BLOCKED') THEN NULL ELSE lease_expires_at END, heartbeat_at = CASE WHEN status IN ('QUEUED', 'RETRY_WAIT', 'NEEDS_REVIEW', 'BLOCKED') THEN NULL ELSE heartbeat_at END, revision = revision + 1 WHERE run_id = ? AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')",
                (now.isoformat(), run_id),
            )
            self._audit(connection, "run_cancellation_requested", run_id=run_id, status=RunStatus.CANCELLED.value, detail="queued work cancelled; running work fenced at finalization")
            return self._run_from_row(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def resume_job(self, *, job_id: str, now: datetime) -> JobRecord:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise PlatformError("job was not found")
            if row["status"] not in {JobStatus.NEEDS_REVIEW.value, JobStatus.BLOCKED.value, JobStatus.FAILED.value}:
                raise PlatformError("only review, blocked or failed jobs can be resumed")
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, attempt_id = NULL, available_at = ?, lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, cancellation_requested = 0, cancellation_requested_at = NULL, failure_code = NULL, failure_classification = NULL, failure_reason = NULL, delivery_phase = ?, durable_result_json = NULL, revision = revision + 1 WHERE job_id = ? AND status = ?",
                (JobStatus.QUEUED.value, now.isoformat(), DeliveryPhase.ATTEMPT_CREATED.value, job_id, row["status"]),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflictError("job resume lost its state compare-and-swap")
            self._audit(connection, "job_resumed", run_id=str(row["run_id"]), status=JobStatus.QUEUED.value, detail=f"job={job_id}")
            return self._job_from_row(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())

    def mark_artifact_tombstoned(self, artifact_id: str, *, plan_id: str, reason: str) -> None:
        with self._transaction() as connection:
            cursor = connection.execute("UPDATE artifacts SET publication_state = ?, tombstone_reason = ?, tombstoned_at = ? WHERE artifact_id = ? AND publication_state = ?", (ArtifactPublicationState.TOMBSTONED.value, reason, datetime.now().astimezone().isoformat(), artifact_id, ArtifactPublicationState.PUBLISHED.value))
            if cursor.rowcount != 1:
                raise PlatformError("only a published artifact can be tombstoned")
            self._audit(connection, "artifact_tombstoned", artifact_id=artifact_id, status=ArtifactPublicationState.TOMBSTONED.value, detail=f"plan={plan_id}; {reason}")

    def record_audit_event(self, event_type: str, *, run_id: str | None = None, artifact_id: str | None = None, status: str, content_hash: str | None = None, detail: str) -> None:
        with self._transaction() as connection:
            self._audit(connection, event_type, run_id=run_id, artifact_id=artifact_id, status=status, content_hash=content_hash, detail=detail)

    def build_reproducibility_manifest(self, run_id: str, *, artifact_store) -> ReproducibilityManifest:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        artifacts = self.list_artifacts(run_id=run_id)
        return ReproducibilityManifest(run_id=run.run_id, configuration_fingerprint=run.configuration_fingerprint, git_content_commit=run.git_content_commit, source_snapshot_refs=run.source_snapshot_refs, root_artifact_refs=tuple(artifact for artifact in artifacts), gate_refs=run.gate_refs, control_schema_version=self.schema_version)

    def list_audit_events(self, *, run_id: str | None = None) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            if run_id is None:
                rows = connection.execute("SELECT * FROM audit_events ORDER BY event_id").fetchall()
            else:
                rows = connection.execute("SELECT * FROM audit_events WHERE run_id = ? ORDER BY event_id", (run_id,)).fetchall()
            return tuple(dict(row) for row in rows)

    @staticmethod
    def _audit(connection: sqlite3.Connection, event_type: str, *, status: str, detail: str, run_id: str | None = None, artifact_id: str | None = None, content_hash: str | None = None) -> None:
        connection.execute("INSERT INTO audit_events(event_type, run_id, artifact_id, status, content_hash, recorded_at, detail) VALUES (?, ?, ?, ?, ?, ?, ?)", (event_type, run_id, artifact_id, status, content_hash, datetime.now().astimezone().isoformat(), detail))

    def close(self) -> None:
        """Operations use short-lived connections; retained for port symmetry."""
