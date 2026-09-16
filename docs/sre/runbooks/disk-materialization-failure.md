# Runbook: disk or materialization failure (`DEGRADE-002`)

1. Treat a failed write, quota breach, unreadable target or failed row-count
   check as terminal for that materialization attempt.
2. Confirm the temporary target is removed and no partial target is published.
3. Resolve disk/quota conditions, then resume through the durable plan if the
   attempt is replay-safe; otherwise reconcile first.
4. Never report a materialization success from a partial DuckDB file.
