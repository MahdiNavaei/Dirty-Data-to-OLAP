# Step35 SRE reliability model

This document defines the local-first reliability vocabulary and candidate
objectives for the V1 runtime. These are reference objectives for a future
operational environment, not measurements or availability commitments for a
production deployment.

## Reliability model

SQLite is authoritative for run, job, attempt, lease, review, idempotency and
artifact-reference metadata. The local content-addressed artifact store owns
bytes by hash, and DuckDB materialization publishes only after validation to a
temporary target and an atomic replace. Worker delivery is at-least-once
compatible. A handler delivery whose outcome is unknown is never upgraded to
success: it enters `RECONCILIATION_REQUIRED`.

The recovery loop is:

1. reopen and integrity-check the control store;
2. verify the backup manifest and schema version;
3. reconstruct typed run, job, attempt, review and artifact projections;
4. verify referenced artifact hash and size;
5. resume only durable work known to be safe, retry only bounded transient
   failures, and route uncertain side effects or missing bytes to review or
   reconciliation.

## SLI inventory

The Step34 telemetry already emits the following bounded signals. They are
inputs to future measurement; the presence of a metric does not establish a
service-level result.

| SLI | Source signal | Candidate interpretation |
| --- | --- | --- |
| Run completion correctness | `ddo_run_lifecycle_total` | terminal `SUCCEEDED` runs divided by admitted runs, with cancelled and blocked runs reported separately |
| Runnable queue age | `ddo_queue_oldest_runnable_age_seconds` | oldest eligible `QUEUED` or `RETRY_WAIT` job age |
| Delivery retry pressure | `ddo_job_retries_total` | retries per run/stage, bounded by the configured retry policy |
| Stage latency | `ddo_stage_duration_seconds` | stage duration distribution by bounded stage kind and result class |
| Review waiting time | `ddo_review_lifecycle_total` | time from review-required transition to an explicit human decision, when timestamps are available |
| Telemetry health | `ddo_telemetry_exporter_failures_total` and diagnostic degradation state | whether observability is degraded; it is never used to declare business success |

## Reference objectives (candidate only)

These values are planning thresholds for a later controlled operating review.
They are not production SLOs and no production compliance claim is made here.

| Objective | Candidate reference | Measurement window / caveat |
| --- | --- | --- |
| Durable acknowledgement | 99% of accepted commands have a durable command record | controlled local test window; acceptance is not completion |
| Runnable queue age | 99% of sampled runnable jobs are below 60 seconds | excludes jobs explicitly waiting for review or reconciliation |
| Retry containment | 100% of transient retries remain within `RetryPolicy.max_retries` | checked per job; no exactly-once claim |
| Materialization safety | 100% of failed materializations leave no published temporary target | verified by targeted fault tests |
| Recovery integrity | 100% of restore attempts reject hash, size, schema or SQLite-integrity mismatch | applies to the tested backup bundle |

G11 resilience, capacity, adversarial-security, usability and release gates
remain outside this Step35 evidence. Fault injection and stress validation are
owned by Step36 and later specialists.
