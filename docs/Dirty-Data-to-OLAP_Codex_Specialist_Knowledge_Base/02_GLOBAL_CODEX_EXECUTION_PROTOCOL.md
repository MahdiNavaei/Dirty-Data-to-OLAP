---
doc_type: codex_execution_protocol
project: "Dirty Data to OLAP"
priority: highest
---

# Global Codex Execution Protocol

This protocol applies to **every specialist**.

## 1. Evidence before edits

Before modifying code, Codex must inspect:

1. the actual files/functions involved;
2. existing tests for that behavior;
3. the specialist playbook;
4. relevant base report(s) when scope/contracts/architecture/semantics are affected.

Never modify code from a README-level assumption.

## 2. Do not reinvent the wheel

Existing open-source engines are deliberate building blocks:

- dlt — source ingestion/schema reflection;
- Capital One DataProfiler — structured profiling;
- Desbordante — dependency/constraint discovery;
- Valentine — schema matching;
- Splink — probabilistic entity resolution.

Use them behind project adapters where they genuinely solve the problem. Do not copy their whole architecture or trust their output as project truth.

## 3. Internal contracts are the integration boundary

No cross-stage API should depend on a third-party native object. Convert external outputs into versioned internal contracts containing stable IDs, provenance, scope and evidence semantics.

## 4. Source systems are protected

Default source interaction is read-only. No destructive production mutation is allowed in V1. Cleaning is proposed/simulated/materialized into controlled targets unless an explicit future design says otherwise.

## 5. Distinguish kinds of truth

Never conflate:

- declared metadata;
- measured full-data observation;
- sample-based estimate;
- inferred hypothesis;
- calibrated probability;
- human/domain decision;
- materialized output.

## 6. LLM is bounded

An LLM may help with semantic hypotheses and explanations. It may not bypass deterministic checks, evidence fusion, review gates or validation.

## 7. Every fact table has explicit grain

No analytical fact model may be accepted or materialized without a machine-readable and human-readable grain plus validation of that grain.

## 8. No silent data loss

Every cleaning, filtering, deduplication or transformation must account for affected rows/records/entities and preserve provenance. Entity resolution does not mean deleting source records.

## 9. Tests must match the type of claim

- correctness claim → unit/integration/data tests;
- matching/ML quality claim → labeled evaluation;
- performance claim → benchmark with hardware/data metadata;
- scalability claim → load/stress evidence;
- security claim → security tests/review;
- recovery claim → chaos/resilience or recovery test;
- UX claim → E2E/usability evidence.

## 10. Honest completion

Never say a test passed unless it was executed. Never say a bug is fixed because code “looks right.” Report exact commands/results when available, and state remaining limitations.

## 11. Minimal coherent changes

Prefer the smallest change that restores or adds correct behavior while preserving architecture. Broad refactors require concrete evidence that the existing boundary is the problem.

## 12. Mandatory final self-review

Before considering a task complete, Codex asks:

- Did I break a contract or downstream assumption?
- Could this infer a false relationship or merge?
- Could this change fact grain/measure semantics?
- Could this write to or overload a source DB?
- Could it expose credentials/PII?
- Does failure produce an explicit state?
- Is there a regression test?
- Did I inspect the produced artifact/data?
