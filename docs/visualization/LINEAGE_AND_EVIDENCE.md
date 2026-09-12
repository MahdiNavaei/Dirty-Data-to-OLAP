# Lineage and evidence

Lineage is a directed relationship view. The accessible description states that source-to-target direction is lineage, not causality. Upstream traversal follows reverse edges, downstream traversal follows forward edges, and both follows both directions. `FOCUSED_PATH` requires an explicit focus reference and depth bound, and means focused bounded lineage exploration (a deterministic reachable subgraph), not a unique source-to-target path.

Evidence is a separate breakdown view. Evidence families, roles, directions, reliability, observation scope, provenance, conflict references, supporting/contradicting flags, and human assertions remain separate rows. The service does not average independent producers or collapse disagreement into one visual score. Numeric fields carry their declared metric semantics and are not relabeled as probability.

Schema-matching candidates, ER candidate links, ER authorized linkage, accepted links, canonical mappings, and canonical conflicts use different edge/state fields. Candidate and authorized linkage are visibly distinct; a candidate, authorized linkage, or cluster is not a canonical identity and a proposed state is not an accepted review decision.
