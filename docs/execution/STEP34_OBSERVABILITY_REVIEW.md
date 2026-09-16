# Step34 Observability Review

## Result and scope

Step34 covers project-owned runtime telemetry for the local/reference Dirty
Data to OLAP product path. It does not close G11 resilience and does not start
Step35 SRE. G6, G7, G8, G9 and G10 remain upstream accepted gates.

Starting HEAD: `a0c526652758b0b6401ff638e04519fa9885c50d`.

## Architecture and instrumentation

`TelemetryClient` is a typed, exporter-neutral boundary. The default local
implementation is a bounded in-memory sink. It is wired once through
`build_local_product` into `BackendService`, `LocalProductStageHandlers` and
the real `JobWorker`; the FastAPI middleware uses the same backend client.

Actual boundaries exercised are API requests, run creation, durable command
and stage job claim, stage attempt creation, handler-delivery marker, result
recording, finalization/retry, review pause and resume, run status changes,
product stage handler execution, adapter-family timing and diagnostic query.
The stage identity comes from the authoritative execution plan, not a second
telemetry DAG. Source snapshot observed rows and validation outcomes are
reported only when the owning runtime already knows those values.

## Dependencies and policy

No new runtime dependency was selected. The repository already has Pydantic,
FastAPI and the local platform stores needed by the boundary. Avoiding a
mandatory OTel/Prometheus exporter preserves the local-first/no-collector
runtime and keeps third-party objects out of internal contracts. A future
exporter can implement `TelemetrySink` without changing application services.

## Evidence

- Metric definitions include type, unit, aggregation, allowed labels, bounded
  cardinality rationale and instrumentation point.
- Structured events contain correlation and normalized error class while
  redaction excludes secrets, PII, paths and source values.
- Worker lifecycle evidence preserves at-least-once semantics and makes no
  exactly-once claim.
- Four review checkpoint names remain server-authoritative and are never
  metric labels containing subject payloads.
- Queue gauges are snapshots of the existing local SQLite job queue; no new
  queue or distributed-production claim was introduced.
- Diagnostics reuse authorized run access and ControlStore projections. They
  do not expose raw source artifacts or review rationale.
- Sink failure is isolated and explicitly represented as degradation.
- Trace sampling is deterministic and no remote collector is required.
- Dashboard panels reference only emitted metric names and define no alert or
  SLO thresholds.

## Scenario matrix

The machine-readable scenario matrix is in
`output/step34_observability_validation.json`. The executable suite is
`tests/observability/test_step34_observability.py`; its integration scenario
uses the actual FastAPI and local durable product runtime. If the optional
Desbordante provider is not installed in a local environment, the real
dependency boundary remains a correlated, classified failure; it is not
reported as a successful OLAP run. CI/provider availability is therefore kept
distinct from telemetry contract evidence.

## Limitations handed to Step35/SRE

This work does not establish SLOs, alert thresholds, error budgets,
production deployment telemetry, broker/HA behavior, capacity breakpoints,
chaos/recovery proof, backup/restore evidence, or physical multi-node
continuity. Durable IDs survive runtime reconstruction, but uninterrupted
distributed trace continuity across restart is not claimed. G11 remains
pending.

Content commit: `PENDING_STEP34_CONTENT_COMMIT`.
Content exact-head CI: `PENDING`.
Closure commit: `PENDING`.
