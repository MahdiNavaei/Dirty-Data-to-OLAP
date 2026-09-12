# Review Workflows

## Common review states

The project-owned `ReviewDecision` statuses are `ACCEPTED`, `REJECTED`, `DEFERRED`, `SKIPPED`, and `INVALIDATED`. The UI also displays lifecycle and evidence states such as `REVIEW_REQUIRED`, `FAILED`, `INCOMPLETE`, `STALE`, and `SUPERSEDED`; these are not interchangeable with acceptance.

Only `ACCEPTED` and an explicitly authorized, compatible `SKIPPED` decision satisfy a downstream review guard. `REJECTED`, `DEFERRED`, `INVALIDATED`, and a missing decision do not. A skip always shows policy ID, policy version, applicability fingerprint, scope, and reason.

## Four exact checkpoints

### `REVIEW_EVIDENCE_DECISIONS`

Subject stage: `EVIDENCE_FUSION`. Subjects include relationship decisions, schema mapping decisions, repair proposals, and conflicts. Show both endpoints or the exact issue/repair target, evidence ledger, score semantics, source/schema scope, and downstream `CANONICAL_HYPOTHESES` consequence. A high uncalibrated score is a ranking/decision signal, never a probability.

### `REVIEW_CANONICAL_IDENTITY`

Subject stage: `CANONICAL_IDENTITY_PREPARATION`, with required hypothesis and conditional ER result context. Show proposed identity type, source record membership, authorized edges, ER-required status, ER result completeness/semantic hash, survivorship/conflicts, and source provenance. An entity cluster remains evidence; it does not become a canonical ID without the exact review and finalization contract.

### `REVIEW_ANALYTICAL_PLAN`

Subject stage: `ANALYTICAL_PLANNING`. Show the exact plan and child spec package: facts, dimensions, grain key columns and validation fingerprint, measure aggregation class, aggregation rule, unit/currency semantics, unresolved concepts, domain assertions, and canonical/input bindings. A `NON_ADDITIVE` measure must not be presented with a default sum action.

### `REVIEW_MATERIALIZATION_PLAN`

Subject stage: `COMPILATION`. Show compiled plan ID/hash, generated SQL hash, target configuration fingerprint, guarded target, no-source-write boundary, and exact consequence of approval. A target or SQL change invalidates the compatible decision; it does not mutate the prior approval.

## Action semantics

| Action | Required input | Visible consequence |
|---|---|---|
| Accept | exact subject context, rationale, actor and timestamp | satisfies only the compatible guarded stage |
| Reject | rationale and, when useful, remediation hint | blocks downstream use; upstream revision creates a new subject/decision |
| Defer | rationale and follow-up condition | remains unresolved and cannot satisfy a guard |
| Skip | versioned policy authorization, scope and applicability fingerprint | records a policy decision; never implies evidence was observed |
| Label | label meaning, scope and actor | adds an assertion/annotation; does not authorize a stage |
| Lock | exact object and reason | prevents casual mutation; does not turn a hypothesis into truth |
| Retry / rerun | retryable failure and unchanged or new binding | creates a new run/artifact; prior failure remains in history |

Actions that merge identities, publish materialization, or expose sensitive data require a consequence dialog. There is no generic “approve all” control.

## Re-review and invalidation

When `subject_content_hash`, schema fingerprint, policy version, domain assertion scope, model version, target fingerprint, or applicable semantic inputs change, the old decision is preserved and marked stale/invalidated or superseded according to the contract. The UI must link the replacement decision and say which downstream artifacts are no longer consumable.
