# Dirty Data to OLAP — Artifact and Cache Lifecycle

## Artifact envelope

Every persisted artifact exposes, where applicable:

- artifact_id, artifact_type and schema_version;
- run_id, stage_id and attempt_id;
- lifecycle status;
- created_at and producer;
- content_hash and config_hash;
- upstream_artifact_refs and upstream_artifact_hashes;
- source_snapshot_refs and provenance;
- project-relative logical location and size.

The envelope is project-owned metadata. Large rows are referenced from the envelope rather than embedded in the Control Store.

## Lifecycle

The lifecycle is WRITING, COMPLETE, INVALIDATED and SUPERSEDED.

- WRITING includes attempt-local and incomplete artifacts. They are never consumable.
- COMPLETE means serialization, schema validation, metadata validation and content hashing passed and publication was atomic.
- INVALIDATED means a previously usable artifact can no longer support its dependents.
- SUPERSEDED means a newer compatible artifact replaced it while preserving the prior record.

Legal publication is WRITING -> COMPLETE. Failed or cancelled writes remain WRITING until safely invalidated; the failure evidence remains in the attempt. COMPLETE -> INVALIDATED occurs when an upstream semantic dependency changes or validation proves the artifact unusable. COMPLETE -> SUPERSEDED occurs only through an explicit replacement. INVALIDATED and SUPERSEDED are not revived; a new attempt creates a new artifact.

## Atomic publication

The Artifact Store writes to a project-local run/stage/attempt temporary location, completes serialization, validates schema and metadata, calculates the content hash, then atomically publishes and registers the artifact. A file existing on disk is not evidence of COMPLETE.

## Cache identity

A semantic cache key includes stage version, ordered upstream artifact IDs and hashes, source snapshot/schema fingerprints, sampling fingerprint where relevant, configuration hash, adapter/engine version, domain/model/policy version and random seed where relevant. Filename, mtime or run_id alone cannot identify a reusable result.

## Invalidation

- Schema/source fingerprint change invalidates dependent discovery and downstream evidence.
- Sampling policy change invalidates sample-dependent profiles and evidence.
- Adapter version change re-evaluates adapter-derived artifacts.
- Accepted semantic mapping change invalidates canonical, ER and analytical descendants as applicable.
- Entity-resolution policy/configuration change invalidates ER and dependent canonical/analytical artifacts.
- Fact-grain decision change invalidates analytical planning, compilation, materialization and validation.
- Documentation-only changes do not invalidate runtime artifacts.

## Decision replay

Human decisions are persisted with subject stable IDs, source/schema fingerprints, policy version, domain assertion scope and upstream semantic references. Replay is allowed only when compatibility checks pass. An incompatible override becomes review-required rather than silently surviving structural change.
