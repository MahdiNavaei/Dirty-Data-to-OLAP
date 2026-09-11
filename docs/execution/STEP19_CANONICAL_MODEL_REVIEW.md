# Step19 Canonical Model Engineer Review

## GOAL RESULT

PASS. Step19 now has an executable, project-owned, two-phase canonicalization
path: reviewed evidence decisions -> `CanonicalModelHypothesis` -> conditional
ER evidence -> exact `REVIEW_CANONICAL_IDENTITY` -> `CanonicalModel`.

## REPOSITORY BASELINE

- Starting branch: `main`
- Starting HEAD, `origin/main`: `01822ebcca1dcad4a6a308d6bef4dbdbf5989fcb`
- Protected untracked path `tests/quality_unit_artifacts/` was preserved and not staged.

## AUTHORITATIVE INPUTS REVIEWED

The Step19 specialist playbook and shared governance, base reports 01-08,
canonical data-architecture documents, ADR-0006/0007, stage/review/component
specifications, current domain-reviewed material, Step18 v5 report and actual
normalized project-owned ER result were inspected before implementation.

## G5 / REVIEW-ONLY BOUNDARY

G5 remains `PASS` / `REVIEW_ONLY_VALIDATED`; automation remains
`NOT_AUTHORIZED` and no threshold was selected. Fusion scores remain evidence
only. A score, candidate, cluster, or Step18 evaluation result cannot authorize
canonical semantics.

## REVIEW DECISION CONTRACT

`ReviewDecision` and `ReviewCompatibilityContext` are reusable project-owned
contracts. Exact checkpoint, artifact/content/schema/model bindings, source
fingerprints, policy, domain scope, semantic subject and applicability are
checked. Rejected, deferred, invalidated, superseded, or stale decisions fail
closed; invalidation returns a new artifact and preserves the old decision.

## REVIEW_EVIDENCE_DECISIONS

`CanonicalHypothesisService` requires accepted or policy-allowed skipped
`REVIEW_EVIDENCE_DECISIONS`. It accepts typed `RelationshipDecision`,
`SemanticMappingDecision`, and domain references while preserving their IDs;
review-only fusion states are never changed to accepted by this service.

## TWO-PHASE CANONICALIZATION

`CanonicalHypothesisService` creates proposal semantics before identity
assignment. `CanonicalFinalizationService` is a separate guarded operation and
requires an exact identity review after any required ER result.

## CANONICAL MODEL HYPOTHESIS

`CanonicalModelHypothesis` is schema-versioned and includes execution context,
model version, source/snapshot/schema fingerprints, upstream and review refs,
domain refs, entity types, attributes, relationships, mappings, identity
hypotheses, ER requirements/specs, unresolved items, and provenance. Its
semantic content hash excludes the volatile creation timestamp.

## CANONICAL ENTITY TYPES

The reference fixture represents an identity-capable Customer concept and an
Order event concept. Event concepts are not sent through generic ER merely
because they have identifiers. No dimensional or fact role is assigned.

## CANONICAL ATTRIBUTES

Attributes retain entity scope, logical type, candidate/accepted mapping refs,
normalization, authority policy, review, conflict, null-state and lineage
envelopes. Raw values are not written to logs or reports.

## SOURCE ATTRIBUTE MAPPINGS

`SourceAttributeMapping` binds source, snapshot, schema, table, column,
normalization, upstream decision, review and provenance. An accepted mapping
must carry a review ref; only accepted mappings enter the finalized model.

## ENTITY RESOLUTION REQUIREMENTS

ER is explicit per entity-resolution family. `ER_REQUIRED` requires a matching
`EntityResolutionSpec`, a complete project-owned `EntityResolutionResult`, and
an accepted identity review. `ER_NOT_REQUIRED` can proceed under a
policy-recorded skipped path; the event reference path does not fabricate ER.

## ENTITY RESOLUTION INTEGRATION

The reference run consumed the normalized Step18 provider result as an
`EntityResolutionResult` contract and bound its semantic hash into the identity
review. No Splink native object, evaluation truth, cluster truth, or score was
read by canonical runtime code.

