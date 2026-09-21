# Original Product Gap Audit

## Audit status

This document is the Prompt 01 audit deliverable. The audit is complete, but
the original V1 product is not accepted as complete.

| Field | Verified value |
|---|---|
| Repository | `MahdiNavaei/Dirty-Data-to-OLAP` |
| Audited HEAD | `32b5a0d6e613044d17e4381f1b7e50f8683b52fa` |
| Branch | `main` |
| Remote baseline | `origin/main` matched HEAD at audit start |
| Workflow state | Steps 01-41 terminal; no Step42 started |
| Prompt 01 state | `PASS` — audit artifacts created |
| Original V1 product acceptance | `PENDING` |
| Product acceptance state | See `07_PRODUCT_ACCEPTANCE_STATE.yml` |

The terminal G0-G15 workflow result is evidence that the specialist sequence
and release documentation gates passed. It is not evidence that the original
multi-source, generic, review-complete product contract has been accepted.

## Original product definition

The original V1 product is a bounded, evidence-backed data-to-OLAP workflow:

> one or more heterogeneous tabular sources -> source-faithful snapshots and
> measured evidence -> reviewed canonical hypotheses and identity -> explicit
> fact/dimension analytical model -> validated DuckDB target and safe output
> projection.

The product must support source discovery, profiling, candidate keys and
relationships, cross-source schema matching, quality diagnosis, optional
entity resolution, canonical modeling, explicit analytical grain and
measures, DuckDB materialization, validation/reconciliation, and human review.
The source rows remain preserved and every boundary must have explicit record
accounting. A score is evidence, not a probability, and an ER cluster is not
canonical truth without a reviewed decision.

The required V1 source contract names PostgreSQL, MySQL/MariaDB, SQL Server,
CSV, and Parquet, with SQLite as a local reference and XLSX optional. Oracle
is deferred. The V1 definition of done requires at least three source systems
plus one file source in a reproducible acceptance run, source profiling,
measured relationship/schema/identity evidence, canonical lineage, at least
one fact and three dimensions, explicit grain and measures, DuckDB, validation
and reconciliation, no unexplained record loss, and clean-clone reproduction.

## Verified repository reality

### What is genuinely implemented and evidenced

- The repository has typed source, snapshot, profiling, dependency, quality,
  evidence-fusion, canonical, analytical, materialization, semantic, and
  validation contracts and services.
- The accepted Step29 composition root executes a real local CSV product path
  through project-owned services and a real DuckDB target. The focused
  integration test proves four input rows, four accepted review checkpoints,
  `dim_date`, `dim_order`, and `fact_orders`, and G6 PASS.
- The current API is run-scoped, idempotent, artifact-bound, and protects
  review subjects with content hashes and revision checks. The runtime does
  not fabricate an identity when a duplicate order key reaches the dependency
  boundary; the negative Step29 test fails at that boundary.
- The wider adapter and compatibility estate contains file adapters for CSV,
  Parquet, and optional XLSX, a SQL adapter boundary for SQLite/PostgreSQL/
  MySQL/MariaDB/SQL Server, and isolated provider evidence for matching and
  entity resolution. Step32 and Step37 reports document the scope of those
  checks.
- Release and terminal workflow documentation is materially careful about
  local/reference limits, deferred Oracle, optional provider availability,
  uncalibrated scoring, and the distinction between adapter support and the
  browser product path.

### What the accepted product path actually does

`src/dirty_data_to_olap/application/product_runtime.py` is the relevant
composition root. It registers only:

```text
file_source -> FileSourceAdapter(SourceType.CSV)
ProductSourceService -> one managed CSV selection per run
OrderProductPolicy -> config/product/order_v1.json
worker pool -> one local worker
```

The product handlers execute the order-specific stage sequence. The runtime
does not register product handlers for the selected `SCHEMA_MATCHING` or
`ENTITY_RESOLUTION` stages. Its evidence and hypothesis stages set the
cross-source scope to false and use source-local event identity. Its analytical
input, truth/accounting, quality policy, and validation policy are all order
policy implementations.

The API and browser path confirm the same boundary:

- `POST /api/v1/sources/import` accepts one bounded CSV upload of at most 5 MB.
- The CSV contract requires exactly `order_id`, `customer_id`,
  `customer_id_ref`, `order_date`, `quantity`, and `unit_price`.
- A run has one `source-selection`; a second different binding is rejected.
- The browser sends `cross_source_mapping_requested: false` and
  `entity_resolution_requested: false`.
- The browser review control sends `ACCEPTED` only. The transport exposes
  `ACCEPTED`, `REJECTED`, and `DEFERRED`; it does not expose the broader
  override/label/lock actions required by the original review contract.
- The product summary exposes a safe status projection and row counts. There
  is no product-level, run-bound export package endpoint that publishes the
  complete catalog/profiling/evidence/lineage/SQL/validation/target package.

## Gap register

