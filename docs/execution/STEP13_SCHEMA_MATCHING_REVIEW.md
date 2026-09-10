# Specialist Step13 — Schema Matching Engineer

## Status

`PASS` for the bounded, local Step13 implementation and executed evidence scope. This receipt does not claim G4/G4A, accepted mappings, entity resolution, canonical identity, or production-scale performance. G4/G4A and later gates remain `PENDING`.

## Authorization and boundary

- Starting repository state was main at `d629319ee990f25b5a31aafc7eea44fee30644bc`; the only pre-existing untracked path was `tests/quality_unit_artifacts/`, which was preserved and never staged.
- Step12 was hardening-only before Step13: Docker now executes the inspected immutable image ID rather than the mutable tag; `DependencyObservationScope` records staged/provider/null-excluded rows and complete-case semantics; the real-provider test uses `workspace/test-temp/dependencies/<unique-run>` with cleanup; the stale state commit note was corrected.
- The Step13 service accepts multiple catalogs and pinned snapshot results, table scopes, optional columns, profiles, dependency evidence and a request. Instance-aware mode requires a policy-issued exact source/snapshot/table/column/artifact authorization; schema-only mode does not.
- Matching reads only `COMPLETE` hash-valid staged Parquet through the project-owned staged reader. Source reconnect, source writes, external processing, LLM processing, and raw values in artifacts/logs are forbidden.

## Open-source research and runtime

- Official `delftdata/valentine` source was inspected in a disposable clone at the reachable v1.0.0 tag `f0f738927455063841a4ebdda2f1420abc26922b`, including `pyproject.toml`, Apache-2.0 `LICENSE`, package sources and tests. The published package is `valentine==1.0.0`, Python `>=3.10,<3.15`, Apache-2.0.
- The pasted requested SHA `5d5163f04da304985bd51a476ccf7653de3973c9` is not present in the official remote. The official `master` ref exposes `5d5163f04da304985bd51a476ccf7653de3979c3`; this one-character/suffix discrepancy is recorded rather than silently claimed as verified. Runtime evidence used the official PyPI v1.0.0 package, never the research clone.
- The disposable official runtime and research clone were removed before handoff. `pyproject.toml` records the optional pinned `matching` dependency; no embeddings or LLM extras are used.

## Implementation

- Added project contracts for requests, modes, bounds, exact authorization, observation/sampling provenance, matcher references, raw native scores, candidate-only candidates, signal families, failures/capabilities, pruning/coverage, aggregate-only artifacts and Recall@k/MRR evaluations.
- Added `SchemaMatchingService`, `ValentineSchemaMatchingAdapter`, atomic JSON artifact publication under `workspace/runs/<execution_context_id>/schema_matching/`, deterministic hash-ordered sampling, conservative type pruning, top-k/output bounds, symmetric candidate IDs, domain-assertion evidence-only handling, and optional profile/dependency signals.
- Added a separate synthetic labeled fixture with renamed positives, same-name/table-context, low-cardinality, similar-value/type negatives and a multilingual name without fabricated translation.

## Executed evidence

- Step12 hardening: focused unit plus real Docker provider chain `12 passed`.
- Real official Valentine integration: `3 passed, 1 warning`; two materially different official configurations executed through the project adapter. Labeled evaluation: COMA schema Recall@1/3/5 `0.5/0.5/0.5`, MRR `0.5`; DistributionBased instance Recall@1/3/5 `1.0/1.0/1.0`, MRR `1.0`.
- Full regression: `114 passed, 42 warnings in 49.74s`. Warnings are the known non-failing dlt/SQLAlchemy cursor-finalizer traceback after successful pytest completion.
- Validators: schema matching `17` checks; engineering post-gate `73` checks with `23/23` negative tests; database security `22`; dependency discovery `21`; domain, data architecture, solution architecture, source ingestion, profiling and data quality validators all PASS.
- `python -m compileall -q src tools tests` and `git diff --check` PASS. Artifact inspection confirmed candidate-only state, raw native score semantics, no probability/confidence fields, no synthetic cell values in published JSON, and explicit scope/capability/failure/evaluation records.

## Limitations and handoff

- Valentine ranking evidence is not calibrated confidence and does not establish semantic equivalence, keys, foreign keys, accepted mappings, entity identity or canonical truth. Dependency evidence remains structural and reduced-scope where applicable.
- The executed evidence is local synthetic/staged evidence with bounded row/table/column/pair/matcher/output/runtime limits. It is not live-provider, production-scale, physical-acceptance or release evidence.
- The requested Valentine design SHA remains unresolved as an upstream textual pin discrepancy; the reachable official release and runtime are documented above.
- Step14 Entity Resolution is the next owner. No Step14 implementation was started. `G4`, `G4A` and `G5-G15` remain `PENDING`.

## Recording

- Content commit: `8296cbb60e5a2baa006562352e5c4ccbc2489931` (`feat: implement bounded cross-source schema matching`)
- Metadata/state/log commit is recorded separately by Git after this receipt is added.
