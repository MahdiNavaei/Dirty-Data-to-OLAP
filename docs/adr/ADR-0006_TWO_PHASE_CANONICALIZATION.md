# ADR-0006: Two-Phase Canonicalization

Status: Accepted for v1 architecture

## Context

Canonical hypotheses can be produced from evidence before all identity
resolution is available. Treating a hypothesis as a final canonical record
would hide uncertainty and make later entity-resolution changes unsafe.

## Decision

The canonical lifecycle is explicit: evidence fusion produces canonical
hypotheses; optional entity resolution produces only `EntityMatchEdge` and
`EntityCluster` linkage evidence; a separate finalization stage publishes
canonical records and `SourceRecordCanonicalMap` only after review and policy
validation. Hypothesis, entity-resolution, decision, and finalization artifacts
remain distinct and linked by provenance.

## Consequences

- Identity uncertainty remains visible.
- Entity-resolution configuration invalidates downstream canonical and analytical
  artifacts without invalidating unrelated source evidence.
- A run can be review-required or blocked without pretending finalization passed.
- For an ER-required family, finalization cannot proceed after absent, failed or
  unacceptable linkage evidence. For an ER-not-required family, absent or
  policy-recorded skipped ER does not block finalization.

## Rejected alternative

- A single monolithic canonicalization stage that overwrites evidence with
  provisional identity decisions.
