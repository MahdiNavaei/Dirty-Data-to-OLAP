# Step25 — UX / Product Designer Review

## GOAL RESULT

`PASS` for the bounded Step25 UX/product-design scope: a source-grounded review
experience contract, information architecture, uncertainty language, explicit
interaction states, high-impact safety rules, accessibility requirements, and
executable contract checks were added. This is not a claim of frontend
implementation, browser usability, user research, visualization, backend/API,
or end-to-end product completion. `G7B_REVIEWABLE_DECISIONS` remains `PENDING`
and `G7_END_TO_END_PRODUCT` remains `PENDING`.

## REPOSITORY BASELINE

- project root: `D:\Projects\Dirty-Data-to-OLAP`
- starting branch: `main`
- starting `HEAD`: `4fbabf90fd1015c00c39a7df136e97a5d62cc0b7`
- starting `origin/main`: `4fbabf90fd1015c00c39a7df136e97a5d62cc0b7`
- remote: `https://github.com/MahdiNavaei/Dirty-Data-to-OLAP.git`
- protected `tests/quality_unit_artifacts/` was preserved, not read, not
  modified, not staged, and not committed

## AUTHORITATIVE INPUTS REVIEWED

The full Step25 prompt, the bootstrap reference requested in the continuation,
the six governance files under
`docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base/`, all eight base
reports, the UX specialist playbook `specialists/21_UX_PRODUCT_DESIGNER.md`,
the Step24 report and execution history, architecture checkpoint specs, and
the current implementation contracts were reviewed.

The current source of truth was then checked directly in the repository:

- `ReviewDecision`, `ReviewCompatibilityContext`, checkpoint/status enums and
  invalidation rules in `domain/contracts/canonical.py`;
- evidence lineage, raw metric semantics, reliability/presence states,
  conflict references, and relationship/mapping decisions in
  `domain/contracts/evidence_fusion.py`;
- quality issues, observation/measurement scope, failures and repair proposals
  in `domain/contracts/quality.py`;
- canonical/ER identity contracts, analytical plan/spec/grain/measure contracts
  and materialization contracts;
- typed validation statuses, required checks, discrepancies and derived G6
  status in `domain/contracts/validation.py`;
- privacy classification, exposure and failure contracts in
  `domain/contracts/privacy.py`;
- `application/review_policy.py` and
  `docs/architecture/specs/review_checkpoints.yml`.

## STEP24 HANDOFF

Step24 was accepted at the bounded scale-preserving boundary:

- G5: `PASS`; G6: `PASS`; G7A: `PASS`; G7: `PENDING`;
- report: `scale-equivalence_75ff1905690fbd638121c246c493ee6f`;
- report semantic content hash:
  `589329b16a158fbf46ca2f77ae118127d9543b2482f018466a49b9c3468715c8`;
- local/partitioned semantic output hash:
  `2e27bda2d225304119971e9aad5dd98f91ef91439560d64817d494d216b683b6`;
- partition plan: `partition-plan_b2c2d68ba07c60c937178a0a59114bc8`;
- partition plan hash:
  `78e17f4044477ae4fd5a16dbd3cd3aa7d3b73d90061822be4f74aa9e233585ba`.

The UX contract preserves the Step24 meaning boundary: partitioning and
worker scheduling are execution evidence, not a new user-facing truth claim.

## UX / INFORMATION ARCHITECTURE DECISION

The V1 journey is organized by reviewer work and lifecycle: Projects/Sources,
Runs/Snapshots, Data condition, Relationships, Schema mappings, Identity/ER,
Canonical, Analytical/OLAP, Materialization, Validation, and Lineage/Evidence.
Algorithm/provider names remain evidence-producer metadata and are not the
navigation model. The persistent review context is
`project -> run -> snapshot -> stage -> subject`.

## REVIEW WORKFLOW AND EVIDENCE CONTRACT

The four exact checkpoints are preserved:

1. `REVIEW_EVIDENCE_DECISIONS` — evidence-fusion relationship, mapping, repair,
   or conflict subjects; guards `CANONICAL_HYPOTHESES`.
2. `REVIEW_CANONICAL_IDENTITY` — canonical identity preparation with conditional
   ER-required complete-result context; guards `CANONICAL_FINALIZATION`.
3. `REVIEW_ANALYTICAL_PLAN` — exact analytical plan and child spec package;
   guards `COMPILATION`.
4. `REVIEW_MATERIALIZATION_PLAN` — compiled plan, generated SQL and target
   fingerprint; guards `MATERIALIZATION`.

Every checkpoint uses the exact subject context fields from `ReviewDecision`:
stage, artifact ID, content hash, schema version, model version, source schema
fingerprints, policy version, domain assertion refs, semantic ID,
applicability fingerprint, actor, actor source, reviewed-at and rationale.
Only compatible `ACCEPTED` or explicitly authorized `SKIPPED` satisfies a
downstream guard. Rejected, deferred, invalidated, superseded, missing and
stale decisions remain non-authorizing.

The reusable review object includes subject, state, meaning, risk, evidence,
conflicts, missing evidence, scope, provenance, downstream consequence,
technical details, actions, and freshness/invalidation. Supporting,
contradicting, missing, unavailable, failed, incomplete and privacy-blocked
evidence are separate rows/states. Human decisions include actor and rationale
and are not model evidence.

Uncalibrated raw/normalized scores and confidence bands are labeled as
decision/ranking evidence only. They are never displayed or copied as
probabilities. Sampled, bounded, full, unmeasured and incomplete scopes remain
distinct. An entity cluster does not become a canonical identity. A
`NON_ADDITIVE` measure has no default additive action. Validation failure,
`NOT_EVALUATED`, and `REVIEW_REQUIRED` cannot be promoted to `PASS`.

