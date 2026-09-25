# Product Completion Execution Plan

This plan follows Prompt 01. Prompt 02 has a historical exact-commit PASS;
Prompt 03 is the current bounded composition scope. The plan does not
authorize Prompt 04 or any later prompt.

## Completion sequence

| Prompt | Scope | Depends on | Exit evidence |
|---|---|---|---|
| 02 | Multi-source product composition and acceptance | Prompt 01 audit; existing adapter/service contracts | One real run over three SQL systems plus file source; independent oracle comparison; negative controls |
| 03 | Generic product policy and second domain | Prompt 02 source/run boundary | Order plugin and telemetry/device policy both pass through one orchestration path |
| 04 | Complete review contract/API/UI | Prompt 02 reviewed subjects; Prompt 03 generic subjects | Action matrix and UI/system tests for accept/reject/override/label/lock/defer |
| 05 | Safe validated output/export package | Prompt 02-04 artifact/review/output contracts | Run-bound export manifest/package, authorization and integrity tests |
| 06 | Independent final product acceptance | Prompts 02-05 | Clean clone, exact scenario, no pre-authored output, complete evidence, final acceptance state |

## Prompt 02 — Multi-source composition

Reuse the accepted discovery, snapshot, profiling, dependency, matching,
fusion, canonical, planner, compiler, materialization, semantic, and validation
services. Extend the composition boundary so one run owns a set of source
selections and source-scoped artifacts. Select conditional schema matching and
ER from the server-owned plan; do not hardcode a pass-through or create a
second pipeline. Implement the scenario and oracle in
`03_MULTI_SOURCE_ACCEPTANCE_SPEC.md`, with disposable SQL services in CI and
explicit no-oracle runtime controls.

Do not solve generic domain policy, review UI action expansion, or export in
this prompt except where a minimal shared contract is required to execute the
scenario.

### Prompt 02 current closure status

Prompt 02 is historically accepted for exact commit
`7786ea301e3482b374e57447974b73d9a0176f8c` by successful run
`36081128931`, with artifact `prompt02-acceptance-evidence-36081128931`
(`10844010695`). The receipt records all 16 jobs successful, real PostgreSQL,
MySQL, SQL Server and CSV participation, persisted reviews, DuckDB
materialization, G6, independent oracle comparison, and NC01-NC14 `14/14`.

The earlier failed run `35700636572` remains historical failure evidence, not
the current Prompt 02 verdict. The current Prompt 03 branch has not repeated
the full four-source regression locally; its focused result is `3 passed, 1
skipped` because the local database environment is unavailable. That limitation
is separate from the accepted Prompt 02 result.

## Prompt 03 — Generic product boundary

Extract a stable domain policy port/configuration from the current order
policy. Retain the order policy as one plugin and add the telemetry/device
policy. Move truth/accounting and analytical input construction behind that
policy boundary. Prove fact grain, dimensions, measure semantics, and
validation are policy artifacts, not order-specific branches.

## Prompt 04 — Review completion

Complete the server policy and product UI for the action matrix in
`05_REVIEW_AND_EXPORT_ACCEPTANCE_SPEC.md`. Keep hash/revision/idempotency and
artifact compatibility checks. Make review subjects and terminal dispositions
visible for both the order and second-domain paths. Do not allow UI state to
advance a stage without a durable server decision.

## Prompt 05 — Output/export

Add the minimum safe, run-bound package projection. Gate it on terminal success,
review completion, usable materialization, and G6. Exclude credentials, host
paths, arbitrary SQL execution, and raw rows by default. Add authorization,
cross-run, integrity, invalidation, and pre-G6 negative tests.

## Prompt 06 — Independent final acceptance

Run the multi-source and generic scenarios from a clean checkout with the
oracle isolated from the runtime. Re-run the order compatibility path, review
action matrix, export negative controls, repository scope guard, and all
required CI validators. Record exact commits, service versions, test commands,
artifact hashes, skips, and limitations. Only Prompt 06 may change product
acceptance state to PASS after all gates are evidenced.

## Cross-cutting guardrails

- Preserve `tests/quality_unit_artifacts/` exactly; do not read, stage, modify,
  or commit it.
- Preserve the untracked knowledge-base RAR exactly; do not stage it.
- Do not change the accepted Step41 terminal state or begin Step42.
- Do not claim a multi-source, generic, physical, production, or calibrated
  result from isolated provider or compatibility evidence.
- Preserve source read-only behavior, explicit accounting, independent truth,
  evidence semantics, and no invented business measures.
- Every prompt must add focused executable evidence and then inspect the diff
  and working tree before commit.
