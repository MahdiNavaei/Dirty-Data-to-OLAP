# Runbook: telemetry degradation (`DEGRADE-001`)

1. Treat exporter failure as an observability incident, not as a business
   success or failure signal.
2. Preserve local structured events and bounded in-memory diagnostics where
   available; do not add secrets or unbounded labels.
3. Restore the exporter and compare the durable run/job state with telemetry
   after recovery.
4. Record missing telemetry intervals and leave candidate SLI windows
   qualified as incomplete.
