---
doc_type: master_specialist_execution_sequence
project: "Dirty Data to OLAP"
repository: "Dirty-Data-to-OLAP"
version: "1.0"
status: "authoritative"
execution_model: "one_primary_codex_prompt_per_specialty"
specialist_count: 41
keywords:
  - specialist execution order
  - master build sequence
  - codex orchestration
  - phase gates
  - handoff
  - prerequisites
  - one prompt per specialist
---

# Dirty Data to OLAP — Master Specialist Execution Sequence

## 1. Purpose

This document is the **authoritative execution-order contract** for building Dirty Data to OLAP when each specialty is given **one primary, comprehensive Codex prompt** from project start to release readiness.

It defines for every specialty:

1. **When** it should be activated.
2. **What must already exist** before it starts.
3. **What it must read and verify** before modifying the repository.
4. **What it must produce or validate** before handing off.
5. **What gate must pass** before downstream specialties may rely on its work.

This file does **not** replace the specialist playbooks. It decides **when** to activate them. Each `specialists/*.md` file defines **how** that specialty must work.

---

# 2. Critical Rule: Specialist File Number ≠ Execution Order

The files under `specialists/` are numbered for stable retrieval, not chronological execution.

For example:

```text
specialists/41_BUSINESS_DOMAIN_DATA_EXPERT.md
```

is **Execution Step 02**, while:

```text
specialists/01_TECHNICAL_LEAD.md
```

is **Execution Step 05**.

Therefore:

> **Never execute specialist files by filename order.**

The only authoritative chronological order is the sequence in this document.

---

# 3. Why This Sequence Exists

In a normal engineering team, Product, Architecture, QA, Security, SRE and Technical Leadership would participate repeatedly.

This plan assumes a special orchestration constraint:

> **One specialty receives one primary Codex prompt.**

Under that constraint, a specialty should be activated only when:

- enough upstream artifacts exist to make its pass meaningful;
- its output can still influence downstream implementation;
- the chance of needing a second full pass is minimized;
- optimization does not precede correctness;
- security and data safety are introduced before risky processing expands;
- release hardening occurs only after the real attack/runtime surface exists.

This is a **dependency-optimized build sequence**, not a claim that human teams should use strict waterfall.

---

# 4. Global Execution Rules

Every specialist must also follow:

- `00_README.md`
- `01_SPECIALIST_ROUTING_MATRIX.md`
- `02_GLOBAL_CODEX_EXECUTION_PROTOCOL.md`
- `03_SHARED_PROJECT_INVARIANTS.md`
- `04_SPECIALIST_ACTIVATION_TEMPLATE.md`
- relevant `base_reports/*.md`
- its own `specialists/*.md`

## 4.1 One prompt does not mean one careless attempt

A specialist's single primary prompt may and should include:

- repository inspection;
- implementation;
- refactoring within ownership;
- tests;
- correction of defects found during self-review;
- regression testing;
- documentation updates;
- evidence collection;
- final handoff.

The specialist must complete its responsibility, not merely produce recommendations.

## 4.2 Failed gate means stop and repair

Never do:

```text
FAIL → ignore → continue
```

Required behavior:

```text
FAIL
  ↓
diagnose
  ↓
repair
  ↓
re-test
  ↓
PASS
  ↓
continue
```

If the defect belongs to upstream work, the current specialist may patch it when necessary to restore a documented invariant, but must:

1. preserve valid upstream intent;
2. update affected contracts and tests;
3. record the correction;
4. never silently redefine business semantics.

## 4.3 Every specialist leaves a handoff receipt

Each execution should update:

```text
docs/execution/SPECIALIST_EXECUTION_LOG.md
```

Minimum record:

```yaml
execution_step:
role_id:
specialist_file:
status: PASS | BLOCKED
commit_sha:
inputs_reviewed:
artifacts_created_or_changed:
tests_run:
tests_passed:
known_limitations:
blocking_issues:
handoff_to:
```

## 4.4 Documentation is not implementation evidence

Before relying on upstream work, inspect:

- actual code;
- current branch and HEAD;
- tests;
- generated artifacts;
- execution log;
- previous gate evidence.

A Markdown claim that something exists is not proof that the current checkout implements it.

---

# 5. Master Sequence at a Glance

