# 04 — Internal Data Contracts Report

## 1. Why this report is critical

Dirty Data to OLAP integrates engines with incompatible outputs. If their native objects are passed directly between stages, the system becomes brittle and impossible to reason about.

Therefore Dirty Data to OLAP owns a canonical internal language.

The rule is:

> **External tools produce evidence; Dirty Data to OLAP contracts define meaning.**

Contracts should initially be implemented with Pydantic models plus explicit schema versioning.

---

## 2. Contract design principles

Every persisted contract must include:

- stable ID;
- schema version;
- `run_id`;
- provenance;
- timestamps where relevant;
- source references by stable IDs, not Python object pointers;
- no opaque third-party classes.

Evidence-bearing contracts must distinguish:

- measured values;
- estimated values;
- inferred hypotheses;
- human assertions.

---

## 3. Identity model

Recommended stable identifiers:

```text
source_id
table_id
column_id
record_ref
entity_id
candidate_id
decision_id
run_id
```

Examples:

```text
source_id = src_crm_prod
table_id  = src_crm_prod.public.customers
column_id = src_crm_prod.public.customers.customer_code
```

IDs must survive display-name changes where possible, but schema fingerprints should detect real structural change.

---

## 4. Source contracts

### 4.1 SourceDescriptor

```yaml
schema_version: 1
source_id: src_crm
name: CRM
source_type: postgresql
connection_profile: crm_readonly
included_namespaces: [public]
excluded_tables: [audit_log]
fingerprint: "..."
```

### 4.2 TableDescriptor

Fields:

- `table_id`
- `source_id`
- namespace/schema
- physical name
- estimated row count
- table kind: table/view/file
- fingerprint

### 4.3 ColumnDescriptor

Fields:

- `column_id`
- `table_id`
- physical name
- ordinal
- native type
- normalized physical type
- nullable according to schema
- declared default if available

### 4.4 DeclaredConstraint

Types:

- PRIMARY_KEY
- FOREIGN_KEY
- UNIQUE
- NOT_NULL

A declared FK must remain distinct from an inferred FK candidate.

---

## 5. Profile contracts

### 5.1 ObservationScope

Every profile stores:

```yaml
scope:
  mode: sample   # full | sample
  rows_observed: 100000
  estimated_total_rows: 8300000
  sampling_method: reservoir
  seed: 42
```

This prevents a sample statistic from being mistaken for a whole-table fact.

### 5.2 ColumnProfile

Suggested structure:

```yaml
schema_version: 1
column_id: src_crm.public.customers.mobile
scope: {...}
physical_type: string
null_count: 7400
null_ratio: 0.074
distinct_count: 91100
distinct_ratio: 0.911
min_value: null
max_value: null
length:
  min: 4
  max: 16
  mean: 11.2
patterns:
  - type: PHONE_LIKE
    support: 0.89
  - type: EMAIL_LIKE
    support: 0.00
statistics: {}
provenance:
  engine: dataprofiler
  engine_version: "..."
```

### 5.3 TableProfile

Fields may include:

- row observations;
- duplicate-row ratio;
- column profile references;
- correlations only when statistically appropriate;
- quality summary references.

---

## 6. Key and dependency contracts

### 6.1 KeyCandidate

```yaml
candidate_id: key_...
table_id: ...
columns:
  - customer_code
key_type: UNIQUE_CANDIDATE
uniqueness_ratio: 0.9998
null_ratio: 0.0001
confidence: null
status: CANDIDATE
evidence_refs: [...]
```

`confidence` is not assigned by the adapter. It is assigned by Dirty Data to OLAP evidence fusion/policy.

### 6.2 InclusionDependencyEvidence

```yaml
left_columns:
  - orders.customer_no
right_columns:
  - customers.customer_no
coverage: 0.974
violation_ratio: 0.026
algorithm: FAIDA
provenance: {...}
```

### 6.3 FunctionalDependencyEvidence

```yaml
determinant: [order_id]
dependent: [order_date]
error_rate: 0.0
algorithm: ...
```

---

## 7. Relationship contracts

### 7.1 RelationshipCandidate

```yaml
candidate_id: rel_123
from_table: orders
from_columns: [customer_no]
to_table: customers
to_columns: [customer_no]
proposed_cardinality: MANY_TO_ONE
evidence_refs:
  - ev_ind_1
  - ev_unique_1
  - ev_name_1
state: CANDIDATE
```

### 7.2 RelationshipDecision

