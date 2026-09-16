# Runbook: artifact integrity failure (`ART-001`)

1. Stop downstream processing when a referenced artifact is missing,
   unreadable, hash-mismatched or size-mismatched.
2. Run the content-addressed artifact verification and retain the typed
   integrity result.
3. Rebuild or republish only through the owning durable stage; never edit a
   published blob in place.
4. Route uncertain lineage to reconciliation and retain the original evidence.
