# Engineering Plan — v1

Status: G2 PASS. This document freezes implementation governance; it does not claim that product implementation exists.

The implementation starts at Specialist Step 06. The first slice is deliberately small: establish the `dirty_data_to_olap` namespace, versioned project-owned contracts, persistence ports/schemas and a test harness. Source adapters and pipeline stages follow the frozen 41-step sequence.

## Architectural commitments

- The repository contracts in `docs/product`, `docs/domain`, `docs/data-architecture` and `docs/architecture` are the source of truth.
- The canonical graph is produced before OLAP planning, and fact implementation is blocked until `GrainSpec` is explicit and validated.
- External engines remain behind replaceable adapters. Splink emits linkage evidence only; canonical mappings are emitted only by canonical finalization.
- Operational sources are read-only. ControlStore holds metadata, lifecycle and decisions; ArtifactStore holds large immutable evidence and output artifacts.
- Review decisions are stage-scoped, hash-bound and invalidated by incompatible subject or policy changes.
- Every transformation boundary preserves lineage and one terminal record disposition per input record.

## Execution method

Each specialist owns a bounded contract, implementation slice, test evidence and handoff. A gate is a release decision over evidence, not a claim that a later specialist's work already exists. The machine-readable plan, ownership map, test matrix, enforcement rules, integration matrix and gate map under `docs/engineering/specs/` are normative companions to this document.

No dependency installation, CI workflow, OSS clone, benchmark generator or production source was added in Step 05.
