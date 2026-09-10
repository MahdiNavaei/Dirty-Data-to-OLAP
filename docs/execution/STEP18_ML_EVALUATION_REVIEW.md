# Specialist Step18 — ML Evaluation Engineer

## Result

`PASS` for review-only inference validity. G5 is `REVIEW_ONLY_VALIDATED`; automation remains unauthorized. Step19 was not started.

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
