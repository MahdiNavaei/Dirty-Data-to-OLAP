"""Versioned staged-dataset organization over the artifact store."""

from __future__ import annotations

from typing import BinaryIO

from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, StagingStorePort
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactManifest,
    ArtifactRef,
    ArtifactIntegrityState,
    ArtifactStorageMode,
    StagedDatasetManifest,
    StagedDatasetPart,
    staged_part_artifact_id,
    staged_part_logical_key as scoped_staged_part_logical_key,
)


def staged_part_logical_key(manifest: StagedDatasetManifest, part_id: str) -> str:
    """Return an object-store-compatible logical key, independent of Windows."""

    return scoped_staged_part_logical_key(
        run_id=manifest.run_id,
        source_id=manifest.source_id,
        snapshot_id=manifest.snapshot_id,
        table_id=manifest.table_id,
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        part_id=part_id,
    )


class LocalStagingStore(StagingStorePort):
    """A thin staging policy adapter; serialization remains caller-owned."""

    def __init__(self, artifact_store: ArtifactStorePort, *, control_store: ControlStorePort | None = None) -> None:
        self.artifact_store = artifact_store
        self.control_store = control_store

    def publish_part(self, manifest: StagedDatasetManifest, part_id: str, payload: bytes | BinaryIO, *, attempt_id: str, producer: str) -> StagedDatasetPart:
        logical_key = staged_part_logical_key(manifest, part_id)
        artifact_manifest = ArtifactManifest(
            artifact_id=staged_part_artifact_id(
                run_id=manifest.run_id,
                source_id=manifest.source_id,
                snapshot_id=manifest.snapshot_id,
                table_id=manifest.table_id,
                dataset_id=manifest.dataset_id,
                dataset_version=manifest.dataset_version,
                part_id=part_id,
                schema_fingerprint=manifest.schema_fingerprint,
            ),
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
        validated = StagedDatasetManifest.model_validate(manifest.model_dump(mode="json"))
        for part in validated.parts:
            try:
                stored = self.artifact_store.stat(part.artifact_ref)
            except (KeyError, PlatformError) as exc:
                raise ValueError("staged manifest part is not present in the configured artifact store") from exc
            if stored != part.artifact_ref:
                raise ValueError("staged manifest part differs between control and artifact stores")
            registered = self.control_store.get_artifact(part.artifact_ref.artifact_id)
            if registered is None:
                integrity = self.artifact_store.verify(part.artifact_ref)
                if integrity.state is not ArtifactIntegrityState.VERIFIED:
                    raise ValueError("staged part bytes are not verified before manifest registration")
                self.control_store.register_artifact(part.artifact_ref)
            elif registered != part.artifact_ref:
                raise ValueError("staged manifest part does not match the registered artifact")
            elif registered.publication_state.value != "PUBLISHED":
                raise ValueError("staged manifest part artifact is not published")
            integrity = self.artifact_store.verify(part.artifact_ref)
            if integrity.state is not ArtifactIntegrityState.VERIFIED:
                raise ValueError("staged manifest part bytes are not verified")
        return self.control_store.record_staged_dataset(validated)
