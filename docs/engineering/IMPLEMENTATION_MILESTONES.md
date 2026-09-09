# Implementation Milestones

The authoritative order is the 41-step sequence in `docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base/05_MASTER_BUILD_SEQUENCE.md`. The machine-readable sequence and grouped work packages are in `specs/implementation_plan.yml`; grouping does not permit reordering.

Step 06 is the first implementation specialist. Steps 01–05 establish and review contracts only. The first implementation slice must leave the stage services unimplemented while making their contracts, persistence boundaries and test entry points explicit.

The handoff pattern is: prerequisite gate → bounded change → focused tests → artifact/evidence receipt → next specialist. Evidence producers precede fusion; fusion and evaluation precede canonical finalization; canonical finalization precedes OLAP; correctness precedes performance; AppSec precedes Red Team; Technical Writer is final.
