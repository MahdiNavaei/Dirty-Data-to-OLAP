# Prompt04-R1 Downstream Review Closure

Date: 2026-09-25
Branch: `codex/prompt04-review-completion`
Starting HEAD: `9f70a2e749ad02a3771e648cf50b1f31bd79d73e`
Prompt03 baseline: `a735f157945df890ef809f1990ed00417f44a4bd`

## Result

`PASS_LOCAL_PROMPT04_R1_DOWNSTREAM_REVIEW_CLOSURE`

This closure covers the human-decision lifecycle from the public API through
the durable review boundary and into a real local product execution. It does
not start Prompt05, Prompt06, or Step42, and it does not claim fresh
four-source Prompt02 acceptance.

## Local real-product evidence

The real local run used the repository product runtime and the locally
available provider image `dirty-data-to-olap-desbordante-step37:local`. It did
not use a mocked product consumer or manually inserted success state.

- Run: `run_23e07b110bf774b8377c738b644ef6de`
- Source and snapshot, profiling, dependency discovery, quality analysis, and evidence fusion: `SUCCEEDED`
- Original relationship review: `ACCEPTED`, then later invalidated through the public invalidation boundary
- Override: typed `RELATIONSHIP_DISPOSITION / EXCLUDE_CANDIDATE`, with the original subject bound as `old_value_ref`
- Replacement subject: persisted as `ReviewOverrideProposal`, registered, and required a fresh compatible review
- Replacement review: `ACCEPTED` only after the replacement was visible as `REVIEW_REQUIRED`
- Canonical hypotheses: first hypothesis contained the original relationship; the recomputed hypothesis contained no relationship
- Current hypothesis job: pointed only to the recomputed hypothesis artifact; the old hypothesis was not retained in current `result_refs`
- Canonical finalization, planning, compilation, materialization, and semantic modeling: `SUCCEEDED`
- Final validation: `FAILED` with `G6_VALIDATION_FAILED`, `g6_status=FAIL`, and `g6_eligible=false`

The final G6 rejection is intentional. The override changes the relationship
set while the independent oracle remains unchanged, so the product must reject
the incompatible materialized result rather than report a false accepted G6
result. The materialization artifact and typed validation/reconciliation
artifacts were still produced and verified before the guard rejected the run.

## Gap closure

### Gap A — override downstream consumption

`tests/integration/test_prompt04_r1_real_override.py` proves that a reviewed
replacement is consumed by the real `CANONICAL_HYPOTHESES` stage. The proposal
is read through the registered artifact boundary, the replacement provenance
is present on the recomputed hypothesis, and the current stage reference is
the new hypothesis only.

### Gap B — lock enforcement

`NC-R21` proves the legacy review boundary cannot mutate a locked subject.
The action boundary requires a compatible accepted decision before `LOCK`, and
lock state is checked again for later mutation. `NC-R32` exercises concurrent
CAS behavior and verifies that revisions and history remain coherent.

### Gap C — unified invalidation

`NC-R22` and the real-provider scenario prove that invalidation is an atomic
lifecycle transition: current lock state is cleared, the decision is
invalidated, descendants are requeued, and historical decisions/actions are
preserved. A stale action revision is rejected by `NC-R23`.

### Gap D — run-wide execution eligibility

`NC-R28` requires all current review subjects in the run to be accepted before
the durable execution boundary becomes eligible. `NC-R29` proves that an
invalidation between acceptance and resume blocks the worker and downstream
execution. Requeue processing reopens work only after the shared readiness
predicate is satisfied.

## Positive scenarios

- A: accept a real review subject and resume through the public API.
- B: create and accept a typed replacement, then observe its effect in a real downstream stage.
- C: lock an accepted subject and reject mutation through the legacy boundary.
- D: invalidate a decision, clear the lock, preserve history, and requeue descendants.
- E: require run-wide acceptance across multiple subjects before execution can continue.

## Negative controls

The Prompt04-R1 controls are executable tests, not static claims:

- `NC-R21` lock bypass rejection.
- `NC-R22` invalidation lifecycle and history preservation.
- `NC-R23` stale revision rejection.
- `NC-R24` unaccepted replacement is not execution-eligible.
- `NC-R25` rejected replacement is not execution-eligible.
- `NC-R26` replacement must be consumed through the registered artifact path.
- `NC-R27` stale downstream hypothesis references are excluded after requeue.
- `NC-R28` one unaccepted subject blocks a multi-subject run.
- `NC-R29` invalidation before resume blocks the worker.
- `NC-R30` unsupported override targets are rejected and not advertised by the server.
- `NC-R31` injected compound audit failure leaves no effective untracked review.
- `NC-R32` concurrent lock/invalidation CAS preserves durable revisions and history.

`NC-R31` uses an injected audit failure at the compound persistence seam; it
does not merely inspect a prewritten report. `NC-R32` uses concurrent calls
against the live SQLite control store. The real override test also verifies
that G6 rejects an incompatible result, rather than treating downstream
materialization as proof of acceptance.

