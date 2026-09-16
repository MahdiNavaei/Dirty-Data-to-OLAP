# Runbook: provider unavailable (`RETRY-001`)

1. Classify the provider failure using the bounded failure taxonomy.
2. Retry only a transient failure within `RetryPolicy`; use exponential delay
   and the configured maximum.
3. Mark terminal or unknown outcomes durably. Unknown side effects require
   reconciliation and must not be retried blindly.
4. Keep source data read-only and surface unavailable capability explicitly.
