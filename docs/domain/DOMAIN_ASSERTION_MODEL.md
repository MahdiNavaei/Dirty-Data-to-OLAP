# Dirty Data to OLAP — Domain Assertion Model

This is a reusable documentation contract for **RUNTIME CUSTOMER DOMAIN ASSERTION**. It is not an implementation API and it does not turn benchmark semantics into universal truth.

## 1. Assertion types

The model supports these assertion types:

`ENTITY_DEFINITION`, `ATTRIBUTE_MEANING`, `BUSINESS_KEY_ASSERTION`, `RELATIONSHIP_MEANING`, `SOURCE_AUTHORITY`, `STATUS_CODE_MEANING`, `CATEGORY_MEANING`, `BUSINESS_EVENT_DEFINITION`, `GRAIN_ASSERTION`, `MEASURE_MEANING`, `QUALITY_RULE`, `EXCEPTION_RULE`, `TEMPORAL_RULE`, and `IDENTITY_RULE`.

## 2. Required assertion envelope

Each assertion conceptually contains:

```yaml
schema_version: 1
assertion_id: da_<stable-id>
assertion_type: SOURCE_AUTHORITY
scope:
  project_id: customer-project
  source_ids: [src_a]
  subject_refs: [src_a.orders.customer_code]
statement: "..."
evidence_refs: [domain-note-17, source-policy-4]
asserted_by:
  actor_id: domain-owner-42
  actor_role: business-data-owner
assertion_time: "2026-09-09T00:00:00Z"
valid_from: "2026-01-01"
valid_to: null
confidence_kind: HUMAN_DOMAIN_ASSERTION
status: PROPOSED | ACCEPTED | REJECTED | REVIEW_REQUIRED | CONFLICTED | SUPERSEDED
exceptions: []
supersedes: []
replaced_by: null
provenance:
  source_type: human-domain-evidence
  source_ref: meeting-or-policy-reference
  recorded_by: reviewer-id
```

`actor_id`, timestamps and source references must be privacy-safe and sufficient for audit. A human assertion has no fabricated numeric probability. `confidence_kind` identifies the kind of evidence; it is not an ML score.

## 3. Evidence and conflict rules

1. A domain assertion, declared metadata, measured observation and inferred hypothesis are separate evidence objects.
2. A human assertion may establish intended meaning, but it does not erase contradictory measurements.
3. If the assertion and measurement conflict, retain both, record the conflict type and set the semantic decision to `CONFLICTED` or `REVIEW_REQUIRED` until an attributable resolution exists.
4. A resolution records the actor, rationale, evidence references, effective time and any exception. It does not delete superseded evidence.
5. A matcher score, IND, LLM output or linkage probability cannot substitute for an assertion where business authority is required.
6. Benchmark-domain specifications are valid only under their explicit benchmark scope and version; they are not runtime customer assertions.

## 4. Lifecycle and supersession

`PROPOSED` assertions may be reviewed. `ACCEPTED` assertions may inform downstream policy within their scope. `REJECTED` and `SUPERSEDED` assertions remain audit records and cannot drive new decisions. `CONFLICTED` or `REVIEW_REQUIRED` assertions block automatic semantic acceptance for the affected subject.

Assertions are time-bounded when meaning or authority changes. `valid_from`/`valid_to` describe semantic applicability; `assertion_time` describes when the statement was recorded. Overlapping assertions with incompatible statements create a visible conflict. A new assertion uses `supersedes`/`replaced_by` rather than silently editing history.

## 5. Benchmark adaptation

The domain-reviewed YAML files under `benchmarks/labels/domain-reviewed/` use the same principles but are project-owned synthetic specifications. Their provenance is `Specialist Step 02 domain definition`; they do not assert facts about real customers and do not contain record-level labels.
