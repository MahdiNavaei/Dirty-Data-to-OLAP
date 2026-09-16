# Runbook: control-store restore (`CTRL-002`)

1. Obtain the backup bundle and its adjacent manifest from the approved local
   backup location.
2. Verify SHA-256, byte size, required tables, schema version and
   `PRAGMA integrity_check` before restore.
3. Restore to an isolated path with the SQLite backup API and reopen through
   `SQLiteControlStore`.
4. Confirm run/job/attempt/review/artifact-reference counts and reconcile any
   in-flight delivery before resuming.
