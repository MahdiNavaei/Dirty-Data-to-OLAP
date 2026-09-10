# External Processing Policy

Raw sensitive and unknown values are blocked from external processing by
default. The only implemented allowed path is an aggregate-only payload made
of numeric aggregate scalars. Strings, raw identifiers, free text and unknown
values are rejected. The decision is explicit and carries blocked field names,
not values.

This boundary is ready for the future Specialist Step16 semantic/LLM service:
that service must request an external-processing decision before constructing
a provider payload. Step10 does not implement an LLM, provider call,
credential flow or semantic fallback.
# Step11 external boundary hardening

Aggregate-only transport requires an explicit aggregate-safe metric with
metric identity, aggregate kind, derivation scope, privacy classification,
provenance and `contains_raw_identifier=false`; a naked numeric identifier is
blocked.
