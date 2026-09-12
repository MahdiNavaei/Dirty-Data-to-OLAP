# V1 User Journey

## Purpose and boundary

The journey helps a reviewer move from a selected source snapshot to a validated analytical output while preserving the distinction between observation, hypothesis, decision, accepted semantics, materialization, and validation. A reviewer is never asked to infer business truth from a score or to approve an unbounded batch of high-impact changes.

The UI that eventually implements this contract must expose run, snapshot, content hash, schema fingerprint, scope, provenance, and downstream consequence at the point of decision. Synthetic examples may be used in documentation and tests; raw PII must not appear in URLs, navigation labels, audit text, fixtures, or previews.

## Journey map

| Stage | Reviewer question | Review object and evidence | Safe completion condition | If unresolved |
|---|---|---|---|---|
| Projects / Sources | What source and snapshot am I examining? | source ID, snapshot ID, table, observation mode, row-count semantics, privacy decision, source provenance | selected snapshot is identified and exposure is allowed | `PRIVACY_BLOCKED`, `SOURCE_UNAVAILABLE`, or `REVIEW_REQUIRED` |
| Runs / Snapshots | What did this run actually observe? | run ID, source/schema fingerprints, table observation status, content hash, full vs bounded scope | scope and freshness are visible before findings | `INCOMPLETE`, `STALE`, or retryable failure |
| Data condition | What was observed, where, and how measured? | quality issue, dimension, rule, observation scope, measurement semantics, affected count, evidence, repair proposal | each issue has a bounded subject and an explicit evidence strength | no finding is treated as a clean result |
| Relationships | Is this relationship a supported hypothesis? | `RelationshipDecision`, endpoints, cardinality, raw/normalized score semantics, supporting and contradicting refs | human decision is recorded at `REVIEW_EVIDENCE_DECISIONS` | rejected/deferred/incomplete hypotheses do not flow to canonicalization |
| Schema mappings | Does this source field map to another source field? | `SemanticMappingDecision`, both source/column IDs, evidence, conflicts, missing/unavailable providers | the cross-source hypothesis is accepted or remains review-required | no silent mapping or score-as-probability interpretation |
| Identity / ER | Which records may belong to the same identity? | canonical identity proposal, ER requirement/result, membership edges, evidence and conflicts | compatible identity review is accepted; required ER is complete | no automatic merge; a cluster is not a canonical ID |
| Canonical | What canonical entity/event semantics are authorized? | canonical model, survivorship/conflict references, source record lineage, review compatibility | accepted canonical semantics are distinct from evidence and from the later analytical plan | preserve prior artifact and invalidate/re-review on change |
| Analytical / OLAP | What is the grain and what does each measure mean? | fact/dimension/grain/measure specs, aggregation class, unit/currency semantics, domain assertions | `REVIEW_ANALYTICAL_PLAN` accepts an exact plan/spec package | ambiguous grain or non-additive measure remains blocked/review-required |
| Materialization | What exact compiled target may run? | compiled plan, generated SQL hash, target fingerprint, controlled target and materialization review | `REVIEW_MATERIALIZATION_PLAN` accepts the exact compiled subject | target change or rejected review prevents execution |
| Validation | Does the output reconcile with independent truth? | typed `ValidationReport`, required checks, accounting, grain, lineage, discrepancies | blocking checks derive `PASS`; validation failure cannot be promoted by a reviewer | `FAIL`, `REVIEW_REQUIRED`, `NOT_EVALUATED`, or `NOT_APPLICABLE` remains visible |

## Decision moment

Every decision view answers, in this order:

1. **Subject:** what exact object, endpoint, record set, plan, or compiled target is under review?
2. **Meaning:** is this an observation, hypothesis, human decision, accepted semantic, materialized output, or validation result?
3. **Evidence:** what supports it, what contradicts it, what is missing, and what provider states apply?
4. **Scope:** full snapshot, bounded sample, observed subset, or an explicitly unmeasured scope?
5. **Consequence:** what downstream stage is enabled, blocked, or invalidated?
6. **Action:** accept, reject, defer, label, lock, retry, or request a new run, with an audit rationale.

The reviewer must be able to leave without converting uncertainty into approval. Closing a view is not acceptance.

## Safe completion language

Use “accepted for this exact subject and scope” rather than “true” or “verified” for review decisions. Use “validation passed for the required checks” rather than “the data is correct” unless the project’s explicit G6 contract supports that claim. Use “sample observation” and “full snapshot observation” as different labels.
