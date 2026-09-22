# Prompt02 Negative-Control Evidence

Status: `IMPLEMENTED — EXECUTION PENDING THE NEXT CI ATTEMPT`

Each control has separate `implementation_status`, `execution_status`, and
`acceptance_status` fields. The isolated control runner executes a named pytest
fault injection for every row, writes its JUnit result, and requires the test
to emit an assertion-produced `observed_rejection` plus `assertion_reference`
before the row can be PASS. The aggregate
`PROMPT02_NEGATIVE_CONTROLS.json` evidence is bound to the execution commit
and names both the pytest node and JUnit file. The acceptance receipt may only
copy a passing row from that file; the independent validator checks the
receipt against the execution evidence. Expected strings are never copied
into the observed field by the runner.

| ID | Required fault | Accepted boundary | Implementation | Execution | Acceptance |
|---|---|---|---|---|---|
| NC01 | Required source unavailable | `SourceSnapshotService.extract` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC02 | Source/snapshot fingerprint changed | snapshot adapter boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC03 | Oracle unavailable to runtime | runtime/oracle trust boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC04 | Stale persisted review hash/revision | `BackendService.review` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC05 | Missing source-record disposition | G6 validation boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC06 | Invalid or overlapping canonical identity | `CanonicalIdentityProposalService` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC07 | Cross-run or pre-authored artifact substitution | verified artifact input boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC08 | Missing mandatory server-owned stage | `ExecutionPlanService` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC09 | Undeclared monetary measure | analytical policy/compiler boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC10 | Incompatible source-set mutation | `BackendService.bind_product_source_set` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC11 | Selected-source omission/single-source fallback | `ProductSourceService.source_set_selection` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC12 | Fact-grain multiplication | G6 validation boundary | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC13 | Hard-negative identity merge | `CanonicalIdentityProposalService` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |
| NC14 | Required human review omitted | `BackendService.resume` | `IMPLEMENTED` | `NOT_EXECUTED` | `PENDING` |

No control is reported as executed by this document. `PASS` can appear only in
the retained execution artifact after its named JUnit test passes; the
independent receipt validator then requires that matching artifact alongside a
successful product run before it can report overall acceptance.