| Exec | Specialist | Specialist file | Primary outcome |
|---:|---|---|---|
| 01 | Product Manager / Technical Product Owner | `02_PRODUCT_OWNER.md` | Frozen V1 product contract |
| 02 | Business / Domain Data Expert | `41_BUSINESS_DOMAIN_DATA_EXPERT.md` | Business semantics and ambiguity rules |
| 03 | Principal Data Architect | `03_PRINCIPAL_DATA_ARCHITECT.md` | Data architecture and modeling invariants |
| 04 | Software / Solution Architect | `04_SOLUTION_ARCHITECT.md` | Component boundaries and internal contracts |
| 05 | Technical Lead / Engineering Lead | `01_TECHNICAL_LEAD.md` | Executable engineering plan and governance |
| 06 | Database Engineer / DBA | `07_DATABASE_ENGINEER_DBA.md` | Safe database access and introspection |
| 07 | Senior Data Engineer | `05_SENIOR_DATA_ENGINEER.md` | Source ingestion and staging |
| 08 | Data Profiling Specialist | `08_DATA_PROFILING_SPECIALIST.md` | Structured profiles and semantic hints |
| 09 | Data Quality Engineer | `09_DATA_QUALITY_ENGINEER.md` | Quality findings and repair proposals |
| 10 | Data Security / Privacy Engineer | `29_DATA_SECURITY_PRIVACY_ENGINEER.md` | PII and sensitive-data controls |
| 11 | Database Security Specialist | `30_DATABASE_SECURITY_SPECIALIST.md` | Read-only / least-privilege guarantees |
| 12 | Dependency Discovery Engineer | `11_DEPENDENCY_DISCOVERY_ENGINEER.md` | Hidden key/relationship evidence |
| 13 | Schema Matching Engineer | `10_SCHEMA_MATCHING_ENGINEER.md` | Cross-source schema equivalence evidence |
| 14 | Entity Resolution Engineer | `12_ENTITY_RESOLUTION_ENGINEER.md` | Cross-source identity linkage |
| 15 | Applied ML Engineer | `13_APPLIED_ML_ENGINEER.md` | Ranking, learning and calibration primitives |
| 16 | LLM / Semantic AI Engineer | `14_LLM_SEMANTIC_AI_ENGINEER.md` | Bounded semantic reasoning |
| 17 | Evidence Fusion Engineer | `15_EVIDENCE_FUSION_ENGINEER.md` | Explainable evidence/confidence engine |
| 18 | ML Evaluation Engineer | `25_ML_EVALUATION_ENGINEER.md` | Empirical accuracy and calibration evidence |
| 19 | Canonical Data Model Engineer | `16_CANONICAL_DATA_MODEL_ENGINEER.md` | Canonical entities and provenance |
| 20 | Data Warehouse / OLAP Engineer | `06_OLAP_ENGINEER.md` | Facts, dimensions and explicit grain |
| 21 | Analytical / Semantic Layer Engineer | `17_ANALYTICAL_SEMANTIC_LAYER_ENGINEER.md` | Measures, metrics and analytical semantics |
| 22 | Data QA Engineer | `24_DATA_QA_ENGINEER.md` | Source-to-OLAP correctness proof |
| 23 | Data Platform Engineer | `35_DATA_PLATFORM_ENGINEER.md` | Durable metadata/state/artifact platform |
| 24 | Distributed Data Engineer | `36_DISTRIBUTED_DATA_ENGINEER.md` | Large-scale execution strategy |
| 25 | UX / Product Designer | `21_UX_PRODUCT_DESIGNER.md` | Human review workflows |
| 26 | Data Visualization Engineer | `22_DATA_VISUALIZATION_ENGINEER.md` | Schema/lineage/evidence visualization |
| 27 | Senior Backend Engineer | `18_SENIOR_BACKEND_ENGINEER.md` | API/control plane |
| 28 | Distributed Job Processing Engineer | `19_DISTRIBUTED_JOB_PROCESSING_ENGINEER.md` | Durable async execution |
| 29 | Frontend Engineer | `20_FRONTEND_ENGINEER.md` | Review/control UI |
| 30 | DevOps Engineer | `32_DEVOPS_ENGINEER.md` | CI/CD, containers, reproducible runtime |
| 31 | QA Automation Engineer | `23_QA_AUTOMATION_ENGINEER.md` | E2E/integration/regression automation |
| 32 | Compatibility Test Engineer | `38_COMPATIBILITY_TEST_ENGINEER.md` | Supported source/environment matrix |
| 33 | Application Security Engineer | `28_APPLICATION_SECURITY_ENGINEER.md` | Full application hardening |
| 34 | Observability Engineer | `34_OBSERVABILITY_ENGINEER.md` | Logs, metrics, traces and diagnostics |
| 35 | Site Reliability Engineer | `33_SRE.md` | Reliability policy and recovery behavior |
| 36 | Chaos / Resilience Engineer | `37_CHAOS_RESILIENCE_ENGINEER.md` | Proven failure recovery |
| 37 | Performance Engineer | `26_PERFORMANCE_ENGINEER.md` | Bottleneck removal and baselines |
| 38 | Load / Stress Test Engineer | `27_LOAD_STRESS_TEST_ENGINEER.md` | Capacity and breakpoint evidence |
| 39 | Penetration Tester / Red Team | `31_PENETRATION_TESTER_RED_TEAM.md` | Adversarial security validation |
| 40 | Developer Experience Engineer | `40_DEVELOPER_EXPERIENCE_ENGINEER.md` | Reliable setup/CLI/dev workflow |
| 41 | Technical Writer | `39_TECHNICAL_WRITER.md` | Final implementation-accurate documentation |

---

# 6. Phase 0 — Product and Architecture Foundation

## Step 01 — Product Manager / Technical Product Owner

**Playbook:** `specialists/02_PRODUCT_OWNER.md`

### Why first

No implementation should begin until V1 has a precise product boundary.

### Must establish

- V1 objective;
- target users;
- supported source types;
- user journey;
- user approval points;
- in-scope capabilities;
- explicit non-goals;
- acceptance criteria;
- product terminology;
- final V1 Definition of Done.

### Required output

A product contract answering:

```text
What does the user provide?
What does the system discover?
What may it change?
What must a human approve?
What exact OLAP-ready result is delivered?
What is explicitly not V1?
```

### Gate G0 — Product Contract

PASS only if:

- V1 scope is unambiguous;
- OLAP-ready output is defined;
- destructive source modification is excluded;
- streaming/CDC/unstructured creep is excluded;
- acceptance criteria are testable.

---

## Step 02 — Business / Domain Data Expert

**Playbook:** `specialists/41_BUSINESS_DOMAIN_DATA_EXPERT.md`

### Why second

Statistical evidence cannot define business truth by itself.

A field called `customer`, `account`, `sale`, `order`, `payment` or `status` may have organization-specific meaning.

### Must establish

- domain glossary;
- business entity definitions;
- important business events;
- business-key candidates;
- relationship semantics;
- source authority expectations;
- ambiguity catalogue;
- examples of valid/invalid interpretations.

### Gate G1 — Domain Truth

PASS only if critical business terms have explicit definitions and ambiguous concepts are marked rather than guessed.

---

## Step 03 — Principal Data Architect

**Playbook:** `specialists/03_PRINCIPAL_DATA_ARCHITECT.md`

