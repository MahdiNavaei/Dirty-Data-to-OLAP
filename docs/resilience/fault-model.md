# Step36 Fault Model

Step36 exercises the existing durable product boundaries with deterministic,
bounded faults. The suite is an integrity review, not a claim of production
high availability or a randomized chaos platform.

## Failure taxonomy

- Worker lifecycle: before claim, after attempt creation, after delivery, after
  result recording, and stale-worker fencing.
- Source boundaries: disappearance after discovery, bounded extraction timeout,
  and access failure. Source adapters remain read-only.
- Engine and control plane: provider unavailable/timeout, retry exhaustion,
  claim and transition write failures, reopen/recovery, and cancellation.
- Queue and storage: lease expiry, stale lease, bounded backlog, atomic artifact
  publication, controlled DuckDB materialization, missing/mutated/truncated
  artifacts, and review subject mutation.
- Observability and recovery: exporter failure isolation and semantic equality
  between clean and recovered executions.

## Recovery oracle

Every scenario has an explicit durable transition and a recovery oracle in
`docs/resilience/step36-fault-matrix.json`. A worker may resume only when the
durable phase and artifact integrity make replay safe. Handler delivery with an
unknown outcome is reconciliation-required; result-recorded work is finalized
from the durable result without re-execution; stale lease generations are
rejected.

## G11 oracle

`CHAOS-G11-001` is the no-silent-validated-corruption oracle. A registered
artifact is mutated after publication, the real artifact verifier reports the
integrity failure, and the recovery projection requires reconciliation. The
suite never converts missing, mismatched, truncated, or unreadable bytes into a
successful analytical result.

## Evidence boundary

The test suite uses the production SQLite control store, local content-addressed
artifact store, DuckDB materializer, source adapter boundary, backend review
service, worker, retry policy, recovery projection, and telemetry client. It
uses disposable fixtures and deterministic fault injection at those boundaries;
it does not claim external provider availability, production deployment,
multi-node fencing, capacity, or exactly-once processing.
