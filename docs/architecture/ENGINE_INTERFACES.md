# Dirty Data to OLAP — Engine and Repository Interfaces

These are logical ports for Step 05. They are not Python signatures and do not define implementation modules.

## Review / Policy Service boundary

Review interaction is one reusable project-owned application service, not an
engine adapter. The runtime graph invokes it through four stage-scoped
checkpoints: evidence/mapping, canonical identity/linkage, analytical plan and
materialization approval. Each checkpoint is entered only after its subject
artifact is complete and submits a `ReviewCheckpointRequest` containing the
exact artifact ID, content hash, schema/model version, source/schema
fingerprints, policy/domain scope and semantic subject ID. Entry points and
future UI/API transports call this service; they do not mutate decision records
directly.

## QualityStagedReader

- Purpose: read a requested projection from COMPLETE staged Parquet evidence for
  deterministic quality measurement.
- Input: SourceSnapshotResult, SourceCatalog, TableDescriptor, BatchReference,
  SourceRecordReference and explicit physical-column projection.
- Output: project-owned QualityStagedRow values with snapshot-bound record refs.
- Side effects: staged artifact reads only; quality artifact publication is
  handled separately through ArtifactStorePort.
- Idempotency: source/snapshot/schema/content hashes and projection are pinned.
- Optionality: required for QUALITY_ANALYSIS.
- Failure: path, hash, row-count, schema or reference mismatch is an explicit
  input-integrity failure; it cannot produce a clean result.
- Forbidden: source reconnects, source writes, raw-value persistence, hidden
  foreign-key inference, entity resolution or canonicalization.

## SourceAdapter

- Purpose: expose two explicit read-only operations: `discover_source` and `create_bounded_snapshot`.
- `discover_source(SourceSelection, SourceRegistryRecord, ConnectionProfileReference)` returns project-owned SourceDescriptor, TableDescriptor, ColumnDescriptor and DeclaredConstraint values for the Discovery service to assemble into SourceCatalog.
- `create_bounded_snapshot(SourceCatalog, SamplingPolicy, ConnectionProfileReference)` returns SourceSnapshot, BatchReference and SourceRecordReference values for the Source Snapshot service.
- Discovery does not require SourceSnapshot; snapshot creation does not re-run catalog ownership.
- Side effects: source reads and project-local attempt artifacts only.
- Idempotency: same pinned source/configuration produces the same snapshot identity or an explicit changed-snapshot result.
- Optionality: required for a selected source.
- Failure: access failure blocks the affected run scope; extraction failure fails the stage; no fallback truth is guessed.
- Forbidden: dlt/database/file-native objects in outputs; source writes.

## ProfilingAdapter

- Purpose: produce scoped column/table observations.
- Input: project-owned source snapshot/batch references and observation policy.
- Output: ColumnProfile, TableProfile, ValuePatternSummary.
- Side effects: artifact-plane writes only.
- Idempotency: pinned inputs, configuration and adapter version define the result.
- Optionality: required when profiling is selected by the plan.
- Failure: item failure is recorded; completeness determines stage NEEDS_REVIEW or FAILED.
- Forbidden: native DataProfiler objects or business truth labels.

## DependencyDiscoveryAdapter

- Purpose: produce bounded UCC, FD, AFD, IND and approximate-IND evidence over immutable staged rows.
- Input: project-owned catalog, complete SourceSnapshotResult/batch references, optional profiles, DependencyRequest, local-only privacy context and a policy-issued DependencyAuthorization bound to the exact snapshot/table scope.
- Output: UniqueColumnCombinationEvidence, derived KeyCandidate, FunctionalDependencyEvidence, InclusionDependencyEvidence, provider metric observations, candidate-only RelationshipCandidate, capability/failure/search statistics and artifact references.
- Side effects: read staged artifacts and create short-lived project-local engine input; a native binding or controlled network-disabled local Docker process may execute the provider; no source writes or reconnect.
- Idempotency: pinned snapshot hashes, request/configuration and adapter version define the attempt.
- Optionality: required for plans that request structural discovery; unavailable capability is an explicit failure.
- Failure: FAILED or INCOMPLETE with retained failure/capability evidence; downstream must not consume incomplete output.
- Forbidden: context self-authorization, native Desbordante objects, raw values in results/logs/artifacts, unbounded combinations, silent null coercion, fabricated provider metrics, confirmed FK or business-truth acceptance.

## SchemaMatchingAdapter

- Purpose: generate bounded, explainable cross-source mapping candidates over pinned snapshots.
- Input: multiple SourceCatalog/SourceSnapshotResult pairs, optional profiles and dependency evidence, SchemaMatchRequest and instance-mode policy authorization.
- Output: SchemaMatchCandidate plus separate native SchemaMatchScore, signal-family, capability, failure, pruning and artifact contracts.
- Side effects: complete hash-bound staged reads, deterministic local sampling and atomic aggregate-only artifact writes.
- Idempotency: pinned source/snapshot hashes, request configuration and matcher version.
- Optionality: conditional by plan.
- Failure: explicit failure or conditional skip; a selected multi-source request with no successful matcher is not complete.
- Forbidden: native Valentine results in project contracts, raw values in artifacts/logs, source reconnect, unbounded pair search, canonical mutation or scores presented as probabilities.

## EntityResolutionAdapter

