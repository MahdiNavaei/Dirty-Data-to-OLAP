"""Content-addressed local implementation of :class:`ArtifactStorePort`."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import BinaryIO, Iterator

from dirty_data_to_olap.application.platform import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactStorePort,
    CleanupAuthorizationError,
    PathConfinementError,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactIntegrityResult,
    ArtifactIntegrityState,
    ArtifactManifest,
    ArtifactPublication,
    ArtifactPublicationState,
    ArtifactRef,
    ArtifactStorageMode,
    CleanupAuthorization,
    normalize_logical_key,
)


def _sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("artifact stream must yield bytes")
        chunk_bytes = bytes(chunk)
        digest.update(chunk_bytes)
        size += len(chunk_bytes)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> tuple[str, int]:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


class LocalArtifactStore(ArtifactStorePort):
    """A bounded local artifact store with immutable content-addressed blobs.

    The store owns only its injected root.  ``refs`` are durable logical
    references; ``blobs`` contain bytes addressed by SHA-256.  A published
    reference is never overwritten by a different hash.
    """

    def __init__(self, root: Path, *, project_root: Path | None = None, disk_budget_bytes: int | None = None) -> None:
        self.project_root = (project_root or root.parent).resolve()
        self.root = Path(root).resolve()
        try:
            self.root.relative_to(self.project_root)
        except ValueError:
            raise PathConfinementError("artifact root must remain under the configured project root") from None
        if self.root == self.project_root:
            raise PathConfinementError("artifact root must be a dedicated child of the project root")
        if self._is_protected(self.root):
            raise PathConfinementError("protected workspace cannot be an artifact root")
        if disk_budget_bytes is not None and disk_budget_bytes <= 0:
            raise ValueError("disk budget must be positive")
        self.disk_budget_bytes = disk_budget_bytes
        self._lock = threading.RLock()
        self._blobs = self.root / "blobs" / "sha256"
        self._refs = self.root / "refs"
        self._tmp = self.root / "tmp"
        self._blobs.mkdir(parents=True, exist_ok=True)
        self._refs.mkdir(parents=True, exist_ok=True)
        self._tmp.mkdir(parents=True, exist_ok=True)

    def _is_protected(self, path: Path) -> bool:
        protected = self.project_root / "tests" / "quality_unit_artifacts"
        try:
            path.relative_to(protected)
        except ValueError:
            return False
        return True

    @staticmethod
    def _ref_filename(artifact_id: str) -> str:
        if not artifact_id or "\x00" in artifact_id or "/" in artifact_id or "\\" in artifact_id:
            raise PathConfinementError("artifact identity cannot be used as a filesystem path")
        digest = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
        return f"{digest[:2]}-{digest}.json"

    def _ref_path(self, artifact_id: str) -> Path:
        path = self._refs / self._ref_filename(artifact_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _record_payload(record_type: str, payload: object) -> dict[str, object]:
        value = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        return {"record_type": record_type, "payload": value}

    def _write_record(self, record_type: str, payload: object, artifact_id: str) -> None:
        path = self._ref_path(artifact_id)
        partial = self._tmp / f"ref-{uuid.uuid4().hex}.partial"
        data = json.dumps(self._record_payload(record_type, payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            with partial.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(partial, path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _read_record(self, artifact_id: str) -> tuple[str, object] | None:
        path = self._ref_path(artifact_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record_type = str(payload["record_type"])
            body = payload["payload"]
            if record_type == "ref":
                return record_type, ArtifactRef.model_validate(body)
            if record_type == "manifest":
                return record_type, ArtifactManifest.model_validate(body)
            if record_type == "tombstone":
                artifact = ArtifactRef.model_validate(body["artifact"])
                return record_type, artifact.model_copy(update={"publication_state": ArtifactPublicationState.TOMBSTONED})
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(f"artifact reference metadata is unreadable: {artifact_id}") from exc
        raise ArtifactIntegrityError(f"unknown artifact reference record: {artifact_id}")

    def _published_ref(self, artifact: ArtifactRef | str) -> ArtifactRef:
        artifact_id = artifact.artifact_id if isinstance(artifact, ArtifactRef) else artifact
        record = self._read_record(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        record_type, value = record
        if record_type != "ref" or not isinstance(value, ArtifactRef) or value.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise ArtifactIntegrityError(f"artifact is not published: {artifact_id}")
        return value

    def _blob_path(self, content_hash: str) -> Path:
        return self._blobs / content_hash[:2] / content_hash

    def _used_bytes(self) -> int:
        total = 0
        for path in self._blobs.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def _check_quota(self, additional_bytes: int) -> None:
        if self.disk_budget_bytes is not None and self._used_bytes() + additional_bytes > self.disk_budget_bytes:
            raise ArtifactIntegrityError("artifact publication exceeds the configured disk budget")

    def _identity_matches(self, existing: ArtifactManifest | ArtifactRef, manifest: ArtifactManifest) -> bool:
        fields = ("run_id", "stage_id", "attempt_id", "artifact_kind", "producer", "storage_mode")
        if not all(getattr(existing, field) == getattr(manifest, field) for field in fields):
            return False
        if manifest.storage_mode is ArtifactStorageMode.EXTERNAL and getattr(existing, "external_locator", None) not in (None, manifest.external_locator):
            return False
        return True

    def reserve(self, manifest: ArtifactManifest) -> ArtifactPublication:
        if manifest.storage_mode is not ArtifactStorageMode.MANAGED:
            raise ValueError("reserve is only valid for managed artifacts")
        with self._lock:
            existing = self._read_record(manifest.artifact_id)
            if existing is not None:
                record_type, value = existing
                if isinstance(value, ArtifactRef) and record_type == "tombstone":
                    raise ArtifactConflictError("tombstoned artifact identity cannot be reused")
                if not isinstance(value, (ArtifactManifest, ArtifactRef)) or not self._identity_matches(value, manifest):
                    raise ArtifactConflictError("artifact identity metadata conflicts with the existing reference")
                if isinstance(value, ArtifactRef):
                    return ArtifactPublication(
                        artifact_id=value.artifact_id,
                        content_hash=value.content_hash,
                        byte_size=value.byte_size,
                        storage_key=value.storage_key,
                        state=value.publication_state,
                        idempotent=True,
                    )
                return ArtifactPublication(
                    artifact_id=manifest.artifact_id,
                    content_hash=manifest.content_hash or "0" * 64,
                    byte_size=manifest.byte_size or 0,
                    storage_key=manifest.storage_key,
                    state=manifest.publication_state,
                    idempotent=True,
                )
            self._write_record("manifest", manifest.model_copy(update={"publication_state": ArtifactPublicationState.RESERVED}), manifest.artifact_id)
            return ArtifactPublication(
                artifact_id=manifest.artifact_id,
                content_hash=manifest.content_hash or "0" * 64,
                byte_size=manifest.byte_size or 0,
                storage_key=manifest.storage_key,
                state=ArtifactPublicationState.RESERVED,
            )

    def publish(self, manifest: ArtifactManifest, payload: bytes | BinaryIO) -> ArtifactRef:
        if manifest.storage_mode is not ArtifactStorageMode.MANAGED:
            raise ValueError("publish is only valid for managed artifacts")
        with self._lock:
            existing = self._read_record(manifest.artifact_id)
            if existing is not None and existing[0] == "tombstone":
                raise ArtifactConflictError("tombstoned artifact identity cannot be republished")
            if existing is not None and isinstance(existing[1], ArtifactRef):
                existing_ref = existing[1]
                payload_hash, payload_size = self._payload_hash(payload)
                if payload_hash != existing_ref.content_hash or payload_size != existing_ref.byte_size:
                    raise ArtifactConflictError("immutable artifact identity was reused with a different hash")
                if not self._identity_matches(existing_ref, manifest):
                    raise ArtifactConflictError("artifact identity metadata conflicts with the existing reference")
                return existing_ref
            self.reserve(manifest)
            temp_path = self._tmp / f"artifact-{uuid.uuid4().hex}.partial"
            try:
                if isinstance(payload, (bytes, bytearray, memoryview)):
                    source: BinaryIO = _BytesReader(bytes(payload))
                else:
                    source = payload
                with temp_path.open("wb") as target:
                    digest = hashlib.sha256()
                    size = 0
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        if not isinstance(chunk, (bytes, bytearray, memoryview)):
                            raise TypeError("artifact stream must yield bytes")
                        chunk_bytes = bytes(chunk)
                        size += len(chunk_bytes)
                        if manifest.byte_size is None and self.disk_budget_bytes is not None:
                            self._check_quota(size)
                        digest.update(chunk_bytes)
                        target.write(chunk_bytes)
                    target.flush()
                    os.fsync(target.fileno())
                content_hash = digest.hexdigest()
                if manifest.content_hash is not None and content_hash != manifest.content_hash:
                    raise ArtifactIntegrityError("claimed artifact hash does not match the payload")
                if manifest.byte_size is not None and size != manifest.byte_size:
                    raise ArtifactIntegrityError("claimed artifact size does not match the payload")
                blob = self._blob_path(content_hash)
                blob.parent.mkdir(parents=True, exist_ok=True)
                if blob.exists():
                    actual_hash, actual_size = _sha256_file(blob)
                    if actual_hash != content_hash or actual_size != size:
                        raise ArtifactIntegrityError("existing content-addressed blob is corrupt")
                    temp_path.unlink(missing_ok=True)
                else:
                    self._check_quota(size)
                    os.replace(temp_path, blob)
                ref = ArtifactRef(
                    artifact_id=manifest.artifact_id,
                    run_id=manifest.run_id,
                    stage_id=manifest.stage_id,
                    attempt_id=manifest.attempt_id,
                    artifact_kind=manifest.artifact_kind,
                    schema_version=manifest.schema_version,
                    media_type=manifest.media_type,
                    content_hash=content_hash,
                    byte_size=size,
                    storage_mode=ArtifactStorageMode.MANAGED,
                    logical_key=manifest.logical_key,
                    storage_key=f"blobs/sha256/{content_hash[:2]}/{content_hash}",
                    producer=manifest.producer,
                    retention_class=manifest.retention_class,
                    sensitivity_ref=manifest.sensitivity_ref,
                    publication_state=ArtifactPublicationState.PUBLISHED,
                    created_at=manifest.created_at,
                    provenance_refs=manifest.provenance_refs,
                )
                self._write_record("ref", ref, ref.artifact_id)
                return ref
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

    @staticmethod
    def _payload_hash(payload: bytes | BinaryIO) -> tuple[str, int]:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            value = bytes(payload)
            return hashlib.sha256(value).hexdigest(), len(value)
        return _sha256_stream(payload)

    def _resolve_external(self, locator: str) -> Path:
        if not locator or "\\" in locator or ":" in locator or Path(locator).is_absolute() or any(part in {"", ".", ".."} for part in locator.split("/")):
            raise PathConfinementError("external artifact locator must be a relative POSIX path without traversal")
        if self._is_protected(self.project_root / Path(*locator.split("/"))):
            raise PathConfinementError("protected workspace is outside platform authority")
        candidate = (self.project_root / Path(*locator.split("/"))).resolve(strict=True)
        try:
            candidate.relative_to(self.project_root)
        except ValueError:
            raise PathConfinementError("external artifact locator escapes the project root") from None
        if self._is_protected(candidate):
            raise PathConfinementError("protected workspace is outside platform authority")
        if not candidate.is_file():
            raise PathConfinementError("external artifact locator must identify a regular file")
        return candidate

    def register_external(self, manifest: ArtifactManifest, locator: str) -> ArtifactRef:
        if manifest.storage_mode is not ArtifactStorageMode.EXTERNAL:
            raise ValueError("external registration requires EXTERNAL storage mode")
        path = self._resolve_external(locator)
        content_hash, size = _sha256_file(path)
        if manifest.content_hash is not None and manifest.content_hash != content_hash:
            raise ArtifactIntegrityError("claimed external artifact hash does not match the file")
        if manifest.byte_size is not None and manifest.byte_size != size:
            raise ArtifactIntegrityError("claimed external artifact size does not match the file")
        with self._lock:
            existing = self._read_record(manifest.artifact_id)
            if existing is not None:
                if existing[0] == "tombstone":
                    raise ArtifactConflictError("tombstoned external artifact identity cannot be reused")
                if isinstance(existing[1], ArtifactRef):
                    if existing[1].content_hash != content_hash or not self._identity_matches(existing[1], manifest):
                        raise ArtifactConflictError("external artifact identity conflicts with the existing reference")
                    return existing[1]
            ref = ArtifactRef(
                artifact_id=manifest.artifact_id,
                run_id=manifest.run_id,
                stage_id=manifest.stage_id,
                attempt_id=manifest.attempt_id,
                artifact_kind=manifest.artifact_kind,
                schema_version=manifest.schema_version,
                media_type=manifest.media_type,
                content_hash=content_hash,
                byte_size=size,
                storage_mode=ArtifactStorageMode.EXTERNAL,
                logical_key=manifest.logical_key,
                external_locator=locator,
                producer=manifest.producer,
                retention_class=manifest.retention_class,
                sensitivity_ref=manifest.sensitivity_ref,
                publication_state=ArtifactPublicationState.PUBLISHED,
                created_at=manifest.created_at,
                provenance_refs=manifest.provenance_refs,
            )
            self._write_record("ref", ref, ref.artifact_id)
            return ref

    def open(self, artifact: ArtifactRef | str) -> BinaryIO:
        ref = self._published_ref(artifact)
        path = self._blob_path(ref.content_hash) if ref.storage_mode is ArtifactStorageMode.MANAGED else self._resolve_external(ref.external_locator or "")
        return path.open("rb")

    def read(self, artifact: ArtifactRef | str) -> bytes:
        with self.open(artifact) as stream:
            return stream.read()

    def exists(self, artifact: ArtifactRef | str) -> bool:
        try:
            return self.verify(artifact).state is ArtifactIntegrityState.VERIFIED
        except (KeyError, ArtifactIntegrityError, PathConfinementError):
            return False

    def verify(self, artifact: ArtifactRef | str) -> ArtifactIntegrityResult:
        artifact_id = artifact.artifact_id if isinstance(artifact, ArtifactRef) else artifact
        record = self._read_record(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        if record[0] == "tombstone" and isinstance(record[1], ArtifactRef):
            ref = record[1]
            return ArtifactIntegrityResult(
                artifact_id=ref.artifact_id,
                state=ArtifactIntegrityState.NOT_CHECKED,
                expected_content_hash=ref.content_hash,
                expected_byte_size=ref.byte_size,
                detail="tombstoned artifact is not consumable",
            )
        ref = self._published_ref(artifact)
        path = self._blob_path(ref.content_hash) if ref.storage_mode is ArtifactStorageMode.MANAGED else None
        try:
            if ref.storage_mode is ArtifactStorageMode.EXTERNAL:
                path = self._resolve_external(ref.external_locator or "")
        except (FileNotFoundError, OSError, PathConfinementError):
            return ArtifactIntegrityResult(
                artifact_id=ref.artifact_id,
                state=ArtifactIntegrityState.EXTERNAL_UNAVAILABLE,
                expected_content_hash=ref.content_hash,
                expected_byte_size=ref.byte_size,
                detail="controlled external artifact is unavailable",
            )
        if path is None or not path.is_file():
            return ArtifactIntegrityResult(
                artifact_id=ref.artifact_id,
                state=ArtifactIntegrityState.MISSING,
                expected_content_hash=ref.content_hash,
                expected_byte_size=ref.byte_size,
                detail="managed artifact blob is missing",
            )
        try:
            actual_hash, actual_size = _sha256_file(path)
        except OSError:
            return ArtifactIntegrityResult(
                artifact_id=ref.artifact_id,
                state=ArtifactIntegrityState.UNREADABLE,
                expected_content_hash=ref.content_hash,
                expected_byte_size=ref.byte_size,
                detail="artifact bytes could not be read",
            )
        if actual_hash != ref.content_hash:
            state = ArtifactIntegrityState.HASH_MISMATCH
            detail = "artifact bytes do not match the registered content hash"
        elif actual_size != ref.byte_size:
            state = ArtifactIntegrityState.SIZE_MISMATCH
            detail = "artifact bytes do not match the registered size"
        else:
            state = ArtifactIntegrityState.VERIFIED
            detail = "content hash and byte size verified"
        return ArtifactIntegrityResult(
            artifact_id=ref.artifact_id,
            state=state,
            expected_content_hash=ref.content_hash,
            actual_content_hash=actual_hash,
            expected_byte_size=ref.byte_size,
            actual_byte_size=actual_size,
            detail=detail,
        )

    def stat(self, artifact: ArtifactRef | str) -> ArtifactRef:
        artifact_id = artifact.artifact_id if isinstance(artifact, ArtifactRef) else artifact
        record = self._read_record(artifact_id)
        if record is None or not isinstance(record[1], ArtifactRef):
            raise KeyError(artifact_id)
        return record[1]

    def _all_refs(self) -> Iterator[ArtifactRef]:
        for path in self._refs.rglob("*.json"):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
                if body.get("record_type") == "ref":
                    yield ArtifactRef.model_validate(body["payload"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                raise ArtifactIntegrityError("artifact reference inventory contains unreadable metadata") from None

    def list_artifacts(self, *, run_id: str | None = None, stage_id: str | None = None, artifact_kind: str | None = None) -> tuple[ArtifactRef, ...]:
        values = [item for item in self._all_refs() if (run_id is None or item.run_id == run_id) and (stage_id is None or item.stage_id == stage_id) and (artifact_kind is None or item.artifact_kind == artifact_kind)]
        return tuple(sorted(values, key=lambda item: item.artifact_id))

    def delete(self, artifact: ArtifactRef | str, authorization: CleanupAuthorization) -> None:
        if not isinstance(authorization, CleanupAuthorization) or not authorization.authorized or not authorization.plan_id:
            raise CleanupAuthorizationError("artifact deletion requires an authorized cleanup plan")
        with self._lock:
            ref = self._published_ref(artifact)
            if ref.retention_class.value == "PINNED_GATE_EVIDENCE":
                raise CleanupAuthorizationError("pinned gate evidence cannot be deleted")
            self._write_record("tombstone", {"artifact": ref.model_dump(mode="json"), "plan_id": authorization.plan_id}, ref.artifact_id)
            shared = any(item.content_hash == ref.content_hash and item.artifact_id != ref.artifact_id for item in self._all_refs())
            if not shared and ref.storage_mode is ArtifactStorageMode.MANAGED:
                self._blob_path(ref.content_hash).unlink(missing_ok=True)


class _BytesReader:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        value = self._value[self._offset : self._offset + size]
        self._offset += len(value)
        return value
