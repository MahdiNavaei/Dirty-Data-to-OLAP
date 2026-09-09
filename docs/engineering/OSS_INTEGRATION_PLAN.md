# OSS Integration Plan

OSS projects are narrow engines, not project architecture. Planned boundaries are: dlt → `SourceAdapter`; DataProfiler → `ProfilingAdapter`; Desbordante → `DependencyDiscoveryAdapter`; Valentine → `SchemaMatchingAdapter`; Splink → `EntityResolutionAdapter`; DuckDB/materialization tooling → `MaterializerPort`. Optional semantic providers use `OptionalSemanticEvidenceAdapter`.

Each adapter normalizes into project-owned contracts, records engine/version/config/scope provenance, has compatibility fixtures, and has an unavailable/failure path. Native vendor objects must not reach application planning or persistence. Splink produces `EntityMatchEdge` and `EntityCluster`; it never produces `SourceRecordCanonicalMap`. Canonical finalization creates accepted mappings after the identity review guard.

`research/oss` remains a pointer-only area. No clone, submodule, vendored code or runtime import may be added as part of this plan. A deletion test is required once adapters exist. Licenses and dependency versions are implementation-time evidence, not invented Step 05 claims.
