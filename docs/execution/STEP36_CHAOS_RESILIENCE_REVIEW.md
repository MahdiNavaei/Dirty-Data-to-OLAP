# Step36 Chaos / Resilience Review

## Scope

This review attacks the accepted Step35 durable-runtime claims at the real
worker, control-store, source, provider, queue, artifact, materialization,
review, cancellation, telemetry, and recovery boundaries. It does not begin
Step37 and does not claim production HA, randomized chaos, capacity, or
exactly-once execution.

- Starting head: `3b1da0a910829f75b405721a3562a73cd7a9cc5a`
- Required scenarios: `29`
- Matrix: `docs/resilience/step36-fault-matrix.json`
- Fault model: `docs/resilience/fault-model.md`
- Executable evidence: `tests/chaos/test_step36_chaos.py`
- Validator: `tools/validate_step36_resilience.py`
- Protected path: `tests/quality_unit_artifacts/` remained unread and untouched

## Executed result

The 29 required scenario IDs are represented by deterministic parameterized or
focused tests. The suite exercises production implementations rather than
mock-only stand-ins and asserts explicit durable transitions, recovery policy,
telemetry behavior, and artifact integrity. The checked-in validation report
records the content-phase result while G11 is still pending exact CI closure.

## State boundary

Before the exact content-head CI run, the authoritative state remains
`current_step=36`, `current_role=chaos_resilience`,
`step36_started=false`, `step36_status=NOT_STARTED`, `G11=PENDING`, and
`blocked=false`. Only after the content CI receipt is verified may closure
metadata advance the pointer to Step37 and set the resilience gate to `PASS`.

## Limitations

Evidence is bounded to local disposable SQLite/filesystem/DuckDB and in-process
provider boundaries. No production multi-node, external-provider, network
partition, randomized, capacity, or public-release claim is made.
