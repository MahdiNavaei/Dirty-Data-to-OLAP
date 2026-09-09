# Dirty Data to OLAP — Extension Points

New implementations extend project-owned ports and declare capability/version support:

- SourceAdapter for a new SQL/file source;
- ProfilingAdapter for another profiler;
- DependencyDiscoveryAdapter for another dependency engine;
- SchemaMatchingAdapter for another matcher;
- EntityResolutionAdapter for another linkage engine;
- OptionalSemanticEvidenceAdapter for another bounded semantic provider;
- MaterializerPort for another analytical target;
- ControlStorePort for another metadata repository;
- ArtifactStorePort for another artifact backend;
- StageExecutorPort for a worker or distributed executor.

An extension must preserve source read-only behavior, project-owned contract outputs, provenance, error/lifecycle semantics, artifact hashing, cache/invalidation rules and the relevant schema version. It must declare whether it is optional or required by a selected plan.

An extension cannot bypass review policy, canonical identity rules, record accounting, grain validation, security boundaries or final validation. Capability absence is explicit; it cannot trigger fake outputs or silent semantic fallback.
