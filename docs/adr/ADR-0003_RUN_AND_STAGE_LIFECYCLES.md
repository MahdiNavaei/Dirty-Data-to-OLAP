# ADR-0003: Separate Run and Stage Lifecycles

Status: Accepted for v1 architecture

## Context

The product is a staged DAG with retries, optional work, review decisions, and
cooperative cancellation. A run outcome is not the same thing as an individual
stage execution state, and the word `PARTIAL` is too ambiguous for a terminal
run contract.

## Decision

`RunStatus` and `StageStatus` are separate enumerations. Runs use
`CREATED`, `RUNNING`, `NEEDS_REVIEW`, `BLOCKED`, `FAILED`, `CANCELLED`, and
`SUCCEEDED`; `PARTIAL` is not a run state. Stages additionally support
`PENDING`, `INVALIDATED`, and `SKIPPED`. Every execution has a new attempt ID,
pinned inputs, configuration, adapter version, and output references.

## Consequences

- A review-required stage cannot be mistaken for a crashed run.
- Required and optional skip behavior is explicit.
- Resume and retry can be idempotent and auditable.
- Success is guarded by final validation, materialization completeness, and the
  absence of required unresolved conditions.
