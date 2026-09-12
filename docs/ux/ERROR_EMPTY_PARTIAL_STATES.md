# Error, Empty, Partial, and Recovery States

Every state has a visible label, meaning, scope, recovery action, and downstream consequence. States are not reduced to a green/red icon.

| State | Meaning | Primary recovery | Downstream rule |
|---|---|---|---|
| `NO_EVIDENCE` | no evidence was produced for this subject | configure/run the required producer or record a policy skip | no inference or acceptance from absence |
| `NO_CANDIDATES` | producer ran but found no candidates in scope | inspect scope/rules; do not manufacture a candidate | no candidate exists to approve |
| `CAPABILITY_UNAVAILABLE` | selected capability cannot run in this environment | choose supported capability or schedule later | remains unresolved |
| `PROVIDER_FAILED` | provider attempted and failed | inspect failure, retry if marked retryable | failure is not negative evidence |
| `TIMEOUT` | bounded execution exceeded its limit | retry with bounded scope or investigate | result is incomplete, not success |
| `INCOMPLETE` | required inputs/results are partial | complete missing inputs or narrow explicitly | cannot satisfy completeness guard |
| `PRIVACY_BLOCKED` | policy denied requested exposure/processing | use masked/aggregate/pseudonymized mode or request policy change | no raw exposure or derived approval |
| `REVIEW_REQUIRED` | human decision is required | open the exact review object | guarded stage remains blocked |
| `STALE` | subject no longer matches review context | compare hashes/fingerprints and re-review | old decision cannot authorize new subject |
| `INVALIDATED` | prior artifact/decision was explicitly invalidated | follow replacement link and review new artifact | prior result is not consumable |
| `SUPERSEDED` | a newer compatible decision or artifact replaced this one | inspect superseding item | history remains visible |
| `TARGET_CHANGED` | compiled target/config differs from approved context | recompile and re-review | no materialization using old approval |
| `VALIDATION_FAILED` | required check failed or has discrepancy | inspect check evidence and correct upstream stage | cannot be promoted to PASS |
| `NOT_EVALUATED` | required check did not run | run it or record why it is not applicable | never presented as PASS |
| `NOT_APPLICABLE` | policy says the check does not apply to this subject | show policy and scope reason | only the typed validation policy can derive the gate |
| `PARTIAL_SUCCESS` | some bounded items completed while others did not | show completed and failed selections separately | no overall success claim; incomplete items remain actionable |
| `LONG_RUNNING` | an explicitly bounded local operation is still running | show progress scope and allow safe navigation away | no result is consumable until terminal state |

Empty states explain whether the collection is empty, filtered, not yet run, unavailable, or privacy-blocked. Partial states show numerator/denominator and selected/unselected scope. Retry creates a new run or attempt; it does not erase the prior failure.
