# Multi-Source V1 Acceptance Specification

Status: `SPECIFICATION_ONLY — NOT YET EXECUTED`

This is the executable acceptance contract for Prompt 02. It is deliberately
separate from the runtime and from the truth oracle. The current checkout does
not claim that this scenario passes.

## Objective

Prove that the accepted project-owned pipeline can execute one run over at
least three heterogeneous source systems plus one file source and produce an
evidence-backed, human-reviewed canonical and analytical model. The run must
use the real discovery, snapshot, profiling, relationship, schema-matching,
quality, evidence-fusion, conditional identity, canonical, planner, compiler,
DuckDB, semantic, and validation services. A pre-authored output artifact or a
test-only fake service is not an acceptance implementation.

## Source manifest

Prompt 02 must create a versioned, disposable fixture manifest. The required
main scenario is:

| Source | Required engine | Fixture role | Deliberate variation |
|---|---|---|---|
| `crm_postgres` | PostgreSQL | customer registry | `crm_customer_id`, `full_name`, `email`, `phone` |
| `erp_mysql` | MySQL or MariaDB | customer/accounts registry | `account_no`, `customer_name`, `email_addr`, `phone_e164` |
| `sales_sqlserver` | SQL Server | order/event facts | `ticket_id`, `buyer_ref`, `booked_on`, `units`, `unit_price` |
| `legacy_csv` | CSV | legacy order/event facts | `sale_key`, `client_code`, `sale_day`, `qty`, `price_each` |

Parquet must remain covered by the existing source compatibility contract and
may be the file source in an additional matrix case. The main acceptance
minimum is three live SQL systems plus one file source. SQLite remains the
local reference path and Oracle remains deferred.

The fixture generator must publish only disposable local/CI credentials to
the test process. Credentials must never enter artifacts, logs, the oracle, or
the repository.

## Dirty-data cases

The manifest must include and label these cases without relying on hidden
expected output:

1. A renamed customer identifier and renamed date/quantity columns requiring
   schema matching.
2. A missing foreign-key declaration that is discoverable only from observed
   inclusion evidence.
3. An orphan `buyer_ref`/`client_code` that must receive an explicit terminal
   disposition; it may not disappear.
4. A duplicate candidate key in one source that must cause a review decision or
   a visible quarantine/failure, never fabricated identity.
5. A same-name, different-contact hard negative that must not be merged by
   name alone.
6. Null markers and a type-compatible but semantically false candidate.
7. A grain trap: source event rows must not be multiplied by a customer join.
8. A measure trap: `units`/`qty` may be additive only at the declared event
   grain. `unit_price` is preserved as source data but must not become revenue,
   GMV, or currency without an explicit reviewed contract.

## Independent truth oracle

The oracle must be a versioned fixture outside the runtime input directory,
for example:

```text
tests/product_acceptance/oracle/multi_source_v1_truth.yml
```

The runtime test process must not import, open, or pass this file to any
application service. The test harness loads it only after the run completes and
compares it to verified output artifacts. The oracle must contain:

- source snapshot identity and expected input row count per table;
- expected schema-match labels, including accepted and hard-negative pairs;
- expected relationship decisions and explicit orphan disposition;
- canonical customer memberships with source record references;
- event-level canonical identity and source-to-canonical disposition;
- analytical fact grain, dimension membership, and the quantity measure only;
- expected target table set and aggregate checks;
- no raw production secrets or unbounded data.

The oracle must use stable logical references, not implementation-generated
artifact IDs. The test compares artifact content and provenance to those
logical references.

## Required product run

The test must perform, through the supported product boundary or its approved
run command:

```text
register/select all four sources
create one run
prepare a server-owned plan with cross-source mapping requested
submit the run
poll durable stage/job state
resolve every required review checkpoint with explicit decisions
resume only after review
wait for terminal success
read verified run artifacts and target
compare to the independent oracle
```

The test must assert that actual source IDs, snapshot fingerprints, stage
attempts, provider metadata, review records, and output artifact references are
from the run under test. The plan must select schema matching and must select
entity resolution when the fixture's identity policy requires it.

## Acceptance invariants

The run passes only if all of the following are true:

- every selected source was discovered and snapshotted read-only;
- every source and table has a complete or explicitly bounded observation state;
- schema-match candidates cite both source scopes and are reviewed;
- every relationship and identity decision has evidence, subject identity,
  snapshot binding, and a durable review record;
- every input record has exactly one terminal disposition at each required
  accounting boundary: emitted, linked, quarantined, rejected with reason, or
  another contract-approved explicit disposition;
- canonical membership never silently drops or duplicates a source record;
- the hard-negative customer pair remains separate;
- the orphan is visible in accounting and is not joined into a fact silently;
- the fact grain is one source event, and the quantity aggregate agrees with the
  oracle at global and declared slices;
- the output has at least one fact and three dimensions, with explicit grain,
  measure, and unit semantics;
- DuckDB target tables, row counts, uniqueness, foreign-key/referential checks,
  lineage, and source-to-target reconciliation pass;
- output is not considered accepted until the required review and G6 checks
  pass.

## Negative controls

The acceptance suite must fail, not pass partially, when:

1. one selected source is unavailable;
2. a source file changes after registration or during snapshot;
3. the truth oracle is made unavailable to the application process;
4. a review decision is submitted with a stale subject hash or revision;
5. an input record has no terminal disposition;
6. a duplicate source key is forced into a canonical entity without review;
7. a pre-authored `CanonicalModel`, `AnalyticalPlan`, or `ValidationReport` is
   placed in the artifact store before execution;
8. the plan omits the required cross-source stage;
9. a measure is relabeled as revenue/GMV without the required domain assertion;
10. a second source is bound after the run has a different binding set.

## Execution and evidence commands

Prompt 02 should add a bounded test with a stable name such as:

```text
python -m pytest tests/product_acceptance/test_multi_source_v1.py -q
```

The CI job must start disposable PostgreSQL, MySQL/MariaDB, and SQL Server
services, publish a temporary CSV or Parquet fixture, run the test, retain
aggregate-only evidence, and destroy the services. The test must be skipped
with an explicit non-acceptance status when the required service matrix is not
available; a skip is not a product PASS.

## Current verdict

`NOT EXECUTED`. Existing Step29 CSV G7/G6 evidence, Step32 compatibility
evidence, provider tests, and benchmark fixtures are useful prerequisites but
do not satisfy this specification until they are composed in one product run.

