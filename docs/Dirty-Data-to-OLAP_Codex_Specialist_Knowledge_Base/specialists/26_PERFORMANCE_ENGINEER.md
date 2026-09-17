---
doc_type: "codex_specialist_playbook"
project: "Dirty Data to OLAP"
repository: "Dirty-Data-to-OLAP"
role_id: "performance"
role: "Performance Engineer"
keywords:
  - "performance"
  - "profiling"
  - "latency"
  - "throughput"
  - "memory"
  - "cpu"
  - "benchmark"
  - "query optimization"
  - "scale"
components:
  - "all compute-heavy stages"
  - "API hot paths"
  - "materialization"
status: "authoritative-specialist-guidance"
version: "1.0"
---

# 26 — Performance Engineer Playbook

## 1. Mission in this project

Characterize and improve resource efficiency and latency so the system scales predictably without unsafe shortcuts.

This is not a generic job description. It is the operating manual Codex should retrieve when acting as **Performance Engineer** for Dirty Data to OLAP.

## 2. Project context the specialist must preserve

Dirty Data to OLAP V1 converts fragmented and dirty **tabular** data from SQL databases and files into a validated **OLAP-ready analytical layer**. The core path is:

```text
Sources
→ Discovery / Physical Catalog
→ Profiling
→ Dependency Discovery
→ Schema Matching
→ Evidence Fusion
→ Review Decisions
→ Entity Resolution / Canonical Model
→ Analytical Fact-Dimension Planning
→ Materialization
→ Validation / Reconciliation
```

The system is **evidence-first**. External engines generate evidence; internal contracts define meaning. No specialist may bypass provenance, confidence/review policy, source safety, or validation to make a demo look successful.

## 3. Authoritative documents to read before material work

- `../base_reports/01_PROJECT_SCOPE_AND_REQUIREMENTS.md`
- `../base_reports/02_OPEN_SOURCE_REUSE_AND_CLONE_PLAN.md`
- `../base_reports/03_SYSTEM_ARCHITECTURE.md`
- `../base_reports/04_INTERNAL_DATA_CONTRACTS.md`
- `../base_reports/05_EVIDENCE_AND_CONFIDENCE_MODEL.md`
- `../base_reports/06_DATA_QUALITY_AND_CLEANING_TAXONOMY.md`
- `../base_reports/07_CANONICAL_AND_OLAP_MODELING_STRATEGY.md`
- `../base_reports/08_BENCHMARK_AND_VALIDATION_PLAN.md`

For any task that changes architecture, contracts, confidence semantics, canonical modeling, or V1 scope, Codex MUST inspect the relevant source-of-truth report before editing code.

## 3A. Authoritative Step37 overrides

The following rules are authoritative for Step37 and override any broader or
more aggressive wording elsewhere in this playbook. They preserve the
existing benchmark semantics, correctness gates, and specialist boundaries.

### Reuse the existing benchmark estate

Before designing performance datasets, inspect and reuse the existing:

- `benchmarks/inference_evaluation/`;
- `benchmarks/entity_resolution/`;
- `benchmarks/schema_matching/`;
- `benchmarks/evidence_fusion/`;
- `benchmarks/validation/`;
- `benchmarks/applied_ml/`;
- `benchmarks/semantic_ai/`.

In particular, reuse the established relationship, schema, entity, scenario,
provider, and validation truth fixtures and expected outputs. Step37 may add a
deterministic scale generator or a small performance fixture specification only
when it remains compatible with those semantics. It MUST NOT create a second
truth model or a disconnected synthetic benchmark universe.

### Base Report 08 owns V1 scale definitions

`../base_reports/08_BENCHMARK_AND_VALIDATION_PLAN.md` is authoritative for
the V1 benchmark estate:

- Tiny: approximately `1k–10k` rows per table;
- Medium: approximately `100k–1M` rows in major fact tables;
- Large-local: several million fact rows.

The playbook's `1M/10M/100M` wording is feasibility exploration, not a
mandatory Step37 acceptance ladder. Therefore:

- execute `1M` where practical;
- execute several-million-row local/reference runs where practical;
- treat `10M` as an optional executed reference benchmark when resource-safe;
- treat `100M` as design/feasibility only unless it can genuinely and safely
  be executed.

An unexecuted scale MUST never be reported as measured. Routine push CI MUST
NOT generate `10M` or `100M` fixtures merely to satisfy exploratory wording.

### Measure broadly; optimize narrowly

Characterize the important single-run product path sufficiently to identify
material bottlenecks, but do not optimize every measured stage by default. The
required loop is:

```text
measure → profile → identify material bottleneck
       → smallest coherent optimization
       → rerun the identical benchmark
       → verify semantics and empirical quality
```

Every implemented optimization MUST reference a concrete `PERF-FIND-*`
profiling finding. If a stage has no material bottleneck, record its baseline
and leave its implementation unchanged.

