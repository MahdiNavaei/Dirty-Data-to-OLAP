# Step38 Load / Stress Test Report

- Result: load profile `PASS`; formal G12 `PASS`
- Suite: `step38-load-stress-v1`
- Assessed commit: `69b28bb5521c8d63275be232f783fc8a6d0c8cd2`
- Evidence class: bounded local-reference only; not production capacity.

## Environment

```json
{
  "api_process": {
    "host": "127.0.0.1",
    "pid": 27364,
    "port": 18921,
    "workers": 1
  },
  "architecture": "AMD64",
  "control_store": "project SQLite control store; WAL; synchronous=FULL; busy_timeout=30000ms",
  "cpu_logical_count": 6,
  "git_head": "69b28bb5521c8d63275be232f783fc8a6d0c8cd2",
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
  "started_at_utc": "2026-09-18T15:02:25.647297+00:00",
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
      "max_ms": 67.703,
      "mean_ms": 42.717,
      "p50_ms": 40.817,
      "p90_ms": 63.949,
      "p95_ms": 67.457,
      "p99_ms": 67.687,
      "sample_count": 32
    },
    "replayed_responses": 0,
    "requests": 32,
    "statuses": {
      "200": 32
    },
    "successful_requests": 32,
    "wall_time_ms": 84.67
  },
  "OVERLOAD": {
    "concurrency": 20,
    "errors": 0,
    "label": "OVERLOAD_RUN_SUBMISSIONS",
    "latency": {
      "max_ms": 22160.713,
      "mean_ms": 16525.878,
      "p50_ms": 17536.406,
      "p90_ms": 22160.532,
      "p95_ms": 22160.557,
      "p99_ms": 22160.682,
      "sample_count": 20
    },
    "replayed_responses": 0,
    "requests": 20,
    "statuses": {
      "202": 20
    },
    "successful_requests": 20,
    "wall_time_ms": 22170.674
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
      "max_ms": 32.527,
      "mean_ms": 13.21,
      "p50_ms": 11.839,
      "p90_ms": 26.78,
      "p95_ms": 29.32,
      "p99_ms": 31.89,
      "sample_count": 24
    },
    "replayed_responses": 0,
    "requests": 24,
    "statuses": {
      "200": 24
    },
    "successful_requests": 24,
    "wall_time_ms": 49.984
  },
  "STEADY": {
    "concurrency": 1,
    "latency": {
      "max_ms": 18.259,
      "mean_ms": 4.002,
      "p50_ms": 2.913,
      "p90_ms": 5.278,
      "p95_ms": 7.717,
      "p99_ms": 15.921,
      "sample_count": 24
    },
    "requests": 24,
    "statuses": {
      "200": 24
    },
    "wall_time_ms": 1187.273
  }
}
```

## Safe operating point

- Worker count: `1`
- Completed jobs: `2`
- Measured throughput: `0.654` jobs/s
- Queue-wait p95: `1759.497` ms
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

## G12 formal closure and limitations

- Formal G12 is `PASS` after exact-head metadata CI run `35381577846` on
  `d773042c6ea1c8da2c3499431a1c91fea921b4ec`; its clean-room G8 validator,
  image scan, independent Step31 QA, and Steps32-37 upstream checks passed.
- The post-repair host-global pytest diagnostic was `561 passed, 4 skipped,
  12 failed` in 557.43 seconds. Its optional-provider, DataProfiler, accepted
  Step29 real-provider, and unconfigured system-test observations remain
  explicit host limitations outside the clean-room/protected-path regression
  boundary; they are not silently upgraded or hidden.
- Exact-head content CI run `35374242475` on
  `8a5a2352468e0bf28be6f69d0492ffc27ebf441f` also passed G8, secret scan, image
  scan, independent Step31 QA, and Steps32-37.
- No production capacity, deployment, HA, multi-node, 1M, 10M or 100M measured claim.
- No exactly-once claim; the worker remains at-least-once with durable replay fences.
- Optional provider availability is reported as observed and fail-closed; it is not silently upgraded.
- Step39 was not started.
- Protected quality artifact contents were not read; the path was not modified, staged or committed.
