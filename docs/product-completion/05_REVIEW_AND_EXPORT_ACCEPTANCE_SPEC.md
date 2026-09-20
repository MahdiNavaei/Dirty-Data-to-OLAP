# Review and Export Acceptance Specification

Status: `SPECIFICATION ONLY — NOT YET EXECUTED`

## Review contract

Every consequential decision must be attached to a run, checkpoint, subject
artifact ID, subject content hash, compatible context assertion, principal,
rationale, expected revision, and idempotency key. A stale subject or revision
must be rejected. Review records are append-only; invalidation creates a new
durable state and never rewrites history.

The applicable action set is:

| Action | Meaning | Required behavior |
|---|---|---|
| `ACCEPT` | accept the presented evidence/hypothesis | advances the compatible stage only |
| `REJECT` | reject the subject | blocks or routes to an explicit terminal disposition |
| `OVERRIDE` | replace a candidate with a reviewed value | requires structured override payload and evidence |
| `LABEL` | add a domain-reviewed label | retains the original candidate and audit trail |
| `LOCK` | freeze a reviewed decision for downstream reuse | rejects incompatible mutations until invalidated |
| `DEFER` | leave the subject unresolved | keeps the run review-blocked or explicitly partial |

The action is not available merely because a client sends a string. Server-side
checkpoint policy must decide which actions are valid for the subject type,
principal, stage, and current state. The action and its effect must be visible
in the review record and summary.

Required review subjects include, when selected: evidence/schema candidates,
entity memberships or clusters, canonical model, analytical plan, and
materialization/output plan. No review action may bypass a failed integrity,
authorization, or accounting check.

## Current review verdict

`PARTIAL`. The current transport uses hash/revision/idempotency protections and
exposes `ACCEPTED`, `REJECTED`, and `DEFERRED`. The browser client submits
`ACCEPTED` only and the visible workflow is four checkpoint accept/resume
cycles. Override, label, and lock are not exposed as an accepted product UI/API
action set. The current product path also does not produce multi-source review
subjects.

## Safe output package

After a run is terminal `SUCCEEDED`, all required reviews are complete, the
materialization is usable, and G6 validation is PASS/eligible, the product may
publish a run-bound output package with this manifest:

```text
package_schema_version
run_id / project_id / configuration_fingerprint / policy_version
source manifest: logical source IDs, source types, snapshot IDs, row counts,
  source fingerprints, read-only status
profiling summary: artifact IDs, hashes, completeness, boundedness
relationship and schema evidence: reviewed decision IDs, evidence refs,
  source scopes, confidence/score semantics, dispositions
canonical summary: model ID, entity types, membership/accounting counts,
  source-record disposition counts, lineage refs
analytical summary: fact IDs, dimension IDs, grain IDs, measure IDs and units
compiled plan summary: plan hash and safe generated-SQL artifact hash
target manifest: DuckDB target identity, table names, row counts, schema hashes
validation: G6 status, check counts, reconciliation summaries, report hash
artifact manifest: immutable artifact IDs, content hashes, media types,
  provenance and retention classes
```

The default package is aggregate/metadata-only. It must not include credentials,
host filesystem paths, arbitrary SQL execution, or raw source rows. If a raw
artifact is ever downloadable, it requires an explicit restricted artifact
classification, project-scoped authorization, audit event, bounded streaming,
and a documented retention policy. A generic artifact-content route must not
be treated as a safe product export by default.

The package must be deterministic for the same run and policy. Its manifest
must be content-addressed and verifiable. Export must be denied for runs that
are failed, review-blocked, unvalidated, cross-run, or not owned by the caller.

## Required negative tests

- reject export before G6;
- reject export after a review is invalidated;
- reject an artifact ID from a different run/project;
- reject a stale review hash/revision and preserve the prior record;
- reject an unauthorized override/lock;
- prove package output contains no credentials, host paths, or unapproved raw
  rows;
- mutate an artifact after manifest creation and fail verification;
- prove the browser cannot claim “ready” from a summary alone when the target
  or validation artifact is missing.

## Current output verdict

`PARTIAL — INTERNAL VALIDATED ARTIFACTS AND SAFE SUMMARY EXIST; COMPLETE
PRODUCT EXPORT DOES NOT`. Prompt 05 owns the implementation and focused tests.

