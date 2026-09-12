# Information Architecture

## Top-level navigation

Navigation is organized by user work and lifecycle, not by algorithm or provider name:

1. **Projects / Sources** — project scope, source catalog, snapshots, privacy and exposure state.
2. **Runs / Snapshots** — run history, freshness, content and schema bindings, observation coverage.
3. **Data condition** — quality dimensions, issues, repair proposals, failures and coverage.
4. **Relationships** — relationship hypotheses and their evidence decisions.
5. **Schema mappings** — cross-source field mappings and semantic conflicts.
6. **Identity / ER** — identity proposals, membership edges, ER requirement/result, canonical conflicts.
7. **Canonical** — accepted canonical entities/events, survivorship and source lineage.
8. **Analytical / OLAP** — facts, dimensions, grains, measures, units, currencies and plans.
9. **Materialization** — compiled plan, generated SQL identity, controlled target configuration and outputs.
10. **Validation** — source accounting, independent truth, reconciliation checks, discrepancies and G6 receipt.
11. **Lineage / Evidence** — provenance graph, evidence producer status, source references, decision history and invalidation.

Algorithm names such as Cupid, Splink, or a matcher may appear as producer metadata inside an evidence detail panel. They must not be the primary navigation model.

## Page composition

Every list page has:

- current project, run, and snapshot context;
- coverage and freshness banner;
- filters that show scope and do not silently alter it;
- review objects grouped by lifecycle state;
- explicit counts for complete, incomplete, failed, unavailable, privacy-blocked, stale, and invalidated items;
- a non-color status label and a keyboard-accessible focus order.

Every detail page has a stable, privacy-safe route built from opaque project-owned artifact IDs. It does not put raw values, emails, phone numbers, connection strings, or source record values in URLs or page titles.

## Review hierarchy

The default hierarchy is:

1. **Scope and identity strip:** project, run, snapshot, stage, subject ID, content hash and schema version.
2. **Decision summary:** current state, meaning, risk, and the action consequence.
3. **Evidence ledger:** supporting, contradicting, missing, unavailable, failed, privacy-blocked and derived references.
4. **Context and provenance:** source/schema fingerprints, producer, policy, model version, domain assertion refs and lineage.
5. **Downstream impact:** guarded stage, dependent artifact IDs, what is blocked or invalidated by each action.
6. **Technical expansion:** raw metric names/semantics, normalization, aggregation rule, SQL hash or other details only when relevant.

The summary is never allowed to hide a conflict, an incomplete producer, or a stale subject. Collapsed sections must expose their presence and be keyboard/screen-reader expandable.

## Cross-page context

The current context is persistent across the journey: `project -> run -> snapshot -> stage -> subject`. Changing any context shows a confirmation summary and recalculates whether the visible review decision is still compatible. A changed content hash or schema fingerprint yields `STALE` / `INVALIDATED`, not a silent refresh.
