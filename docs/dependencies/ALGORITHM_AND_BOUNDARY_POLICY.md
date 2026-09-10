# Step12 Dependency Discovery Boundary

Step12 measures structural evidence from a pinned, immutable source snapshot. It does not decide business truth, create foreign keys, assign primary keys, or mutate source data.

## Engine policy

The replaceable `DependencyDiscoveryAdapter` owns the boundary to Desbordante. The project consumes only `KeyCandidate`, `FunctionalDependencyEvidence`, `InclusionDependencyEvidence`, `RelationshipCandidate`, capability, failure, scope and search-stat contracts. Native provider objects never cross the boundary. Desbordante is an optional runtime capability and is not installed as a host dependency by this repository.

The reviewed Desbordante source was version 2.4.1 at commit `b211961f3f272ed8815ef1ffbda90573b11e1116`, licensed AGPL-3.0-only. It is research/runtime-boundary material; no source was copied and no direct dependency was added. A Linux Docker build was used for the real-provider smoke receipt because the current Windows environment has no importable binding and no matching host wheel.

## Bounded search

Requests carry explicit maximum tables, projected columns per table, determinant width, column pairs, output candidates and runtime. The adapter projects the configured column limit, passes `max_lhs` to FD/AFD and `max_arity` to approximate IND discovery, refuses to start an IND search when the pair budget would be exceeded, and records pruning/truncation statistics. A budget failure is not a clean result.

Column names, types, target uniqueness, cardinality and orphan measurements are evidence features only. Low-cardinality target domains remain visible in IND evidence but are excluded from relationship-candidate emission by default because high inclusion on a tiny domain is a known false-positive trap.

## Nulls and scope

Physical nulls are excluded from dependency denominators by the default `EXCLUDE_PHYSICAL_NULL` policy. The staged reader preserves record references for evidence localization, while artifacts contain no raw values. Every result is bound to source ID, snapshot ID, complete batch hashes, observation mode and record-reference population. Incomplete or tampered staged input fails closed.

## Downstream handoff

Relationship outputs are candidates only (`final_acceptance_allowed=false`). Later evidence fusion and review may combine this evidence with type/name/cardinality/orphan and other project-owned signals. Step13 schema matching is not implemented by Step12.
