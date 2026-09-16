# Runbook: control store unavailable (`CTRL-001`)

1. Treat command delivery as unavailable or unknown; do not return an accepted
   acknowledgement from a failed control-store write.
2. Preserve the stable command identity and idempotency key for reconciliation.
3. Restore service only after SQLite opens, integrity-checks and reports the
   supported schema version.
4. Replay through the durable command boundary, never by manually duplicating
   a handler call.
