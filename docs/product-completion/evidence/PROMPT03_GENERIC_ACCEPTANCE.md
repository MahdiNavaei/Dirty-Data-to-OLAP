# Prompt03 Generic Product Acceptance

## Result

`PASS_LOCAL_TELEMETRY_BOUNDED` for the generic telemetry/device composition path. This is local acceptance evidence, not an overall V1 PASS and not external-CI confirmation.

Prompt03-R2 was executed on branch `codex/prompt03-generic-product` from starting HEAD `eaace9876da21393df1b66a86f8bb6ffe6117894`. The exact tested code commit is `28f8777342e053c685ae41c6ab143cc174a737c9`. Prompt04/Step42 was not started. No GitHub Actions workflow was started or modified.

Prompt02 is historically accepted for commit `7786ea301e3482b374e57447974b73d9a0176f8c`, successful run `36081128931`, and artifact `prompt02-acceptance-evidence-36081128931` (artifact ID `10844010695`). The earlier failed run `35700636572` remains historical failure evidence. The current Prompt03 branch has not repeated the full four-source Prompt02 scenario locally; its focused result remains `3 passed, 1 skipped` because the local database environment is unavailable.

## Generic policy boundary

- The registry resolves only exact, bounded product policy identities: product ID, version, and content fingerprint.
- `ProductDomainPolicy` is the typed capability boundary for product identity, source roles, logical values, dependency/relationship semantics, identity membership, analytical planning inputs, source records, truth/accounting, and validation policy.
- The shared runtime owns durable orchestration, evidence fusion, persisted review, canonicalization, planning, compilation, materialization, semantic modeling, and G6 validation.
- `MultiSourceProductService` requires an explicitly bound policy; it no longer defaults to `OrderProductPolicy`.
- Shared orchestration has no direct `product_policy.product_id` or `policy.product_id` dispatch branches. Product-specific behavior is delegated through the policy contract.
- The telemetry policy uses the same durable worker, stage registry, review boundary, canonical finalization, analytical planner, compiler, DuckDB materializer, semantic layer, and G6 validator as the accepted order path.

The test-only `ConformingFixturePolicy` proves registry extension, exact binding, service delegation, artifact provenance, and rejection of an incompatible execution-plan fingerprint. It is not claimed as a third accepted business domain.

## Durable local pipeline evidence

Final exact-commit durable run: `run_b8738ac0388d163aaeaaceca77cd74be`.

- `24/24` durable jobs succeeded.
- `28` review-history entries were persisted.
- `G6_DATA_CORRECTNESS=PASS`, `eligible=true`.
- `verified_content_commit=28f8777342e053c685ae41c6ab143cc174a737c9`.
- Provider: local Docker image `dirty-data-to-olap-desbordante-step37:local`.
- Policy: `telemetry`, version `telemetry-product-v1`, fingerprint `baaecd58325f42c41187ee6ddadce83518175cf9e67925b06ef36dc347b3f42b`.
- Durable artifacts include source catalogs/snapshots, dependency results, schema/fusion/relationship/mapping artifacts, canonical proposal/model, analytical plan/specifications, compiled plan, generated SQL, target configuration, materialization, semantic model/validation, source truth, record accounting, and reconciliation.

The earlier successful durable run `run_c99d8fea7e44ab363b37a952c5d3eea9` remains historical bounded evidence. The original liveness-limited wrapper run `run_f0eb05efc17509621affacc0251ed30a` remained queued at `CANONICAL_HYPOTHESES`; it is not reported as a pass. A pre-commit validation run `run_d0180eb1abf7c4d8ce73704fc637e1ef` passed on the fixed working tree but recorded the previous HEAD in its gate evidence, so it is not used as the final exact-commit acceptance. The final wrapper below is the new exact-commit result.

## Local runtime liveness closure

The durable state of `run_f0eb05efc17509621affacc0251ed30a` showed the resume command `SUCCEEDED`, followed by a newly enqueued `CANONICAL_HYPOTHESES` job with `status=QUEUED`, `delivery_count=0`, and no stage attempt. The code path confirmed the cause: `LocalProductExecutionSubmission._wake()` discarded a wake arriving while the bounded pump thread was still alive; the thread then returned without replaying the newly queued command.

The repair adds a lock-protected pending-wake flag. The existing worker thread drains one additional bounded pump whenever a wake arrives during the active pump, while close/shutdown and the existing worker, lease, review, planner, compiler, materializer, and G6 boundaries remain unchanged. No timeout was increased and no stage was manually advanced.

The deterministic regression reproduced the old behavior as `pump_calls=1` and now observes `pump_calls=2`. The committed regression is `test_local_submission_replays_wakeup_arriving_during_active_pump`.

