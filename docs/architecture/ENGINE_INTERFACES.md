# Dirty Data to OLAP — Engine and Repository Interfaces

These are logical ports for Step 05. They are not Python signatures and do not define implementation modules.

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
- Input: project-owned EntityResolutionSpec and SourceRecordReferences.
- Output: EntityMatchEdge, EntityCluster, SourceRecordCanonicalMap as project-owned artifacts.
- Side effects: no source writes; artifact-plane writes only.
- Idempotency: same inputs/configuration/version produce a repeatable attempt identity.
- Optionality: conditional per entity family.
- Failure: affected family is failed/review-required; unrelated stages may continue only when their dependencies remain valid.
- Forbidden: assigning canonical IDs alone, deleting source records or exposing native Splink objects.

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