```yaml
decision_id: decision_rel_123
candidate_id: rel_123
outcome: ACCEPTED   # ACCEPTED | REJECTED | REVIEW_REQUIRED
confidence: 0.982
confidence_band: HIGH
policy_version: relationship-v1
reasons:
  - target key nearly unique
  - 97.4% source-value inclusion
  - compatible identifier semantics
review:
  reviewer: null
  override: false
```

Important: evidence and decision are separate. Re-running policy can change the decision without recomputing all observations.

---

## 8. Schema matching contracts

### SchemaMatchCandidate

```yaml
left_column: crm.customer.customer_code
right_column: erp.client.client_no
matcher_scores:
  coma: 0.88
  cupid: 0.81
  jaccard: 0.73
instance_similarity: 0.93
provenance:
  engine: valentine
state: CANDIDATE
```

After evidence fusion it may become a `SemanticMappingDecision`.

### SemanticMappingDecision

Fields:

- source columns;
- canonical attribute hypothesis;
- confidence;
- evidence refs;
- conflicts;
- review state.

---

## 9. Quality issue contract

```yaml
issue_id: dq_001
entity_type: COLUMN
entity_id: crm.customers.mobile
issue_type: INCONSISTENT_PHONE_FORMAT
severity: HIGH
observed_support: 0.31
affected_rows_estimate: 280000
detection_evidence_refs: [...]
repairability: AUTO_SAFE
proposed_repairs:
  - normalize_to_e164
status: OPEN
```

Separate:

- detection;
- severity;
- repair proposal;
- repair execution.

Do not collapse them into one flag.

---

## 10. Entity-resolution contracts

### 10.1 EntityResolutionSpec

Defines configuration before Splink runs:

```yaml
entity_type: CUSTOMER
sources:
  - crm.customers
  - webshop.users
identity_attributes:
  - canonical_name
  - canonical_phone
  - canonical_email
blocking_strategy: ...
thresholds: ...
```

### 10.2 EntityMatchEdge

```yaml
left_record_ref: ...
right_record_ref: ...
match_probability: 0.978
comparison_summary: {...}
```

### 10.3 EntityCluster

```yaml
cluster_id: cluster_42
entity_type: CUSTOMER
members:
  - record_ref_A
  - record_ref_B
confidence_summary: ...
```

`EntityCluster` and `EntityMatchEdge` are linkage evidence only. They do not
assign `canonical_entity_id`, publish accepted canonical identity or create a
source-to-canonical mapping.

---

## 11. Canonical model contracts

### 11.1 SourceRecordCanonicalMap

```yaml
record_ref: crm.customers#981
canonical_entity_id: cust_000182
cluster_id: cluster_42
```

`SourceRecordCanonicalMap` is produced only by Canonical Finalization after the
canonical hypothesis, accepted or review-acceptable linkage evidence where
required, domain assertion, identity policy, conflict state, review decision and
provenance have been evaluated. `cluster_id` may be referenced, but a cluster
ID is never reused as a canonical entity ID.

### CanonicalEntityType

```yaml
name: Customer
canonical_key: customer_id
attributes:
  - name
  - phone
  - email
source_mappings:
  - source: crm.customers
  - source: erp.clients
```

### CanonicalAttribute

Must include:

- name;
- semantic type;
- normalized type;
- candidate source columns;
- conflict-resolution policy;
- nullability expectation;
- lineage.

### SourceAttributeMapping

```yaml
source_column: crm.customer.mobile
canonical_attribute: Customer.phone
transform: normalize_phone
confidence: 0.96
evidence_refs: [...]
```

---

## 12. Analytical model contracts

### DimensionSpec

Fields:

- name;
- grain/uniqueness definition;
- surrogate key;
- business key(s);
- attributes;
- SCD policy;
- lineage.

### FactSpec

Fields:

- name;
- grain;
- foreign keys;
- degenerate dimensions if any;
- measures;
- source relationships;
- filters defining event validity.

### GrainSpec

```yaml
fact: fact_order_line
grain_description: one row per product line in a completed order
grain_keys:
  - order_id
  - line_number
validation:
  expected_unique: true
```

### MeasureSpec

The following is a generic contract-shape example only. `net_amount` and the
expression are not reference-benchmark semantics; a runtime or benchmark
monetary measure requires an explicit versioned domain contract before it can
be accepted or described as revenue.

```yaml
name: net_amount
expression: gross_amount - discount_amount
aggregation: SUM
measure_type: ADDITIVE
```

---

## 13. Evidence contract

All evidence families derive from a common envelope:

