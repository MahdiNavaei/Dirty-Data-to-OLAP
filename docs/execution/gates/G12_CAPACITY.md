# G12 - Capacity

Status: `PENDING`

Step38 established a bounded, reproducible local load/stress profile for the
real Dirty Data to OLAP listener and worker/control-plane path, but the formal
G12 closure remains pending because the required repository-wide regression and
clean-room closure did not pass in this environment. This is a local reference
result, not a production capacity, SLA, or deployment claim.

## Evidence

- suite: `step38-load-stress-v1`
- assessed content commit: `69b28bb5521c8d63275be232f783fc8a6d0c8cd2`
- evidence/report binding commit: `fbd0367d287511ad7e173bcc800658ac78f2e407`
- scenario matrix: `load/step38_scenarios.json`
- machine-readable receipt: `output/step38_load_stress_validation.json`
- report: `reports/load/STEP38_LOAD_STRESS_REPORT.md`
- validator: `tools/validate_step38_load_stress.py`
- provider: `dirty-data-to-olap-desbordante-step37:local`
- provider image digest: `sha256:2cc4b944805dd55b6ee23de3ac4a9a6fb4cdc9daede9d86ea4b71c97937f3638`
- load profile result: `PASS`; formal G12 result: `PENDING`

## Measured controls

- Four arrival patterns were exercised: `STEADY`, `RAMP`, `BURST`, and
  `OVERLOAD`.
- The real local listener accepted 20 concurrent overload submissions with no
  submission errors.
- At-least-once replay safety used five same-command idempotency-key cases.
- The bounded worker ramp covered worker counts `1, 2, 4, 8` with queue depth
  and queue-wait latency recorded.
- The final receipt measured worker `1` as the local reference safe point at
  `0.654 jobs/s` and `1759.497 ms` queue-wait p95. Worker `2` was the first
  measured throughput decline while queue-wait p95 increased; this is a local
  SQLite/filesystem boundary, not a universal product limit.
- Two concurrent real-pipeline runs reached G6 and each passed four review
  checkpoints.
- Artifact staging, SQLite contention, backpressure, bounded memory, provider
  concurrency, cancellation/recovery, run isolation, review pause/resume,
  source read-only protection, and state-machine invariants passed.
- Step28/Step29 regression evidence passed: 18 integration tests.

## Boundaries

- `1M`, `10M`, and `100M` executions were not performed.
- No production capacity, public SLA, exactly-once, or production-source-stress
  claim is made.
- The source protection probe confirmed read-only behavior and unchanged source
  hash.
- `tests/quality_unit_artifacts/` contents were not read and the path was not
  modified, staged, or committed.
- Step39 was not started.

## Closure blockers

- The latest full pytest run was not green: `558 passed, 4 skipped, 15
  failed` in 638.44 seconds. It included unavailable optional provider
  modules, missing `STEP31_BASE_URL`, a host-only profiler expectation, and
  the accepted Step29 real-provider path unavailable on this host. Three
  Step33 state-predicate failures from that run were repaired afterward and
  the focused Step33 suite now passes `12` tests plus `6` state-classification
  tests; a green full rerun is still required.
- Step30 clean-room validation passed through locked Python/frontend setup,
  deterministic OpenAPI generation, typecheck, lint, frontend tests and build,
  but its project-owned Chromium install failed with Playwright CDN HTTP `403`
  (`location access denied`).
- Step31 QA validation passed locked frontend install and deterministic API
  generation, but did not complete the Docker backend/frontend build, so it is
  not reported as PASS.
- Steps22-29 and Steps32-37 critical validators passed on the current state;
  Step30 remains externally blocked and Step31 remains incomplete.

## Gate state

`G6=PASS`, `G7=PASS`, `G8=PASS`, `G9=PASS`, `G10=PASS`, `G11=PASS`,
`G12=PENDING`, `G13-G15=PENDING`, `blocked=false`.
