# Step27 Backend API Review

Status: `PASS` for the bounded V1 backend/control-plane boundary. This
receipt does not start Step28, Step29, Step30 or Step33 and does not claim
end-to-end product completion or production authentication.

## Baseline

- Starting branch: `main`.
- Starting `HEAD=origin/main=83488a7e7b0d2bd066eaec1fbb13e09ceb061876`.
- Step26 Repair 2 content commit `e4d578a42bc888b4ff35a7c21d8b1424d67f1719` and all Step26 receipts were preserved.
- Starting state: `last_completed_step=26`, `current_step=27`, `current_role=senior_backend_engineer`, `step27_started=false`, `step27_status=NOT_STARTED`, G5/G6/G7A/G7B `PASS`, G7 `PENDING`, `blocked=false`.
- `tests/quality_unit_artifacts/` was not read, modified, staged, committed, or used as evidence.

## API architecture

The executable path is:

```text
HTTP /api/v1
  -> entrypoints.api.create_app
  -> application.backend.BackendService
  -> ControlStorePort + ArtifactStorePort + ReviewPolicyService
  -> optional ExecutionSubmissionPort
```

Routes do not open SQLite, resolve host paths, invoke providers, execute SQL,
run data engines, recompute validation, or implement review compatibility.
The local composition root wires the existing Step23 `LocalPlatform` stores.

Implemented resources include run/project control, stage attempts, registered
artifact metadata, review history/actions, trusted validation projection,
stored visualization graph projection, and execution command handoff.

## Framework / OSS decision

FastAPI `0.128.0` was present and compatible with the repository's Pydantic
`2.12.5`. It is added as an optional `api` extra together with Uvicorn. The
framework owns HTTP routing, request validation, ASGI and executable OpenAPI;
all project semantics remain in project-owned contracts/services. No copied
OSS implementation or renderer was added.

## Endpoint surface

