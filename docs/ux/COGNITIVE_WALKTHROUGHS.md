# Cognitive Walkthroughs

These are design-time walkthroughs against the current contracts, not user research or usability study results. Each case asks whether a first-time reviewer can identify the subject, understand the evidence and uncertainty, predict the action consequence, and recover safely.

| Case | Expected walkthrough result |
|---|---|
| Strong relationship | Endpoint subject, scope, supporting refs, uncalibrated score semantics, and `REVIEW_EVIDENCE_DECISIONS` consequence are visible; accept is an exact decision, not automatic truth. |
| Contradictory relationship | Contradicting refs and `CONFLICTED` band are prominent; reject/defer is available; conflict cannot be hidden by sorting or collapsed summary. |
| High matcher score not calibrated | Copy says uncalibrated decision/ranking score; no probability or percentage wording appears; acceptance requires human rationale. |
| Human ER decision | Entity cluster, ER result, authorized edges, actor, rationale, and canonical impact are separated; source records remain preserved. |
| Canonical conflict | Differing values, authority/temporal conflict, survivorship policy and provenance are shown; unresolved conflict blocks finalization. |
| OLAP grain / nonadditive measure | Grain key, duplicate check, aggregation class and unit are visible; non-additive measure has no default SUM path. |
| Stale hash | Changed content/schema fingerprint is explained; old decision is preserved as stale/invalidated and a re-review path is clear. |
| Failed G6 | Required failed check, expected/observed, discrepancy, evidence and downstream gate effect are visible; no promote-to-PASS action exists. |
| Unavailable / incomplete provider | Unavailable, failed and incomplete are distinct; the user sees configuration/retry/scope options and no false negative inference. |
| Safe and unsafe bulk review | Bounded low-impact selection shows preview/exclusions/audit confirmation; identity merge or high-impact selection is excluded from mass approval and requires item-level review. |

## Acceptance questions

For each case, the reviewer must be able to answer without hidden knowledge:

- What exact subject and context am I deciding about?
- What is observed versus inferred versus human-authorized?
- Which evidence is missing, conflicting, sampled, stale, failed, or privacy-blocked?
- What does the action enable or invalidate?
- Can I recover without deleting source data or losing the prior decision?
