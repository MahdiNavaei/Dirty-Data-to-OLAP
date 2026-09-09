# Bootstrap Baseline

## Scope

Bootstrap / Prompt 0 establishes a controlled repository baseline. It does not execute Specialist Step 01 and does not implement application features.

## Git repair

- The existing local project directory was inventoried before Git initialization.
- Git was initialized in place with branch `main`.
- `origin` was added only for `https://github.com/MahdiNavaei/Dirty-Data-to-OLAP.git`.
- The existing remote `origin/main` history was fetched and inspected.
- Local `main` was attached with `git reset --mixed origin/main`; the working tree was not hard-reset or cleaned.
- The remote `README.md` was materialized because no local README existed.
- The existing local `docs/` tree was preserved and brought under version control.

## Required baseline state

- Project root: `D:\Projects\Dirty-Data-to-OLAP`
- Branch: `main`
- Expected remote: `MahdiNavaei/Dirty-Data-to-OLAP`
- Initial remote HEAD: `9a73eb90cbc0e6c77829863b87a055bfdf692391`
- Next specialist: Step 01 — Product Manager / Technical Product Owner
- Completed specialist steps: none
- G0: `PENDING`
- All later gates: `PENDING`

## Boundary and reuse policy

Project-owned runtime state is intended to remain inside the project root. Later OSS research clones belong only under `research/oss/`, are ignored by Git except for its README, and must never be runtime dependencies. The provenance ledger is intentionally empty because Bootstrap did not inspect or clone an external project.

## Bootstrap limitations

No source code, tests, application dependencies, benchmark datasets, connectors, or runtime infrastructure existed in the local project at bootstrap. None were invented here.
