# Slice and Error Analysis

Errors are categorized as ranking error, conflict deferred, or missing evidence. Slice denominators are retained for low-cardinality, type mismatch, same-name/different-meaning, missing-candidate, no-match, multiple-target, matcher-disagreement, and ER transitive/Unicode cases.

The current held-out relationship errors include a low-cardinality false positive and a same-name/type-mismatch false positive. Schema errors include a no-match false positive and a multiple-target alternative. These are diagnostic findings only; they do not authorize changing Step17 weights or inventing a product threshold.
