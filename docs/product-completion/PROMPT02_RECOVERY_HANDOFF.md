# Prompt02-R Recovery Handoff

Status: `BLOCKED — LIVE HETEROGENEOUS ACCEPTANCE PENDING`

## Baseline and worktree

- Repository: `MahdiNavaei/Dirty-Data-to-OLAP`
- Branch: `main`
- Starting committed HEAD: `eb6f9aa81bacabf5b2c05e59fdcf3d4e28e0c304`
- `origin/main` was at the same SHA at the start of recovery.
- Current recovery work is uncommitted; no commit, push, or new CI run was
  initiated by this recovery.
- Historical latest CI: run `35668147926`, completed failure in Prompt02
  four-source acceptance.

## What was completed locally

The pre-existing migration was preserved and completed through the accepted
composition root. Multi-source execution now uses the shared run, immutable
source-set binding, server-owned plan, worker/lease/attempt lifecycle, typed
artifact publication, durable review boundary, canonical services, analytical
planner/compiler, accepted materializer, semantic stage and G6 validation.

The four-source harness now describes PostgreSQL CRM `crm_customers`, MySQL
ERP `erp_accounts`, SQL Server Sales `sales_events`, and Legacy CSV events.
The fixture keeps registry records distinct from events, includes the
same-name hard negative, unresolved event references, duplicate ERP email
candidate evidence, and a null-marked orphan value. The independent oracle is
loaded only after product execution by the test.

## Evidence obtained

- Python compileall: passed.
- Architecture-equivalence test: `4 passed`.
- Focused source/canonical/analytical contract tests: `19 passed`.
- `git diff --check`: passed.
- Prompt02 acceptance collection is valid under `python -m pytest`, but the
  test skips because no live database URLs are configured.
- The existing Step29 focused test was run and failed in the current local
  provider-less environment at dependency discovery; it was not converted into
  a Prompt02 claim.

## Blockers and next bounded action

The current environment has no Prompt02 PostgreSQL/MySQL/SQL Server URLs and
does not provide the Desbordante provider module. Consequently there is no
durable four-source run ID, no accepted-stage attempt set, no persisted review
evidence, no materialized Prompt02 target, and no G6 acceptance receipt to
report. Do not start another remote CI attempt until the local provider-capable
ladder can run.

Prompt02 remains `BLOCKED`; the original V1 state remains `PENDING`; Prompt03
and Step42 remain `NOT_STARTED`.

The protected `tests/quality_unit_artifacts/` directory and the user-owned RAR
remain unmodified, unstaged and uncommitted. No reset, clean, amend, rebase,
force push, or history rewrite was used.