### Must establish

- source representation rules;
- business key vs surrogate key rules;
- provenance;
- lineage;
- canonical identity boundaries;
- source precedence principles;
- conflict handling principles;
- dimensional modeling constraints;
- fact-grain rules;
- SCD boundaries;
- reversible traceability.

### Non-negotiable

No later layer may depend on:

- undocumented identity;
- implicit keys;
- destructive deduplication;
- facts without grain;
- canonical fields with no provenance.

---

## Step 04 — Software / Solution Architect

**Playbook:** `specialists/04_SOLUTION_ARCHITECT.md`

### Must define

```text
Sources
→ Source Adapter
→ Profiling
→ Quality
→ Dependency Discovery
→ Schema Matching
→ Entity Resolution
→ Evidence Fusion
→ Canonical Model
→ OLAP Planner
→ Validation
→ Materialization
```

### Critical adapter rule

Third-party tools must not become the project's internal language.

Examples:

```text
dlt          → SourceAdapter
DataProfiler → ProfilingAdapter
Desbordante  → DependencyDiscoveryAdapter
Valentine    → SchemaMatchingAdapter
Splink       → EntityResolutionAdapter
```

All downstream components consume project-owned contracts.

---

## Step 05 — Technical Lead / Engineering Lead

**Playbook:** `specialists/01_TECHNICAL_LEAD.md`

### Why Step 05 instead of Step 01

A one-pass Technical Lead is more valuable after Product, Domain, Data Architecture and Software Architecture already exist.

### Must produce

- repository structure;
- implementation milestones;
- coding standards;
- dependency rules;
- ownership rules;
- test strategy;
- architecture enforcement;
- CI expectations;
- release gates;
- Definition of Done for implementation stages.

### Gate G2 — Architecture Ready

Required state:

```text
Product contract       PASS
Domain semantics       PASS
Data architecture      PASS
Software architecture  PASS
Engineering plan       PASS
```

Only then may implementation begin.

---

# 7. Phase 1 — Source Access and Ingestion

## Step 06 — Database Engineer / DBA

**Playbook:** `specialists/07_DATABASE_ENGINEER_DBA.md`

### Owns

- database connection behavior;
- introspection;
- transactions/isolation implications;
- timeouts;
- pooling;
- query cost;
- safe sampling;
- large-table behavior;
- database-specific metadata quirks.

### Must not

- infer business semantics;
- perform destructive cleaning;
- require excessive privileges.

### Handoff

Provide tested database access/introspection foundations to the Senior Data Engineer.

---

## Step 07 — Senior Data Engineer

**Playbook:** `specialists/05_SENIOR_DATA_ENGINEER.md`

### Owns

- dlt integration;
- SQL extraction;
- CSV/Excel/Parquet ingestion;
- staging;
- chunked reads;
- source snapshots;
- schema ingestion;
- source-to-internal-contract adaptation.

### Critical invariant

Raw source truth remains recoverable.

Canonicalization or cleaning never overwrites the only copy of raw evidence.

### Gate G3A — Source Usability

A representative source can execute:

```text
connect
→ introspect
→ extract
→ normalize into internal source/schema contract
→ stage
```

repeatably and under tests.

---

# 8. Phase 2 — Data Understanding

## Step 08 — Data Profiling Specialist

**Playbook:** `specialists/08_DATA_PROFILING_SPECIALIST.md`

### Must generate project-owned profile artifacts for

- null ratio;
- uniqueness;
- cardinality;
- distribution;
- physical type;
- patterns;
- semantic hints;
- sample provenance.

Raw DataProfiler classes must not leak into cross-component contracts.

---

## Step 09 — Data Quality Engineer

**Playbook:** `specialists/09_DATA_QUALITY_ENGINEER.md`

### Must detect and classify

- completeness issues;
- uniqueness issues;
- validity issues;
- consistency issues;
- referential issues where evidence exists;
- semantic inconsistencies where safely detectable.

### Must separate

```text
Detection
Proposal
Approval
Transformation
Validation
```

A repair proposal is not permission to mutate the source.

### Gate G3B — Data Condition Measurable

The system can answer:

```text
What is wrong?
Where?
How much?
Why does the system believe it?
Can it be repaired?
How will repair be validated?
```

---

# 9. Phase 3 — Early Data and Source Security

## Step 10 — Data Security / Privacy Engineer

**Playbook:** `specialists/29_DATA_SECURITY_PRIVACY_ENGINEER.md`

### Why before Entity Resolution and LLM work

These later stages may touch or amplify exposure of:

- names;
- phone numbers;
- email;
- national identifiers;
- addresses;
- account data.

### Must establish

- PII classification;
- masking/redaction;
- safe sampling;
- log redaction;
- artifact sensitivity;
- retention;
- LLM data exposure rules.

---

## Step 11 — Database Security Specialist

**Playbook:** `specialists/30_DATABASE_SECURITY_SPECIALIST.md`

### Must prove

- least privilege;
- read-only access;
- credentials safety;
- query guardrails;
- connection isolation;
- no unsafe SQL path;
- no secret leakage into logs/artifacts.

### Gate G3 — Source Safety

No later intelligence component may depend on unsafe production privileges or unrestricted raw PII exposure.

---

# 10. Phase 4 — Structural and Identity Discovery

## Step 12 — Dependency Discovery / Constraint Mining Engineer

**Playbook:** `specialists/11_DEPENDENCY_DISCOVERY_ENGINEER.md`

### Discover evidence for

- candidate keys;
- UCC;
- FD;
- approximate FD;
- IND;
- approximate IND;
- hidden FK candidates.

### Critical rule

An inclusion dependency is **evidence**, not a confirmed business relationship.

---

## Step 13 — Schema Matching / Data Integration Research Engineer

