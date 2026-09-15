# Step31 Independent System Test Matrix

Step31 tests the assembled product through the running HTTP and browser
boundaries. Each scenario provisions its own project identity and uses the
isolated Compose runtime started by `tools/validate_step31_qa.py`.

| Scenario | Layer | Entrypoint / fixture | Expected behavior | Covered invariant |
| --- | --- | --- | --- | --- |
| QA-SYS-001 | system | HTTP API + `tests/fixtures/step31_orders.csv` | Four ordered reviews pause/resume a real run; restart preserves the pending checkpoint; final output is validated | review compatibility, durability, G6, output truth, source immutability |
| QA-SYS-002 | system/failure | HTTP API + `tests/fixtures/step31_duplicate_orders.csv` | Duplicate order identity fails at dependency discovery and never becomes success | failure truth, provider boundary, no silent partial success |
| QA-API-001 | API black-box | Public v1 API | Health/OpenAPI, errors, idempotency, invalid transition, cancellation and project filtering behave as typed contracts | API envelope, isolation, durable command state |
| QA-E2E-001 | browser/failure | `frontend/e2e/step31_qa.spec.ts` + duplicate fixture | Browser shows `FAILED`, dependency stage and non-consumable output | user-visible failure truth |
| QA-E2E-002 | browser/review | `frontend/e2e/step31_qa.spec.ts` + valid fixture | Reload preserves the server-owned review checkpoint and actionability | durable review state, no route mocking |
| QA-G7-REGRESSION | accepted browser regression | `frontend/e2e/step29_product_path.spec.ts` + accepted fixture | Existing four-checkpoint G7 path still reaches validated output | G7 preservation |

## Current versus historical test estate

The current Step31 regression is the self-contained `tests/system` and
`tests/api` suite, the Step31 browser spec, and the accepted Step29/G7 browser
regression. It creates source and runtime state through product APIs and removes
its Compose project and named volume at validator teardown. G7 runs on its own
fresh stack, which is removed before the Step31 browser scenarios start.

The following Step30 exclusions remain classified as historical evidence or
component-specific coverage; they are not silently called current regression:

- `tests/integration/test_step20_generic_olap_flow.py` — historical artifact validation (C)
- `tests/integration/test_step20_olap_flow.py` — historical artifact validation (C)
- `tests/integration/test_step23_platform.py` — historical artifact validation (C)
- `tests/integration/test_step28_integrity_repair.py` — historical artifact validation (C)
- `tests/integration/test_step28_job_processing.py` — historical artifact validation (C)
- `tests/unit/test_step18_v4_integrity.py` — historical artifact validation (C)
- `tests/integration/test_step19_canonical_flow.py` — historical artifact validation (C)
- `tests/contract/test_canonical_contracts.py` — historical artifact validation (C)
- `tests/integration/dependencies/test_step12_real_provider.py` — replaced for system QA by the container-provider path (B)

Optional Splink and Valentine tests remain explicitly optional-provider
coverage. They are not promoted to PASS when the runtime is absent.

## Test design controls

- No browser route interception, API mocking, direct database access, review-row writes, or pre-seeded completion is used by the Step31 path.
- Polling uses bounded deadlines and observable API state rather than fixed long sleeps.
- Expected fixture truth is explicit: four input rows, four ordered checkpoints, three four-row output tables, and the duplicate failure code `DEPENDENCY_INCOMPLETE`.
- Browser tests assert user-visible state, not private implementation layout.