- Purpose: link selected source-record families after accepted identity specifications exist.
- Input: project-owned EntityResolutionSpec, SourceRecordReferences, accepted identity-field specification and relevant evidence references.
- Output: EntityMatchEdge and EntityCluster as project-owned linkage-evidence artifacts.
- Side effects: no source writes; artifact-plane writes only.
- Idempotency: same inputs/configuration/version produce a repeatable attempt identity.
- Optionality: conditional per entity family.
- Failure: affected family is failed/review-required; unrelated stages may continue only when their dependencies remain valid.
- Forbidden: assigning `canonical_entity_id`, producing `SourceRecordCanonicalMap`, deleting source records, or exposing native Splink objects.

## Canonical Finalization Service

- Purpose: turn a reviewed canonical hypothesis and acceptable linkage evidence into accepted canonical identity and source mappings.
- Input: CanonicalModelHypothesis, EntityMatchEdge/EntityCluster where the family requires ER, the post-ER `REVIEW_CANONICAL_IDENTITY` ReviewDecision, domain assertion, identity policy and conflict/provenance references.
- Output: CanonicalModel, CanonicalAttribute and `SourceRecordCanonicalMap`.
- Ownership: this is the sole producer of accepted source-record-to-canonical mappings. An EntityCluster is evidence and is never reused as a canonical ID.
- Conditional dependency: when `entity_resolution_required(entity_family) == true`, an acceptable complete ER result and a compatible post-ER linkage decision are required; when false, absent or policy-recorded SKIPPED ER and identity review are legal according to policy.

## ReviewCheckpoint

- Purpose: pause a dependent stage until a policy-required review decision is
  accepted for the artifact that already exists.
- Stages: `REVIEW_EVIDENCE_DECISIONS`, `REVIEW_CANONICAL_IDENTITY`,
  `REVIEW_ANALYTICAL_PLAN` and `REVIEW_MATERIALIZATION_PLAN`.
- Semantics: unresolved or deferred required review drives `NEEDS_REVIEW`;
  rejected review cannot satisfy a downstream guard; policy-recorded `SKIPPED`
  is allowed only where the checkpoint specification permits it.
- Replay: a prior decision is reusable only when its applicability fingerprint
  remains compatible. Subject hash/schema/model/policy/domain/source changes
  invalidate the old decision while retaining its history.

## OptionalSemanticEvidenceAdapter

- Purpose: provide bounded semantic hypotheses as one evidence family.
- Input: project-owned evidence references and versioned prompt/configuration.
- Output: project-owned LLMEvidence.
- Side effects: provider call may occur; no source writes.
- Idempotency: provider repeatability is recorded rather than assumed.
- Optionality: optional.
- Failure: SKIPPED when disabled/unavailable and not required; BLOCKED when selected as required.
- Forbidden: sole authority for keys, relationships, entity merges, repairs, reconciliation or final validation.

## MaterializerPort

- Purpose: execute an approved compiled analytical plan into a controlled target.
- Input: project-owned compiled plan and approved artifact references.
- Output: materialization artifact reference and execution evidence.
- Side effects: controlled target writes only.
- Idempotency: target path is attempt-scoped; retries never treat an unknown partial target as published.
- Optionality: required for a final validated run.
- Failure: incomplete target is non-consumable; stage FAILED.
- Forbidden: redefining grain/measures or writing operational sources.

## ControlStorePort

- Purpose: persist run, attempt, decision, artifact-index, validation and cache metadata.
- Input/output: project-owned metadata contracts and references.
- Side effects: transactional metadata writes.
- Idempotency: state transitions use compare-and-record semantics.
- Optionality: required.
- Failure: lifecycle transition fails explicitly; no in-memory-only success.
- Forbidden: raw large source/intermediate row storage.

## ArtifactStorePort

- Purpose: allocate attempt-local locations, publish and read immutable artifacts, verify hashes and invalidate/supersede references.
- Input/output: project-owned artifact envelopes and references.
- Side effects: filesystem/object storage writes behind the port.
- Idempotency: publication is keyed by artifact identity/content hash and is atomic.
- Optionality: required.
- Failure: artifact remains non-consumable and evidence is retained.
- Forbidden: exposing path manipulation throughout core services.

## Privacy policy service boundary

Privacy is deliberately a project-owned application service and does not add a
provider-facing engine interface at Step10. Callers submit an
`ExposureRequest` and receive a `PrivacyDecision`; artifact publication carries
`ArtifactSensitivity`. External processing has a separate aggregate-only
decision path. A future semantic/LLM adapter must call this service before any
payload is assembled. No raw value, secret key or provider-native privacy type
is a transport contract.

## StageExecutorPort

- Purpose: run heavy stages behind a local or future worker boundary.
- Input: project-owned stage request, attempt identity, cancellation token and pinned configuration.
- Output: project-owned attempt result and artifact references.
- Side effects: controlled process/work execution only.
- Idempotency: executor never overwrites prior attempts.
- Optionality: required for heavy stages.
- Failure: preserves item/stage/run failure semantics and cancellation evidence.
- Forbidden: queue/distributed implementation is not required in V1.
# Step11 source security boundary

Database security is a cross-cutting enforcement boundary around `SourceAdapter`.
It emits project-owned assurance, guard decision and safe audit contracts.
No runtime credential, SQLAlchemy object, dlt object or caller SQL crosses the
contract boundary.