**Playbook:** `specialists/10_SCHEMA_MATCHING_ENGINEER.md`

### Generate evidence such as

```text
crm.mobile
≈
erp.phone_number
```

using:

- metadata similarity;
- instance/value similarity;
- profile information;
- structural signals.

A name match alone cannot become semantic truth.

---

## Step 14 — Entity Resolution / Record Linkage Engineer

**Playbook:** `specialists/12_ENTITY_RESOLUTION_ENGINEER.md`

### Must implement/evaluate

- normalization;
- blocking;
- comparison features;
- probabilistic linkage;
- clustering;
- threshold behavior;
- false merge controls;
- source-record preservation.

### Gate G4A — Independent Evidence Producers

At this point the project should have separately traceable:

```text
profiles
quality evidence
dependency evidence
schema-match evidence
entity-linkage evidence
```

---

# 11. Phase 5 — Learned and Semantic Intelligence

## Step 15 — Applied ML Engineer

**Playbook:** `specialists/13_APPLIED_ML_ENGINEER.md`

### May add

- candidate ranking;
- learned match scores;
- uncertainty;
- calibration infrastructure;
- active learning hooks;
- anomaly prioritization.

### Rule

Learned signals complement deterministic evidence; they do not erase it.

---

## Step 16 — LLM / Semantic AI Engineer

**Playbook:** `specialists/14_LLM_SEMANTIC_AI_ENGINEER.md`

### Good responsibilities

- propose business meaning;
- identify semantic hypotheses;
- suggest entity type;
- explain ambiguous fields;
- produce structured semantic evidence.

### Forbidden design

```text
send schema to LLM
→ accept answer as truth
```

### Gate G4 — AI Is Bounded

- LLM output is structured and attributable.
- LLM output is evidence, not ground truth.
- privacy controls apply.
- failure/timeout cannot corrupt deterministic processing.
- no core integrity rule depends solely on an LLM response.

---

# 12. Phase 6 — Core Evidence Intelligence

## Step 17 — Evidence Fusion Engineer

**Playbook:** `specialists/15_EVIDENCE_FUSION_ENGINEER.md`

This is one of the project's core differentiators.

### Must combine, without losing provenance

- declared metadata;
- type compatibility;
- profiles;
- overlap/inclusion;
- FD/IND/UCC;
- schema matching;
- entity-resolution consistency;
- ML signals;
- bounded LLM semantic evidence;
- conflict signals.

### Required style of output

```yaml
hypothesis: "orders.client_no -> customers.customer_code"
relationship_type: "many_to_one_candidate"
confidence_score: 0.94
confidence_kind: "uncalibrated_score"
evidence:
  - source: inclusion_dependency
    score: 0.973
  - source: target_uniqueness
    score: 0.998
  - source: schema_match
    score: 0.91
conflicts: []
decision_state: "review_required"
```

### Critical invariant

A score is not called a probability unless empirical calibration supports that claim.

---

## Step 18 — ML Evaluation Engineer

**Playbook:** `specialists/25_ML_EVALUATION_ENGINEER.md`

### Why immediately after fusion

Canonical modeling must not rely on inference until inference quality is measured.

### Must evaluate

- relationship precision/recall;
- schema-match precision/recall;
- entity-resolution precision/recall;
- ranking quality;
- calibration;
- threshold trade-offs;
- corruption-type failure modes;
- synthetic ground-truth scenarios.

### Gate G5 — Inference Validity

Any auto-accept/review/reject threshold must have empirical justification.

Uncalibrated confidence must remain labeled as score/confidence, not probability.

---

# 13. Phase 7 — Canonical and OLAP Modeling

## Step 19 — Canonical Data Model Engineer

**Playbook:** `specialists/16_CANONICAL_DATA_MODEL_ENGINEER.md`

### Must build

```text
CRM.Customer
ERP.Client
Web.User
     ↓
Canonical Customer
```

with:

- canonical IDs;
- source mappings;
- field-level provenance;
- survivorship;
- conflicts;
- source precedence where justified;
- traceability back to source rows.

### Critical rule

Entity resolution does not mean deleting original records.

---

## Step 20 — Data Warehouse / OLAP Engineer

**Playbook:** `specialists/06_OLAP_ENGINEER.md`

### Must produce

- dimension candidates;
- fact candidates;
- explicit grain;
- surrogate-key strategy;
- time dimensions;
- measures;
- conformed dimensions;
- SCD behavior in V1 scope;
- materializable star schema.

### Non-negotiable

No fact table may be accepted without explicit grain.

Good:

```text
one product line in one completed order
```

Bad:

```text
sales
```

---

## Step 21 — Analytical Model / Semantic Layer Engineer

**Playbook:** `specialists/17_ANALYTICAL_SEMANTIC_LAYER_ENGINEER.md`

### Adds

- measures;
- metrics;
- analytical terminology;
- dimensional semantics;
- additive/semi-additive/non-additive behavior;
- semantic contracts.

---

## Step 22 — Data QA Engineer

**Playbook:** `specialists/24_DATA_QA_ENGINEER.md`

### Must reconcile

```text
source
↕
canonical
↕
OLAP
```

### Required checks

- row/count reconciliation;
- legitimate deduplication;
- lost records;
- duplicate facts;
- orphan rates;
- key integrity;
- aggregate reconciliation;
- conditional monetary/amount reconciliation only where a versioned domain contract defines the measure;
- date coverage;
- fact-grain uniqueness;
- source-to-target traceability.

### Gate G6 — Data Correctness

Representative fixtures must prove business truth survives transformation.

The UI/API layer is not allowed to present the analytical output as trustworthy before this gate passes.

---

# 14. Phase 8 — Data Platform and Scale

## Step 23 — Data Platform Engineer

**Playbook:** `specialists/35_DATA_PLATFORM_ENGINEER.md`

