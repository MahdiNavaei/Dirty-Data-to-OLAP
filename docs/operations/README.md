# Operations and recovery

The reference runtime is local and project-owned. Use the commands in the
[developer workflow](../development/DEVELOPER_WORKFLOW.md) to diagnose tools,
bootstrap locked dependencies, start the local service, and inspect durable
run/job state.

## State and ownership

- Disposable environment and demo state stay under `.venv/` and `.ddo/`.
- The Step40 demo state root is resolved under `<repo>/.ddo`; absolute external,
  traversal, symlink-escape, invalid-type, and pre-existing non-directory
  roots fail before backend construction or writes.
- SQLite control state owns run/job/review metadata; raw source payloads are
  not moved into the control store as a generic artifact cache.
- Durable commands have replay/uncertainty semantics. The runtime does not
  claim exactly-once delivery or a production broker.

## Recovery boundary

Use the existing SRE [recovery matrix](../sre/recovery-matrix.json) and
[resilience report](../execution/STEP36_CHAOS_RESILIENCE_REVIEW.md) for the
tested local fault cases. A local restart/replay result is not evidence of
multi-node HA or production disaster recovery.

## Security operations

The local `local_test` principal header and `trusted_proxy` resolver are
integration boundaries. They are not password/JWT/credential-store
authentication. Keep source credentials out of logs and browser projections;
use read-only provider roles and the source security matrix for deployment
design rather than treating these local modes as production identity.
