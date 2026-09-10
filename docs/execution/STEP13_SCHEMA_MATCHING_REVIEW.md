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
- The prior closure receipt contained a Codex transcription error about the requested source pin. A disposable clone was detached at the exact official commit `5d5163f04da304985bd51a476ccf7653de3979c3` and reviewed across `valentine/`, `tests/`, `pyproject.toml` and `LICENSE`. Runtime evidence used the official PyPI v1.0.0 package, never the research clone.
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
- The exact requested Valentine commit was independently reachable and reviewed; the separate v1.0.0 runtime remains the executed provider evidence.
- Step14 Entity Resolution is the next owner. No Step14 implementation was started. `G4`, `G4A` and `G5-G15` remain `PENDING`.

## Recording

- Content commit: `8296cbb60e5a2baa006562352e5c4ccbc2489931` (`feat: implement bounded cross-source schema matching`)
- Integrity-closure content commit: `9e4d3e0` (`fix: close Step13 schema matching integrity gaps`)
- Metadata/state/log commit is recorded separately by Git after this receipt is added.

## Post-Step13 Independent Integrity Closure

- Scope: surgical repair of independently verified Step13 defects only. Step14 contracts and implementation were not started.
- Privacy repair: `SCHEMA_ONLY` now constructs zero-row schema inputs from catalog metadata, never invokes the staged reader, records `instance_rows_read=0` and `instance_evidence_used=false`, and uses a seed-independent schema-only sample identity. Instance-consuming matcher configurations are rejected with `UNSUPPORTED_MODE`.
- Provider-boundary repair: Valentine is invoked once per approved cross-source table pair with projected type-compatible columns only. Source/table/column scope validation rejects unknown or cross-boundary IDs; requested table/column truncation and column-pair budget limits are recorded and cannot produce `COMPLETE`.
- Coverage/provenance repair: pruning records provider table calls, provider-visible column pairs, eligible project pairs, returned pairs, retained top-k pairs and actual emitted candidates. Candidate union ordering is deterministic and matcher-semantic-neutral; raw scores are not fused across matcher families. Instance sample seed is part of instance configuration provenance; schema-only has no fake sample identity.
- Evidence repair: the runtime fixture now creates `crm_orders.status`, `erp_customers.status_code`, and `crm_customers.شناسه_مشتری`; the actual labeled JSON is loaded and executed with positives, hard negatives, same-name context, low-cardinality overlap, type-incompatible and multilingual cases. Evaluation retains Recall@K/MRR and reports hard-negative exposure/false-positive-at-k as non-calibrated diagnostics. Abbreviation dictionaries feed only the project lexical signal, and table context is an explicit separate signal.
- Contract repair: architecture specs and the integration matrix now consistently list `BatchReference`, optional `DependencyEvidence`, and conditional `MatchingAuthorization` for `INSTANCE_AWARE`.
- Runtime semantics: `max_runtime_seconds` remains a cooperative/post-call budget, not an in-process hard timeout.
- Exact research evidence: commit `5d5163f04da304985bd51a476ccf7653de3979c3` was cloned, source/tests/license areas were inspected, and the clone was removed. The optional official runtime was project-local and removed after execution.
- Validation evidence: Step13 focused integration `6 passed, 9 warnings`; unit contracts `3 passed`; unit suite `49 passed`; contract suite `5 passed, 2 warnings`; integration suite `33 passed, 49 warnings`; architecture suite `7 passed`; security suite `23 passed`; full suite `117 passed, 50 warnings`. Compileall, diff check and all required validators PASS. The known warnings are official PuLP deprecations, existing dependency deprecations/profile warnings, and the non-failing dlt/SQLAlchemy cursor-finalizer traceback after successful integration completion.
- State: Step13 remains complete; `last_completed_step=13`, `current_step=14`, `current_role=entity_resolution_engineer`, G0-G3/G3A/G3B PASS, G4/G4A/G5-G15 PENDING, `blocked=false`; Step14 remains NOT STARTED.
