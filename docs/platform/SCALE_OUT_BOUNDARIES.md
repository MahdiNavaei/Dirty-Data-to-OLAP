# Step24 Handoff: Scale-Out Boundaries

Step23 hands off stable semantics, not an existing distributed system.

## Must remain stable

- artifact identity is logical ID plus content hash, never filename or host
  path;
- published managed bytes are immutable and hash-verified;
- external references are controlled and re-verified;
- control metadata is typed, parameterized and separate from raw data;
- dependencies pin upstream hashes and stale edges fail closed;
- cache keys include content, policy, configuration and contract versions;
- run/stage records are durable and revisioned;
- staged manifests bind source, snapshot, table, schema, parts and hashes;
- G6 is restored from the exact `ValidationReport` receipt fields;
- cleanup is authorized, retention-aware and protects pinned evidence.

## PostgreSQL mapping

The SQLite tables are the V1 reference schema. A PostgreSQL adapter should
preserve the table-level identity constraints, foreign keys, transaction
boundaries and compare-and-swap revision predicates. Migration compatibility
must be explicit and a newer schema must still fail closed for older adapters.
Connection credentials and database administration remain outside application
contracts.

## Object-store mapping

The filesystem storage key `blobs/sha256/<prefix>/<hash>` is already an
object-store-compatible key. A future object adapter should use conditional
create or equivalent immutable-write semantics, retain metadata and hashes,
and separate managed object ownership from external references. Listing and
cleanup must be scoped by typed artifact references rather than arbitrary
prefix deletion.

## Explicitly out of scope for Step23

There is no worker scheduler, queue, distributed partition execution,
checkpoint coordinator, Backend RunManager, API/UI orchestration, or claim of
multi-node consistency. Those concerns begin in later specialist steps.
