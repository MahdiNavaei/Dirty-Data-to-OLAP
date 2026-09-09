# Dirty Data to OLAP — Canonical Model Principles

## Canonical concepts

The canonical model answers: “What business concepts exist, and how do source representations map to them?” It is independent of the analytical star schema.

- `CanonicalEntityType` describes a semantic type such as Customer or Product.
- `CanonicalEntityInstance` is a project-owned instance after an accepted identity decision.
- `SourceRecord` is the original source representation in a snapshot.
- `EntityMatchEdge` is linkage evidence between source records.
- `EntityCluster` is a linkage result/hypothesis and is not automatically a canonical identity.
- `SourceRecordCanonicalMap` records an accepted mapping from a source record to a canonical instance.

## Entity-boundary rule

Multiple source structures may represent one canonical entity type only when domain assertions, schema/identity evidence, relationship context, source role and required human review support the boundary. Same names, similar values or shared code domains alone are insufficient. The Step 02 ambiguity catalogue remains binding.

Customer, Product and Branch are benchmark identity families. Order, OrderLine and Payment are event concepts whose identity follows event/grain semantics; generic clustering is not applied to them by default.

## Canonical attribute envelope

A canonical attribute is not just a name and final value. It must be able to retain:

- canonical semantic name and normalized logical type;
- candidate source mappings and source values/references;
- transformation/normalization references;
- provenance and authority-policy reference;
- survivorship/selection decision and rationale;
- disagreement/conflict records;
- null/unknown state;
- review state and decision references.

Canonical values are derived representations. They never erase source values, source records or rejected alternatives.

## Acceptance boundary

An entity cluster may contribute to a canonical identity only after accepted entity scope, source-record provenance, conflict state and review policy requirements are satisfied. A source-record mapping must remain replayable and versioned. Later cluster changes must be auditable rather than silently rewriting historical mappings.
