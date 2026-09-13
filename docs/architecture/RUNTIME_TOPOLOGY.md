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
Step28 consumes. Cheap control operations such as reading run metadata and
listing artifacts may be synchronous.

## Heavy versus control execution

Profiling, dependency discovery, schema matching, entity resolution, materialization and validation use StageExecutor. Control operations do not invoke engines directly. Step28 adds a bounded local worker over the SQLite control store; it does not introduce a second DAG or require Celery, Kafka, Kubernetes or a hosted broker.

## Step28 durable job boundary

`ExecutionCommand.command_id`, durable `JobRecord.job_id`,
`StageExecutionRequest.request_id`, `StageAttemptRecord.attempt_id`, artifact
IDs and the run ID are distinct identities. Delivery is at-least-once
compatible. SQLite transactions claim jobs with leases and monotonically
increasing fencing generations; stale workers cannot finalize reclaimed work.
Retries create new stage attempts and never claim exactly-once execution.
Queued cancellation is immediate, running cancellation is cooperative and the
finalization fence converts a completion race to `CANCELLED`. Review resume is
authorized only by the existing `ReviewPolicyService` and a compatible
persisted review context.

The durable job tables are schema version 6 in the existing control store.
`GET /api/v1/runs/{run_id}/jobs` and the scoped job detail route expose only
safe metadata for the later frontend step. Step24's synchronous distributed
partition/scale layer remains unchanged and is wrapped only at this stage
boundary.

Before a submit command is accepted, the application-owned
`ExecutionPlanService` compiles the authoritative `stage_graph.yml` for the
run. The public preparation route
`/api/v1/runs/{run_id}/execution/prepare` accepts only bounded
`ExecutionPlanIntent`; server authority resolves conditional stages from
verified, published run planning artifacts and persists an `ExecutionPlan`
bound to the resulting `ExecutionPlanSelection`. Conditional stages never
default to omitted. An unresolved or conflicting trusted input returns a typed
`BLOCKED` result and does not create a guessed plan. Selection policy,
evidence, scope and scope fingerprints are server-owned.

Review checkpoint stages are control-plane pauses. A worker derives their
authoritative context from registered, verified typed upstream artifacts by
calling the existing evidence, canonical-identity, analytical-plan or
materialization context builder. It persists the context, reaches
`NEEDS_REVIEW`, and only resumes after a compatible Step27 review decision.
`COMPILATION` publishes `CompiledPlan`, `GeneratedSQL` and `TargetConfig` with
one run/stage/attempt lineage. Before materialization review or execution, the
runtime verifies generated SQL ID/hash, target fingerprint and lineage
bindings; transport SHA-256 integrity and the semantic review context hash
remain distinct. G6 remains the final success authority; Step29/frontend work
is not started.

## Future replacement boundaries

- SQLite may be replaced by PostgreSQL through ControlStorePort.
- Filesystem artifacts may be replaced by object storage through ArtifactStorePort.
- Local execution may be replaced by queue/workers through StageExecutorPort.
- DuckDB may be joined by another materialization target through MaterializerPort.

The project-owned contracts, state machines, artifact envelope and orchestration semantics remain stable across these replacements.

## Workspace boundary

All runtime artifacts remain under the project-local workspace. Logical paths are relative to the configured project workspace and are recorded in artifact envelopes. Research clones, if ever used, remain under research/oss and are never runtime dependencies.
