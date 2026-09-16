# Runbook: graceful shutdown and restart (`SHUTDOWN-001`)

1. Mark the local execution submission boundary closed and request worker-pool
   shutdown.
2. Allow the current bounded batch to finish; the pool admits no new claims
   after the shutdown request.
3. Reopen the same durable control store and inspect leases, delivery phases
   and cancellation requests.
4. Restart only with a bounded worker pool and verify the recovery projection
   before accepting new work.