### Owns

- metadata persistence;
- run state;
- artifact storage;
- caching;
- staging lifecycle;
- workspace/project state;
- reproducibility;
- artifact organization.

Minimal staging may exist earlier; this is the comprehensive platform pass.

---

## Step 24 — Distributed Data Engineer

**Playbook:** `specialists/36_DISTRIBUTED_DATA_ENGINEER.md`

### Optimize for

- large tables;
- partitioned processing;
- parallel profiling;
- distributed comparisons;
- large joins;
- bounded memory;
- scalable materialization.

### Gate G7A — Scale Preserves Meaning

Distributed paths must be semantically equivalent to the validated reference path.

Do not make incorrect computation faster.

---

# 15. Phase 9 — Human Review Experience

## Step 25 — UX / Product Designer

**Playbook:** `specialists/21_UX_PRODUCT_DESIGNER.md`

### Must design flows for

- source connection;
- quality review;
- relationship review;
- schema mapping approval;
- entity merge review;
- transformation approval;
- canonical model review;
- OLAP model review;
- validation failure handling.

### Core principle

Uncertainty must remain visible.

A candidate score of `0.82` cannot be displayed as "Confirmed."

---

## Step 26 — Data Visualization Engineer

**Playbook:** `specialists/22_DATA_VISUALIZATION_ENGINEER.md`

### Must support visualization of

- source schema graph;
- inferred relationships;
- lineage;
- quality heatmap;
- entity clusters;
- evidence breakdown;
- before/after state;
- fact/dimension model.

### Gate G7B — Reviewable Decisions

A user can understand:

```text
what the system believes
why
how certain it is
what will change
what remains unresolved
```

---

# 16. Phase 10 — Application Layer

## Step 27 — Senior Backend Engineer

**Playbook:** `specialists/18_SENIOR_BACKEND_ENGINEER.md`

### Must implement

- project/run APIs;
- orchestration surface;
- artifact access;
- review/approval endpoints;
- state transitions;
- error contracts;
- authentication integration points.

Business/data-engine logic must not be duplicated inside API handlers.

---

## Step 28 — Distributed Systems / Job Processing Engineer

**Playbook:** `specialists/19_DISTRIBUTED_JOB_PROCESSING_ENGINEER.md`

### Make heavy operations

- asynchronous;
- retryable;
- idempotent;
- cancellable;
- resumable where appropriate;
- observable;
- project/run isolated.

### State must be truthful

Example:

```text
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCELLED
BLOCKED
```

Partial failure must not masquerade as success.

---

## Step 29 — Frontend Engineer

**Playbook:** `specialists/20_FRONTEND_ENGINEER.md`

### Must consume

- real API contracts;
- real uncertainty states;
- real validation states;
- real evidence structures;
- approved UX/visualization specifications.

### Gate G7 — End-to-End Product Path

A representative user can execute:

```text
connect/import source
→ discover/profile
→ inspect evidence
→ review/approve
→ canonicalize
→ build OLAP
→ validate
→ inspect output
```

through the product path.

---

# 17. Phase 11 — Engineering Infrastructure

## Step 30 — DevOps Engineer

**Playbook:** `specialists/32_DEVOPS_ENGINEER.md`

### Must establish

- Docker images;
- Compose/local stack;
- reproducible environment;
- CI;
- automated checks;
- build pipeline;
- dependency/security scan hooks;
- release foundation;
- secret injection pattern.

### Gate G8 — Reproducible Build

A clean environment can build and run required checks without undocumented local state.

---

# 18. Phase 12 — System Verification

## Step 31 — QA Automation Engineer

**Playbook:** `specialists/23_QA_AUTOMATION_ENGINEER.md`

### This is not the first testing activity

Every prior specialist must already test its own work.

This pass owns **independent system-level** automation:

- integration tests;
- E2E tests;
- API tests;
- review-flow tests;
- failure-path tests;
- regression suite.

---

## Step 32 — Compatibility Test Engineer

**Playbook:** `specialists/38_COMPATIBILITY_TEST_ENGINEER.md`

### Validate only what the project claims to support

Examples:

- PostgreSQL versions;
- MySQL/MariaDB where claimed;
- SQL Server where claimed;
- CSV dialect/encoding cases;
- Excel variants;
- Parquet;
- OS/container boundaries.

### Gate G9 — Claims Match Tested Support

Unsupported/untested combinations must not appear as supported in release documentation.

---

# 19. Phase 13 — Application Security

## Step 33 — Application Security Engineer

**Playbook:** `specialists/28_APPLICATION_SECURITY_ENGINEER.md`

### Why now

The complete application attack surface now exists.

### Review and remediate

- authentication;
- authorization;
- cross-project isolation;
- SSRF;
- SQL injection surfaces;
- upload handling;
- path traversal;
- unsafe deserialization;
- secret handling;
- dependency risks;
- API abuse;
- debug leakage;
- sensitive error output.

### Required behavior

Do not stop at a report.

Implement safe fixes and regression tests for actionable findings.

### Gate G10 — Application Security

No known critical/high application vulnerability may remain unmitigated without explicitly blocking release.

---

# 20. Phase 14 — Observability and Reliability

## Step 34 — Observability Engineer

**Playbook:** `specialists/34_OBSERVABILITY_ENGINEER.md`

### Add

- structured logs;
- metrics;
- traces;
- run correlation;
- worker/job telemetry;
- adapter timing;
- error classification;
- resource/queue visibility.

---

## Step 35 — Site Reliability Engineer (SRE)

**Playbook:** `specialists/33_SRE.md`

### Use actual runtime telemetry to define

- SLO candidates;
- recovery expectations;
- resource constraints;
- retry limits;
- operational failure behavior;
- incident diagnostics;
- reliability policies.

---

