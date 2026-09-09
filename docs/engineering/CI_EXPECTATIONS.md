# CI Expectations

No CI workflow is created by Step 05. Once implementation begins, CI should run the deterministic repository validators on every change, then focused unit/contract/architecture tests, adapter tests for touched integrations, and the small deterministic benchmark on protected branches. Heavy scale and external-provider checks are scheduled and must publish environment metadata.

Expected blocking order: repository contracts → lint/type/import boundaries → unit/contract → security negatives → local integration → deterministic end-to-end → release audit. CI reports must distinguish skipped, unavailable, synthetic and executed evidence.
