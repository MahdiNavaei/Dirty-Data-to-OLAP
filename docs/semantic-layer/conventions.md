# Semantic Layer Conventions

This document defines the Step21 semantic projection over the reviewed Step20
analytical package. `SemanticModel` is metadata and a bounded query boundary; it
is not a second planner, a physical-schema redesign, or a source-to-target
correctness certificate.

## Semantic versus physical

`DimensionSpec`, `FactSpec`, `GrainSpec` and `MeasureSpec` remain authoritative
for physical tables, keys, grain, SCD/unknown-member policy and aggregation
class. `SemanticDimension`, `SemanticAttribute`, `SemanticMeasure` and
`MetricSpec` add business-readable labels and safe references while preserving
those exact bindings. Warehouse surrogate keys stay implementation details;
reviewed alternate keys may be exposed only as declared attributes.

## Measures and metrics

`SemanticMeasure` points to one Step20 `MeasureSpec` content hash, fact and
grain. A base `MetricSpec` is created only as a deterministic projection of
that measure. `ADDITIVE` may expose reviewed `SUM`; `SEMI_ADDITIVE` exposes only
an explicitly reviewed operation such as `MAX` and requires an explicit time
scope in query resolution; `NON_ADDITIVE` remains visible as metadata but has
no executable `SUM` metric. Null remains null unless an upstream contract says
otherwise.

Derived metrics use a bounded expression shape, currently explicit aggregate
references or a ratio with numerator, denominator, zero-denominator behavior,
result units, compatible fact/grain and domain evidence. Missing evidence is
`REVIEW_REQUIRED` or `UNAVAILABLE`, never a guessed formula. Revenue, GMV,
gross amount, currency and tax semantics are not inferred from field names.

## Dimensions, roles and hierarchies

Dimensions carry conformed/role-playing/date role and canonical concept
references. Attribute names and aliases are case-insensitively unique within
their semantic namespace. Time roles bind a reviewed fact date column to a
Gregorian Date dimension and grain. A Date hierarchy is emitted only when the
reviewed dimension contains `year`, `quarter`, `month` and `day` attributes in
that order. Product or geography roll-ups are not inferred from co-existing
columns.

## Compatibility and lineage

Every executable metric has one explicit fact and grain. Query resolution
rejects cross-fact or cross-grain combinations, incompatible dimensions,
unclassified relationships and fanout-prone joins. Relationship scope is
either `CANONICAL_ACCEPTED` or `ANALYTICAL_TIME_ROLE`; arbitrary relationship
strings are not semantic truth. Each semantic object retains refs to the
analytical plan/package, compiled plan, materialization artifact, canonical
model and source/canonical lineage.

## Query safety and G6 boundary

`SemanticQueryRequest` permits only declared IDs, bounded `EQUALS`, `IN` and
date-range filters, selected-field sorting and a bounded limit. The service
generates one parameterized `SELECT`; callers cannot supply raw SQL, tables,
expressions or predicates. The DuckDB adapter opens the exact target read-only
and verifies its file hash. Semantic validation compares aggregates with that
same target only. `READY` means internally valid against the reviewed Step20
model; it does not mean source truth, production certification or G6 PASS.