## Step 36 — Chaos / Resilience Engineer

**Playbook:** `specialists/37_CHAOS_RESILIENCE_ENGINEER.md`

### Inject

- source DB disconnect;
- worker crash;
- timeout;
- queue interruption;
- adapter failure;
- storage failure where testable;
- process restart;
- partial availability.

### Must prove

- no silent corruption;
- truthful state;
- safe retry;
- recoverability;
- audit trail preservation.

### Gate G11 — Resilience

Failure must never silently become corrupted analytical output.

---

# 21. Phase 15 — Performance and Capacity

## Step 37 — Performance Engineer

**Playbook:** `specialists/26_PERFORMANCE_ENGINEER.md`

### Profile first, optimize second

Measure:

- CPU;
- RAM;
- disk;
- DB query cost;
- serialization;
- profiling;
- dependency discovery;
- schema matching;
- entity resolution;
- evidence fusion;
- OLAP materialization.

Step37 MUST reuse the existing benchmark estate and truth fixtures, with
`base_reports/08_BENCHMARK_AND_VALIDATION_PLAN.md` owning the canonical V1
scale definitions. The `1M/10M/100M` wording is feasibility exploration, not
a mandatory acceptance ladder: execute `1M` or several-million-row local
benchmarks where practical, treat `10M` as optional when resource-safe, and
treat `100M` as design/feasibility unless it can genuinely be executed.

Any optimization affecting inference search space, candidate sets, matching,
entity resolution, evidence coverage, or confidence inputs requires a rerun of
the relevant truth-backed empirical quality evaluation; semantic hashes alone
do not establish correctness. Every accepted optimization must reference a
concrete `PERF-FIND-*` bottleneck finding and use the smallest coherent change.

### Required output

Pre/post optimization measurements.

No unsupported speedup claims.

Step37 produces single-run baselines and bottleneck evidence only. It does not
close G12 or claim capacity. Concurrency, arrival rates, saturation, soak,
capacity breakpoints, overload recovery, and the capacity envelope belong to
Step38. The Step37 handoff therefore preserves `G11=PASS`, `G12=PENDING`, and
`Step38=NOT_STARTED`.

---

## Step 38 — Load / Stress Test Engineer

**Playbook:** `specialists/27_LOAD_STRESS_TEST_ENGINEER.md`

### Establish

- throughput;
- concurrency;
- connection pressure;
- queue saturation;
- memory/resource exhaustion;
- graceful degradation;
- breakpoint;
- post-overload recovery.

### Gate G12 — Capacity Known

The release should know not just whether it works, but roughly **where and how it fails under load**.

---

# 22. Phase 16 — Final Adversarial Security

## Step 39 — Penetration Tester / Red Team

**Playbook:** `specialists/31_PENETRATION_TESTER_RED_TEAM.md`

### Attack

- auth boundaries;
- project isolation;
- SSRF;
- SQL abuse;
- credential exposure;
- malicious input;
- privilege escalation;
- raw PII leakage;
- job/control-plane abuse.

### Gate G13 — Adversarial Security Acceptance

Critical/high findings block release until remediated or explicitly accepted under a documented release-blocking decision.

---

# 23. Phase 17 — Developer Experience

## Step 40 — Developer Experience Engineer

**Playbook:** `specialists/40_DEVELOPER_EXPERIENCE_ENGINEER.md`

### Goal

No tribal knowledge should be required to run or contribute.

### Owns

- installation;
- local dev;
- CLI ergonomics;
- example config;
- deterministic demo;
- developer commands;
- useful errors;
- troubleshooting path.

### Gate G14 — Usability

A clean-machine-style setup path succeeds using documented commands.

---

# 24. Phase 18 — Final Documentation

## Step 41 — Technical Writer

**Playbook:** `specialists/39_TECHNICAL_WRITER.md`

### Why last

The final documentation must describe the product that actually survived:

- implementation;
- QA;
- compatibility;
- security;
- reliability;
- performance;
- DX.

### Must document

- problem statement;
- architecture;
- quickstart;
- supported sources;
- workflow;
- configuration;
- confidence/evidence semantics;
- canonical/OLAP model;
- security;
- limitations;
- benchmarks;
- troubleshooting;
- developer guide.

### Gate G15 — Release

Documentation must not claim:

- unsupported sources;
- unmeasured accuracy;
- unmeasured performance;
- full autonomy where human review is required;
- probability where only a score exists;
- safe/lossless deduplication without reconciliation proof.

---

# 25. Cross-Phase Gate Table

| Gate | After step | Required proof |
|---|---:|---|
| G0 Product Contract | 01 | V1 scope + testable acceptance |
| G1 Domain Truth | 02 | Business glossary + ambiguity rules |
| G2 Architecture Ready | 05 | Product/domain/data/software/engineering alignment |
| G3 Source Safety | 11 | Safe ingestion + privacy + read-only controls |
| G4 Bounded Intelligence | 16 | Independent evidence + bounded ML/LLM |
| G5 Inference Validity | 18 | Benchmark/evaluation/calibration evidence |
| G6 Data Correctness | 22 | Source→canonical→OLAP reconciliation |
| G7 End-to-End Product | 29 | Real UI/API/engine path |
| G8 Reproducible Build | 30 | Clean build/test environment |
| G9 Functional Support | 32 | E2E + compatibility matrix |
| G10 Application Security | 33 | No unresolved critical/high app defect |
| G11 Resilience | 36 | Failure recovery without silent corruption |
| G12 Capacity | 38 | Performance/load envelope characterized |
| G13 Adversarial Security | 39 | Red-team acceptance |
| G14 Usability | 40 | Repeatable setup/dev path |
| G15 Release | 41 | Docs match tested implementation |

---

# 26. Artifact Handoff Chain

