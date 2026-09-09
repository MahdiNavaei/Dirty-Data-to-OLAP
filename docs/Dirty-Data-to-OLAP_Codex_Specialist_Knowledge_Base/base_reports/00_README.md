# Dirty Data to OLAP V1 — Pre-Implementation Documentation Pack

> Working title: **Dirty Data to OLAP**  
> Goal of V1: turn fragmented, dirty tabular data into a validated, unified, OLAP-ready analytical layer.

## Purpose of this pack

This pack defines the project before implementation starts. It is intentionally opinionated: the goal is to prevent feature creep, avoid reimplementing mature open-source components, keep external libraries behind stable internal contracts, and make every inference auditable.

The eight required reports are:

1. `01_PROJECT_SCOPE_AND_REQUIREMENTS.md`
2. `02_OPEN_SOURCE_REUSE_AND_CLONE_PLAN.md`
3. `03_SYSTEM_ARCHITECTURE.md`
4. `04_INTERNAL_DATA_CONTRACTS.md`
5. `05_EVIDENCE_AND_CONFIDENCE_MODEL.md`
6. `06_DATA_QUALITY_AND_CLEANING_TAXONOMY.md`
7. `07_CANONICAL_AND_OLAP_MODELING_STRATEGY.md`
8. `08_BENCHMARK_AND_VALIDATION_PLAN.md`

## Core design rule

The project does **not** directly chain third-party tools together. Every external component is wrapped by an adapter and converted into Dirty Data to OLAP's own internal contracts.

```text
External sources
   ↓
Adapters
   ↓
Dirty Data to OLAP contracts
   ↓
Evidence engine
   ↓
Canonical model
   ↓
Analytical model
   ↓
Validation
   ↓
OLAP-ready output
```

## Non-negotiable principles

- Source systems are read-only in V1.
- No destructive action on production data.
- LLM output is never accepted as truth without deterministic or statistical evidence.
- Every inferred relationship, mapping or merge must preserve evidence and provenance.
- Ambiguity is represented explicitly instead of being hidden behind a single score.
- V1 ends at a validated analytical layer; continuous production ETL/ELT and CDC belong to later versions.
- The benchmark dataset must contain known ground truth so claims can be measured, not demonstrated only by screenshots.

## Reviewed upstream snapshots

The open-source reuse report pins the reviewed upstream state to these commits:

| Project | Reviewed commit |
|---|---|
| dlt | `a1c530114cc347496d1f00f38891475a047b6d05` |
| Capital One DataProfiler | `4b5ab37bb28a2104d0898d21a8c9681b5c5deed1` |
| Desbordante | `b211961f3f272ed8815ef1ffbda90573b11e1116` |
| Valentine | `5d5163f04da304985bd51a476ccf7653de3979c3` |
| Splink | `ca89ee92d5472b5e5de71cff3001193e04faf0e7` |
| star-schema-generator | `8bdc51846e669874400d2a532ca2849287fbac24` |

These are review anchors, not permanent dependency versions. Actual dependency versions should be locked in the implementation repository once compatibility tests are green.
