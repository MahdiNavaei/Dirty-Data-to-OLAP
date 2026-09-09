# ADR-0005: Artifact Publication, Caching, and Invalidation

Status: Accepted for v1 architecture

## Context

Retries and resume require trustworthy outputs. A path that exists may still be
partial, stale, or produced from incompatible inputs. Reusing it without a
semantic cache key can contaminate evidence and decisions.

## Decision

Artifacts are written to a temporary attempt location, validated, hashed, and
published atomically. Only `COMPLETE` artifacts are consumable. Cache identity
includes stage/version, upstream artifact references and hashes, schema/sample,
configuration, adapter version, domain/policy version, and deterministic seed.
Changes to any dependency, schema, grain, configuration, adapter, policy, or
upstream artifact invalidate affected descendants. Decisions record the exact
artifact and input references needed for replay.

## Consequences

- Failed or cancelled writes cannot masquerade as valid results.
- Reuse is deterministic and explainable.
- Invalidation may recompute more than a filename-only cache, but preserves
  correctness.