## INTERACTION STATES / RECOVERY

`docs/ux/specs/interaction_states.yml` defines explicit states for no evidence,
no candidates, capability unavailable, provider failure, timeout, incomplete,
privacy blocked, review required, stale, invalidated, superseded, target
changed, validation failure, not evaluated, not applicable, partial success,
and long-running work. Every state has meaning, scope, recovery, retryability,
and downstream consequence. Partial success is not overall success, and
provider failure is not negative evidence.

## BULK AND HIGH-IMPACT SAFETY

Bulk review requires bounded scope, homogeneous grouping, preview, explicit
conflict/missing/stale/failure exclusions, visible partial selection,
confirmation and an audit rationale. Arbitrary mass entity merge,
canonicalization, analytical acceptance, or materialization approval is not
allowed. High-impact actions use item-level consequence dialogs and preserve
prior decisions through invalidation/supersession and replacement artifacts.

## ACCESSIBILITY AND PRIVACY

The contract requires non-color status, keyboard/focus operability, semantic
headings/tables/dialogs, nonvisual text/table representations for future
relationship/lineage graphs, screen-reader expansion of evidence, associated
errors, and explicit disabled-action reasons. UX examples use only synthetic
opaque identifiers. Raw PII is forbidden in URLs, navigation, audit text,
fixtures and previews; privacy-blocked exposure has a distinct recovery path.

## COGNITIVE WALKTHROUGHS

Ten design-time cognitive walkthroughs cover a strong relationship,
contradictory relationship, high uncalibrated matcher score, human ER review,
canonical conflict, OLAP grain/nonadditive measure, stale hash, failed G6,
unavailable/incomplete provider, and safe versus unsafe bulk review. They are
contract-based walkthroughs, not user research or human acceptance evidence.

## ARTIFACTS CREATED

- `docs/ux/README.md`
- `docs/ux/USER_JOURNEY.md`
- `docs/ux/INFORMATION_ARCHITECTURE.md`
- `docs/ux/REVIEW_WORKFLOWS.md`
- `docs/ux/REVIEW_OBJECT_PATTERN.md`
- `docs/ux/EVIDENCE_AND_UNCERTAINTY_PRESENTATION.md`
- `docs/ux/CONTENT_AND_TERMINOLOGY_GUIDELINES.md`
- `docs/ux/ERROR_EMPTY_PARTIAL_STATES.md`
- `docs/ux/BULK_ACTION_AND_HIGH_IMPACT_SAFETY.md`
- `docs/ux/ACCESSIBILITY_REQUIREMENTS.md`
- `docs/ux/COGNITIVE_WALKTHROUGHS.md`
- `docs/ux/specs/review_experience.yml`
- `docs/ux/specs/interaction_states.yml`
- `tools/validate_step25_ux.py`
- `tests/architecture/test_step25_ux_contracts.py`

Content commit: `1e52bc4` (`docs: define Step25 UX review contracts`).

## VALIDATION

- `python tools/validate_step25_ux.py`: `PASS`, 62 checks, 0 failures;
- `python -m pytest -q tests/architecture/test_step25_ux_contracts.py`: `6 passed`;
- negative fixtures failed closed for raw score as probability, unbounded mass
  merge, and color-only accessibility;
- protected-boundary repository regression:
  `338 passed, 2 skipped, 41 warnings`;
- the two skips are optional real-provider tests for Splink and Valentine;
- all `25/25` repository validators passed in the validator sweep, including
  `validate_step25_ux.py`;
- `python -m compileall -q src tools tests/architecture/test_step25_ux_contracts.py`:
  passed;
- `git diff --check` and staged diff review: passed;
- YAML parse checks for both machine-readable UX specs: passed;
- secret scan over Step25 artifacts: no matches;
- the known non-fatal dlt/SQLite cursor-cleanup traceback was observed during
  the passing regression and remains a documented dependency limitation.

The repository regression intentionally excluded only
`tests/unit/test_quality_engine.py` and
`tests/security/test_step23_platform_security.py`, the tests that directly
write to the protected `tests/quality_unit_artifacts/` path. That path was not
used as evidence for Step25.

## SELF-REVIEW / SCOPE LIMITS

No product/architecture semantics, scoring formula, calibration, domain truth,
canonical meaning, analytical grain, backend/API, frontend, visualization,
job orchestration, authentication, provider runtime, or source write path was
added. No user research or browser acceptance is claimed. Step26 remains the
next authorized specialist and owns visualization implementation; Step27–29
remain untouched.

## EXECUTION STATE AFTER HANDOFF

- `last_completed_step=25`, role `ux_designer`;
- `current_step=26`, role `data_visualization_engineer`;
- `step25_status=COMPLETED_UX_DESIGN`;
- `step26_status=NOT_STARTED`;
- `G5=PASS`, `G6=PASS`, `G7A=PASS` unchanged;
- `G7B_REVIEWABLE_DECISIONS=PENDING`;
- `G7_END_TO_END_PRODUCT=PENDING`;
- `blocked=false`.

## KNOWN LIMITATIONS

- No frontend, browser flow, user research, or assistive-technology runtime
  test exists in this documentation-only pass.
- The machine-readable contracts are enforceable design specifications; actual
  implementation of the UI remains future specialist scope.
- Provider availability and physical/runtime scale remain the prior-step
  limitations; this pass does not alter them.

## HANDOFF

Hand off to `Step26 - Data Visualization Engineer`. Step26 may consume the
information architecture, review-object fields, state registry, evidence
semantics, accessibility criteria, and cognitive walkthrough cases. It must
not reinterpret uncalibrated scores, collapse review states, or introduce
visualization claims beyond the evidence contracts.
