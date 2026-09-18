# Step38 Load / Stress Test Report

- Result: `PASS`
- Suite: `step38-load-stress-v1`
- Assessed commit: `0bf205bb215bd3a63f9fade85c0c1d8667165d51`
- Evidence class: bounded local-reference only; not production capacity.

## Environment

```json
{
  "api_process": {
    "host": "127.0.0.1",
    "pid": 5560,
    "port": 19245,
    "workers": 1
  },
  "architecture": "AMD64",
  "control_store": "project SQLite control store; WAL; synchronous=FULL; busy_timeout=30000ms",
  "cpu_logical_count": 6,
  "git_head": "0bf205bb215bd3a63f9fade85c0c1d8667165d51",
  "os": "Windows-10-10.0.26200-SP0",
  "provider_image": "dirty-data-to-olap-desbordante-step37:local",
  "provider_image_digest": "sha256:2cc4b944805dd55b6ee23de3ac4a9a6fb4cdc9daede9d86ea4b71c97937f3638",
  "python": "3.10.11",
  "queue_limits": {
    "api_payload_bytes": 1000000,
    "max_active_per_run": 1,
    "max_active_per_source": 1
  },
  "ram_total_bytes": 34281078784,
  "source_caps": {
    "api_upload_bytes": 5242880,
    "extraction_max_rows": 10000,
    "source_write_policy": "read-only"
  },
  "started_at_utc": "2026-09-18T14:51:35.467719+00:00",
  "worker_ramp": [
    1,
    2,
    4,
    8
  ]
}
```

## Arrival patterns

The suite exercised STEADY, RAMP, BURST and OVERLOAD arrivals. OVERLOAD used twenty concurrent run submissions through the real localhost HTTP listener.

```json
{
  "BURST": {
    "concurrency": 32,
    "errors": 0,
    "label": "BURST",
    "latency": {
      "max_ms": 60.048,
      "mean_ms": 36.981,
      "p50_ms": 35.01,
      "p90_ms": 52.694,
      "p95_ms": 54.322,
      "p99_ms": 58.457,
      "sample_count": 32
    },
    "replayed_responses": 0,
    "requests": 32,
    "statuses": {
      "200": 32
    },
    "successful_requests": 32,
    "wall_time_ms": 67.85
  },
  "OVERLOAD": {
    "concurrency": 20,
    "errors": 0,
    "label": "OVERLOAD_RUN_SUBMISSIONS",
    "latency": {
      "max_ms": 23733.795,
      "mean_ms": 17309.015,
      "p50_ms": 18490.091,
      "p90_ms": 23730.536,
      "p95_ms": 23730.86,
      "p99_ms": 23733.208,
      "sample_count": 20
    },
    "replayed_responses": 0,
    "requests": 20,
    "statuses": {
      "202": 20
    },
    "successful_requests": 20,
    "wall_time_ms": 23744.599
  },
  "RAMP": {
    "concurrency": 8,
    "concurrency_schedule": [
      1,
      2,
      4,
      8
    ],
    "errors": 0,
    "label": "RAMP",
    "latency": {
      "max_ms": 29.421,
      "mean_ms": 12.156,
      "p50_ms": 9.942,
      "p90_ms": 25.015,
      "p95_ms": 27.087,
      "p99_ms": 28.943,
      "sample_count": 24
    },
    "replayed_responses": 0,
    "requests": 24,
    "statuses": {
      "200": 24
    },
    "successful_requests": 24,
    "wall_time_ms": 48.803
  },
  "STEADY": {
    "concurrency": 1,
    "latency": {
      "max_ms": 21.557,
      "mean_ms": 7.927,
      "p50_ms": 3.462,
      "p90_ms": 17.511,
      "p95_ms": 17.593,
      "p99_ms": 20.648,
      "sample_count": 24
    },
    "requests": 24,
    "statuses": {
      "200": 24
    },
    "wall_time_ms": 1244.79
  }
}
```

## Safe operating point

- Worker count: `1`
- Completed jobs: `2`
- Measured throughput: `0.674` jobs/s
- Queue-wait p95: `1615.095` ms
- This is a local reference envelope, not a production or public-SLA claim.

## First saturation region and recovery

- Region: `WORKER_RAMP`
- Breakpoint: `MEASURED_BOUNDARY`
- Observation: Measured local worker ramp peaked at worker_count=1 and declined at worker_count=2 while queue-wait p95 increased; this is a local SQLite/filesystem boundary, not a universal product limit
- Controlled cancellation and recovery were exercised; no source database was stressed.

## Required controls

| Control | Result |
|---|---|
| `queue_depth` | `PASS` |
| `queue_wait_latency` | `PASS` |
| `control_store_contention` | `PASS` |
| `source_read_only_protection` | `PASS` |
| `provider_concurrency` | `PASS` |
| `backpressure` | `PASS` |
| `artifact_staging_concurrency` | `PASS` |
| `multi_run_isolation` | `PASS` |
| `review_pause_resume` | `PASS` |
| `cancellation` | `PASS` |
| `controlled_overload_recovery` | `PASS` |
| `bounded_soak_memory` | `PASS` |
| `state_machine_invariants` | `PASS` |
| `at_least_once_replay_safety` | `PASS` |
| `g6_concurrent_correctness` | `PASS` |
| `g11_regression` | `PASS` |

## Explicit limitations

- No production capacity, deployment, HA, multi-node, 1M, 10M or 100M measured claim.
- No exactly-once claim; the worker remains at-least-once with durable replay fences.
- Optional provider availability is reported as observed and fail-closed; it is not silently upgraded.
- Step39 was not started.
- Protected quality artifacts were not read, modified, staged or committed.
