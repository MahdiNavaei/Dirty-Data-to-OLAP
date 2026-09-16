# V1 telemetry specification

## Boundary and lifecycle

The project-owned boundary is `dirty_data_to_olap.observability.TelemetryClient`.
Application code emits `StructuredEvent`, `MetricSample` and `TraceSpan`
records. Exporters implement `TelemetrySink`; no OpenTelemetry, Prometheus,
Grafana, collector or remote service is required. The default sink is a
bounded deterministic `InMemoryTelemetrySink`. `NoopTelemetrySink` and
`TelemetryClient(enabled=false)` are available for semantic-equivalence tests.

Telemetry is diagnostic only. It is not stored in the control database and
does not participate in product IDs, artifact content hashes, review
compatibility, execution-plan authority, or G6 validation.

## Metrics

All metric names are `ddo_`-prefixed and are defined in
`src/dirty_data_to_olap/observability/telemetry.py`. Types and units are
validated by `MetricDefinition`; every sample is rejected if it uses a label
outside its definition. The emitted inventory is:

| Metric | Type | Unit | Labels |
|---|---|---|---|
| `ddo_run_lifecycle_total` | counter | runs | `run_status` |
| `ddo_stage_duration_seconds` | histogram | seconds | `stage_kind`, `result_class` |
| `ddo_job_lifecycle_total` | counter | jobs | `job_kind`, `job_status` |
| `ddo_job_execution_duration_seconds` | histogram | seconds | `result_class` |
| `ddo_job_retries_total` | counter | retries | `retry_reason_class` |
| `ddo_review_lifecycle_total` | counter | reviews | `review_checkpoint`, `result_class` |
| `ddo_adapter_operation_duration_seconds` | histogram | seconds | `adapter_kind`, `result_class` |
| `ddo_source_records_observed_total` | counter | records | `observation` |
| `ddo_validation_outcomes_total` | counter | validations | `result_class` |
| `ddo_queue_jobs` | gauge | jobs | `queue_state` |
| `ddo_queue_oldest_runnable_age_seconds` | gauge | seconds | none |
| `ddo_worker_activity_total` | counter | events | `activity` |
| `ddo_telemetry_exporter_failures_total` | counter | failures | `signal`, `exporter` |

`run_id`, `project_id`, `source_id`, `job_id`, `attempt_id`, `artifact_id`,
table/column names, filenames, SQL and error messages are forbidden metric
labels. Stage IDs are mapped to the finite architecture stage taxonomy before
being used as `stage_kind`; unknown values become `OTHER`.

The source-record counter is emitted only when the authoritative snapshot
stage already reports its observed row count. Sampling is not converted to a
full-source count.

## Structured events

Events use the stable fields `timestamp`, `level`, `event_name`, `component`,
`operation`, `correlation`, `status`, `error_class`, `retry_count`,
`duration_ms`, and bounded `details`. `CorrelationContext` carries the
portable hierarchy `request_id`, `project_id`, `run_id`, `stage_id`, `job_id`,
`attempt_id`, `command_id`, `artifact_id`, `checkpoint`, `trace_id`, and span
parentage where known. IDs are diagnostic references, not semantic identity.

The real API emits `api.request` and the backend emits `run.created`, review
and execution-submission events. The worker emits claim, attempt, delivery,
result-recorded, finalization, retry, review-wait, review-resume and run-state
events. Stage handlers emit the stage result event. No request body,
authentication header, raw SQL, source row, uploaded file, credential-bearing
URL or arbitrary exception message is emitted.

## Error classification

`classify_error` maps the existing job/product vocabulary to the bounded
classes `AUTHORIZATION`, `SOURCE_CONNECTION`, `SOURCE_TIMEOUT`,
`SOURCE_PERMISSION`, `SOURCE_FORMAT`, `PROVIDER_UNAVAILABLE`,
`PROVIDER_TIMEOUT`, `VALIDATION`, `REVIEW_REQUIRED`, `RETRY_EXHAUSTED`,
`LEASE_LOST`, `STALE_WORKER`, `CANCELLED`, `MATERIALIZATION`, `INTERNAL`, or
`UNKNOWN`. The product exception and failure classification remain unchanged.

## Traces and sampling

Trace boundaries are API request, run creation, job claim, worker attempt,
stage handler, adapter operation, review pause/resume, and worker
finalization. Spans are portable `TraceSpan` records; live provider span
objects are not passed into the application. The default sample rate is
`1.0`; sampling uses a deterministic hash of `trace_id`. `0.0` and `1.0` are
tested explicitly. There is no claim of uninterrupted trace continuity after
restart; durable run/job/attempt IDs remain the restart correlation source.

## Diagnostics and privacy

`GET /api/v1/runs/{run_id}/diagnostics` returns the authorized
`DiagnosticBundle` projection built from the existing ControlStore and
in-memory telemetry. It contains run state, stage/job/attempt lifecycle,
review checkpoint decisions without rationale, artifact IDs/hashes/provenance,
safe event records, metric/trace names, and exporter degradation notes. It
contains no source rows, sampled values, PII, credentials, connection strings,
host paths, raw logs or SQL. The existing backend run authorization is applied
before construction, so a foreign project receives the same not-found policy
as other run resources.

`redact_value` is applied to event details and span attributes. It removes
secret-shaped values, credential URLs, email-like values, canaries and path
payloads. Error clients continue to receive the existing generic API
envelopes.

## Failure and ownership behavior

Every external sink call is guarded. A sink failure increments the bounded
exporter-failure metric in the local diagnostic sink and records a degradation
note; it is not raised into the worker, API, stage handler or product result.
There is no retry loop and no recursive emission to the failed sink. An
observability failure therefore cannot alter source writes, review decisions,
semantic IDs, artifact hashes, materialization, reconciliation or run truth.

This is V1 local/reference telemetry. It provides signals for the Step35 SRE
handoff; it does not define SLOs, alert thresholds, error budgets, capacity,
RTO/RPO, chaos recovery, or production deployment readiness.