- `GET /api/v1/health`
- `POST /api/v1/runs`
- `GET /api/v1/runs`, `GET /api/v1/projects/{project_id}/runs`
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/attempts` and individual attempt inspection
- `GET /api/v1/runs/{run_id}/artifacts`
- `GET /api/v1/artifacts/{artifact_id}` with explicit run scope
- controlled `POST .../artifacts/register` by registered artifact identity
- `GET .../artifacts/{artifact_id}/content` is deliberately denied
- review list, exact checkpoint action and explicit invalidation action
- `GET .../validation/{artifact_id}` and `GET .../visualizations/{artifact_id}`
- `POST /api/v1/runs/{run_id}/execution`, `/cancel`, and `/resume`

Run status is read-only at this boundary. Run states remain exactly
`CREATED`, `RUNNING`, `NEEDS_REVIEW`, `BLOCKED`, `FAILED`, `CANCELLED`, and
`SUCCEEDED`; `PARTIAL` and `QUEUED` are not introduced. Stage attempt states
remain the existing Step23 vocabulary and historical attempts are retained.
All list responses use deterministic ordering, bounded page sizes and
validated filters.

## Control Store changes

The existing `ControlStorePort` was extended with bounded run/attempt listing,
review current/history persistence and safe idempotency records. SQLite adds
the additive `api_idempotency`, `review_current`, and `review_history` tables
under the existing Step23 schema compatibility path. No second database,
parallel review store, raw dataset table, or schema reset was introduced.

Review history is append-only; the current projection is updated with a CAS
revision. Existing Step23 migration tests remained passing.

## Review / concurrency evidence

Review actions require one of the four exact `ReviewCheckpoint` values and a
complete `ReviewCompatibilityContext`. The subject must be a registered,
published artifact in the requested run with the exact content hash.
`ReviewPolicyService.create_decision`, `invalidate`, and the domain
compatibility contract remain the semantic authorities. The request cannot
provide an actor; the actor is derived from the trusted principal.

`expected_revision` is persisted through SQLite compare-and-swap. A bounded
two-thread race produced exactly one success and one
`REVIEW_REVISION_CONFLICT`; rejected, deferred, invalidated, stale, wrong
subject and wrong checkpoint decisions remain non-authorizing.

## Idempotency evidence

Run creation, review actions, execution submission, cancellation and resume
require `Idempotency-Key`. Only a safe semantic fingerprint, status, response
projection and resource reference are persisted. Same key plus the same
semantic request replays the same logical response; a changed request returns
`IDEMPOTENCY_KEY_REUSED` with HTTP 409. Raw request bodies, credentials and
secrets are not persisted.

## Auth integration

The reference app defaults to explicit `local_test` authentication: mutation
routes require `X-Local-Principal`, which creates a clearly labeled local
principal with test scopes. `trusted_proxy` accepts an injected principal
resolver. No password database, JWT implementation or production-auth claim
was added. Missing or unauthorized mutation callers receive stable 401/403
responses.

## Artifact / privacy evidence

Artifact reads are restricted to control-store references scoped to the run;
the control and artifact references must match and content hash/size must
verify before exposure. Reserved, failed, incomplete and tombstoned artifacts
are non-consumable. API metadata omits storage keys and external locators.
Generic payload access is denied, including restricted/raw artifacts. Path
traversal, arbitrary locators and cross-run access are rejected.

## Validation / visualization evidence

Validation JSON is read only from a verified registered `ValidationReport`
artifact. It is revalidated and passed to the exact Step26
`ValidationReport -> ValidationReportVisualizationBinding -> UI_PREVIEW`
path. G6 is not recomputed. FAIL, PENDING/NOT_EVALUATED, required checks,
provenance, evidence/discrepancy references and privacy display states remain
visible while raw canaries remain absent.

Visualization graph JSON is read only from a verified registered
`VisualizationGraph` artifact. The existing Step26 contract is serialized as
is, preserving disclosure totals, hidden/truncated counts, filters,
accessibility rows, review/conflict/scope fields and content hash. No
unlimited graph query exists.

## Orchestration boundary

`ExecutionSubmissionPort` is a project-owned injection point for Step28. The
reference composition uses an explicit unavailable implementation, so
submit/cancel/resume return `UNAVAILABLE` with HTTP 503 and never claim
`QUEUED` or execute heavy work in an HTTP handler. Durable ownership,
retries, workers, cancellation and resume remain Step28 scope.

## OpenAPI result

OpenAPI is generated by the executable FastAPI app at
`/api/v1/openapi.json`, with no disconnected hand-maintained specification.
The validator fetched it twice and compared the parsed documents. The
document is versioned and contains no repository paths, credentials or raw
privacy values.

## Step27 validator result

`tools/validate_step27_backend.py`: `STEP27_VALIDATOR=PASS scenarios=39`.
The scenarios cover app/OpenAPI, runs, attempts, filters/pagination,
artifact scope/registration/integrity/privacy, exact review binding/CAS/race,
actor/auth/error sanitization, validation/visualization projections,
submission availability/idempotency and forbidden status mutation.

## Tests and validation

- Step27 targeted API/architecture suite: `10 passed`.
- Step23 platform unit/contract/integration/security suite: `25 passed`.
- Final protected-boundary full regression, excluding only the two established
  protected-path writers `tests/unit/test_quality_engine.py` and
  `tests/security/test_step23_platform_security.py`: `385 passed, 2 skipped,
  41 warnings` in `145.20s`.
- The two skips are the optional official Splink and Valentine runtimes.
- The known non-fatal dlt/SQLite cursor-cleanup traceback appeared after the
  successful suite and remains a limitation of the existing environment.
- Final validator sweep: all `27/27` `tools/validate_*.py` validators exited 0,
  including Step23, Step25, Step26 and Step27.
- Compileall over `src`, `tools`, and all non-protected test directories:
  PASS. The protected directory was intentionally excluded by the repository
  boundary.
- YAML parse and `git diff --check`: PASS.
- Actual API responses and generated OpenAPI were inspected by the API tests
  and Step27 validator.

## Git and handoff

- Content commit: `4c3fbd86f022fa640e86841ae4586e4489ab7b32`
  (`feat: add Step27 backend control plane API`).
- Metadata commit: the final metadata-only commit containing this receipt,
  state and execution-log handoff.
- Normal push and final `HEAD==origin/main` verification are required after
  metadata commit.
- Step28 was not started. Step29, Step30 and Step33 were not started.
- G7 remains `PENDING`; G5, G6, G7A and G7B remain `PASS`.

## Known limitations

This is bounded local/reference backend evidence. It does not claim durable
distributed execution, queue semantics, production authentication, browser or
frontend usability, deployment/CI, AppSec completion, live providers, or
production capacity. FastAPI is optional and must be installed with the
`api` extra. The local-test principal is not production authentication.
