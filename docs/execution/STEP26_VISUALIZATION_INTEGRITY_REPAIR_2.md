# Post-Step26 Visualization Integrity Repair 2

Status: `PASS` for the bounded Step26 surgical repair. This receipt does not
start Step27 and does not claim browser, API, renderer, physical, live-provider,
or end-to-end product completion.

## Scope and baseline

- Execution step: `26`; repair only; Step27 remains `NOT_STARTED`.
- Starting branch: `main`.
- Starting baseline: `HEAD=origin/main=c0d8369edb5aced23c6221833016e4724d8221ff`.
- Prior Repair 1 content commit `6f108e924444fe72b6d93c7d297b8aa51988aae9` and both prior Step26 receipts remain preserved.
- `tests/quality_unit_artifacts/` was not read, modified, staged, committed, or used as evidence.

## Defect A: analytical review binding

`VisualizationService.build_measure_from_spec(...)` now requires an actual
`FactSpec` and an actual `ReviewDecision`. It derives the exact analytical
context through `ReviewPolicyService.analytical_plan_context(plan)` and uses
`ReviewPolicyService.require_compatible(review, context)` as the single
compatibility authority. No parallel compatibility rules were introduced.

The projection preserves the review decision ID and content hash, checkpoint,
decision status, applicability fingerprint, plan ID/version/content hash,
analytical specification package hash, measure semantic hash, fact semantic
hash, grain reference, domain references, and provenance. Existing
MeasureSpec/FactSpec ownership and semantic-hash checks remain in force.

The executed validator used the real Step20 reference plan and fact context,
created an `ACCEPTED` ReviewDecision from that context, and projected the
actual `quantity`, `unit_price`, and `discount_rate` MeasureSpecs. It rejected
missing FactSpec/review, unauthorized `SKIPPED`, `REJECTED`, `DEFERRED`,
`INVALIDATED`, superseded, stale, wrong-checkpoint, wrong-plan,
wrong-package/source-fingerprint, wrong-domain, and wrong-applicability
decisions, plus same-ID MeasureSpec and FactSpec mutations.

## Defect B: ValidationReport UI-preview privacy

Authoritative validation expected/observed values now pass through the existing
`PrivacyPolicyService` classification and `ExposureContext.UI_PREVIEW`
decision boundary. Unknown arbitrary strings are masked rather than exposed
because they are short. Structured values are redacted without stringifying
their contents. A denied policy decision or failed masking operation becomes
the explicit `PRIVACY_BLOCKED` display state, never `UNAVAILABLE`.

`ValidationDisplayState` distinguishes `SHOWN`, `MASKED`, `UNAVAILABLE`, and
`PRIVACY_BLOCKED`. Only safe status labels and finite numeric/boolean
aggregate primitives are shown. The authoritative projection records
`display_context=UI_PREVIEW`; its serialized checks and generated Step26
artifact contain no raw email, phone, password/secret, long identifier,
free-text, or structured-value canaries.

## Defect C: disclosure contract

`DisclosureMetadata` now requires `truncated` and `show_more_available` to
equal the exact presence of hidden or aggregated nodes/edges. A complete graph
with `truncated=true` is rejected, as are hidden counts without truncation or
without a reason. The prior Repair 1 graph-closure and accessibility checks
remain intact.

## Executed evidence

- Focused Repair 2 suite: `27 passed`.
- Cross-step focused suites: `90 passed` across Step10 privacy, Step20 review/compiler, Step22 validation, Step25 UX, and all Step26 suites.
- Step26 validator: `66 checks PASS`, `25 scenarios`, `32 negative controls`; artifact `workspace/runs/step26-visualization/visualization_reference.json` regenerated and inspected.
- Full regression command excluded only the two documented protected-path writers: `tests/unit/test_quality_engine.py` and `tests/security/test_step23_platform_security.py`.
- Full regression result: `375 passed, 2 skipped, 41 warnings`.
- The two skips are the optional official Splink and Valentine runtimes; the known non-fatal dlt/SQLite cursor-cleanup traceback remains documented.
- Compileall, YAML parsing (`27` files), `git diff --check`, and artifact canary scan passed.

## Git and handoff

- Content commit: `e4d578a42bc888b4ff35a7c21d8b1424d67f1719` (`fix: close Step26 visualization integrity repair 2`).
- Metadata commit: recorded by the final metadata-only commit after this receipt, state, and execution-log entry are validated.
- Normal push and `HEAD==origin/main` verification are required after the metadata commit.
- Final state remains: `last_completed_step=26`, `current_step=27`,
  `current_role=senior_backend_engineer`, `step27_started=false`,
  `step27_status=NOT_STARTED`, `G7B_REVIEWABLE_DECISIONS=PASS`,
  `G7_END_TO_END_PRODUCT=PENDING`, `blocked=false`.

## Known limitations

This is deterministic local contract and synthetic-reference evidence only.
There is no browser/assistive-technology test, renderer/GPU test, API or
network test, production-capacity claim, physical deployment, or live
provider execution. Splink and Valentine remain optional unavailable skips.
