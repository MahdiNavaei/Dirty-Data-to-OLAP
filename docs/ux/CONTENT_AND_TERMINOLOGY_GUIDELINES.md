# Content and Terminology Guidelines

## Canonical terms

| Use | Avoid | Reason |
|---|---|---|
| observation | truth, proof | an observation has a declared scope and method |
| hypothesis / candidate | match, confirmed relationship | inference is not an accepted semantic |
| uncalibrated decision score | probability, confidence percent | V1 scores are not calibrated probabilities |
| confidence band | probability band | bands are operational categories |
| supporting / contradicting evidence | good / bad evidence | makes direction and conflict explicit |
| missing / unavailable / failed / incomplete | no data | recovery differs by state |
| sample / bounded observation | dataset result | prevents sample-to-full overclaim |
| human review decision | human truth | decision records authorization and rationale |
| entity cluster | canonical entity | ER candidate membership needs authorization |
| canonical identity | merged row | source records remain preserved |
| grain | level of detail | the fact key and duplicate semantics matter |
| non-additive measure | totalable measure | prevents invalid aggregation |
| materialized output | published truth | output still requires validation and lineage |
| validation check | correctness proof | each check has scope and evidence |

## Copy rules

- Lead with subject and scope, then state and consequence.
- Put uncertainty next to the claim it qualifies.
- Name the actor and evidence producer separately.
- State why an action is unavailable instead of hiding it.
- Use “not evaluated” when no evaluation occurred; never use “pass” as a placeholder.
- Use absolute dates/times in audit detail and the exact content hash/fingerprint where freshness matters.
- Explain the next safe action: retry, broaden scope, resolve conflict, request authorization, or re-review.

## Privacy-safe examples

Use stable synthetic identifiers such as `src_demo`, `snap_demo_01`, `record_demo_0042`, and `entity_demo_07`. Never put email, phone, address, government ID, raw connection strings, private keys, or raw row values in examples, URL paths, event names, or audit summaries.
