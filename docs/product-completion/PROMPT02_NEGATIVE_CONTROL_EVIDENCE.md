# Prompt02 Negative-Control Evidence

Status: `IMPLEMENTED — EXECUTION PENDING THE NEXT CI ATTEMPT`

Each control has separate `implementation_status`, `execution_status`, and
`acceptance_status` fields. The isolated control runner executes a named pytest
fault injection for every row, writes its JUnit result, then writes the
aggregate-only `PROMPT02_NEGATIVE_CONTROLS.json` evidence. The acceptance
receipt may only copy a passing row from that file; the independent validator
checks the receipt against the JUnit evidence. Positive-output assertions and
pre-existing guards are not control evidence.

| ID | Required fault | Accepted boundary | Implementation | Execution | Acceptance |
|---|---|---|---|---|---|
| NC01 | Required source unavailable | `SourceSnapshotService.extract` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC02 | Source/snapshot fingerprint changed | snapshot adapter boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC03 | Oracle unavailable to runtime | runtime/oracle trust boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC04 | Stale persisted review hash/revision | `BackendService.review` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC05 | Missing source-record disposition | G6 validation boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC06 | Invalid or overlapping canonical identity | canonical finalization boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC07 | Cross-run or pre-authored artifact substitution | verified artifact input boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC08 | Missing mandatory server-owned stage | `ExecutionPlanService` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC09 | Undeclared monetary measure | analytical policy/compiler boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC10 | Incompatible source-set mutation | `BackendService.bind_product_source_set` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC11 | Selected-source omission/single-source fallback | `SourceSetSelection` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC12 | Fact-grain multiplication | G6 validation boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC13 | Hard-negative identity merge | canonical identity/G6 boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC14 | Required human review omitted | `JobWorker` review checkpoint | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |

No control is reported as executed by this document. `PASS` can appear only in
the retained execution artifact after its named JUnit test passes; the
independent receipt validator then requires that matching artifact alongside a
successful product run before it can report overall acceptance.
