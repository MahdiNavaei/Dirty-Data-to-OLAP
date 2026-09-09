# Step11 Security Handoff

Step10 leaves the repository with a project-owned privacy policy service,
classification/artifact/exposure contracts, conservative defaults, recursive
redaction, keyed pseudonymization boundary, privacy-owned cleanup and executed
canary tests.

Step11 must independently verify least privilege, read-only database access,
credential handling, query guardrails, connection isolation and database
security. Step10 does not implement those controls and does not mark formal
G3 Source Safety complete. Formal `G3_SOURCE_SAFETY` remains `PENDING` until
Step11 evidence is accepted.