```yaml
evidence_id: ev_...
evidence_type: INCLUSION_DEPENDENCY
subject_ref: ...
object_ref: ...
value: 0.974
value_semantics: coverage
reliability: 0.95
scope: {...}
provenance:
  engine: desbordante
  version: ...
  config_hash: ...
  run_id: ...
```

This is essential for report 05.

---

## 14. Versioning rules

Every persisted model includes `schema_version`.

Breaking changes require:

- version bump;
- migration function;
- compatibility test.

External adapter upgrades must not silently alter contract semantics.

---

## 15. Serialization formats

Recommended:

- Pydantic model in code;
- JSON for individual decision artifacts;
- YAML for human-editable model specifications;
- Parquet for large structured result sets;
- DuckDB for materialized analytical output.

Avoid pickle for persisted cross-version artifacts.

---

## 16. Review decision contract

`ReviewDecision` is a project-owned decision envelope, not a generic approval
flag and not a permanent approval of a concept. It is created by the reusable
Review / Policy Service at a stage-scoped checkpoint only after the subject
artifact exists.

```yaml
schema_version: 1
review_decision_id: review_...
review_type: linkage
subject_artifact_id: artifact_...
subject_artifact_type: EntityCluster
subject_content_hash: sha256:...
subject_schema_version: 1
model_version: canonical-model-v1
run_id: run_...
subject_stage_id: ENTITY_RESOLUTION
subject_attempt_id: attempt_...
decision: ACCEPT   # ACCEPT | REJECT | OVERRIDE | LABEL | LOCK | DEFER | REVIEW_REQUIRED
actor: human:reviewer-reference
reason: evidence-backed rationale
created_at: 2026-09-09T00:00:00Z
policy_version: review-policy-v1
domain_assertion_refs: [assertion_...]
evidence_refs: [artifact_...]
conflict_refs: [conflict_...]
source_schema_fingerprints: [source_...]
subject_semantic_id: entity-family/customer
applicability_fingerprint:
  artifact_id: artifact_...
  content_hash: sha256:...
  schema_version: 1
  model_version: canonical-model-v1
  source_schema_fingerprints: [source_...]
  policy_version: review-policy-v1
  domain_assertion_refs: [assertion_...]
  subject_semantic_id: entity-family/customer
status: ACTIVE   # ACTIVE | SKIPPED | DEFERRED | REJECTED | INVALIDATED | SUPERSEDED
supersedes: null
invalidated_by: null
```

The decision must include the exact artifact ID, content hash, schema/model
version, run/stage/attempt identity, relevant source/schema fingerprints,
policy/domain scope and semantic subject ID. `ACCEPT`, `OVERRIDE`, `LABEL` and
`LOCK` may satisfy a checkpoint only according to its policy; `REJECT`,
`DEFER`, `REVIEW_REQUIRED` and `INVALIDATED` never satisfy an acceptance guard.
Policy-recorded `SKIPPED` is a checkpoint outcome, not an unrecorded absence of
review.

Replay is valid only when the applicability fingerprint remains semantically
compatible. A mapping, ER configuration, canonical hypothesis, grain, measure,
compiled-plan or generated-SQL change invalidates dependent review. The old
decision is retained and marked `INVALIDATED` or `SUPERSEDED`; it is never
mutated into a new approval and never replayed because a display name or
`review_type` happens to match. Incompatible replay leaves the guarded stage
and run in `NEEDS_REVIEW`.

All four runtime checkpoints use this same envelope and Review / Policy Service:
evidence/mapping review, canonical identity/linkage review, analytical-plan
review and materialization approval. Validation review may inspect a failed
`ValidationReport`, but cannot turn validation failure into PASS or run
`SUCCEEDED`; a correction requires invalidation, rerunning affected stages and
a new validation result.

---

## 17. Critical review applied before approval

### Problem A — A single generic “score” field would hide meaning

**Correction:** raw evidence values keep `value_semantics`, scope and provenance. Final confidence lives in decision contracts.

### Problem B — Sample statistics could be mistaken for exact facts

**Correction:** `ObservationScope` is mandatory.

### Problem C — Declared and inferred FKs could become indistinguishable

**Correction:** declared constraints and relationship decisions use separate types and provenance.

### Problem D — Entity cluster IDs could become canonical business IDs accidentally

**Correction:** `EntityCluster` and `SourceRecordCanonicalMap` are separate from the canonical entity ID assignment policy.

### Problem E — SQL generation could bypass model semantics

**Correction:** compiler consumes typed `FactSpec`/`DimensionSpec`, never ad-hoc prompt text.

**Status: APPROVED AS THE V1 INTERNAL CONTRACT BASELINE.**
