# ADR-0001: Project-Owned Contracts and Inward Dependencies

Status: Accepted for v1 architecture

## Context

The product must combine heterogeneous engines without allowing vendor models or
runtime drivers to become domain contracts. A source-analysis library, matcher,
materializer, or persistence implementation may change independently of the
product's evidence, decision, artifact, and analytical contracts.

## Decision

Project-owned domain types, ports, and application services are the only types
crossing the core boundary. Dependencies point inward from entrypoints and
adapters through application orchestration to project-owned contracts. Concrete
wiring is isolated in the composition boundary. Adapters translate external
types at ingress and egress; native third-party objects do not leak into core
or persisted contracts.

## Consequences

- Engine replacement is a port/adaptor change, not a domain rewrite.
- Contract validation can run without installing optional engines.
- Adapter versions, configuration, and evidence remain explicit in artifacts.
- The architecture accepts a small amount of translation code in exchange for
  stable ownership and testability.

## Rejected alternatives

- Importing vendor result classes throughout the application.
- Making a third-party engine the canonical source of truth.
- Letting entrypoints instantiate adapters directly.
