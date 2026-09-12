"""Versioned staged-dataset organization over the artifact store."""

from __future__ import annotations

from typing import BinaryIO

from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, StagingStorePort
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactManifest,
    ArtifactRef,
    ArtifactStorageMode,
    StagedDatasetManifest,
    StagedDatasetPart,
)
from dirty_data_to_olap.domain.contracts.source import stable_id


def staged_part_logical_key(manifest: StagedDatasetManifest, part_id: str) -> str:
    """Return an object-store-compatible logical key, independent of Windows."""

    safe_parts = (manifest.run_id, manifest.source_id, manifest.snapshot_id, manifest.table_id, manifest.dataset_id, manifest.dataset_version, part_id)
    if any(not value or "/" in value or "\\" in value or ".." in value for value in safe_parts):
        raise ValueError("staged dataset identity contains an unsafe path component")
    return f"runs/{manifest.run_id}/source/{manifest.source_id}/snapshot/{manifest.snapshot_id}/table/{manifest.table_id}/dataset/{manifest.dataset_id}/version/{manifest.dataset_version}/part/{part_id}.parquet"


class LocalStagingStore(StagingStorePort):
    """A thin staging policy adapter; serialization remains caller-owned."""

    def __init__(self, artifact_store: ArtifactStorePort, *, control_store: ControlStorePort | None = None) -> None:
        self.artifact_store = artifact_store
        self.control_store = control_store

    def publish_part(self, manifest: StagedDatasetManifest, part_id: str, payload: bytes | BinaryIO, *, attempt_id: str, producer: str) -> StagedDatasetPart:
        logical_key = staged_part_logical_key(manifest, part_id)
        artifact_manifest = ArtifactManifest(
            artifact_id=stable_id("staged", {"dataset_id": manifest.dataset_id, "dataset_version": manifest.dataset_version, "part_id": part_id}),
            run_id=manifest.run_id,
            stage_id="SOURCE_SNAPSHOT_STAGE",
            attempt_id=attempt_id,
            artifact_kind="STAGED_DATASET_PART",
            media_type="application/vnd.apache.parquet",
            producer=producer,
            storage_mode=ArtifactStorageMode.MANAGED,
            logical_key=logical_key,
            retention_class=manifest.retention_class,
            provenance_refs=manifest.provenance_refs,
        )
        artifact_ref = self.artifact_store.publish(artifact_manifest, payload)
        return StagedDatasetPart(
            part_id=part_id,
            logical_key=logical_key,
            artifact_ref=artifact_ref,
            schema_fingerprint=manifest.schema_fingerprint,
            sampling_scope=manifest.sampling_scope,
        )

    def register_manifest(self, manifest: StagedDatasetManifest) -> StagedDatasetManifest:
        if self.control_store is None:
            return manifest
        for part in manifest.parts:
            if self.control_store.get_artifact(part.artifact_ref.artifact_id) is None:
                self.control_store.register_artifact(part.artifact_ref)
        return self.control_store.record_staged_dataset(manifest)
