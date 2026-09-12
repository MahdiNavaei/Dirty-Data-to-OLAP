# Accessibility Requirements

These are acceptance criteria for the future implementation. They are design requirements, not a claim that a frontend exists in Step25.

## Information and state

- Never convey state by color alone. Every state has text, an icon/shape where useful, and a programmatic label.
- Pair numeric scores with their metric name, semantics, scope, and band. Do not rely on color gradients to express evidence strength.
- Put subject, stage, run, snapshot, and consequence in a readable heading hierarchy.
- Use semantic headings, lists, tables, buttons, dialogs, and landmarks; do not make a whole card one unlabeled click target.
- Make conflicts, missing evidence, stale state, and required action visible in the collapsed summary and expandable in detail.

## Keyboard and focus

- All filters, tabs, expansion controls, evidence links, actions, dialogs, and tables are keyboard reachable.
- Focus order follows the visual and decision order; focus is visible and never trapped except in a modal dialog.
- Opening a consequence dialog moves focus to its heading; closing returns focus to the triggering control.
- No action is available only through hover, drag, gesture, or a color picker.

## Tables, graphs, and nonvisual review

- Relationship and lineage graphs, when implemented in later scope, must have a synchronized text/table representation with subject IDs, edges, direction, scope, provenance, and state.
- Tables expose headers, row/column associations, sort state, selected state, and counts/denominators to assistive technology.
- Long evidence lists support a screen-reader-readable expansion of all supporting, contradicting, missing, unavailable, and failed references.
- Do not require visual proximity or color to understand which evidence belongs to which subject.

## Errors and actions

- Errors are associated with the relevant field/control and explain recovery.
- Confirmation dialogs state what will change, which exact objects are selected, what cannot be undone, and which downstream stages are affected.
- Disabled actions expose a reason and a safe next step.
- Timeouts and long-running states announce status without repeatedly stealing focus.
