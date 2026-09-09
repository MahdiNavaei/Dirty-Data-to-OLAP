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

## SourceAdapter

- Purpose: discover and read a bounded source snapshot using read-only access.
- Input: SourceSelection, connection-profile reference, sampling/chunk policy.
- Output: project-owned SourceDescriptor, catalog descriptors, declared constraints, SourceSnapshot and batch/source-record references.
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

- Purpose: produce key, functional-dependency and inclusion-dependency evidence.
- Input: project-owned catalog/profile/sample contracts.
- Output: KeyCandidate, FunctionalDependencyEvidence, InclusionDependencyEvidence, RelationshipCandidate.
- Side effects: no source writes.
- Idempotency: safe for pinned inputs/configuration.
- Optionality: required for plans that request structural discovery.
- Failure: stage FAILED or NEEDS_REVIEW; no confirmed FK or synthetic fallback.
- Forbidden: native Desbordante results or direct business-truth acceptance.

## SchemaMatchingAdapter

- Purpose: generate cross-source mapping candidates.
- Input: project-owned table/column/profile contracts.
- Output: SchemaMatchCandidate with raw score semantics and provenance.
- Side effects: artifact-plane writes only.
- Idempotency: pinned inputs and matcher version.
- Optionality: conditional by plan.
- Failure: stage failure or explicit optional skip; no automatic canonical mutation.
- Forbidden: native Valentine results or scores presented as probabilities.

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

## StageExecutorPort

- Purpose: run heavy stages behind a local or future worker boundary.
- Input: project-owned stage request, attempt identity, cancellation token and pinned configuration.
- Output: project-owned attempt result and artifact references.
- Side effects: controlled process/work execution only.
- Idempotency: executor never overwrites prior attempts.
- Optionality: required for heavy stages.
- Failure: preserves item/stage/run failure semantics and cancellation evidence.
- Forbidden: queue/distributed implementation is not required in V1.
