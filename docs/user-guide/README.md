# User guide

## The real product journey

The implemented product path is:

```text
managed CSV import
  -> source binding
  -> server-owned execution plan
  -> durable worker stages
  -> review checkpoints
  -> canonical identity
  -> analytical / OLAP plan
  -> materialization
  -> G6 validation and output projection
```

The browser uploads a bounded CSV through `POST /api/v1/sources/import`, binds
the registered source to a run, and requests a server-owned plan. The server
derives review subjects from verified artifacts; the browser cannot submit a
complete authoritative plan or write review rows directly.

## Review checkpoints

The run may pause at these typed checkpoints:

- `REVIEW_EVIDENCE_DECISIONS`
- `REVIEW_CANONICAL_IDENTITY`
- `REVIEW_ANALYTICAL_PLAN`
- `REVIEW_MATERIALIZATION_PLAN`

Each accepted decision authorizes the next guarded stage. A rationale is
durable and revision-bound. Stale or incompatible decisions are rejected.
Each review decision is bound to its exact subject and scope; it is not a
generic truth flag.

## What the user sees

The workspace presents source condition, evidence candidates, canonical
identity state, analytical facts/dimensions/measures, materialization state,
and validation/G6 state. Raw source rows, filesystem locators, generated SQL,
target paths, credentials, and generic artifact payloads are deliberately not
part of the browser projection.

Output is shown as consumable only when the run is `SUCCEEDED`, materialization
has completed, G6 is `PASS` and eligible, and the bound validation evidence is
valid. A failed, cancelled, incomplete, or review-paused run is not presented
as success.

## Full walkthrough evidence

The accepted Step29 real-pipeline receipt records a four-row CSV fixture,
durable pause/resume across all four checkpoints, a terminal `SUCCEEDED` run,
visible G6 PASS/eligibility, validated OLAP output, and zero browser console
errors. This is executed local/CI reference evidence, not a production
deployment or capacity result.

## The smaller developer demo

The [Step40 demo](../getting-started/README.md#what-the-demo-proves) is a
control-plane smoke. Do not describe it as a substitute for this full product
journey.
