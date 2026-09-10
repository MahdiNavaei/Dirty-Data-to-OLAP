# Feature Schema

Schema ID: relationship-ranker-features-v1, version 1.

The numeric vector contains only aggregate/project-owned evidence: declared
metadata, inclusion coverage and violations, target uniqueness, type and
low-cardinality flags, dependency scope, schema matcher aggregate scores and
ranks, instance-signal presence, profile null/distinct ratios, and quality
scope/issues.

Identifiers, table/column names, source IDs, snapshot IDs, candidate IDs,
record references, raw values, and labels are not features. Every feature has
explicit missing semantics. The vector carries provenance references and
risk flags beside the numeric values.