## REVIEW_CANONICAL_IDENTITY

The identity review is bound to the hypothesis semantic hash and the exact ER
result hash when ER is required. Changing either binding, source fingerprints,
policy, domain scope or applicability causes a fail-closed stale-review error.

## CANONICAL FINALIZATION

Finalization emits only reviewed canonical instances and terminal source-record
dispositions. It refuses incomplete provenance and required identity families
without accepted memberships. Source records remain represented in maps; no
source row is deleted or overwritten.

## CANONICAL ID STRATEGY

Project-owned IDs use distinct namespaces: `chyp_` for hypotheses, `cmodel_`
for models, and `cent_` for canonical entity instances. Canonical IDs are
derived from entity-type ID, reviewed source-record refs and model version.
`EntityCluster.cluster_id`, candidate IDs, fusion IDs and source keys are never
used as canonical IDs.

## SOURCE RECORD CANONICAL MAP

`SourceRecordCanonicalMap` preserves record, source, snapshot, table, canonical
entity, review, linkage evidence, model version, terminal disposition and
provenance. The reference run mapped two synthetic source records to one
reviewed Customer instance with `CONSOLIDATED` terminal disposition.

## SURVIVORSHIP

Versioned policies cover preferred source, most recent valid, most complete,
consensus and review required. Preferred-source selection is scoped to an
explicit policy; no global source authority is invented. Temporal selection
requires comparable valid timestamps.

## CONFLICT RETENTION

Differing source values produce inspectable conflict artifacts even when a
scoped policy selects a survivor. All eligible alternatives, the selected ref,
policy, review ref, rationale and provenance remain available. Missing authority
and unresolved consensus remain review-required.

## NULL / UNKNOWN SEMANTICS

`CanonicalValueReference` distinguishes source null/missing, not captured,
unknown, not applicable, invalid/unparseable, withheld/redacted and quarantined
states. Redacted values cannot carry a safe raw value.

## PROVENANCE / LINEAGE

Canonical artifacts carry source/snapshot/table refs, upstream decision refs,
review refs, ER artifact/evidence refs, domain refs, lineage refs and policy
refs. The reference run persists these envelopes without publishing sensitive
identity values.

## RECORD ACCOUNTING

The reference accounting artifact records two source records considered, two
mapped, one canonical instance emitted, preserved source records, and no
destructive deduplication. The finalized model binds the accounting artifact
ref.

## REFERENCE BENCHMARK FLOW

`tools/run_step19_reference.py` executed a synthetic/domain-reviewed Customer
and Order flow. It used the existing normalized ER contract only as linkage
evidence, persisted outputs under the ignored
`workspace/runs/step19-reference-run/canonical/` convention, and emitted one
canonical instance, two source maps and one resolved-by-policy conflict.

## NEGATIVE / FAIL-CLOSED CASES

Tests and the behavioral validator cover wrong checkpoint, stale content,
source-fingerprint mismatch, invalidation, missing required ER, skipped
required identity, missing provenance, missing source authority, temporal
incomparability, withheld-value leakage, cluster-ID separation and prohibited
Step20/runtime truth imports.

## DETERMINISM / REPRODUCIBILITY

Input collections are normalized before semantic hashing and IDs exclude wall
clock metadata. The reference flow was rerun by the validator with identical
hypothesis/model semantic hashes. Volatile artifact timestamps remain separate
from semantic identity.

## TESTS

Added canonical unit, contract and integration coverage, including exact
review compatibility, null semantics, survivorship, conflict retention,
record-map preservation and event/identity boundaries.

## FULL REGRESSION

Before handoff: unit `139 passed`; contract `9 passed`; integration `34 passed,
2 skipped`; architecture `9 passed`; security `24 passed`. The two skips are
the existing optional clean-environment Valentine/Splink tests. Compileall and
`git diff --check` passed.

## VALIDATORS

Every existing `tools/validate_*.py` passed, including Step18 v5 and the new
`tools/validate_step19_canonical.py` with 14 behavioral checks. The validator
also reruns the reference flow and checks stable semantic hashes.

