# Prompt03 Generic Product Acceptance

## Result

`PASS_LOCAL_TELEMETRY_BOUNDED` for the generic telemetry/device composition path. This is local acceptance evidence, not an overall V1 PASS and not external-CI confirmation.

Prompt03 was executed on branch `codex/prompt03-generic-product` from starting HEAD `61276c68d0393d44636782632ebe646b6f7c77db`. The tested code commit is `2762d1eed7add2e06e583a31bf4a2347b2203aef`. Prompt04/Step42 was not started. No GitHub Actions workflow was started or modified.

The accepted Prompt02 base remains `7786ea301e3482b374e57447974b73d9a0176f8c`; its historical live-acceptance blocker is preserved and is not upgraded by this local result.

## Generic policy boundary

- The registry resolves only exact, bounded product policy identities: product ID, version, and content fingerprint.
- `ProductDomainPolicy` is the typed capability boundary for product identity, source roles, logical values, dependency/relationship semantics, identity membership, analytical planning inputs, source records, truth/accounting, and validation policy.
- The shared runtime owns durable orchestration, evidence fusion, persisted review, canonicalization, planning, compilation, materialization, semantic modeling, and G6 validation.
- `MultiSourceProductService` requires an explicitly bound policy; it no longer defaults to `OrderProductPolicy`.
- Shared orchestration has no direct `product_policy.product_id` or `policy.product_id` dispatch branches. Product-specific behavior is delegated through the policy contract.
- The telemetry policy uses the same durable worker, stage registry, review boundary, canonical finalization, analytical planner, compiler, DuckDB materializer, semantic layer, and G6 validator as the accepted order path.

The test-only `ConformingFixturePolicy` proves registry extension, exact binding, service delegation, artifact provenance, and rejection of an incompatible execution-plan fingerprint. It is not claimed as a third accepted business domain.

## Durable local pipeline evidence

Primary durable run: `run_c99d8fea7e44ab363b37a952c5d3eea9`.

- `24/24` durable jobs succeeded.
- `28` review-history entries were persisted.
- `G6_DATA_CORRECTNESS=PASS`, `eligible=true`.
- Provider: local Docker image `dirty-data-to-olap-desbordante-step37:local`.
- Policy: `telemetry`, version `telemetry-product-v1`, fingerprint `baaecd58325f42c41187ee6ddadce83518175cf9e67925b06ef36dc347b3f42b`.
- Durable artifacts include source catalogs/snapshots, dependency results, schema/fusion/relationship/mapping artifacts, canonical proposal/model, analytical plan/specifications, compiled plan, generated SQL, target configuration, materialization, semantic model/validation, source truth, record accounting, and reconciliation.

The first corrected wrapper execution exposed and fixed an assertion defect in the acceptance test. A subsequent wrapper run (`run_f0eb05efc17509621affacc0251ed30a`) remained `RUNNING` at `CANONICAL_HYPOTHESES=QUEUED` after the local timeout because of provider/worker liveness. Therefore this document does not claim that the corrected pytest wrapper completed successfully. Acceptance evidence is the completed durable run above plus the independent post-run replay validation.

## Telemetry product acceptance

The independent oracle is `tests/product_acceptance/oracle/telemetry_v1_truth.yml`. Product runtime code does not import or read it; the acceptance test loads it only after the run and materialization are complete.

The replay inspected the actual local DuckDB materialization read-only and verified:

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

The durable run persisted 28 review entries, and stage handlers consume the existing accepted-review boundary rather than relying only on in-memory decisions. The telemetry path has no direct DuckDB materialization bypass: it supplies typed inputs to the existing analytical planner/compiler, and the shared `MaterializationService(DuckDBMaterializer)` performs materialization. The test's read-only DuckDB connection is post-run inspection, not product materialization.

## Prompt02 regression

`tests/product_acceptance/test_multi_source_v1.py`: `3 passed, 1 skipped`. The live multi-source test was skipped because local PostgreSQL was not configured. This is not a full four-source live PASS. Prompt02 remains historically `BLOCKED` in the product state document.

## Local validation

- `python -m compileall`: PASS for the changed source and test scopes.
- Focused Prompt03 negative controls and policy extension: `19 passed, 2 warnings`.
- Scoped unit/contract/security suite excluding the historical Step18 runtime test: `331 passed, 2 warnings`.
- Prompt02 regression: `3 passed, 1 skipped` because PostgreSQL was unavailable.
- Independent replay of `run_c99...`: PASS; output verified 24 jobs, 28 reviews, G6 PASS, 3 facts, 9 truth records, and both accounting scopes.
- The full broad suite was not upgraded to PASS: the untouched historical `tests/unit/test_step18_v4_integrity.py` path timed out after 120 seconds in the local provider/worker environment.
- `git diff --check`: PASS.

## Scope and limitations

Only Prompt03 source, contract/acceptance tests, and these product-completion documents were changed. CI/PR was intentionally not inspected or triggered. This result does not claim production deployment, external-provider acceptance, Prompt02 live four-source completion, overall V1 completion, Prompt04, or Step42.

The recovery branch, draft PR, `docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base.rar`, and `tests/quality_unit_artifacts/` remain preserved and outside the staged scope.
