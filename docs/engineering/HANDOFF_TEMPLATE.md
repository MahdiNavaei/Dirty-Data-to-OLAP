# Specialist Handoff Template

```yaml
specialist: "StepXX — Role"
status: PASS | BLOCKED | NEEDS_REVIEW
start_commit: "full sha"
end_commit: "full sha"
scope: "bounded component/stage/contract"
inputs:
  - "gate and artifact"
changes:
  - "file or contract"
tests:
  - command: "exact command"
    result: PASS | FAIL | SKIPPED
evidence:
  - "artifact path and hash or report"
known_limitations:
  - "what is not claimed"
next_specialist: "StepXX — Role"
open_risks:
  - "risk and owner"
```