```text
Product Contract
      ↓
Domain Glossary
      ↓
Data Architecture
      ↓
System Architecture
      ↓
Engineering Plan
      ↓
DB Access / Connector Contracts
      ↓
Staged Source Data + Schema
      ↓
Profiles
      ↓
Quality Findings
      ↓
Privacy + Source Safety
      ↓
Dependency Evidence
      ↓
Schema Matching Evidence
      ↓
Entity Linkage Evidence
      ↓
ML + Semantic Evidence
      ↓
Evidence Fusion
      ↓
Evaluation / Calibration
      ↓
Canonical Model
      ↓
Fact / Dimension / Grain Model
      ↓
Semantic Analytical Layer
      ↓
Data Reconciliation
      ↓
Platform + Scale
      ↓
UX + Visualization
      ↓
Backend + Jobs + Frontend
      ↓
CI / Runtime
      ↓
QA + Compatibility
      ↓
AppSec
      ↓
Observability + SRE + Chaos
      ↓
Performance + Stress
      ↓
Red Team
      ↓
DX
      ↓
Final Documentation
```

---

# 27. Key Dependency Matrix

| Specialist | Must rely on / verify |
|---|---|
| Product Owner | Base reports |
| Domain Expert | Product contract |
| Principal Data Architect | Product + Domain |
| Solution Architect | Product + Data Architecture |
| Technical Lead | Product + Domain + Data + Solution Architecture |
| DBA | Architecture + engineering plan |
| Senior Data Engineer | DBA + internal contracts + reuse plan |
| Profiling | Staged data + contracts |
| Data Quality | Profiles + domain rules |
| Privacy | Sources + profiling + product policy |
| DB Security | DBA + connectors |
| Dependency Discovery | Profiles + staged data |
| Schema Matching | Profiles + dependency evidence |
| Entity Resolution | Profiles + mappings + privacy |
| Applied ML | Evidence producers + benchmark design |
| LLM/Semantic AI | Domain + privacy + evidence contracts |
| Evidence Fusion | All evidence producers |
| ML Evaluation | Fusion + ground-truth fixtures |
| Canonical Model | Evaluated evidence + Data Architect |
| OLAP | Canonical + Domain + Data Architect |
| Semantic Layer | OLAP + Product/Domain |
| Data QA | Source + Canonical + OLAP |
| Data Platform | Validated engine |
| Distributed Data | Validated engine + platform |
| UX | Product + evidence states + model workflows |
| Visualization | UX + lineage/evidence contracts |
| Backend | Engine contracts + UX |
| Distributed Jobs | Backend + engine execution model |
| Frontend | UX + visualization + API |
| DevOps | Whole application stack |
| QA Automation | End-to-end stack |
| Compatibility | Connectors + packaging + QA |
| AppSec | Complete app + privacy/DB security |
| Observability | Backend/jobs/platform/runtime |
| SRE | Observability + runtime |
| Chaos | Jobs + SRE + observability |
| Performance | Correct stable system + telemetry |
| Load/Stress | Performance baseline + deployment |
| Red Team | Hardened application |
| DX | Stable install/runtime/API |
| Technical Writer | Entire tested release |

---

# 28. Work That Must Not Be Reordered

## 28.1 Evidence Fusion before evidence producers — forbidden

Fusion must consume stable project-owned contracts from profiling, dependency discovery, schema matching and entity resolution.

## 28.2 Canonical Model before integrated evaluation — forbidden

Canonicalization must not treat unvalidated inference scores as truth.

## 28.3 OLAP before Canonical Model — forbidden

Fact/dimension modeling must not be driven directly by dirty raw-source naming accidents.

## 28.4 Frontend before UX and API contracts — strongly discouraged

A premature UI tends to invent states that later become accidental backend contracts.

## 28.5 Performance before correctness — forbidden as the primary optimization pass

First establish correct reference behavior, then optimize it.

## 28.6 Red Team before AppSec hardening — inefficient and lower value

Red Team should test defenses, not merely rediscover obvious unpatched issues.

## 28.7 Final Technical Writer pass before hardening — forbidden

Documentation may be updated earlier, but the one final documentation specialty must run after tested release behavior stabilizes.

---

# 29. Controlled Exceptions

The **primary specialist passes** follow this sequence, but supporting responsibilities exist from day one.

Examples:

- Technical Lead may establish minimal CI before DevOps Step 30.
- Every specialist writes tests; QA Automation is not the first testing activity.
- Every specialist follows security invariants; AppSec Step 33 is the full application hardening pass.
- Data Engineer may create minimal artifact storage; Data Platform Step 23 is the comprehensive platform pass.
- Feature owners update working docs; Technical Writer Step 41 is the final release-accuracy pass.

Rule:

> A later specialist pass is the authoritative review/hardening of that discipline, not the first moment anyone is allowed to care about it.

---

# 30. One-Prompt Specialist Activation Header

Every eventual Codex specialist prompt should begin with an activation block equivalent to:

```text
You are executing Specialist Step <N>/41 for Dirty Data to OLAP.

Primary specialist:
<specialist name>

Authoritative playbook:
specialists/<specialist file>.md

Authoritative execution order:
05_MASTER_BUILD_SEQUENCE.md

Shared required documents:
00_README.md
01_SPECIALIST_ROUTING_MATRIX.md
02_GLOBAL_CODEX_EXECUTION_PROTOCOL.md
03_SHARED_PROJECT_INVARIANTS.md
04_SPECIALIST_ACTIVATION_TEMPLATE.md

Before modifying code:
1. Verify current branch and HEAD.
2. Read required upstream artifacts.
3. Inspect actual implementation and tests.
4. Verify the previous hard gate actually passed.
5. Do not trust documentation as implementation evidence.

Complete the entire specialty pass:
inspect → implement/fix → test → self-review → repair → re-test → document → hand off.

Do not stop at recommendations when changes are required, in scope, and safely implementable.

At completion:
- run mandatory specialty tests;
- run relevant regressions;
- record changed artifacts;
- record limitations;
- update specialist execution log;
- report PASS only with evidence.
```

