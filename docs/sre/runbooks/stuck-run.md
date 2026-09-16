# Runbook: stuck run (`STUCK-001`)

1. Record `run_id`, current UTC time and the last durable job/attempt state.
2. Run `python tools/validate_step35_sre.py` and inspect the typed recovery
   projection for expired leases and `RECONCILIATION_REQUIRED` phases.
3. Do not mark a run successful. Resume only queued or expired jobs classified
   `SAFE_TO_RESUME`; route uncertain delivery to reconciliation/review.
4. Record the action and resulting durable status in the execution log.
