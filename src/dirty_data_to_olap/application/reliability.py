"""Step35 reliability primitives for the local durable runtime.

The control store is the source of truth for recovery.  This module deliberately
uses the SQLite backup API and the typed platform ports; it does not infer
successful work from a queue, a process, or an in-memory cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping

from dirty_data_to_olap.adapters.platform.sqlite_control_store import CURRENT_SCHEMA_VERSION, SQLiteControlStore
from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, PlatformError
from dirty_data_to_olap.domain.contracts.jobs import DeliveryPhase, JobRecord, JobStatus, ReplaySafety


class ReliabilityError(PlatformError):
    """A recovery operation failed its explicit reliability contract."""


class ReliabilityFailureDomain(str, Enum):
    CONTROL_STORE = "CONTROL_STORE"
    WORKER = "WORKER"
    PROVIDER = "PROVIDER"
    ARTIFACT_STORE = "ARTIFACT_STORE"
    MATERIALIZATION = "MATERIALIZATION"
    TELEMETRY = "TELEMETRY"
    REVIEW = "REVIEW"
    SHUTDOWN = "SHUTDOWN"


class RecoveryDisposition(str, Enum):
    SAFE_TO_RESUME = "SAFE_TO_RESUME"
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    ACTIVE_LEASE = "ACTIVE_LEASE"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"
    TERMINAL = "TERMINAL"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BackupManifest:
    """Integrity metadata for one SQLite backup bundle."""

    format_version: str
    schema_version: int
    database_sha256: str
    database_bytes: int
    table_names: tuple[str, ...]
    created_at: str
    backup_file: Path
    manifest_file: Path

    @property
    def sha256(self) -> str:
        return self.database_sha256

    def as_json(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "schema_version": self.schema_version,
            "database_sha256": self.database_sha256,
            "database_bytes": self.database_bytes,
            "table_names": list(self.table_names),
            "created_at": self.created_at,
        }


_BACKUP_FORMAT = "step35-sqlite-backup-v1"
_REQUIRED_TABLES = frozenset(
    {
        "schema_meta",
        "schema_migrations",
        "runs",
        "stage_attempts",
        "artifacts",
        "execution_plans",
        "jobs",
        "review_current",
        "review_history",
    }
)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe_path(path: Path, *, project_root: Path, label: str) -> Path:
    resolved = Path(path).resolve()
    root = Path(project_root).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ReliabilityError(f"{label} must remain under the configured project root") from None
    if any(part.lower() == "quality_unit_artifacts" for part in resolved.parts):
        raise ReliabilityError("protected quality artifacts are outside reliability authority")
    return resolved


def _bundle_paths(value: Path) -> tuple[Path, Path]:
    path = Path(value).resolve()
    if path.exists() and path.is_dir():
        return path / "control.sqlite", path / "manifest.json"
    if path.suffix.lower() in {".sqlite", ".db"}:
        return path, Path(f"{path}.manifest.json")
    return path / "control.sqlite", path / "manifest.json"


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _check_database(path: Path, *, expected_sha256: str | None = None, expected_bytes: int | None = None) -> tuple[int, tuple[str, ...]]:
    if not path.is_file():
        raise ReliabilityError("control-store backup database is missing")
    actual_hash, actual_bytes = _sha256_file(path)
    if expected_sha256 is not None and actual_hash != expected_sha256:
        raise ReliabilityError("control-store backup hash does not match its manifest")
    if expected_bytes is not None and actual_bytes != expected_bytes:
        raise ReliabilityError("control-store backup size does not match its manifest")
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ReliabilityError("control-store backup failed SQLite integrity_check")
            version_row = connection.execute("SELECT schema_version FROM schema_meta").fetchone()
            if version_row is None or int(version_row[0]) != CURRENT_SCHEMA_VERSION:
                raise ReliabilityError("control-store backup has an unsupported schema version")
            tables = tuple(sorted(str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()))
        finally:
            connection.close()
    except ReliabilityError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise ReliabilityError("control-store backup is unreadable") from exc
    missing = _REQUIRED_TABLES.difference(tables)
    if missing:
        raise ReliabilityError(f"control-store backup is missing required tables: {sorted(missing)}")
    return actual_bytes, tables


def backup_control_store(control_store: SQLiteControlStore, destination: Path) -> BackupManifest:
    """Create a consistent SQLite backup and a hash/size/schema manifest.

    ``destination`` may be a ``.sqlite``/``.db`` file or a directory bundle.
    Existing backup files are rejected so a prior evidence bundle cannot be
    silently overwritten.
    """

    if not isinstance(control_store, SQLiteControlStore):
        raise TypeError("Step35 backup requires the project SQLite control store")
    source_path = _safe_path(control_store.path, project_root=control_store.project_root, label="control store")
    backup_path, manifest_path = _bundle_paths(Path(destination))
    backup_path = _safe_path(backup_path, project_root=control_store.project_root, label="backup")
    manifest_path = _safe_path(manifest_path, project_root=control_store.project_root, label="backup manifest")
    if backup_path == source_path or backup_path.exists() or manifest_path.exists():
        raise ReliabilityError("backup destination already exists or is the live control store")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = backup_path.with_name(f".{backup_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(str(temporary))
        try:
            source.execute("PRAGMA query_only = ON")
            integrity = str(source.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ReliabilityError("live control store failed SQLite integrity_check")
            source_version = int(source.execute("SELECT schema_version FROM schema_meta").fetchone()[0])
            if source_version != CURRENT_SCHEMA_VERSION or control_store.schema_version != CURRENT_SCHEMA_VERSION:
                raise ReliabilityError("live control store schema is not the supported current version")
            source.backup(target, pages=100, sleep=0.01)
            target.commit()
        finally:
            target.close()
            source.close()
        os.replace(temporary, backup_path)
        byte_hash, byte_size = _sha256_file(backup_path)
        _bytes, tables = _check_database(backup_path, expected_sha256=byte_hash, expected_bytes=byte_size)
        manifest = BackupManifest(
            format_version=_BACKUP_FORMAT,
            schema_version=CURRENT_SCHEMA_VERSION,
            database_sha256=byte_hash,
            database_bytes=byte_size,
            table_names=tables,
            created_at=datetime.now(timezone.utc).isoformat(),
            backup_file=backup_path,
            manifest_file=manifest_path,
        )
        _atomic_json_write(manifest_path, manifest.as_json())
        return manifest
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def inspect_control_store_backup(backup: Path) -> BackupManifest:
    """Verify manifest, bytes, SQLite integrity, schema and required tables."""

    backup_path, manifest_path = _bundle_paths(Path(backup))
    if not manifest_path.is_file():
        raise ReliabilityError("control-store backup manifest is missing")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("format_version") != _BACKUP_FORMAT:
            raise ReliabilityError("unsupported control-store backup manifest")
        sha = str(payload["database_sha256"])
        if len(sha) != 64 or any(character not in "0123456789abcdef" for character in sha):
            raise ReliabilityError("backup manifest has an invalid hash")
        schema_version = int(payload["schema_version"])
        database_bytes = int(payload["database_bytes"])
        table_names = tuple(sorted(str(item) for item in payload["table_names"]))
        created_at = str(payload["created_at"])
    except ReliabilityError:
        raise
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ReliabilityError("control-store backup manifest is unreadable") from exc
    if schema_version != CURRENT_SCHEMA_VERSION or database_bytes < 1 or not created_at:
        raise ReliabilityError("control-store backup manifest has unsupported metadata")
    actual_bytes, actual_tables = _check_database(backup_path, expected_sha256=sha, expected_bytes=database_bytes)
    if actual_bytes != database_bytes or actual_tables != table_names or not _REQUIRED_TABLES <= set(table_names):
        raise ReliabilityError("control-store backup manifest does not describe the verified database")
    return BackupManifest(_BACKUP_FORMAT, schema_version, sha, database_bytes, table_names, created_at, backup_path, manifest_path)


def restore_control_store(backup: Path, destination: Path, *, project_root: Path | None = None) -> SQLiteControlStore:
    """Restore a verified backup through SQLite's backup API and reopen it."""

    manifest = inspect_control_store_backup(Path(backup))
    target = Path(destination).resolve()
    root = Path(project_root or target.parent).resolve()
    target = _safe_path(target, project_root=root, label="restored control store")
    if target == manifest.backup_file:
        raise ReliabilityError("restore destination cannot be the backup database")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.restore")
    try:
        source = sqlite3.connect(f"file:{manifest.backup_file.as_posix()}?mode=ro", uri=True)
        target_connection = sqlite3.connect(str(temporary))
        try:
            source.backup(target_connection, pages=100, sleep=0.01)
            target_connection.commit()
        finally:
            target_connection.close()
            source.close()
        _check_database(temporary, expected_sha256=manifest.database_sha256, expected_bytes=manifest.database_bytes)
        os.replace(temporary, target)
        for sidecar in (Path(f"{target}-wal"), Path(f"{target}-shm")):
            sidecar.unlink(missing_ok=True)
        restored = SQLiteControlStore(target, project_root=root)
        if restored.schema_version != CURRENT_SCHEMA_VERSION:
            restored.close()
            raise ReliabilityError("restored control store did not reopen at the supported schema")
        return restored
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _model_json(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def classify_recovery(job: JobRecord, *, artifact_integrity_states: tuple[str, ...] = (), now: datetime | None = None) -> RecoveryDisposition:
    """Classify one durable job without treating an uncertain side effect as success."""

    if artifact_integrity_states and any(state != "VERIFIED" for state in artifact_integrity_states):
        return RecoveryDisposition.REQUIRES_RECONCILIATION
    if job.status is JobStatus.SUCCEEDED or job.status is JobStatus.FAILED:
        return RecoveryDisposition.TERMINAL
    if job.status is JobStatus.CANCELLED:
        return RecoveryDisposition.CANCELLED
    if job.status is JobStatus.BLOCKED:
        return RecoveryDisposition.BLOCKED
    if job.status is JobStatus.NEEDS_REVIEW:
        return RecoveryDisposition.WAITING_FOR_REVIEW
    if job.status is JobStatus.RETRY_WAIT:
        return RecoveryDisposition.SAFE_TO_RETRY
    if job.status is JobStatus.QUEUED:
        return RecoveryDisposition.SAFE_TO_RESUME
    if job.status is JobStatus.RUNNING:
        expired = now is not None and (job.lease_expires_at is None or job.lease_expires_at <= now)
        uncertain_phase = job.delivery_phase in {DeliveryPhase.HANDLER_DELIVERY_STARTED, DeliveryPhase.RESULT_RECORDED, DeliveryPhase.RECONCILIATION_REQUIRED}
        if uncertain_phase and job.replay_safety is ReplaySafety.RECONCILIATION_REQUIRED_ON_UNKNOWN:
            return RecoveryDisposition.REQUIRES_RECONCILIATION
        if expired:
            return RecoveryDisposition.SAFE_TO_RESUME
        return RecoveryDisposition.ACTIVE_LEASE
    return RecoveryDisposition.UNKNOWN


def build_recovery_projection(*, control_store: ControlStorePort, artifact_store: ArtifactStorePort | None, run_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Build a safe, typed recovery projection for a run after restart/restore."""

    run = control_store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    jobs = tuple(control_store.list_jobs(run_id=run_id, limit=10_000))
    attempts = tuple(control_store.list_stage_attempts(run_id=run_id, limit=10_000))
    artifacts = tuple(control_store.list_artifacts(run_id=run_id, limit=10_000))
    integrity_by_artifact: dict[str, str] = {}
    for artifact in artifacts:
        if artifact_store is None:
            integrity_by_artifact[artifact.artifact_id] = "NOT_CHECKED"
            continue
        try:
            integrity_by_artifact[artifact.artifact_id] = artifact_store.verify(artifact).state.value
        except Exception:
            integrity_by_artifact[artifact.artifact_id] = "UNREADABLE"
    jobs_json = []
    for job in jobs:
        relevant = tuple(job.result_refs)
        states = tuple(integrity_by_artifact.get(ref, "MISSING") for ref in relevant)
        jobs_json.append({
            "job": _model_json(job),
            "recovery_disposition": classify_recovery(job, artifact_integrity_states=states, now=now).value,
            "result_artifact_integrity": {ref: integrity_by_artifact.get(ref, "MISSING") for ref in relevant},
        })
    history = tuple(control_store.list_review_history(run_id=run_id, limit=10_000))
    subjects = sorted({item.subject_key for item in history})
    reviews = []
    for subject in subjects:
        current = control_store.get_current_review(run_id=run_id, subject_key=subject)
        if current is not None:
            reviews.append(_model_json(current))
    return {
        "projection_version": "step35-recovery-v1",
        "schema_version": control_store.schema_version,
        "run": _model_json(run),
        "jobs": jobs_json,
        "stage_attempts": [_model_json(item) for item in attempts],
        "artifacts": [dict(_model_json(item), integrity_state=integrity_by_artifact.get(item.artifact_id, "MISSING")) for item in artifacts],
        "current_reviews": reviews,
        "review_history": [_model_json(item) for item in history],
        "summary": {
            "job_count": len(jobs),
            "attempt_count": len(attempts),
            "artifact_count": len(artifacts),
            "unverified_artifact_count": sum(state != "VERIFIED" for state in integrity_by_artifact.values()),
        },
    }


__all__ = [
    "BackupManifest",
    "RecoveryDisposition",
    "ReliabilityError",
    "ReliabilityFailureDomain",
    "backup_control_store",
    "build_recovery_projection",
    "classify_recovery",
    "inspect_control_store_backup",
    "restore_control_store",
]
