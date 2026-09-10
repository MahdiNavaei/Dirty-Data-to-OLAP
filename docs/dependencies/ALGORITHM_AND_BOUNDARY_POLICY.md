# Step12 Dependency Discovery Boundary

Step12 measures structural evidence from a pinned, immutable source snapshot. It does not decide business truth, create foreign keys, assign primary keys, or mutate source data.

## Engine policy

The replaceable `DependencyDiscoveryAdapter` owns the boundary to Desbordante. The project consumes only `UniqueColumnCombinationEvidence`, derived `KeyCandidate`, `FunctionalDependencyEvidence`, `InclusionDependencyEvidence`, `DependencyMetricObservation`, `RelationshipCandidate`, capability, failure, scope and search-stat contracts. Native provider objects never cross the boundary. Desbordante is an optional runtime capability and is not installed as a host dependency by this repository.

The reviewed Desbordante source was version 2.4.1 at commit `b211961f3f272ed8815ef1ffbda90573b11e1116`, licensed AGPL-3.0-only. It is research/runtime-boundary material; no source was copied and no direct dependency was added. On Windows, the adapter can invoke an externally/local provisioned Docker image by immutable image ID using a network-disabled, read-only, process-level timeout boundary. Containerization does not remove or determine AGPL obligations; distribution authorization remains an explicit project-policy limitation.

## Bounded search

Requests carry separate maximum tables, projected columns per table, UCC arity, FD determinant arity, IND arity, column pairs, output candidates and runtime. The adapter passes the independent arity bounds to the provider, refuses to start an IND search when the pair budget would be exceeded, enforces a real subprocess timeout for Docker, and records actual pruning/truncation statistics. A budget failure is not a clean result.

Column names, types, target uniqueness, cardinality and orphan measurements are evidence features only. Low-cardinality target domains remain visible in IND evidence but are excluded from relationship-candidate emission by default because high inclusion on a tiny domain is a known false-positive trap.

## Nulls and scope

Physical nulls are excluded from the provider relation and project denominators by the default `EXCLUDE_PHYSICAL_NULL` policy. Other supported policies use tagged encodings that keep physical nulls, literal `NULL`, empty strings and source strings that resemble implementation markers distinct. Null transformation and denominator semantics are recorded in provenance. The staged reader preserves record references for evidence localization, while artifacts contain no raw values. Every result is bound to source ID, snapshot ID, complete batch hashes, observation mode and record-reference population. Incomplete or tampered staged input fails closed. A `DependencyPrivacyContext` is not authorization; the adapter requires the policy service's exact-scope authorization.

UCC observations are evidence, not primary-key claims. A derived key candidate references its UCC evidence ID and uses stable project `table_id`/`column_id` values. Approximate provider metrics are retained only when native output exists; otherwise an explicit unavailable observation is recorded. Project diagnostics use named definitions and are not presented as provider error metrics. Relationship candidates require non-low-cardinality, structurally compatible, observed-scope unique targets; IND evidence is retained when gating rejects a candidate.

## Downstream handoff

Relationship outputs are candidates only (`final_acceptance_allowed=false`). Later evidence fusion and review may combine this evidence with type/name/cardinality/orphan and other project-owned signals. Step13 schema matching is not implemented by Step12.
