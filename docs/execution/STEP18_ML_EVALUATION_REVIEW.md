# Specialist Step18 — ML Evaluation Engineer

## Result

`PENDING` for formal G5. The final Step18 v3 run is the current evidence baseline: Desbordante, DataProfiler, and project-owned QualityAnalysisService outputs are normalized, immutable, population-bound, and consumed by the evaluator. Valentine and Splink remain unavailable, so schema and entity metrics are not claimed. Step19 was not started.

The historical v1 content remains bound to `cd2ab6be6b665b972c00135325f3e4954c381a51`. The v2 closure remains historical in `workspace/runs/step18-inference-baseline-v2/evaluation/`; the fresh corrected run is retained separately in `workspace/runs/step18-inference-baseline-v3/evaluation/`.

## Post-Step18 Empirical Integrity Closure

v1 executed real Desbordante, Valentine, and Splink smoke paths, but its relationship, schema, and entity headline numbers were calculated from pre-authored runtime scores or a local ER heuristic. Those numbers are retained only as historical/provisional evidence and are not used by v2.

The v2 chain is provider scenario fixture -> real adapter -> normalized project-owned result -> hashed provider binding -> frozen topology truth -> metric. The Desbordante v2 runner executed 13 corruption scenarios and produced 11 retained topology-positive misses, with candidate-generation recall `2/13 = 0.1538`. Real `ProfileResult` and `QualityResult` artifacts were also executed for all 13 scenarios and loaded into `EvidenceFusionService`; Fusion produced 11 decisions with no Fusion failures. Valentine and Splink v2 could not be executed in the current interpreter because their pinned packages were unavailable and package installation was blocked; their bindings remain `INSUFFICIENT_EVIDENCE`.

Formal state is `G5_INFERENCE_VALIDITY=PENDING`, with separate `inference_validity_mode=UNVALIDATED` and `automation=NOT_AUTHORIZED`. The receipt-only, input-order, zero-candidate, provider-output mutation, topology truth, and evaluation-only shuffle controls are persisted under the v2 run. G4 and G4A remain PASS.

## Scope and evidence

The pass began from branch `main`, HEAD `bd1efcebab1966d052d0ddd84ebbd9b7294ee815`, with the preserved untracked `tests/quality_unit_artifacts/` directory untouched. Step17 relationship and mapping policies were frozen before TEST evaluation. Runtime fixtures and truth artifacts are separate and independently hashed.

Required provider and project-contract executions completed and produced normalized hashed receipts:

- Desbordante Docker dependency discovery: `COMPLETE`.
- DataProfiler `0.13.4` Profiling over the frozen v2 staged scenarios: `COMPLETE`.
- Project-owned QualityAnalysisService over those ProfileResults and staged batches: `COMPLETE`.
- Valentine 1.0.0 schema matching: unavailable; binding `INSUFFICIENT_EVIDENCE`.
- Splink 4.0.17 entity resolution: unavailable; binding `INSUFFICIENT_EVIDENCE`.

The local scikit-learn 1.7.2 path executed as an experimental grouped ranking evaluation with a label-shuffle control. Semantic AI was explicitly optional and not executed; no model was downloaded or pulled.

## Historical v1 result (superseded)

Relationship fusion precision/recall/F1 was `0.500/1.000/0.667`; candidate-generation recall was `0.667` and the missing candidate query remained visible. Schema precision/recall/F1 was `0.333/1.000/0.500`, including one no-match false positive. Held-out ER pairwise precision/recall/F1 was `1.000/1.000/1.000` with zero false merges and zero false splits on the anonymized-token cases.

Those v1 numbers are historical only. The current v2 relationship evidence is provider-bound: candidate-generation recall is `2/13 = 0.1538`, Fusion is `EVALUATED` from actual DependencyResult/ProfileResult/QualityResult artifacts, and schema/entity metrics remain unclaimed.

The threshold frontier was studied on CALIBRATION only. No threshold was selected and no runtime policy changed. Calibration remains insufficient for a product probability claim. Errors and slices retain denominators and expose low-cardinality, type/name, no-match, multiple-target, missing-candidate, Unicode, and transitive controls.

## Boundary

This result is not production performance, temporal generalization, human acceptance, canonical identity, repair execution, release readiness, or authorization for automation. Step19 may decide whether additional truth and calibration work is warranted; Step19 implementation and `ReviewDecision` artifacts are intentionally absent.

## Final Step18 v3 Empirical Closure

The v3 run is a fresh, non-overwriting evaluation under `workspace/runs/step18-inference-baseline-v3/evaluation/`. It uses explicit relationship, schema, and entity group estates; TEST-only headline metrics; a shared relationship fixture generator; exact orphan-rate controls; complete cluster-derived ER truth; order-safe composite endpoint identity; and strict immutable provider receipts with exact population fingerprints.

The current TEST relationship result is candidate-generation recall `5/7 = 0.7143`, with 17 generated candidates, 5 true candidates, 12 false candidates, and missing positive queries `rq-orphan-10` and `rq-missing`. Fusion is `EVALUATED` from actual DependencyResult, ProfileResult, and QualityResult artifacts, with 17 decisions and no Fusion failures. Profile and Quality executions completed for all 13 relationship scenarios.

Valentine `1.0.0` and Splink `4.0.17` were attempted in isolated project-local runtimes and remain unavailable: package installation failed because the configured proxy was refused and no matching distribution was available. Their bindings are `INSUFFICIENT_EVIDENCE`; no schema or ER quality number is published. Coma and Cupid are represented as separate matcher families when provider output exists; no family aggregation is performed.

Executed controls include recomputed truth shuffle, input-order rerun, provider-output mutation, receipt-only fail-closed binding, authored-score absence, exact population binding, reverse-pair split leakage, and TEST slice metrics with denominators. Bootstrap is computed over 7 TEST groups with 1,000 replicates; the interval is `[0.4286, 1.0]`. Calibration is computed but insufficient for a calibrated score claim; the threshold frontier is calibration-only, studied without selecting a threshold or mutating runtime policy. Automation remains `NOT_AUTHORIZED` and formal G5 remains `PENDING`.

This remains synthetic/aggregate-safe evidence and is not production, temporal, causal, human-acceptance, canonical-identity, repair, deployment, or release evidence. Step19 was not started and no threshold was promoted.