## Telemetry product acceptance

The independent oracle is `tests/product_acceptance/oracle/telemetry_v1_truth.yml`. Product runtime code does not import or read it; the acceptance test loads it only after the run and materialization are complete.

The final exact-commit wrapper completed with `1 passed` in `245.97s`. The replay inspected the actual local DuckDB materialization read-only and verified:

- exact tables: `dim_date`, `dim_device`, `dim_location`, `fact_device_reading`;
- dimensions: 3 devices, 2 locations, and 2 dates;
- 3 fact rows at distinct `reading_id` grain;
- joined fact values: `R-001=10.5` and `R-002=11.0` on `2026-02-01`, `R-003=9.5` on `2026-02-02`;
- independent truth records: 9 total, with device/location/reading counts `3/2/4`;
- independent truth facts: 3, including `R-001`, `R-002`, and `R-003`;
- same-name devices remain distinct: `D-001` and `D-002`;
- policy binding, execution-plan binding, compiler/materializer provenance, and relationship references.

The independent oracle supports the core counts, maxima, date-scoped maxima, quarantined reading ID, and same-name device IDs. Accounting expectations are checked against the product-generated source-truth manifest, not misrepresented as oracle fields.

## Quarantine and accounting

Both `SOURCE_TO_CANONICAL` and `CANONICAL_TO_ANALYTICAL` accounting scopes were persisted with 9 inputs and 9 entries. `R-004` is `EMITTED_DIRECT` at the source boundary. Its unresolved `D-404` relationship is `QUARANTINED` at the canonical-to-analytical boundary, with no analytical output. The replay compared these persisted expectations with the product truth/accounting manifest.

## Negative controls

The focused negative-control suite passed `19` tests and covers actual invalid-state injection, including:

- unknown/unregistered policy and changed fingerprint rejection;
- telemetry policy paired with order planning or review context rejection;
- invalid source role and analytical measure/grain contracts;
- accepted planning without the review boundary;
- evidence without a selected fusion subject;
- same-name device merge without identity evidence;
- deferred unsupported `energy_consumption` semantics;
- independent-oracle read guard;
- incomplete source accounting;
- cross-policy identity/review contamination.

The static source-isolation scan remains supplementary only. The real local run and the persisted negative controls provide the primary evidence; no synthetic stage-success record is being used as the telemetry acceptance result. The fixture stage-publish test is explicitly test-only provenance coverage.

## Review and materialization integrity

The final durable run persisted 28 review entries, and stage handlers consume the existing accepted-review boundary rather than relying only on in-memory decisions. The telemetry path has no direct DuckDB materialization bypass: it supplies typed inputs to the existing analytical planner/compiler, and the shared `MaterializationService(DuckDBMaterializer)` performs materialization. The test's read-only DuckDB connection is post-run inspection, not product materialization.

## Prompt02 regression

`tests/product_acceptance/test_multi_source_v1.py`: `3 passed, 1 skipped`. The live multi-source test was skipped because local PostgreSQL was not configured. This is not a fresh four-source regression on the current Prompt03 branch. Prompt02 itself is historically accepted by the exact commit/run receipt recorded above.

## Local validation

- `python -m compileall`: PASS for the changed source and test scopes.
- Focused liveness regression plus shutdown guard: `2 passed`.
- Worker/lease/review integration suite: `38 passed`.
- Prompt03 controls, policy extension, and Prompt02 focused regression: `22 passed, 1 skipped, 2 warnings`; the skipped test required local PostgreSQL.
- Security suite: `57 passed`.
- Scoped unit/contract/security suite excluding the historical Step18 runtime test: `371 passed, 3 warnings`.
- Final exact-commit telemetry wrapper: `1 passed, 12 warnings` in `245.97s`.
- Independent replay of `run_b873...`: PASS; output verified 24 jobs, 28 reviews, G6 PASS, 3 facts, 9 truth records, both accounting scopes, R-004 quarantine, and oracle maxima.
- The full broad suite was not upgraded to PASS: the untouched historical `tests/unit/test_step18_v4_integrity.py` path timed out after 120 seconds in the local provider/worker environment.
- `git diff --check`: PASS.

## Scope and limitations

Only the local submission liveness boundary, its focused regression, Prompt03 acceptance tests, and product-completion status/evidence/historical handoff documents were changed. CI/PR was intentionally not inspected or triggered. This result does not claim production deployment, external-provider acceptance, a fresh Prompt02 four-source run on the current branch, overall V1 completion, Prompt04, or Step42.

The recovery branch, draft PR, `docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base.rar`, and `tests/quality_unit_artifacts/` remain preserved and outside the staged scope.
