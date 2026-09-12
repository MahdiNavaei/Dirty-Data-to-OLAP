# Dirty Data to OLAP — Runtime Topology

## V1 local topology

The reference runtime is one local checkout with:

1. CLI or future API entrypoint;
2. application/orchestration process;
3. local StageExecutor;
4. SQLite Control Store through ControlStorePort;
5. content-addressed filesystem Artifact Store through ArtifactStorePort;
6. separate staged-dataset artifact area for Parquet intermediates;
7. DuckDB analytical target, usually retained as a controlled external artifact.

Step23 does not execute heavy stages. It persists run/stage metadata and
provides the local artifact, staging, capability and lifecycle primitives that
the future executor will consume. Cheap control operations such as reading run
metadata and listing artifacts may be synchronous.

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
