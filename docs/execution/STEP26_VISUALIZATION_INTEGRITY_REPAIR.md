# Post-Step26 Visualization Integrity Repair

## GOAL RESULT

`PASS` for the surgical post-Step26 visualization integrity closure. This was
not Step27 and no Step27 backend/API work was started. Step26 remains the last
completed specialist step; `G7B_REVIEWABLE_DECISIONS=PASS` is re-evaluated from
the repaired evidence, while `G7_END_TO_END_PRODUCT` remains `PENDING`.

The original `docs/execution/STEP26_DATA_VISUALIZATION_REVIEW.md` and its
historical `G7B=PASS` receipt remain unchanged. This document records the
independent post-push findings and their repair.

## BASELINE AND SCOPE

- Starting branch: `main`
- Starting `HEAD`: `7ce1ab6c735e2015781910554f987c03a530ab14`
- Starting `origin/main`: `7ce1ab6c735e2015781910554f987c03a530ab14`
- Historical Step26 content commit: `8cafc28b6f560dc200661b7e1d5a6bda68616b68`
- Historical Step26 receipt: preserved; blob `8c5ea07b39a2367359894d53b999d284cede1c75`
- Step27 state: `step27_started=false`, `step27_status=NOT_STARTED`
- Protected `tests/quality_unit_artifacts/` was not read, modified, staged, or
  used as evidence.

## FINDINGS AND ROOT CAUSES CLOSED

1. Validation visualization accepted caller-selected rows and independently
   derived G6, allowing incomplete or semantically different views to look
   authoritative. The repair adds `ValidationReportVisualizationBinding` and
   `build_validation_from_report(...)`. It revalidates the canonical report,
   verifies report/policy/run/provenance/check-universe/content-hash bindings,
   and preserves the report's status and eligibility without reimplementing
   G6. The old subset path is explicitly exploratory and cannot claim global
   `PASS` or G6 eligibility.
2. Measure visualization accepted unbound, caller-defined OLAP semantics. The
   repair adds `build_measure_from_spec(...)`, bound to the exact reviewed
   `MeasureSpec`, `FactSpec` when supplied, `AnalyticalPlan`, semantic hash,
   plan hash, and analytical package hash. The unbound legacy path now fails
   closed. Step20's actual quantity, unit_price, and discount_rate semantics
   are used; revenue/currency meaning is not invented.
3. Disclosure now derives canonical active-filter labels from node, edge,
   evidence, and review selectors and rejects contradictory reserved
   free-form labels.
4. Disclosure contracts reject hidden/aggregated content with
   `truncated=False`, require a non-empty truncation reason, reconcile exact
   counts, and keep `show_more_available` consistent.
5. Accessible graph rows now have exact one-to-one node closure, unique
   subject IDs, and rendered-node-only related IDs.
6. ER candidate and authorized linkage use distinct line styles and
   accessibility text; both remain explicitly linkage evidence rather than
   canonical identity.
7. `FOCUSED_PATH` documentation and legends now accurately describe the
   current deterministic bounded reachable subgraph, not a unique
   source-to-target path.

## AUTHORITATIVE CONTRACTS USED

- `src/dirty_data_to_olap/domain/contracts/validation.py`: `ValidationReport`,
  `ValidationPolicy`, `ValidationCheck`, `ValidationStatus`,
  `ValidationSeverity`, `GateStatus`, and `report_status_is_derived`.
- `src/dirty_data_to_olap/domain/contracts/analytical.py`: `AnalyticalPlan`,
  `FactSpec`, `GrainSpec`, `MeasureSpec`, aggregation classes, semantic hashes,
  and analytical package hash.
- Step25 interaction/review contracts and Step20 reviewed retail measure
  semantics were retained as the upstream sources of visualization meaning.

## FILES CHANGED

- `src/dirty_data_to_olap/domain/contracts/visualization.py`
- `src/dirty_data_to_olap/domain/contracts/__init__.py`
- `src/dirty_data_to_olap/application/visualization.py`
- `tools/validate_step26_visualization.py`
- `tests/unit/test_step26_visualization_integrity_repair.py`
- Step26 visualization documentation/specifications under `docs/visualization/`

The old Step26 report was not changed. The Step26 handoff's historical
validator compatibility extensions through Step27 were retained; they alter
executable validator behavior and are not described as metadata-only.

## VALIDATION AND NEGATIVE CONTROLS

The updated Step26 validator executed `47` checks across `22` scenarios and
`16` negative controls. It passed controls for partial validation universes,
blocking failures hidden from a view, optional WARNING/NOT_EVALUATED handling,
tampered report/binding/hash/policy context, invented revenue semantics,
same-ID measure mutation, filter disclosure omission/contradiction, malformed
disclosure counts/truncation, duplicate or missing accessible rows, invalid
related IDs, ER candidate/authorized conflation, and the existing privacy,
calibration, quality, profile, and state controls.

The generated artifact was inspected at
`workspace/runs/step26-visualization/visualization_reference.json` and records
`executed_check_count=47`, `scenario_count=22`, and
`negative_control_count=16`, with the representative synthetic graph at
`3,668` nodes and `3,667` edges.

## TESTS AND EXECUTED EVIDENCE

- Final focused Step26 repair/unit/integration/architecture suite: `25 passed`.
- Final full regression, excluding only the two established protected-path
  writers: `363 passed, 2 skipped, 41 warnings` in `132.29s`.
- The two skips are the documented optional Splink and Valentine runtimes.
- All `26/26` repository validators passed serially; the repaired Step26
  validator passed with the counts above.
- `python -m compileall -q src tools tests`: PASS.
- YAML parse: `25` documentation YAML files: PASS.
- `git diff --check`: PASS.
- The known non-fatal dlt/SQLite cursor-cleanup traceback remains a regression
  limitation after successful test completion.

## G7B RE-EVALUATION

`G7B_REVIEWABLE_DECISIONS=PASS` is supported by executed repaired evidence:
authoritative validation status is complete and report-bound; optional and
blocking states remain visible; reviewed analytical semantics are exact and
hash-bound; filters, truncation, accessibility closure, ER linkage state,
uncertainty, provenance, and unresolved states are disclosed. This does not
claim browser usability, API delivery, physical deployment, production
capacity, or end-to-end product completion.

## HANDOFF STATE

- `last_completed_step=26`
- `last_completed_role=data_visualization_engineer`
- `current_step=27`
- `current_role=senior_backend_engineer`
- `step27_started=false`
- `step27_status=NOT_STARTED`
- `G7B_REVIEWABLE_DECISIONS=PASS`
- `G7_END_TO_END_PRODUCT=PENDING`
- `blocked=false`

## GIT

- Content commit: `6f108e924444fe72b6d93c7d297b8aa51988aae9`
- Metadata/handoff commit: recorded after this receipt, state update, and log
  append.
- No history rewrite, amend, rebase, force-push, or Step27 implementation.
