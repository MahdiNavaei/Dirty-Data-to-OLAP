"""Narrow, source-owned Parquet publication helper for Step07.

This is not the general ArtifactStore.  It only writes source ingestion
attempt artifacts, validates them, hashes them and publishes them atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    BatchReference,
    PublicationState,
    SourceFailure,
    SourceFailureKind,
    SourceIngestionError,
    stable_digest,
    batch_id_for,
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_json_write(path: Path, payload: Any) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    try:
        partial.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(partial, path)
    except Exception as exception:
        partial.unlink(missing_ok=True)
        raise SourceIngestionError(SourceFailure(
            kind=SourceFailureKind.STAGING_FAILED,
            operation="write_json_artifact",
            detail="project-owned JSON artifact publication failed",
            retryable=True,
        )) from None


class SourceFaithfulParquetStager:
    def __init__(self, project_root: Path, *, adapter_reference: AdapterReference) -> None:
        self.project_root = project_root.resolve()
        self.adapter_reference = adapter_reference

    def _artifact_path(self, staging_root: Path, table_id: str, batch_id: str) -> Path:
        root = staging_root.resolve()
        try:
            root.relative_to(self.project_root)
        except ValueError:
            raise ValueError("Step07 staging artifacts must remain under the project root") from None
        path = root / table_id / f"{batch_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def stage_rows(
        self,
        *,
        source_id: str,
        snapshot_id: str,
        table_id: str,
        batch_index: int,
        first_ordinal: int,
        rows: Sequence[Mapping[str, Any]],
        schema_fingerprint: str,
        schema: pa.Schema | None = None,
        staging_root: Path,
    ) -> BatchReference:
        try:
            table = pa.Table.from_pylist(list(rows), schema=schema)
            partial = staging_root.resolve() / table_id / f"batch-{batch_index:06d}.parquet.partial"
            partial.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, partial, compression="zstd")
            metadata = pq.read_metadata(partial)
            if metadata.num_rows != len(rows):
                raise ValueError("staged Parquet row count did not validate")
            content_hash = _file_sha256(partial)
            batch_id = batch_id_for(snapshot_id, table_id, batch_index, content_hash)
            final_path = self._artifact_path(staging_root, table_id, batch_id)
            if final_path.exists():
                existing_hash = _file_sha256(final_path)
                existing_meta = pq.read_metadata(final_path)
                if existing_hash == content_hash and existing_meta.num_rows == len(rows):
                    partial.unlink(missing_ok=True)
                else:
                    raise ValueError("immutable batch identity conflicts with an existing artifact")
            else:
                os.replace(partial, final_path)
            relative_location = final_path.relative_to(self.project_root).as_posix()
            return BatchReference(
                batch_id=batch_id,
                source_id=source_id,
                snapshot_id=snapshot_id,
                table_id=table_id,
                batch_index=batch_index,
                row_count=len(rows),
                artifact_location=relative_location,
                content_hash=content_hash,
                schema_fingerprint=schema_fingerprint,
                first_extraction_ordinal=first_ordinal if rows else None,
                last_extraction_ordinal=(first_ordinal + len(rows) - 1) if rows else None,
                adapter_reference=self.adapter_reference,
                publication_state=PublicationState.COMPLETE,
            )
        except SourceIngestionError:
            raise
        except Exception:
            partial = staging_root.resolve() / table_id / f"batch-{batch_index:06d}.parquet.partial"
            partial.unlink(missing_ok=True)
            raise SourceIngestionError(SourceFailure(
                kind=SourceFailureKind.STAGING_FAILED,
                operation="publish_parquet_batch",
                detail="source-faithful Parquet batch publication failed",
                retryable=True,
            )) from None

    def write_catalog(self, catalog: Any, *, run_root: Path) -> None:
        catalog_root = run_root / "catalog"
        payload = catalog.model_dump(mode="json")
        _safe_json_write(catalog_root / "sources.json", [payload["source"]])
        _safe_json_write(catalog_root / "tables.json", payload["tables"])
        _safe_json_write(catalog_root / "columns.json", payload["columns"])
        _safe_json_write(catalog_root / "declared_constraints.json", payload["declared_constraints"])

    def write_manifest(self, result: Any, *, run_root: Path) -> None:
        _safe_json_write(run_root / "source_manifests" / "extraction.json", result.model_dump(mode="json"))

    def discard_batches(self, batches: Sequence[BatchReference]) -> None:
        """Remove only complete artifacts from a failed snapshot attempt."""
        for batch in batches:
            path = (self.project_root / batch.artifact_location).resolve()
            try:
                path.relative_to(self.project_root)
            except ValueError:
                continue
            path.unlink(missing_ok=True)
