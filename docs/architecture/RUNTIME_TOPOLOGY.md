# Dirty Data to OLAP — Runtime Topology

## V1 local topology

The reference runtime is one local checkout with:

1. CLI or future API entrypoint;
2. application/orchestration process;
3. local StageExecutor;
4. SQLite Control Store;
5. filesystem Artifact Store;
6. Parquet intermediate artifacts;
7. DuckDB analytical target.

Heavy stages run through StageExecutor even when the implementation is local. Cheap control operations such as reading run metadata, listing artifacts, submitting a review decision and reading status may be synchronous.

## Heavy versus control execution

Profiling, dependency discovery, schema matching, entity resolution, materialization and validation use StageExecutor. Control operations do not invoke engines directly. No Celery, Kafka, Kubernetes or distributed worker implementation is introduced by Step 04.

## Future replacement boundaries

- SQLite may be replaced by PostgreSQL through ControlStorePort.
- Filesystem artifacts may be replaced by object storage through ArtifactStorePort.
- Local execution may be replaced by queue/workers through StageExecutorPort.
- DuckDB may be joined by another materialization target through MaterializerPort.

The project-owned contracts, state machines, artifact envelope and orchestration semantics remain stable across these replacements.

## Workspace boundary

All runtime artifacts remain under the project-local workspace. Logical paths are relative to the configured project workspace and are recorded in artifact envelopes. Research clones, if ever used, remain under research/oss and are never runtime dependencies.
