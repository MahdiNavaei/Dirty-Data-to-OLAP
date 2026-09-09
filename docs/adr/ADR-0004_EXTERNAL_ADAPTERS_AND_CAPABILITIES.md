# ADR-0004: External Adapters and Capability Negotiation

Status: Accepted for v1 architecture

## Context

Optional profiling, dependency discovery, schema matching, entity resolution,
and semantic evidence engines vary by environment. Availability must not be
silently replaced with a fake result or an unbounded fallback.

## Decision

Each external engine is behind a project-owned adapter port. A capability
registry reports availability, version, configuration, and whether a capability
is required or optional for the selected run. An unavailable required capability
blocks the run with an actionable reason. An unavailable optional capability is
skipped only when the stage graph declares a valid skip path and records the
omission. Adapters return project-owned result contracts.

## Consequences

- Local-first execution remains useful without every optional dependency.
- Missing evidence is visible rather than fabricated.
- Engine upgrades are captured in cache keys and provenance.
- The composition boundary is the only place that selects concrete providers.
