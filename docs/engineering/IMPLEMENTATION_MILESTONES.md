# Implementation Milestones

The authoritative order is the 41-step sequence in `docs/Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base/05_MASTER_BUILD_SEQUENCE.md`. The machine-readable sequence and grouped work packages are in `specs/implementation_plan.yml`; grouping does not permit reordering.

Step 06 is the first implementation specialist. Steps 01–05 establish and review contracts only. Step 06 may create the minimum package/test scaffold, but its substantive deliverable is the read-only database access/introspection substrate and tested handoff to Step 07. It does not implement dlt, complete SourceAdapter ingestion, business stages, entity resolution, canonical semantics or OLAP semantics.

The handoff pattern is: prerequisite gate → bounded change → focused tests → artifact/evidence receipt → next specialist. Evidence producers precede fusion; fusion and evaluation precede canonical finalization; canonical finalization precedes OLAP; correctness precedes performance; AppSec precedes Red Team; Technical Writer is final.
