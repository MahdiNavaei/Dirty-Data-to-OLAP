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
- Review / Policy Service is the one reusable semantic authority for presenting and recording stage-scoped review requests, binding decisions to exact artifacts, checking replay compatibility, invalidating incompatible decisions and exposing unresolved checkpoint state. `REVIEW_EVIDENCE_DECISIONS`, `REVIEW_CANONICAL_IDENTITY`, `REVIEW_ANALYTICAL_PLAN` and `REVIEW_MATERIALIZATION_PLAN` are runtime checkpoints that delegate to this service; they are not four independent policy engines.
- Source Registry manages source metadata, inclusion rules and read-only connection-profile references.
- Source Discovery consumes source selection/registry metadata, uses the discovery operation of `SourceAdapter`, and produces `SourceCatalog` without requiring a snapshot.
- Source Snapshot Coordinator consumes `SourceCatalog` plus `SamplingPolicy`, uses the bounded-snapshot operation of `SourceAdapter`, and stages immutable `SourceSnapshot`, `BatchReference` and `SourceRecordReference` artifacts.
- Stage services own one semantic responsibility: discovery, profiling, dependency discovery, schema matching, quality analysis, optional semantic evidence, evidence fusion, canonical hypotheses, linkage-evidence-only entity resolution, canonical finalization, analytical planning, compilation, materialization and validation/reconciliation.
- Dependency Discovery consumes only complete hash-bound staged snapshot artifacts and an explicit local-only privacy context. It measures UCC/key, FD/AFD, IND/approximate-IND and search-bound evidence, retains orphan/type/uniqueness/cardinality signals, and emits relationship candidates only; it never accepts a PK/FK or reconnects to a source.
- Entity Resolution owns `EntityMatchEdge` and `EntityCluster` linkage evidence only. Canonical Finalization owns accepted canonical identity and `SourceRecordCanonicalMap` after policy, review, conflict and provenance checks.

### Ports and infrastructure

- Project-owned ports define source, profiling, dependency, matching, ER, semantic-evidence, materialization, control-store, artifact-store, stage-executor and capability-registry contracts.
- SQLite Control Store is one future repository implementation.
- Filesystem Artifact Store is one future artifact-plane implementation.
- Local Stage Executor is one future execution implementation.
- Capability Registry reports available adapters and versions without causing import-time failure.

### Adapters

Adapters normalize dlt, DataProfiler, Desbordante, Valentine, Splink, optional semantic providers and DuckDB behavior into project-owned inputs and outputs. Their native objects never cross the adapter boundary.

Quality Analysis is a required staged service between profiling and later evidence
fusion. It consumes only a COMPLETE, hash-bound SourceSnapshotResult and the
selected profile artifacts plus explicit QualityRule values. Its staged reader
does not reconnect to a source. Quality outputs are issue, proposal, validation
plan and result artifacts; a proposal is never an approval or an execution.

The quality service may accept optional future DependencyEvidence, but it does
not depend on Step12 and it never infers a foreign key, business requiredness,
entity duplicate or canonical identity from profiling alone.

The source lifecycle is `SourceSelection -> SOURCE_DISCOVERY -> SourceCatalog -> SOURCE_SNAPSHOT_STAGE -> SourceSnapshot/BatchReference/SourceRecordReference`. Discovery and snapshot responsibilities are not interchangeable.

## 2. Ownership and side effects

| Boundary | Owns | Side-effect rule |
|---|---|---|
| Run Manager / Orchestrator | lifecycle and stage coordination | control metadata only |
| Source Snapshot | source observation and staging references | read-only source access |
| Evidence services | evidence and decision artifacts | project-local artifact writes |
| Entity Resolution | linkage edges and clusters | never assigns canonical identity |
| Canonical Finalization | accepted canonical instances and source mappings | no source writes; family-scoped ER guard |
| Analytical services | analytical plans and mappings | no source writes |
| Compiler | compiled execution plan | no execution |
| Materializer | controlled target creation | writes only controlled target |
| Validation | checks and reconciliation | reads artifacts; records result |
| Review checkpoints | stage-scoped artifact approval guards | unresolved required review drives `NEEDS_REVIEW` |
| Control Store | states, indexes, references | never raw large rows |
| Artifact Store | large and immutable artifacts | atomic publication only |

## 3. Component interaction

The orchestrator asks a stage service for a project-owned request/result. The stage service obtains an adapter through a port, persists an attempt-local artifact, validates its contract, and asks the Artifact Store to publish it. The Control Store records the state transition and references. Downstream services consume only published COMPLETE artifacts. Review checkpoints are explicit DAG boundaries: each is entered only after its subject artifact exists, and each calls the common Review / Policy Service. A required unresolved checkpoint pauses its guarded stage and run in `NEEDS_REVIEW`. Canonical Finalization evaluates the conditional ER guard per entity family; it cannot publish a mapping when required linkage evidence or its post-ER identity review is absent or unacceptable.

### Privacy policy boundary

`application.privacy_policy` is a cross-cutting policy/service boundary rather
than a new processing stage. It owns `PrivacyClassification`,
`ArtifactSensitivity`, `PrivacyDecision`, `ExternalProcessingDecision` and
`PrivacyFailure` contracts. It classifies raw values ephemerally, assigns
conservative artifact sensitivity, masks or pseudonymizes only on an explicit
request, sanitizes nested diagnostic structures and owns cleanup only beneath
its privacy-owned ephemeral root. Quality, future entity resolution and future
semantic/LLM work consume the boundary; no later component may bypass it for
raw sensitive or unknown exposure.
# Step11 source security component

`application.database_security` owns purpose isolation, assurance and query
policy. `adapters.database_security` applies the SQLite and SQLAlchemy/dlt
connection controls before source discovery and extraction.
