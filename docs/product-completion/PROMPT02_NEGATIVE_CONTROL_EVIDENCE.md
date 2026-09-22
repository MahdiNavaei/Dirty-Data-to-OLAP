# Prompt02-R Negative-Control Evidence

Status: `PARTIAL — REAL CONTROLS WIRED, NOT EXECUTED`

The old NC01-NC14 receipt booleans were removed from the acceptance harness.
They were guards and output inspections, not fault-injection evidence.

## Controls currently wired to real boundaries

| Fault | Boundary | Current state |
|---|---|---|
| Stale persisted review revision | `BackendService.review` | Harness expects `BackendError` containing stale; live execution pending |
| Incompatible source-set mutation | `BackendService.bind_product_source_set` | Harness expects `SOURCE_ALREADY_BOUND`; live execution pending |

The harness also verifies that execution reaches a review-required state before
submitting decisions, that decisions carry the actual subject artifact/hash,
and that later resume uses the durable worker path. Those assertions are not
runtime evidence until the provider-backed run executes.

## Controls still required before Prompt02 acceptance

Selected-source unavailability, changed snapshot fingerprint, missing review,
missing source disposition, incompatible artifact substitution, omitted
mandatory stage, unauthorized source access, invalid identity membership,
incorrect fact grain, invented measure semantics, interrupted materialization,
single-source fallback, and lease/fencing faults still need isolated tests
against their actual accepted boundaries. None is reported as PASS here.

## Honest conclusion

No synthetic stage-success, in-memory review, or direct target-construction
record is accepted as a negative-control result. Because no live four-source
run was available in this checkout, this document is evidence of test design
and wiring only, not evidence that the controls passed.
