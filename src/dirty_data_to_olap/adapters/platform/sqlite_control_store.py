"""SQLite V1 adapter for the typed :class:`ControlStorePort`."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
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
    StageAttemptRecord,
    StageStatus,
    StagedDatasetManifest,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport


CURRENT_SCHEMA_VERSION = 3


def _dump(value: object) -> str:
    return json.dumps(value.model_dump(mode="json") if hasattr(value, "model_dump") else value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    return default if value is None else json.loads(value)


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
        connection = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None, check_same_thread=False)
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
            "CREATE TABLE IF NOT EXISTS stage_attempts (attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), stage_id TEXT NOT NULL, attempt_number INTEGER NOT NULL, status TEXT NOT NULL, started_at TEXT, finished_at TEXT, input_artifact_refs TEXT NOT NULL, output_artifact_refs TEXT NOT NULL, failure_code TEXT, failure_reason TEXT, policy_config_fingerprint TEXT NOT NULL, resource_budget_ref TEXT, revision INTEGER NOT NULL, UNIQUE(run_id, stage_id, attempt_number))",
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
            revision=int(row["revision"]),
        )

    def create_run(self, run: RunRecord) -> RunRecord:
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
            if existing:
                stored = self._run_from_row(existing)
                if stored.model_copy(update={"revision": run.revision}) != run:
                    raise ArtifactConflictError("run identity is already bound to different metadata")
                return stored
            connection.execute(
                "INSERT INTO runs(run_id, project_id, created_at, status, configuration_fingerprint, git_content_commit, root_artifact_refs, source_snapshot_refs, gate_refs, latest_stage_refs, revision, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, run.project_id, run.created_at.isoformat(), run.status.value, run.configuration_fingerprint, run.git_content_commit, _dump(run.root_artifact_refs), _dump(run.source_snapshot_refs), _dump(run.gate_refs), _dump(run.latest_stage_refs), run.revision, _dump(run.metadata)),
            )
            self._audit(connection, "run_created", run_id=run.run_id, status=run.status.value, detail="run metadata persisted")
            return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return None if row is None else self._run_from_row(row)

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
                "INSERT INTO stage_attempts(attempt_id, run_id, stage_id, attempt_number, status, started_at, finished_at, input_artifact_refs, output_artifact_refs, failure_code, failure_reason, policy_config_fingerprint, resource_budget_ref, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (attempt.attempt_id, attempt.run_id, attempt.stage_id, attempt.attempt_number, attempt.status.value, attempt.started_at.isoformat() if attempt.started_at else None, attempt.finished_at.isoformat() if attempt.finished_at else None, _dump(attempt.input_artifact_refs), _dump(attempt.output_artifact_refs), attempt.failure_code, attempt.failure_reason, attempt.policy_config_fingerprint, attempt.resource_budget_ref, attempt.revision),
            )
            self._audit(connection, "stage_attempt_created", run_id=attempt.run_id, status=attempt.status.value, detail=f"attempt {attempt.attempt_id} persisted")
            return attempt

    def get_stage_attempt(self, attempt_id: str) -> StageAttemptRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stage_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            return None if row is None else self._attempt_from_row(row)

    def update_stage_attempt(self, attempt: StageAttemptRecord, *, expected_revision: int) -> StageAttemptRecord:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE stage_attempts SET status = ?, started_at = ?, finished_at = ?, input_artifact_refs = ?, output_artifact_refs = ?, failure_code = ?, failure_reason = ?, policy_config_fingerprint = ?, resource_budget_ref = ?, revision = revision + 1 WHERE attempt_id = ? AND revision = ?",
                (attempt.status.value, attempt.started_at.isoformat() if attempt.started_at else None, attempt.finished_at.isoformat() if attempt.finished_at else None, _dump(attempt.input_artifact_refs), _dump(attempt.output_artifact_refs), attempt.failure_code, attempt.failure_reason, attempt.policy_config_fingerprint, attempt.resource_budget_ref, attempt.attempt_id, expected_revision),
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

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None) -> tuple[ArtifactRef, ...]:
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
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(self._artifact_from_row(row) for row in rows)

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
