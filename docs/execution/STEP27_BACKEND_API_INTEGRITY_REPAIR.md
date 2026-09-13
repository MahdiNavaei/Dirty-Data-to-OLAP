# Step27 Backend API Integrity Repair

## GOAL RESULT

PASS for the surgical post-Step27 backend trust-boundary repair. The original
`docs/execution/STEP27_BACKEND_API_REVIEW.md` was preserved unchanged. No
Step28 workers, queues, durable job processing, frontend, deployment, or G7
implementation was started.

## STARTING BASELINE

- branch: `main`
- starting HEAD and `origin/main`: `6c50f348ea6309aa1233380c47bf19de81310bfb`
- prior Step27 content commit: `4c3fbd86f022fa640e86841ae4586e4489ab7b32`
- protected `tests/quality_unit_artifacts/` was preserved, not read, modified,
  staged, committed, or used as evidence

## FINDINGS CLOSED

- Review compatibility is server-authoritative. A control-store-backed
  `ReviewSubjectResolverPort` resolves the registered subject context for the
  run, checkpoint, artifact identity, and content hash. A client context, when
  retained for compatibility, is an exact expected-binding assertion only.
- All eight compatibility fields are independently checked: subject stage,
  schema version, model version, source schema fingerprints, policy version,
  domain assertion references, semantic ID, and applicability fingerprint.
- Review history `subject_key` is built only from the resolved authoritative
  context, so forged semantic or applicability values cannot create a parallel
  current-review stream.
- Review actors remain principal-derived and artifact/run/checkpoint/hash
  binding remains enforced.
- The Step27 API contract explicitly rejects `SKIPPED` with stable
  `REVIEW_SKIP_UNSUPPORTED`; the OpenAPI action enum does not expose it as a
  valid action.
- Run creation and review create/update/invalidation use durable SQLite
  idempotency operations that combine reservation, fingerprint validation,
  CAS/mutation, and replay-result persistence in one transaction. Correctness
  no longer depends on a process-local lock.
- Typed read scopes cover runs, attempts, reviews, artifacts, validation, and
  visualizations. In `trusted_proxy` mode protected reads require a resolved
  principal; health and documented OpenAPI access remain public.
- The SQLite control schema now uses version 4, with an explicit v3-to-v4
  migration and safe reopen/partial-capability repair.
- Legacy repository validators now recognize the actual Step28 handoff while
  preserving their earlier accepted handoff states.

## AUTHORITATIVE REVIEW BINDING

The API selects a registered subject and the backend obtains its compatibility
context from project-owned control metadata. `ReviewPolicyService` remains the
policy authority; the HTTP layer does not reimplement compatibility formulas.
The persisted `ReviewDecision` was checked against every server-derived
compatibility field in the negative-control suite.

## REVIEW SKIP SEMANTICS

Option B was selected for Step27: `SKIPPED` is explicitly unsupported until a
server-owned skip-authorization source exists. A client cannot authorize a
skip by supplying a context or authorization object.

## IDEMPOTENCY / CRASH RECOVERY EVIDENCE

Deterministic fault-injection tests covered the post-mutation/idempotency
finalization boundary for run and review mutations. After store/backend
reopen, retrying the same principal, key, fingerprint, and logical request
replayed the same result without a second review revision or stale conflict.
Same-key changed-fingerprint requests fail with
`IDEMPOTENCY_KEY_REUSED`.

Execution submission records reservation and completion uncertainty
explicitly. If acceptance or finalization cannot be safely known, the API
returns `DELIVERY_UNKNOWN`/HTTP 503 instead of claiming a successful replay.

## EXECUTION COMMAND IDENTITY

`ExecutionSubmissionPort` now receives an immutable project-owned
`ExecutionCommand` containing a stable `command_id`, run ID, action,
idempotency scope/key, request fingerprint, principal identity/source, and
recorded metadata without raw secret-bearing request bodies. The same semantic
API command produces the same command identity across retry and restart.
This is a control-plane duplicate-delivery guarantee; it does not claim
exactly-once distributed execution, which remains Step28 scope.

## MULTI-INSTANCE CONCURRENCY RESULT

Two backend instances using the same SQLite store were exercised with the same
review request. SQLite reservation/CAS produced one logical review action and
one history revision, with safe replay for the competing caller. Same-key
changed-request conflict remained fail-closed.

## AUTH / READ AUTHORIZATION RESULT

Anonymous protected reads return 401 in `trusted_proxy` mode, authenticated
principals without the typed read scope return 403, and the correct scope
succeeds. Mutation scopes remain separate. `local_test` supplies explicit read
scopes for the local contract tests. No JWT/password infrastructure was added.

## SQLITE SCHEMA RESULT

Step23 databases migrate from schema version 3 to version 4 while retaining
existing data. The Step27 idempotency, current-review, review-history, and
trusted-subject-context capabilities are available after migration, repeated
open is safe, and a partial capability layout is repaired on reopen. No second
database was introduced.

## STEP27 VALIDATOR RESULT

`tools/validate_step27_backend.py` passed with `66` scenarios, including the
eight individual context tamper controls, forged parallel-stream rejection,
unsupported skip, reopen replay, multi-instance race, changed-key conflict,
stable command identity, uncertain delivery, typed read authorization, v3-to-v4
migration, partial-capability repair, and OpenAPI determinism.

## FOCUSED TESTS

- Step27/Step23 focused suite: `34 passed in 26.56s`; the command included
  Step27 API and integrity tests, Step27/Step23 architecture and contract
  tests, and Step23 integration tests.
- Cross-step Step20/25/26/10 suite: `57 passed in 4.09s`.
- Compileall over all non-protected source, tool, and test paths: PASS.
- `MASTER_EXECUTION_STATE.yml` YAML parse: PASS (`current_step=28`, Step28
  `NOT_STARTED`, G7 `PENDING`).
- OpenAPI parse/versioning/determinism: PASS in the focused API tests and the
  Step27 validator.
- `git diff --check`: PASS; only normal LF/CRLF conversion warnings were
  emitted by Git.

## FULL REGRESSION

The feasible protected-boundary regression passed:

`393 passed, 2 skipped, 41 warnings in 178.75s`

The two truthful optional skips are the unavailable official Splink and
Valentine runtimes. The existing non-fatal dlt/SQLite cursor-cleanup traceback
was emitted after successful pytest completion and remains a known limitation.

## ALL VALIDATORS

All repository validators passed: `27/27 PASS`.

## GATE STATE

G5, G6, G7A, and G7B remain `PASS`. G7 remains `PENDING`. No gate was promoted
by this repair.

## CONTENT COMMIT

`e840a11dfa128fb7a62afead783b0f25b72b8060`

## KNOWN LIMITATIONS

This repair does not provide durable workers or queues, distributed execution
exactly-once semantics, production authentication, browser/physical
acceptance, deployment, capacity evidence, or G7 completion. Optional Splink
and Valentine integrations remain skipped when their runtimes are unavailable.
The pre-existing dlt/SQLite cleanup traceback remains non-fatal but is
recorded rather than hidden.
