# Dirty Data to OLAP — Component Model

## 1. Boundary map

The component model is a set of small project-owned services connected through ports. There is no run_pipeline god component.

### Entry and composition

- CLI Entry Point accepts local commands and submits application requests.
- API Entry Point is a future transport boundary that calls application services; it does not invoke engines.
- Composition Root resolves configuration, capabilities and concrete adapters, then wires them into ports for one run.

### Application and orchestration

- Run Manager owns run creation, coarse run status, configuration pinning and resume requests.
- Stage Orchestrator resolves the stage DAG, creates attempts, checkpoints cancellation and coordinates StageExecutor.
- Review / Policy Service records human decisions and checks replay compatibility.
- Source Registry manages source metadata, inclusion rules and read-only connection-profile references.
- Source Snapshot Coordinator requests bounded snapshots and stages immutable source references.
- Stage services own one semantic responsibility: discovery, profiling, dependency discovery, schema matching, quality analysis, optional semantic evidence, evidence fusion, canonical hypotheses, entity resolution, canonical finalization, analytical planning, compilation, materialization and validation/reconciliation.

### Ports and infrastructure

- Project-owned ports define source, profiling, dependency, matching, ER, semantic-evidence, materialization, control-store, artifact-store, stage-executor and capability-registry contracts.
- SQLite Control Store is one future repository implementation.
- Filesystem Artifact Store is one future artifact-plane implementation.
- Local Stage Executor is one future execution implementation.
- Capability Registry reports available adapters and versions without causing import-time failure.

### Adapters

Adapters normalize dlt, DataProfiler, Desbordante, Valentine, Splink, optional semantic providers and DuckDB behavior into project-owned inputs and outputs. Their native objects never cross the adapter boundary.

## 2. Ownership and side effects

| Boundary | Owns | Side-effect rule |
|---|---|---|
| Run Manager / Orchestrator | lifecycle and stage coordination | control metadata only |
| Source Snapshot | source observation and staging references | read-only source access |
| Evidence services | evidence and decision artifacts | project-local artifact writes |
| Canonical / Analytical services | semantic plans and mappings | no source writes |
| Compiler | compiled execution plan | no execution |
| Materializer | controlled target creation | writes only controlled target |
| Validation | checks and reconciliation | reads artifacts; records result |
| Control Store | states, indexes, references | never raw large rows |
| Artifact Store | large and immutable artifacts | atomic publication only |

## 3. Component interaction

The orchestrator asks a stage service for a project-owned request/result. The stage service obtains an adapter through a port, persists an attempt-local artifact, validates its contract, and asks the Artifact Store to publish it. The Control Store records the state transition and references. Downstream services consume only published COMPLETE artifacts.
