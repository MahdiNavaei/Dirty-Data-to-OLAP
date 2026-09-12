# Bulk Actions and High-Impact Safety

## Default policy

Bulk review is a bounded convenience for low-impact, homogeneous review objects. There is no arbitrary mass merge, mass canonicalization, mass acceptance of analytical plans, or mass materialization approval.

## Required bulk controls

Before an action is enabled, show:

- exact selection count and scope (project/run/snapshot/table/subject type);
- the grouping rule and why each selected object is homogeneous;
- preview of representative and boundary cases without raw PII;
- excluded conflicts, missing evidence, stale/invalidated items, failures, and high-impact actions;
- partial selection behavior and the list of skipped items;
- expected downstream consequence and invalidation policy;
- actor, policy version, timestamp, rationale, and audit receipt.

The reviewer must explicitly confirm the bounded selection. A filter is not a selection. “Select all” means all items in the current bounded scope and must never silently expand to a project or full source.

## High-impact actions

These always require item-level review or a separately authorized future policy:

- adding or removing identity membership edges;
- merging or finalizing canonical identity;
- choosing survivorship for conflicting values;
- accepting grain or measure semantics;
- approving a compiled target/materialization plan;
- exposing raw sensitive values or external processing.

Use a consequence dialog with before/after meaning, downstream dependencies, reversibility, and the exact audit record. “Undo” must not imply destructive source mutation; invalidation and a replacement artifact are preferred.

## Reversible handling

Review decisions and artifact references are append-only in meaning. A correction creates a new decision or artifact and links the prior item as superseded/invalidated. Source records are not deleted by ER or canonical review. A rejected proposal remains visible with its rationale.