The remainder of each prompt should be generated from the corresponding specialist playbook and current repository state.

---

# 31. Recommended Persistent Execution State

To make future Codex sessions impossible to confuse about progress, create:

```text
docs/execution/
├── MASTER_EXECUTION_STATE.yml
├── SPECIALIST_EXECUTION_LOG.md
└── gates/
    ├── G0_PRODUCT_CONTRACT.md
    ├── G1_DOMAIN_TRUTH.md
    ├── ...
    └── G15_RELEASE.md
```

Example:

```yaml
project: Dirty Data to OLAP
sequence_version: "1.0"
current_step: 12
current_role: dependency_discovery
last_completed_step: 11
last_completed_role: database_security
last_verified_commit: "<sha>"
blocked: false
gates:
  G0_PRODUCT_CONTRACT: PASS
  G1_DOMAIN_TRUTH: PASS
  G2_ARCHITECTURE_READY: PASS
  G3_SOURCE_SAFETY: PASS
```

This is runtime state. This document is policy.

---

# 32. Critical Review of This Sequence

The sequence was challenged from multiple specialist viewpoints before being finalized.

## Criticism A — "Security is too late."

### Risk

Application Security is Step 33 and Red Team is Step 39.

### Fix incorporated

Security is deliberately split:

- Step 10: Privacy
- Step 11: Database Security
- security invariants from the beginning
- Step 33: complete application hardening
- Step 39: adversarial validation

Sensitive data and production DB access are therefore protected before advanced inference, while app-specific testing waits until the application exists.

---

## Criticism B — "QA is too late."

### Risk

QA Automation is Step 31.

### Fix incorporated

Every specialist must test its own work under the Global Codex Execution Protocol.

In addition:

- ML Evaluation is Step 18.
- Data QA is Step 22.
- QA Automation Step 31 is the independent full-system pass.

---

## Criticism C — "Technical Lead should be first."

### Risk

A Technical Lead starting first would have to invent Product/Domain/Data assumptions or waste its one prompt creating temporary placeholders.

### Fix incorporated

Technical Lead is Step 05 after foundational truth and architecture exist.

---

## Criticism D — "ML Evaluation after Evidence Fusion is late."

### Risk

Bad individual algorithms could be fused first.

### Fix incorporated

Each algorithm specialist must run component tests during its own pass.

Step 18 is the integrated evaluation/calibration gate before canonical modeling relies on fused inference.

---

## Criticism E — "Data Platform is too late."

### Risk

Storage and state are needed earlier.

### Fix incorporated

Minimal staging and artifacts are permitted in earlier architecture/data-engineering work.

Step 23 is the comprehensive Data Platform pass after core data correctness is established, avoiding premature infrastructure around unstable contracts.

---

## Criticism F — "DevOps is too late."

### Risk

The repository needs CI before Step 30.

### Fix incorporated

Technical Lead may establish minimum engineering checks earlier.

Step 30 is the complete DevOps/release-infrastructure pass once the actual stack is known.

---

## Criticism G — "One-pass specialists may discover upstream flaws."

### Risk

A downstream specialist could find a real defect owned by an upstream role that will not be prompted again.

### Fix incorporated

The execution rules allow necessary upstream correction if the current specialist:

- preserves invariants;
- updates contracts/tests;
- records the correction;
- does not silently redefine business truth.

Hard gates reduce the frequency of these late defects but do not pretend they are impossible.

---

## Criticism H — "The project could become a polished UI around wrong data."

### Fix incorporated

The Data QA correctness gate at Step 22 occurs **before** UX, Backend and Frontend.

The product layer therefore wraps a data path that already has correctness evidence.

---

# 33. Final Authoritative Order

```text
01  Product Manager / Technical Product Owner
02  Business / Domain Data Expert
03  Principal Data Architect
04  Software / Solution Architect
05  Technical Lead / Engineering Lead
06  Database Engineer / DBA
07  Senior Data Engineer
08  Data Profiling Specialist
09  Data Quality Engineer
10  Data Security / Privacy Engineer
11  Database Security Specialist
12  Dependency Discovery / Constraint Mining Engineer
13  Schema Matching / Data Integration Research Engineer
14  Entity Resolution / Record Linkage Engineer
15  Applied ML Engineer
16  LLM / Semantic AI Engineer
17  Evidence Fusion Engineer
18  ML Evaluation Engineer
19  Canonical Data Model Engineer
20  Data Warehouse / OLAP Engineer
21  Analytical Model / Semantic Layer Engineer
22  Data QA Engineer
23  Data Platform Engineer
24  Distributed Data Engineer
25  UX / Product Designer
26  Data Visualization Engineer
27  Senior Backend Engineer
28  Distributed Systems / Job Processing Engineer
29  Frontend Engineer
30  DevOps Engineer
31  QA Automation Engineer
32  Compatibility Test Engineer
33  Application Security Engineer
34  Observability Engineer
35  Site Reliability Engineer (SRE)
36  Chaos / Resilience Engineer
37  Performance Engineer
38  Load / Stress Test Engineer
39  Penetration Tester / Red Team
40  Developer Experience Engineer
41  Technical Writer
```

---

# 34. Final Authority Rule

If a future conflict exists between:

- the numeric filename order under `specialists/`;
- an old chat;
- a generated task list;
- an ad hoc implementation plan;
- this document;

then for the **one-primary-Codex-prompt-per-specialty build model**, this document is authoritative unless it is intentionally superseded by a newer versioned Master Build Sequence.

> **Never infer execution order from specialist filenames. Retrieve this document first.**
