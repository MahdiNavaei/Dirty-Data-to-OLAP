# Prompt02 Negative-Control Evidence

Status: `BLOCKED — TWO EXECUTED, TWELVE NOT EXECUTED`

The acceptance receipt now carries one structured row for every NC01-NC14
control. A row is `PASS` only when the specified fault is injected at the
named accepted boundary, the boundary rejects it, and durable evidence is
recorded. Positive-output assertions and pre-existing guards are not control
evidence.

| ID | Required fault | Accepted boundary | Current execution status | Durable evidence requirement |
|---|---|---|---|---|
| NC01 | Required source unavailable | `BackendService.bind_product_source_set` | `NOT_EXECUTED` | isolated run and `SOURCE_SELECTION_REJECTED` |
| NC02 | Source/snapshot fingerprint changed | snapshot adapter boundary | `NOT_EXECUTED` | changed source hash and durable failed attempt |
| NC03 | Oracle unavailable to runtime | runtime/oracle trust boundary | `NOT_EXECUTED` | runtime run succeeds without oracle input; harness loads oracle later |
| NC04 | Stale persisted review hash/revision | `BackendService.review` | `PASS` in live acceptance harness | `REVIEW_REVISION_CONFLICT` plus run/review references |
| NC05 | Missing source-record disposition | G6 validation boundary | `NOT_EXECUTED` | tampered accounting rejected and no G6 publication |
| NC06 | Invalid or overlapping canonical identity | canonical finalization boundary | `NOT_EXECUTED` | invalid membership rejected and run remains unaccepted |
| NC07 | Cross-run or pre-authored artifact substitution | verified artifact input boundary | `NOT_EXECUTED` | run-scope/content binding rejection |
| NC08 | Missing mandatory server-owned stage | `ExecutionPlanService` | `NOT_EXECUTED` | incomplete plan rejected before execution |
| NC09 | Undeclared monetary measure | analytical compiler | `NOT_EXECUTED` | revenue/GMV relabel rejected without domain assertion |
| NC10 | Incompatible source-set mutation | `BackendService.bind_product_source_set` | `PASS` in live acceptance harness | `SOURCE_ALREADY_BOUND` plus source-set fingerprint |
| NC11 | Selected-source omission/single-source fallback | `ProductSourceService.source_set_selection` | `NOT_EXECUTED` | fewer-than-two selection rejected |
| NC12 | Fact-grain multiplication | G6 validation boundary | `NOT_EXECUTED` | duplicate event rejected or quarantined with accounting |
| NC13 | Hard-negative identity merge | canonical identity review boundary | `NOT_EXECUTED` | same-name/different-contact merge rejected or held for review |
| NC14 | Required human review omitted | `BackendService.resume` | `NOT_EXECUTED` | resume rejected while review is unresolved |

The two executed controls are real backend calls from the isolated acceptance
run, not receipt booleans. The remaining twelve are emitted as
`NOT_EXECUTED`, never as `PASS`; therefore the independent validator must keep
Prompt02 blocked. The next implementation must add isolated fault-injection
tests at the stated boundaries before changing any row to `PASS`.
