# Prompt04 Review Contract Acceptance

Date: 2026-09-25
Branch: `codex/prompt04-review-completion`
Prompt03 baseline: `a735f157945df890ef809f1990ed00417f44a4bd`

## Result

`PASS_LOCAL_REVIEW_CONTRACT_AND_UI`

This result closes the Prompt04 review action contract, server-owned applicability projection, durable history, API boundary, and browser review workspace locally. It does not close the overall V1 product acceptance, Prompt05 export, Prompt06 release work, or external-provider heterogeneous-source acceptance.

## Implemented contract

- Six server-enforced actions: `ACCEPT`, `REJECT`, `OVERRIDE`, `LABEL`, `LOCK`, and `DEFER`.
- Existing review decisions remain the only execution-guard states; labels, locks, and overrides cannot silently satisfy the guard.
- `OVERRIDE` persists a typed immutable replacement subject and registers a fresh compatible review context. The original artifact is unchanged.
- `LABEL` persists bounded namespace/value labels and never means acceptance, merge, or auto-approval.
- `LOCK` requires an existing compatible accepted decision and explicit confirmation; incompatible mutation is rejected until lifecycle invalidation.
- Authorization, run scope, registered-artifact verification, content hash, context binding, action revision, decision revision, lock state, and idempotency are revalidated at the server boundary.
- Action history and current action state are stored durably in the control store with compare-and-swap revision checks.
- The UI renders server applicability, evidence scope, confidence semantics, provenance, missing/conflicting evidence, downstream effect, typed forms, stale/error states, and durable history.

## API acceptance

Local tests:

- `tests/api/test_prompt04_review_actions.py`: `3 passed`
- Six action durable-effect coverage: PASS.
- NC-R01 through NC-R20: PASS.
- Order and telemetry coverage: PASS; the accepted decision is recorded before its action record, and telemetry result classes remain bounded.
- Focused Prompt27 backend regression: `17 passed` across Prompt04 plus the existing backend/integrity tests.

## Browser acceptance

The repository-owned Playwright browser test passed locally: `1 passed`.

The test used a real local API, SQLite control store, registered immutable artifact, persisted review context, and a durable `NEEDS_REVIEW` job fixture. It exercised all six action controls, evidence/provenance rendering, `LABEL`, reload, durable history, and `ACCEPT`. Because the fixture had no execution plan, the server returned `execution_eligible=false`; the browser correctly did not call resume and kept the review subject visible.

The full source-upload product path was also attempted locally. It reached the real worker and ended at `DEPENDENCY_DISCOVERY` because the optional Desbordante provider is unavailable on this host. That is not upgraded to Prompt04 or G7 PASS evidence.

Playwright CLI was unavailable in this Windows environment (`npm ECOMPROMISED`), and the cached Playwright browser revision did not match the installed package. The repository runner therefore used the installed system Chrome through the explicit local-only `PLAYWRIGHT_EXECUTABLE_PATH` test-runner option. No CI workflow or browser download was used successfully.

## Negative and integrity boundaries

- No arbitrary JSON, SQL, host path, credential, raw source row, or artifact content is accepted as an override.
- A stale hash, stale action revision, forged context, cross-run subject, unauthorized principal, locked subject, invalid override combination, idempotency reuse, tampered artifact, and failed-G6 materialization review are rejected.
- A rejection, deferral, label, or lock does not become a successful execution guard.
- Review decisions remain persisted through the existing review boundary; action records are an additional durable audit layer.

## Limitations

- This is local Prompt04 contract/UI acceptance, not a fresh four-source Prompt02 rerun.
- Optional Desbordante is unavailable locally, so no real heterogeneous-provider review path is claimed here.
- Overall V1 remains `PENDING`; Prompt05 and Prompt06 remain `NOT_STARTED`; Step42 remains `NOT_STARTED`.
