# Engineering Plan — v1

Status: G2 PASS. This document freezes implementation governance; it does not claim that product implementation exists.

The implementation starts at Specialist Step 06. Its substantive first slice is the database-access and introspection foundation required by the Master Sequence: read-only connections, external connection-profile references, normalized capabilities/errors, metadata introspection, transactions/isolation, timeouts, pooling, safe sampling, identifier qualification, cleanup and bounded-query behavior. Only the minimum package/test scaffold needed for that slice is allowed. Step 07 then owns SourceAdapter ingestion and staging.

## Architectural commitments

- The repository contracts in `docs/product`, `docs/domain`, `docs/data-architecture` and `docs/architecture` are the source of truth.
- The canonical graph is produced before OLAP planning, and fact implementation is blocked until `GrainSpec` is explicit and validated.
- External engines remain behind replaceable adapters. Splink emits linkage evidence only; canonical mappings are emitted only by canonical finalization.
- Operational sources are read-only. ControlStore holds metadata, lifecycle and decisions; ArtifactStore holds large immutable evidence and output artifacts.
- Review decisions are stage-scoped, hash-bound and invalidated by incompatible subject or policy changes.
- Every transformation boundary preserves lineage and one terminal record disposition per input record.
- `domain.contracts` has Step05 governance ownership and Step06 bootstrap ownership only; semantic contract families remain owned by their later specialists.
- ControlStore may receive a minimal early metadata schema/port, but Step23 owns comprehensive platform hardening.

## Execution method

Each specialist owns a bounded contract, implementation slice, test evidence and handoff. A gate is a release decision over evidence, not a claim that a later specialist's work already exists. The machine-readable plan, ownership map, test matrix, enforcement rules, integration matrix and gate map under `docs/engineering/specs/` are normative companions to this document.

No dependency installation, CI workflow, OSS clone, benchmark generator or production source was added in Step 05.