## API and frontend

- Server applicability is projected from the supported override target and replacement values; unsupported targets are not presented as executable controls.
- Override, invalidation, lock, resume, and review decisions use the public API and durable control-store boundary.
- OpenAPI and generated frontend types were regenerated locally.
- Frontend checks passed: `npm run typecheck`, `npm run lint`, `npm run test` (`2 files, 3 tests`), and `npm run build`.
- The repository-owned Playwright review test passed: `1 passed` against a real local API and system Chrome. Managed browser installation was blocked by the environment's CDN response, so no browser download or CI run was used.

## Local validation

- `tests/api/test_prompt04_r1_lifecycle.py` plus `tests/integration/test_prompt04_r1_readiness.py`: `9 passed`.
- `tests/integration/test_prompt04_r1_real_override.py`: `1 passed` with the local provider image.
- `tests/integration/test_step29_product_path.py`: `3 passed` with the local provider image.
- `tests/product_acceptance/test_prompt03_negative_controls.py` plus `tests/product_acceptance/test_prompt03_generic_product.py`: `18 passed`.
- Step35 wakeup regression: `1 passed`.
- `compileall -q src tests tools`: passed.
- `git diff --check`: passed before commit.

The repository's pinned Python version is 3.11.16 while the available local
virtual environment is 3.11.7. Therefore the exact `npm run generate:api`
wrapper rejected the environment; the same repository generator was executed
directly with the available environment and the generated OpenAPI/types were
validated by the frontend checks above.

## Acceptance boundary

Prompt04-R1 is locally closed for the review lifecycle and real downstream
override path. Overall V1 remains pending. Historical Prompt02 acceptance
remains bound to its accepted commit and CI evidence; this work does not
upgrade it to a current-branch four-source rerun. Prompt05 and Prompt06 are
not started, and Step42 remains not started.

## Remote completeness repair

The pushed Prompt04-R1 commit `f5a759218ddb9332ef15c7ca37ec2135c496d323`
was tracked-clean in the original working tree but was not self-contained.
The relevant local untracked-file audit found exactly one project-owned
implementation file:

`src/dirty_data_to_olap/application/review_readiness.py`

The file was present locally, was created during the Prompt04-R1 work, and
was omitted because the earlier explicit staging list did not include it. The
R1 implementation imported it from both `BackendService` and `JobWorker`, so
the omission was a publication error rather than a missing design.

No other relevant untracked Python source or Prompt04 test dependency was
found. The import-closure audit scanned 88 project files and 403 project-owned
import edges. There were no unresolved project modules; the readiness module
was the only resolved-but-untracked module.

### Original remote reproduction

A clean detached worktree was created outside the repository at:

`D:\_C_DRIVE_OFFLOAD\temp\ddo-prompt04-r2-remote-20260926`

It was checked out from remote SHA `f5a759218ddb9332ef15c7ca37ec2135c496d323`.
The readiness file was absent there. With `PYTHONPATH` set only to that
worktree's `src` directory, this command:

```text
python -c "import dirty_data_to_olap.application.backend; import dirty_data_to_olap.application.jobs"
```

failed with:

`ModuleNotFoundError: No module named 'dirty_data_to_olap.application.review_readiness'`

### Repair and clean-checkout proof

Repair commit `d653c579aa89778a339ef980e76eaa2964a93a0d` explicitly added:

- `src/dirty_data_to_olap/application/review_readiness.py`, the shared
  server-owned readiness evaluator used by backend action eligibility and
  worker resume eligibility.
- `tests/architecture/test_prompt04_repository_completeness.py`, a focused
  import-path regression that requires the evaluator to resolve from this
  repository's `src` tree.

A second clean detached worktree was created at:

`D:\_C_DRIVE_OFFLOAD\temp\ddo-prompt04-r2-repaired-20260926`

from repair commit `d653c579aa89778a339ef980e76eaa2964a93a0d`. The clean import
smoke test passed for `backend`, `jobs`, `product_runtime`, and
`review_readiness`; every reported `__file__` path was inside that worktree.
Clean `compileall` and `git diff --check` also passed.

The clean checkout ran the Prompt04-R1 tests and the real local-provider
override. Its real run was `run_88cc5be2286cbd79798ce14616f11ac9`:

- Prompt04 action, lifecycle, readiness, and real override tests: `13 passed`
- Repository completeness regression: `1 passed`
- Step35 lost-wakeup regression: `1 passed`
- Prompt03 regression subset: `18 passed`
- Real downstream chain: materialization succeeded; unchanged-oracle G6 guard returned `FAIL` as expected

The final self-contained implementation commit is `d653c579aa89778a339ef980e76eaa2964a93a0d`.
The subsequent documentation/state commit is intentionally separate and is
the final branch HEAD reported by the post-push verification; no implementation
files are changed by that follow-up.
