# ADR-0007 — Stage-Scoped Review Checkpoints

- Status: Accepted for the v1 software architecture
- Date: 2026-09-09
- Scope: Step 04 post-review architecture correction; implementation deferred

## Context

The original runtime DAG placed one generic `REVIEW_DECISIONS` stage directly
after `EVIDENCE_FUSION`. Entity-linkage artifacts were produced later by
`ENTITY_RESOLUTION`, and `AnalyticalPlan` was produced later by
`ANALYTICAL_PLANNING`. The early node therefore could not validly approve
linkage, canonical identity, analytical grain or measure semantics. A generic
approval could also be incorrectly reused for materialization.

## Decision

Keep one reusable application-level Review / Policy Service. Represent runtime
review as four explicit, stage-scoped checkpoints in the DAG:

1. `REVIEW_EVIDENCE_DECISIONS` after `EVIDENCE_FUSION` and before
   `CANONICAL_HYPOTHESES`;
2. `REVIEW_CANONICAL_IDENTITY` after canonical hypotheses and, when required,
   complete `ENTITY_RESOLUTION` output, before `CANONICAL_FINALIZATION`;
3. `REVIEW_ANALYTICAL_PLAN` after `ANALYTICAL_PLANNING` and before
   `COMPILATION`;
4. `REVIEW_MATERIALIZATION_PLAN` after `COMPILATION` and before
   `MATERIALIZATION` when policy requires approval.

Each decision is bound to the exact subject artifact ID, content hash,
schema/model version, source/schema fingerprints, policy/domain scope, semantic
subject ID and applicability fingerprint. Required unresolved review pauses the
guarded stage and run in `NEEDS_REVIEW`. Rejected, deferred and invalidated
decisions cannot satisfy an acceptance guard. Policy-recorded skips are explicit
and versioned; no arbitrary confidence threshold is invented. Incompatible
replay preserves the old decision and invalidates it for the new subject.

Validation failure remains a failure: review may inspect the failed report but
cannot promote it to PASS or `SUCCEEDED`.

## Consequences

- The runtime DAG exposes temporal review ordering and remains acyclic.
- No review decision can approve an artifact that did not exist when it was
  created.
- ER-required finalization has a valid post-ER linkage guard, while ER-not-
  required families retain policy-driven optionality.
- Analytical compilation and materialization consume decisions tied to their
  own plan/artifact hashes.
- `NEEDS_REVIEW` pause/resume and compatibility-checked replay are mechanically
  testable.
- The common Review / Policy Service remains the sole semantic authority;
  checkpoint stages do not duplicate policy engines.

## Rejected alternative

One global pre-canonical `REVIEW_DECISIONS` node was rejected because it cannot
review future artifacts and permits temporally impossible approval semantics.