## OUTPUT INSPECTION

Inspected hypothesis, evidence and identity reviews, ER binding, canonical
model, source maps, accounting, survivorship and conflict JSON artifacts. The
model contains one `cent_` instance, two source maps, one retained conflict and
explicit `canonical-v1`/`schema_version=1.0` envelopes.

## KNOWN LIMITATIONS

The reference data is synthetic and domain-reviewed, not production
acceptance. Memberships are supplied by the identity-review boundary and are
not inferred from cluster IDs. Source authority is fixture-scoped. No claim is
made about G6 source-to-OLAP correctness, temporal production generalization,
warehouse keys, star schema, measures or materialization.

## STEP20 HANDOFF

Step20 may trust the versioned `CanonicalModel`, canonical entity types and
instances where applicable, accepted mappings, accepted relationships,
`SourceRecordCanonicalMap`, survivorship/conflict artifacts, null semantics,
record-accounting refs, lineage/provenance and unresolved ambiguity.

Step20 must independently determine fact/dimension roles, grain, measures,
aggregation, analytical surrogate keys and SCD behavior. It must not assume a
canonical entity is a dimension, an event is a fact, a numeric attribute is a
measure, or a canonical/source/ER ID is a warehouse key.

## EXECUTION STATE

On successful documentation handoff: `last_completed_step=19`,
`last_completed_role=canonical_model_engineer`, `current_step=20`,
`current_role=olap_engineer`; G5 remains PASS and G6 remains PENDING.

## GIT

Content commit: `04fa158` (`feat: implement review-gated canonical model`). A
separate metadata commit records this report and the Step20 handoff.

Specialist Step19 implementation was completed. Specialist Step20
implementation was NOT started. G5 remains PASS. G6 remains PENDING. No
inference automation threshold was selected or enabled. EntityCluster IDs were
not used as canonical entity IDs. Source records were not destructively
deduplicated. No Step18 evaluation truth was used as runtime canonicalization
truth. No fact/dimension/grain/measure implementation was started.

## CRITICAL POST-STEP19 REPAIR / INTEGRITY CLOSURE

The prior Step19 PASS was independently audited before downstream work. The
audit found four integrity gaps: an umbrella `ReviewDecision` could authorize a
different relationship or mapping; finalization accepted free-form memberships
after identity review; ER validation checked completion without binding the exact
family/spec/sources/snapshots/tables/authorized edges and clustering policy; and
event memberships were skipped instead of being finalized. The prior report is
retained as historical evidence, with this section superseding those statements.

The repair adds typed `CanonicalIdentityMembership` and
`CanonicalIdentityProposal` contracts. Hypothesis construction now requires the
exact concrete `RelationshipDecision` or `SemanticMappingDecision` and derives
the exact review context from that object, including artifact/content, semantic
subject, input/scope, policy, applicability and domain bindings. Bare `SKIPPED`
reviews are rejected; skips require explicit versioned, checkpoint- and
applicability-bound authorization.

Finalization now accepts only the reviewed `CanonicalIdentityProposal`. ER-derived
memberships are validated against the hypothesis family and exact ER spec/result
scope, permitted edge bands, and authorized edge population. Canonical IDs remain
project-owned `cent_` values and never use cluster IDs. Human/domain-reviewed
identity is explicit and actor/source/domain-bound. `ER_NOT_REQUIRED` event
memberships use explicit `SOURCE_LOCAL_EVENT_IDENTITY` and emit terminal maps;
they are not silently dropped.

The repaired reference flow produced two canonical instances (Customer and
Order), three source-record maps, one retained conflict, and complete accounting
for the two linked Customer records plus the Order event. It uses an explicit
synthetic project-owned ER contract fixture only; Step18 evaluation truth is not
runtime canonicalization input.

Focused negative controls now fail closed for wrong relationship/mapping review,
changed decision content, stale identity review, changed membership proposal,
unrelated ER spec, missing required ER, unsupported skip, and free-form
membership finalization. Step20 was not started; G5 remains PASS and G6 remains
PENDING.
