# Step28 Durable Runtime Integrity Repair

Status: `PASS`

This is a surgical post-Step28 repair receipt. It closes the durable-runtime integrity findings identified after the original Step28 review. The original receipt at `docs/execution/STEP28_DISTRIBUTED_JOB_PROCESSING_REVIEW.md` is preserved and is not replaced by this document. Step29 was not started.

## Scope and baseline

- Repository: `MahdiNavaei/Dirty-Data-to-OLAP`
- Branch: `main`
- Starting baseline for this repair: `HEAD=origin/main=353e22305e909eb450411a38b2b6f2ee52abe9d2`
- Content commit: `a41f2108951a4167684c8e3da2c8d86ffba0b725`
- Scope: Step28 durable job processing, review resume integrity, delivery uncertainty, cancellation, admission control, execution-plan selection, SQLite migration and the G6 completion guard
- Out of scope: Step24 changes, frontend implementation, Step29, deployment, broker/HA claims and G7 completion
- `tests/quality_unit_artifacts/` was not read, modified, staged or used as evidence

## Integrity closure

1. Review identity is now project-owned and shared end to end. The authoritative key is:
   `review_checkpoint_id|subject_artifact_id|subject_content_hash|subject_semantic_id|applicability_fingerprint`.
   The worker obtains the authoritative context before resume, checks the current artifact hash, applies the same key used by the FastAPI review path and then invokes the compatibility policy. Mutated semantic identity, applicability, policy or content hash cannot authorize a new attempt.

2. Review checkpoints are control-plane stages. The authoritative stage-graph loader retains real review checkpoints, required/optional/conditional selection, explicit selection reasons and conditional dependency metadata. Optional absent branches do not block an otherwise valid plan, while required branches remain enforced.

3. Handler delivery is durable. Jobs and attempts persist `ATTEMPT_CREATED`, `HANDLER_DELIVERY_STARTED`, `RESULT_RECORDED` and `FINALIZED`. The handler-delivery marker is written before invocation. A result is durably recorded before finalization, allowing restart recovery to finalize the recorded typed result without invoking the handler twice.

4. Replay safety is explicit. Replay-safe work can be retried after uncertain delivery. A non-replay-safe handler whose delivery began without a durable result becomes `UNKNOWN_SIDE_EFFECT` / reconciliation-required and is not blindly re-executed. This repair makes no exactly-once claim.

5. Cancellation is cooperative and durable. A handler may observe current run/job cancellation through a live probe. If cancellation is observed at the stage boundary, the worker records typed cancellation and publishes no output references for that result.

6. Admission control is enforced transactionally by the SQLite claim path for active jobs per run and per source scope. The local worker pool also uses bounded executor capacity; it does not create an unbounded task set.

7. Run success is guarded by exact typed G6 evidence. The worker requires current-run `G6_DATA_CORRECTNESS` evidence with `PASS` and eligibility, a registered published `ValidationReport` with matching identifiers and content hash, verified artifact bytes and typed report agreement. Missing, stale, corrupt, wrong-run, wrong-hash, ineligible or negative evidence cannot be promoted to success. G6 is not recomputed in Step28.

8. SQLite v5-to-v6 migration is additive and preserves existing plans, jobs and attempts while adding delivery phase, replay-safety, source-scope and durable-result persistence. Reopen tests verify the migrated state.

## Executed evidence

- Step28 validator: `tools/validate_step28_job_processing.py` — `PASS`, `22` scenarios
- Focused Step28 regression: `31 passed`
- Full feasible regression using Python 3.10: `439 passed, 2 skipped, 41 warnings`
- Skips: one Splink and one Valentine test because their optional official runtimes were not installed in the clean environment
- Repository validators: `28/28 PASS`
- `compileall` for `src` and `tools`: `PASS`
- `git diff --check`: `PASS`
- The passing full run emitted a non-fatal dlt/SQLite cursor-cleanup traceback during generator cleanup; it did not change the exit status or test result
- The default Python 3.13 environment could not collect the full suite because `duckdb`/`pyarrow` were unavailable; Python 3.10 was the feasible verified environment

## Final state

- `last_completed_step=28`
- `last_completed_role=distributed_job_processing_engineer`
- `step28_status=COMPLETED_DURABLE_JOB_PROCESSING`
- `step28_integrity_repair=PASS`
- `current_step=29`
- `current_role=frontend_engineer`
- `step29_started=false`
- `step29_status=NOT_STARTED`
- `G5=PASS`, `G6=PASS`, `G7A=PASS`, `G7B=PASS`
- `G7=PENDING`
- `blocked=false`

The remaining limitations are unchanged: this is a local SQLite reference implementation and does not establish broker-backed HA, multi-node execution, production capacity, frontend/browser acceptance, deployment, full observability or end-to-end product completion.
