# Prompt 02 Implementation Map

This map is the bounded implementation plan for Multi-Source Product
Composition and Independent Acceptance. It preserves the accepted Step29
single-source order path and adds a separate multi-source execution surface.

## Ownership boundaries

- `domain/contracts/source.py`: add immutable source-set selection and
  source-set provenance contracts. Existing `SourceSelection` remains valid.
- `application/product_sources.py` and `application/backend.py`: add additive
  registration/binding methods for a typed source set. The existing single
  source API delegates only when exactly one source is supplied.
- `application/multi_source_product.py`: own the Prompt02 source-set run,
  snapshot/catalog accounting, schema matching, conditional ER, durable
  review requirements, canonical membership, analytical input, DuckDB
  materialization, and independent validation receipt. It calls the existing
  project services and provider adapters; it does not read the independent
  oracle.
- `adapters/sources/sql` and `adapters/sources/files`: use the existing
  read-only source adapters with runtime-only credentials and immutable source
  snapshots. No credentials enter registry metadata or evidence.
- `tests/product_acceptance`: provide a real four-source fixture, a separate
  truth oracle, the acceptance harness, and negative controls. The harness
  loads the oracle only after the product run completes.
- `.github/workflows/ci.yml`: add a bounded Prompt02 job with PostgreSQL,
  MySQL/MariaDB, SQL Server, and CSV fixture inputs. A provider-unavailable or
  skipped acceptance run is not a pass.
- `docs/product-completion/evidence/PROMPT02_MULTI_SOURCE_ACCEPTANCE.md`: add
  only after a real acceptance run and record exact commit/provider/source-set
  evidence without raw rows or secrets.

## Execution graph

1. Register four typed read-only sources and bind one immutable source set.
2. Discover and snapshot every source; persist source IDs, selection scopes,
   snapshot IDs/fingerprints, extraction bounds, row accounting, and source-set
   fingerprint.
3. Profile, discover dependencies, and run real Valentine schema matching.
4. Run Splink only for the declared cross-source customer family; publish
   positive reviewed membership, hard-negative and duplicate/orphan
   dispositions, and terminal record accounting.
5. Fuse evidence through the existing fusion service and require durable
   review decisions before canonical finalization.
6. Build one canonical customer model and one order fact with three dimensions
   (`customer`, `date`, `source`); quantity is additive, unit price remains an
   attribute, and no revenue/GMV measure is emitted.
7. Compile and materialize one DuckDB target, then validate it against an
   independently loaded versioned oracle and negative controls.

## Explicit non-goals

Prompt03 generic-domain completion, full browser UI/action-matrix expansion,
export/SSO/enterprise features, distributed/streaming execution, Oracle,
LLM autonomy, and SLA/HA work remain out of scope.

