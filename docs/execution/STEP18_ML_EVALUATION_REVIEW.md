# Specialist Step18 — ML Evaluation Engineer

## Result

`PENDING` for formal G5. The post-Step18 closure identified that v1 real-provider receipts were capability evidence, not the source of the published quality metrics. The v2 path now fails closed and binds normalized output where available. Step19 was not started.

The historical v1 content remains bound to `cd2ab6be6b665b972c00135325f3e4954c381a51`. The corrected closure is recorded separately in the v2 run under `workspace/runs/step18-inference-baseline-v2/evaluation/`.

## Post-Step18 Empirical Integrity Closure

v1 executed real Desbordante, Valentine, and Splink smoke paths, but its relationship, schema, and entity headline numbers were calculated from pre-authored runtime scores or a local ER heuristic. Those numbers are retained only as historical/provisional evidence and are not used by v2.

The v2 chain is provider scenario fixture -> real adapter -> normalized project-owned result -> hashed provider binding -> frozen topology truth -> metric. The Desbordante v2 runner executed 13 corruption scenarios and produced 11 retained topology-positive misses, with candidate-generation recall `2/13 = 0.1538`. Valentine and Splink v2 could not be executed in the current interpreter because their pinned packages were unavailable and package installation was blocked; their bindings are `INSUFFICIENT_EVIDENCE`. Fusion also remains incomplete because no actual ProfileResult and QualityResult artifacts were available, and status-only producer evidence is rejected.

Formal state is `G5_INFERENCE_VALIDITY=PENDING`, with separate `inference_validity_mode=UNVALIDATED` and `automation=NOT_AUTHORIZED`. The receipt-only, input-order, zero-candidate, provider-output mutation, topology truth, and evaluation-only shuffle controls are persisted under the v2 run. G4 and G4A remain PASS.

## Scope and evidence

The pass began from branch `main`, HEAD `bd1efcebab1966d052d0ddd84ebbd9b7294ee815`, with the preserved untracked `tests/quality_unit_artifacts/` directory untouched. Step17 relationship and mapping policies were frozen before TEST evaluation. Runtime fixtures and truth artifacts are separate and independently hashed.

Required real provider executions completed and produced normalized hashed receipts:

- Desbordante Docker dependency discovery: `COMPLETE`.
- Valentine 1.0.0 schema matching: `COMPLETE`.
- Splink 4.0.17 entity resolution: `COMPLETE`.

The local scikit-learn 1.7.2 path executed as an experimental grouped ranking evaluation with a label-shuffle control. Semantic AI was explicitly optional and not executed; no model was downloaded or pulled.

## Held-out result

Relationship fusion precision/recall/F1 was `0.500/1.000/0.667`; candidate-generation recall was `0.667` and the missing candidate query remained visible. Schema precision/recall/F1 was `0.333/1.000/0.500`, including one no-match false positive. Held-out ER pairwise precision/recall/F1 was `1.000/1.000/1.000` with zero false merges and zero false splits on the anonymized-token cases.

The threshold frontier was studied on CALIBRATION only. No threshold was selected and no runtime policy changed. Calibration remains insufficient for a product probability claim. Errors and slices retain denominators and expose low-cardinality, type/name, no-match, multiple-target, missing-candidate, Unicode, and transitive controls.

## Boundary

This result is not production performance, temporal generalization, human acceptance, canonical identity, repair execution, release readiness, or authorization for automation. Step19 may decide whether additional truth and calibration work is warranted; Step19 implementation and `ReviewDecision` artifacts are intentionally absent.
