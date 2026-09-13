# Step29 - Frontend Engineer Review

## Scope and baseline

- Specialist scope: Step29 frontend product path only. Step30 was not started.
- Starting baseline: `HEAD=origin/main=1cf7af5ed2fd20574d1370fb699901ca8522006b`.
- Content commit: `36071d6057f5bfc7176260ac4832de62424e0475`.
- Protected `tests/quality_unit_artifacts/` was not read, modified, staged or committed.

## Frontend stack

The browser surface is a React 18 + TypeScript + Vite application. It uses a generated OpenAPI TypeScript contract, a typed API client, React Router and responsive CSS. Vitest and Testing Library cover the client error boundary and root product surface. The frontend has no direct filesystem, SQLite, DuckDB or artifact-payload access.

## Product path

1. Load the server-owned product configuration and managed source list.
2. Upload a bounded CSV through `POST /api/v1/sources/import`.
3. Bind the registered source to a new run.
4. Prepare and submit the server-owned execution plan.
5. Poll one durable summary request at a time with visibility-aware bounded backoff.
6. Present exactly four typed review checkpoints and request `ACCEPTED` plus durable resume.
7. Present canonical identity, analytical plan, materialization state and G6 validation.
8. Show validated OLAP output only after `SUCCEEDED`, `G6=PASS`, eligibility and validation evidence are returned by the API.

The acceptance fixture contains four real CSV data rows and the required V1 order columns. The focused backend test reached `SUCCEEDED`, observed four source rows, and verified the four checkpoint identities, materialization, G6 and validated output.

## Source-to-discovery binding

The imported bytes are stored by `ProductSourceService` below the managed project product area and registered by `DurableSourceRegistry`. The first worker stage receives the run-bound source selection through the control store. Discovery, snapshotting, profiling, evidence fusion, canonical preparation/finalization, analytical planning, compilation, DuckDB materialization and validation are executed by the project-owned local runtime. The browser only sends API commands and renders safe projections.

## Review, uncertainty and privacy

The workspace makes the four review checkpoints visible and keeps them distinct from canonical identity and analytical state. Relationship scores are labelled as uncalibrated decision scores and explicitly marked as not probabilities. The UI enumerates incomplete, provider failure, privacy blocked, not evaluated, partial success, long running, stale, validation failed and cancelled states with recovery guidance.

The browser projection excludes raw CSV rows, file locators, target paths, generated SQL, generic artifact payloads, credentials and direct storage URLs. Stable opaque subject identities are used only to bind the visible review object to server truth.

## Independent self-review and repairs

The first browser inspection found and repaired the missing React DOM mount, missing local CORS headers and missing JSON content type on client mutations. The backend path then exposed and repaired the DuckDB schema/query mismatch. A later run exposed a worker shutdown race and a Windows Unicode/CLI-result parsing issue in the validator; explicit runtime shutdown, UTF-8 process capture and robust browser-state parsing were added. The final review also strengthened the browser validator to assert the visible G6/output text, not only an internal status.

The review was rerun after each repair. The final run did not expose console errors, direct storage requests, raw SQL/path content or fixture row values in the page projection.

## Validation evidence

- `rtk python tools/validate_step29_frontend.py`: PASS.
- Step29 validator: OpenAPI generation, Python compile, focused backend product test, frontend typecheck/lint/test/build and real browser G7 all PASS.
- Frontend tests: 3 tests PASS across 2 files; typecheck, lint and production build PASS.
- Focused backend product path: 1 test PASS.
- Full feasible regression: `459 passed, 2 skipped, 41 warnings` under Python 3.10 with the protected directory ignored. Optional Splink and Valentine integrations were skipped because those runtimes are not installed. The existing non-fatal dlt/SQLite cursor-cleanup traceback remained outside assertions.
- All repository validators: `29/29 PASS`, including the final Step29 validator.

## True G7 browser result

The real Playwright CLI session used the running Vite frontend and running local API. It uploaded the CSV through the UI, created a run, observed and accepted the ordered checkpoints `REVIEW_EVIDENCE_DECISIONS`, `REVIEW_CANONICAL_IDENTITY`, `REVIEW_ANALYTICAL_PLAN` and `REVIEW_MATERIALIZATION_PLAN`, and ended with visible `SUCCEEDED`, `G6 PASS - eligible` and `Validated OLAP output available` states. Console errors were zero. Browser evidence is retained under `output/playwright/step29-g7-validator/`; the machine-readable result is `output/step29_frontend_validation.json`.

## Known limitations

This is a bounded local reference product path using project-owned SQLite/filesystem control state, a local DuckDB target and explicit local-test authentication. It is not a production authentication, broker, HA, multi-node, deployment, capacity, observability or release claim. G7 completion does not imply G8-G15 completion. Optional external provider runtimes remain outside this acceptance path.
