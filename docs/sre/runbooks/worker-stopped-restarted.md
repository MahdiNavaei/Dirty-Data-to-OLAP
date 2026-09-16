# Runbook: worker stopped or restarted (`RESTART-001`)

1. Stop admitting new commands and preserve the SQLite file and artifact root.
2. Reopen the same project control store; never replace it with an in-memory
   queue.
3. Build a recovery projection and let lease expiry fence the old worker.
4. Start one bounded worker pool and verify that only safe queued/expired work
   is claimed. Unknown delivery remains reconciliation-required.
