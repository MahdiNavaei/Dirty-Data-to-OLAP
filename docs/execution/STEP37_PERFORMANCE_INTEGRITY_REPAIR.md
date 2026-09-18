# Step37 Performance Integrity Repair

## Scope and starting point

- Execution step: `37`; specialist: `performance_engineer`.
- Starting repository baseline: `2f6270c23eb8999ed4aae0b2f1fa8a7b37b3394c`.
- Previously accepted Step37 content: `72a363a0a4fb30a165626eebbec848b7686de8ad`.
- Defect: the previous closure had only Tiny/reference-scale evidence, no successful real-provider E2E baseline, and no measured Medium-scale production-boundary baseline.
- Step38 was not started and remains explicitly `NOT_STARTED`.

## Repair content and provider boundaries

The repair content is assessed at `18d48a5f0af64e0b2eb2a8aa311c949a59cd05e6`.

- Desbordante was provisioned from the pinned source revision `b211961f3f272ed8815ef1ffbda90573b11e1116`.
- The local provider image was inspected by digest: `sha256:2cc4b944805dd55b6ee23de3ac4a9a6fb4cdc9daede9d86ea4b71c97937f3638`.
- Provider execution used a read-only container, a read-only `/input` bind, and no network access.
- Locked, isolated environments executed the real Dependency Discovery, Valentine schema-matching, and Splink entity-resolution adapter boundaries.
- The three stage baselines were `PASS` with aggregate-only receipts; raw records and protected quality artifacts were not incorporated.

## E2E and scale evidence

- `PERF-E2E-001`: `PASS`; terminal status `SUCCEEDED`; validated output present; G6 status `PASS` and eligible; accepted analytical, canonical-identity, evidence-decision, and materialization review checkpoints were present.
- `PERF-MEDIUM-001`: `PASS`; dataset `step37-medium-100k-v1`; 100,000 input rows; input `1,700,022` bytes; staged `256,658` bytes; output database `16,789,504` bytes.
- Medium semantic oracle: 100,000 fact rows, quantity sum `300,000`, unresolved foreign keys `0`, duplicate grain `0`.
- Medium dependency search was complete, bounded to arity one, made two provider calls, and did not time out or truncate.
- Measured Medium stage wall times were retained for source discovery, extraction/staging, profiling, dependency candidate generation, validation, and DuckDB materialization. Peak Python allocation was measured at `473,756,778` bytes.
- Tiny remains `EXECUTED_REFERENCE_ONLY`; Medium is `EXECUTED`; 1M and several-million are not executed and are reported only with measured Medium evidence; 10M is optional/not executed; 100M is feasibility-designed. No capacity or production-SLA claim is made.

## Validation and CI

- The first content CI run `35294797339` reached Step37 but exposed a workflow-only folded-scalar defect: schema/entity stage commands were interpreted as `\\ --stage`, so argparse rejected the argument.
- The surgical workflow repair is `18d48a5f0af64e0b2eb2a8aa311c949a59cd05e6`; both affected commands now use literal block scalars.
- Exact content-head CI run `35299342517` passed on `18d48a5f0af64e0b2eb2a8aa311c949a59cd05e6`, including clean-room G8, image scan, Step31 QA, Steps32-36 upstream gates, and the Step37 job.
- The local receipt validator passed with 32 benchmarks and the focused Step37 test suite passed `13` tests.
- The validator rejects missing real E2E/G6 evidence, non-executed Medium evidence, unavailable required providers, fabricated scale claims, G12 advancement, and Step38 start.

## Scope guard and handoff

Only Step37 evidence/validator/tests/workflow and this execution metadata are in scope. No product source, frontend source, or Step38 implementation was changed. `tests/quality_unit_artifacts/` remained unread and untouched.

Authoritative handoff after this repair:

```text
last_completed_step=37
last_completed_role=performance_engineer
current_step=38
current_role=load_stress
step37_started=true
step37_status=COMPLETED_PERFORMANCE
step37_integrity_repair=PASS
step38_started=false
step38_status=NOT_STARTED
G6=PASS
G7=PASS
G8=PASS
G9=PASS
G10=PASS
G11=PASS
G12-G15=PENDING
blocked=false
```
