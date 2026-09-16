# Runbook: runnable queue backlog (`SRE-SLI-002`)

1. Inspect `ddo_queue_jobs` and
   `ddo_queue_oldest_runnable_age_seconds` by bounded queue state.
2. Separate runnable work from review, blocked and reconciliation states.
3. Increase concurrency only within the configured worker and per-run/source
   bounds; do not bypass leases or validation gates.
4. If the age threshold is exceeded, record the incident and preserve the
   candidate-objective status for later operational review.