| Priority | Gap | Why it matters | Required closure |
|---|---|---|---|
| P0 | No accepted multi-source product composition | The central V1 value proposition is not exercised end to end | Prompt 02 |
| P0 | Product runtime is order-specific | A second valid domain cannot use the product without changing source-code policy | Prompt 03 |
| P0 | Product path does not execute cross-source schema matching or ER | Isolated provider tests do not prove product composition, review, lineage, or accounting | Prompt 02 |
| P0 | V1 analytical DoD is not met by the product path | Current target has two named dimensions (`dim_order`, `dim_date`), not fact plus three dimensions | Prompt 03 |
| P1 | Review actions/UI are incomplete | Accept-only UI cannot reject, override, label, or lock an applicable decision | Prompt 04 |
| P1 | Full output/export contract is absent | A summary and artifact metadata are not the required deliverable package | Prompt 05 |
| P1 | Independent multi-source oracle/negative controls are absent from the product path | Existing inference fixtures and the Step29 CSV test are not the original acceptance scenario | Prompt 02 and 06 |
| P1 | Adapter support is not equivalent to product support | SQL and file compatibility evidence is isolated from the browser/runtime composition root | Prompt 02 |
| P2 | Capacity and deployment remain local/reference | This is a declared product limitation, not a reason to claim production readiness | Prompt 06 records the bounded claim |
| P2 | Public legal packaging still has the documented LICENSE/NOTICE decision | Open-source reuse cannot be declared fully closed until the repository decision is explicit | Prompt 06 or an explicit release decision |

## Requirement-level verdict

The normalized requirement mapping is in
`02_REQUIREMENTS_IMPLEMENTATION_TRACEABILITY.csv`. The important distinction
is between these statuses:

- `PASS_BOUNDED`: proven for the named narrow scope, not the whole V1 claim.
- `PARTIAL`: a real slice exists but the original acceptance scope is wider.
- `ISOLATED_NOT_PRODUCT_WIRED`: a contract, adapter, or specialist test exists
  without accepted product composition.
- `MISSING`: the user-facing or acceptance contract is not present.
- `DEFERRED`: explicitly outside current V1 scope, such as Oracle.

The audit does not upgrade isolated tests, stale reports, fixtures, or
specialist-gate PASS results into product acceptance.

## Audit-time validation note

The repository's recorded exact-head CI run `35520372671` is retained as
historical CI evidence for the accepted Step41 baseline. A fresh local run of
the focused Step29 file was also attempted during this audit:

```text
python -m pytest tests/integration/test_step29_product_path.py -q
result: 1 failed, 2 passed
```

The first run had no `DESBORDANTE_PROVIDER_IMAGE` in the local process and
failed at the expected dependency capability boundary. A second run supplied
the existing local provider image. The provider then started, but the happy
path timed out after an unhandled worker-thread
`ConcurrencyConflictError: heartbeat rejected by the current lease fence` from
the SQLite control store; the two negative/contract tests passed. This is
recorded as a current local-environment/runtime verification limitation, not
silently converted to PASS and not repaired in Prompt 01. It does not change
the broader conclusion: even a passing Step29 CSV test would not establish the
multi-source or generic V1 product contract.

## Multi-source verdict

`NOT_ACCEPTED — PRODUCT PATH SINGLE-SOURCE`.

The repository has the building blocks and isolated provider evidence for a
multi-source implementation, but no executed product run currently proves
three heterogeneous source systems plus a file source through discovery,
snapshot, profiling, schema matching, evidence fusion, review, canonical
identity, analytical planning, DuckDB materialization, and reconciliation.
The executable acceptance scenario is specified in
`03_MULTI_SOURCE_ACCEPTANCE_SPEC.md`; it is a Prompt 02 deliverable, not a
claim about the current checkout.

## Generic product verdict

`NOT_ACCEPTED — ORDER-SPECIFIC PRODUCT POLICY`.

The current product contract is an honest and useful order demo, but the
runtime imports `OrderProductPolicy`, `build_order_dataset`, and
`build_order_truth_and_accounting`, and the browser describes the required
order columns. A generic domain-policy boundary must be completed and then
proved with a non-order telemetry/device scenario while retaining the order
plugin as a compatibility test.

## Prompt 02 implementation status (2026-09-21)

The historical audit conclusion above remains unchanged. Prompt 02 has since
added an additive source-set boundary, a real four-source acceptance harness,
an independent oracle contract, negative controls, and a CI job. The required
heterogeneous live run has not completed successfully in this checkout and no
exact-commit CI receipt exists yet, so the original product acceptance remains
pending and the multi-source capability is not accepted as PASS.

## Human-review verdict

`PARTIAL — FOUR CHECKPOINT ACCEPTANCE IS REAL; ORIGINAL ACTION CONTRACT IS NOT
COMPLETE`.

Step29 and Step31 prove four typed checkpoints in the real local browser/API
path. The underlying decisions are subject-artifact and content-hash bound.
However, the API enum is only `ACCEPTED`, `REJECTED`, and `DEFERRED`, and the
browser currently renders only an Accept action with rationale. The required
reject/override/label/lock semantics, applicable checkpoint rules, and
reviewable multi-source subjects are not exposed as an accepted product
workflow.

## Output/export verdict

`PARTIAL — VALIDATED LOCAL TARGET AND SAFE SUMMARY, NO COMPLETE PRODUCT EXPORT`.

The current path produces typed internal artifacts and a validated local DuckDB
materialization. The API has artifact metadata/content routes and the browser
shows validated table names and row counts, but it does not publish a complete,
run-bound V1 output package containing catalog, profiling, relationships,
schema/identity evidence, canonical lineage, analytical plan/SQL, validation,
and target manifest with safe access controls. Prompt 05 must define and
implement the minimum safe export projection without exposing arbitrary host
paths, credentials, or unrestricted raw rows.

## Acceptance boundary and next action

Prompt 01 is complete when these audit artifacts are committed. Product
acceptance remains pending. The next authorized work is Prompt 02 only:
execute the specified multi-source acceptance scenario by composing the
existing services and adapters; do not begin generic-product, review-UI,
export, or Step42 implementation in this prompt.