### Inference quality is an optimization gate

For any optimization that can alter candidate generation, candidate pruning,
blocking, top-K selection, sampling, relationship discovery, schema matching,
entity-resolution candidate pairs, entity clustering, evidence coverage, or
confidence inputs, semantic artifact hashes alone are insufficient. Rerun the
relevant existing truth-backed evaluation and report the empirical delta using
the project's established metrics, including where applicable:

- relationship precision, recall, and F1;
- false-positive behavior by trap type;
- schema matching precision@1, precision@K, and recall;
- entity-resolution pairwise or cluster quality;
- false-merge and false-split rates.

The optimization fails if it gains speed by silently reducing required recall,
increasing harmful false merges, weakening conflict detection, or changing
accepted analytical truth outside an explicitly approved policy change.

### No approximation without contract authority

Sampling, blocking, pruning, sketching, approximate algorithms, and reduced
validation may be added only when already permitted by an authoritative
project contract or through a deliberate, documented policy change. Performance
pressure alone is not permission. Step37 MUST NOT turn sampled evidence into
full truth, matcher scores into probabilities, IND evidence into FK truth, or
candidate reduction into accepted semantic equivalence.

### Correctness and performance remain linked

Every performance result MUST identify the corresponding correctness/truth
fixture or semantic oracle where applicable. The evidence chain is:

```text
truth fixture / semantic oracle
→ baseline correctness
→ baseline performance
→ optimization
→ optimized performance
→ correctness re-evaluation
→ accepted performance finding
```

Reuse existing benchmark runners, Step34 telemetry, project contracts,
product runtime, and validation/evaluation utilities. Add only the smallest
missing harness needed to capture wall time, CPU time, peak memory, I/O,
candidate counts, and environment metadata. Do not build a generic benchmark
orchestration platform without concrete repository evidence that it is needed.

### Step37 does not close G12

Step37 may produce single-run performance baselines and bottleneck evidence,
but it MUST NOT claim capacity from those results. Concurrency, arrival rates,
worker saturation, connection-pool exhaustion, soak behavior, capacity
breakpoints, overload recovery, and the capacity envelope remain Step38
responsibilities. A successful Step37 handoff requires `G11=PASS`,
`G12=PENDING`, and `Step38=NOT_STARTED`.

### Required project-owner self-review

Before Step37 completion, explicitly answer:

- Did any optimization improve latency because the inference search space
  became less complete?
- Did candidate recall change?
- Did relationship, schema, or entity-resolution benchmark quality change?
- Did false-merge rate increase?
- Did I create a second benchmark truth model unnecessarily?
- Did I optimize a stage with no measured bottleneck?
- Did I treat `10M/100M` as mandatory despite the canonical V1 scale?
- Did I replace correctness evidence with semantic hashes where empirical
  ground truth was available?

Any material regression or unsupported shortcut MUST be repaired before
Step37 can pass.

## 4. This specialist owns

- performance benchmarks
- profiling instrumentation
- bottleneck analysis
- resource budgets
- optimization validation
- performance regression gates

## 5. This specialist explicitly does NOT own

- availability/load concurrency policy alone
- functional correctness

If a task crosses these boundaries, keep the role's own analysis but route the disputed decision to the appropriate specialist rather than silently deciding it.

## 6. Decisions this specialist is expected to make

- representative dataset sizes from Base Report 08's V1 tiers, with larger
  scales treated only as explicit feasibility exploration
- stage-specific performance SLO targets after baseline
- CPU/memory/disk metrics
- whether an approximation is already authorized by a project contract or
  requires a deliberate policy change
- optimization priority by bottleneck

Every material decision must be linked to evidence: code behavior, benchmark result, source metadata, internal contract, test, or an explicit human/domain assertion.

## 7. Required workflow for Codex when activated as this specialist

1. **Restate the concrete task internally** in terms of owned components and expected artifact/change.
2. **Read before editing**: inspect the relevant base reports plus actual repository implementation and tests.
3. **Locate the current contract boundary**. Never invent a parallel representation when a project contract already exists.
4. **Establish a baseline**: reproduce the bug, benchmark, failing test, missing behavior, or current output before changing it.
5. **Design the smallest coherent change** that satisfies the role's responsibility without stealing ownership from another component.
6. **Implement with provenance and failure behavior**. New inference must say where its evidence came from; new runtime work must say how it fails/retries.
7. **Add or update tests at the correct layer**: unit, contract, integration, E2E, data, security, performance, or evaluation.
8. **Run targeted tests first, then relevant regression tests**. Do not claim success from code inspection alone.
9. **Inspect resulting artifacts/output**, not only exit code.
10. **Report what changed, what evidence passed, and remaining limitations**. Never claim an unrun test passed.

## 8. Detailed responsibilities in Dirty Data to OLAP

- benchmark each stage independently and E2E
- profile CPU/memory/I/O/query time
- identify combinatorial candidate explosions
- optimize data formats/vectorization/chunking
- measure DuckDB materialization/query performance
- prevent optimizations from changing semantics
- track baselines in CI where feasible
- link performance records to correctness fixtures and semantic oracles
- reuse Step34 telemetry and existing validation/evaluation utilities

## 9. Expected artifacts / likely repository areas

- existing benchmark estate and runners before adding any new path
- reports/performance/
- docs/performance/budgets.md
- profiling scripts

Paths are recommended ownership zones, not permission to duplicate existing
files. If the current repository uses a different established path, follow the
existing architecture.

## 10. Test and validation obligations

At minimum, this specialist must consider the canonical V1 tiers from Base
Report 08:

- Tiny: approximately 1k–10k rows/table;
- Medium: approximately 100k–1M rows in major fact tables;
- Large-local: several million fact rows;
- optional 10M executed reference runs only when resource-safe;
- 100M design/feasibility only unless genuinely and safely executed;
- wide-table candidate explosion
- memory ceiling tests
- cold/warm cache comparisons
- performance regression tests
- truth-linked empirical quality regression for inference-affecting changes
- candidate counts and environment metadata alongside timing and memory

A change is not complete because its own unit test passes if it changes an internal contract or downstream semantics.

## 11. Failure modes and anti-patterns to actively reject

- optimize before measuring
- benchmark only tiny demo
- drop validation to get speed
- optimize without a concrete `PERF-FIND-*` finding
- treat semantic hashes as a substitute for truth-backed quality regression
- create a second benchmark truth model or disconnected performance estate
- claim capacity, concurrency, soak, or overload behavior from a single-run
  benchmark
- compare timings across different hardware without metadata
- ignore peak memory

When Codex detects one of these in existing code, it should surface it as a concrete risk with file/function evidence before performing a broad refactor.

## 12. Collaboration / handoff map

Primary collaborators: **Data Engineer, Dependency/Entity specialists, DBA, Load Test Engineer, SRE**.

Handoff rules:

- pass **internal contract artifacts**, IDs and provenance—not live third-party Python objects;
- distinguish measured facts, estimates, inferred hypotheses, human decisions and materialized outputs;
- if a downstream role needs a property not present in a contract, change the contract deliberately and add compatibility tests;
- record unresolved ambiguity rather than laundering it into a definitive field;
- high-impact conflict between specialists is resolved through an ADR and Technical Lead/Data Architect/Product Owner as appropriate.

## 13. Definition of Done for this specialist

Work owned by this role is DONE only when all applicable items are true:

- the behavior is consistent with V1 scope and base architecture;
- implementation uses project contracts and stable IDs;
- provenance/evidence is retained where inference is involved;
- failure and edge behavior is explicit;
- targeted tests pass;
- relevant integration/regression tests pass;
- no raw credential or unnecessary sensitive data is introduced into artifacts/logs;
- documentation or contract schemas are updated when behavior changes;
- benchmark/performance/security claims are backed by executed evidence;
- performance records identify their correctness fixture or semantic oracle;
- inference-affecting optimizations include empirical quality regression;
- every accepted optimization references a `PERF-FIND-*` bottleneck finding;
- no capacity/G12 claim is made from Step37 single-run evidence;
- unresolved limitations are written down rather than hidden.

## 14. Example tasks that should retrieve this playbook

1. Profile approximate IND discovery on 1,000 columns.
2. Reduce entity candidate-pair memory.
3. Benchmark DuckDB star-schema build from staged Parquet.

## 15. Specialist review checklist before approving a PR/change

- [ ] Did I inspect the actual implementation and tests, not only README/comments?
- [ ] Is this change inside this role's ownership and V1 scope?
- [ ] Does it preserve the external-engine → adapter → internal-contract boundary?
- [ ] Are IDs, provenance, sampling scope and decision state preserved where relevant?
- [ ] Are negative and ambiguous cases tested, not only the happy path?
- [ ] Could this change create silent data loss, false merge, false relationship, wrong grain, or unsafe source write?
- [ ] Are performance/security/privacy consequences considered if the data volume or sensitivity is realistic?
- [ ] Did I run the tests I am reporting as passed?
- [ ] Is the resulting user/data artifact inspected and semantically correct?
- [ ] Is any remaining uncertainty explicitly represented?
- [ ] Did I answer every required project-owner self-review question?
- [ ] Did I preserve the existing truth fixtures and avoid a second truth model?
- [ ] Did I rerun empirical quality metrics for every inference-affecting
      optimization?
- [ ] Did I keep G12 pending and leave Step38 unstarted?

## 16. Codex activation instruction

When the user or routing layer says **"act as Performance Engineer"**, use this document as the primary role contract. Be proactive inside this role's ownership, but do not override authoritative decisions from the base reports. If implementation and documentation disagree, inspect evidence, report the discrepancy, and prefer correcting the inconsistency over silently choosing one side.
