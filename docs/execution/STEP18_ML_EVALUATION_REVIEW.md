# Specialist Step18 — ML Evaluation Engineer

## Result

`PASS` for formal G5 in the final Step18 v4 continuation. The v1-v3 records remain historical; the current evidence baseline is the fresh offline run under `workspace/runs/step18-inference-baseline-v4/evaluation/`, with actual Valentine, Cupid, Splink, schema fusion, ER, and control results. Step19 was not started.

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

## Final Step18 Continuation — Cupid, Splink, and Joint Schema Fusion

The final offline rerun used only the project-local wheelhouse and `workspace/test-temp/step18-nltk-data`; no provider download or network access was used. NLTK preflight is `COMPLETE` for `punkt_tab`, `stopwords`, `wordnet`, and `omw-1.4`, with version `3.10.3`, project-relative path, aggregate hashes, and global search disabled recorded in provisioning metadata.

Valentine `1.0.0` completed all 11 frozen schema groups for both independent families: Coma `11/11 COMPLETE` and Cupid `11/11 COMPLETE`. Family ranking remains independent; Coma's ranking metrics are explicitly undefined where there are no eligible multi-candidate TEST queries, while Cupid reports its defined Recall@K, MRR, and NDCG values. Joint candidate generation uses one combined `item["result"]`, producing TEST `5` candidates (`3` true, `2` false), precision `0.6000`, recall `1.0000`, and no-match false positives `0`.

Joint Schema Fusion executes exactly one request per scenario using the combined SchemaMatchResult and the two real ProfileResults, QualityResults, and DependencyResults for each source. It is `EVALUATED` with `5` decisions, candidate-conditional precision/recall/F1 `0.6000/1.0000/0.7500`, AP `0.4778`, MRR `0.5000`, Recall@1/@3 `0.0000/1.0000`, NDCG@3 `0.6309`, and no Fusion failures. A regression asserts that a scenario invokes Fusion once, not once per matcher family; an actual inspection confirms both `matcher:coma:rank` and `matcher:cupid:rank` in one joint decision.

Splink `4.0.17` completed real unsupervised training and `LINK_AND_DEDUPE` over all 24 records, including same-source `crm-r9/crm-r10`. The benchmark spec now includes `cmp-phone` and complementary EM rules `block-email` and `block-phone`; no TEST truth labels or hand-authored parameters are used. TEST ER evaluation covers 10 records and 45 defined TN pairs. It reports TP `0`, FP `0`, FN `8`, precision undefined because there are no strong predicted links, recall `0.0000`, F1 `0.0000`, false merges `0`, contaminated clusters `0`, false splits `0`, and `5` review-band edges. Hard negatives, including r4/r5/r6, remain in the provider evidence and are not suppressed.

All required tasks and negative controls pass. Formal G5 is `PASS`, `inference_validity_mode=REVIEW_ONLY_VALIDATED`, and automation remains `NOT_AUTHORIZED`. The threshold study remains calibration-only with no selected threshold. The evidence is synthetic and aggregate-safe, not production, temporal, causal, human-acceptance, canonical-identity, repair, deployment, or release evidence. Step19 implementation was not started.

## Final Step18 v4 Empirical Closure

The v4 continuation is a fresh immutable run under `workspace/runs/step18-inference-baseline-v4/evaluation/`; v1, v2, and v3 evidence remain preserved. Runtime provisioning uses persistent project-local `matching-venv` and `er-venv` sandboxes with local temporary and pip-cache roots, `PYTHONNOUSERSITE=1`, and child-process proxy cleanup. Both pinned optional installs (`valentine==1.0.0`, `splink==4.0.17`) failed before package availability could be established; no provider output was fabricated.

The TEST relationship candidate result is `5/7 = 0.7143` recall, `5/17 = 0.2941` precision, and F1 `0.4167`; 17 candidates were generated, 5 true and 12 false. Zero-candidate positives are `rq-orphan-10` and `rq-missing`. Actual Step17 Fusion consumed the executed DependencyResult/ProfileResult/QualityResult artifacts: 17 decisions, AP `0.5277`, candidate-conditional precision/recall/F1 `0.2941/1.0000/0.4545`, MRR `0.6250`, Recall@1/@3 `0.5000/0.7500`, NDCG@1/@3 `0.5000/0.6250`; all decisions remain `REVIEW_REQUIRED`, with HIGH `9` and CONFLICTED `8` bands. End-to-end relationship recall remains `5/7`.

Valentine and Splink remain `INSUFFICIENT_EVIDENCE`, so schema candidate IDs, Coma/Cupid matcher rankings, schema Fusion, ER pairwise metrics, ER cluster metrics, and ER hard-negative provider outcomes are not claimed. The corrected ER truth binds the common-name `r4/r4`, placeholder-phone `r5/r5`, and shared-household `r6/r6` negatives to CALIBRATION, while `r7/r7` is the only missing-fields truth case. The TEST ER universe is 10 records and 45 defined negative pairs.

The real split/leakage audit passes for disjoint groups, truth-cluster separation, and case-pair role consistency. Executed negative controls include truth shuffle, input-order invariance, provider-output mutation, and exact population binding. The repeated relationship provider execution is recorded as `FAIL` for normalized-output reproducibility (`9eb042...` versus `818d297...`); this remains a blocker rather than being hidden. Bootstrap covers all 7 positive TEST scenario groups with 1,000 replicates and interval `[0.4286, 1.0]`. Calibration is relationship-Fusion-only and insufficient; the threshold frontier is studied only, with no selected threshold and no runtime-policy mutation.

Formal G5 remains `PENDING`, `inference_validity_mode=UNVALIDATED`, and automation `NOT_AUTHORIZED`. Step19 was not started.

## Step18 Runtime and Reproducibility Continuation

This continuation repaired the Step18 harness only. Step19 was not started. The provisioning contract now names each package and expected version explicitly, installs only `valentine==1.0.0` or `splink==4.0.17` plus bounded provider dependencies, verifies metadata and import success inside the dedicated interpreter, and never installs the project editable. The matching and ER runners use project-owned fixtures and do not import integration tests or create optional-provider sentinel directories.

Both persistent project-local runtimes were attempted with child-local `TEMP`, `TMP`, `PIP_CACHE_DIR`, and `PYTHONNOUSERSITE=1`. Both installs failed with the recorded category `NETWORK_TIMEOUT` after PyPI read timeouts; no provider output was fabricated. The optional integration contract now behaves correctly in a clean environment: `33 passed, 2 skipped`, with Valentine and Splink skipped because their official runtimes are unavailable.

The executed Desbordante relationship provider was run twice on clean equivalent inputs. Exact output bytes differed (`9996f616...` versus `d3c224cd...`), while the semantic inference fingerprint and task metrics matched (`e63a5fb0...`), so semantic reproducibility is `PASS` and byte equality is correctly reported as `FALSE`. Only documented volatile envelope fields are excluded from the semantic fingerprint; provider artifacts were not rewritten.

The current TEST relationship and Fusion metrics are unchanged: candidate recall `5/7`, precision `5/17`, F1 `0.4167`; Fusion AP `0.5277`, MRR `0.6250`, Recall@1/@3 `0.5000/0.7500`, and NDCG@1/@3 `0.5000/0.6250`. The threshold frontier contains eligible decisions, selected count, coverage, TP, FP, precision, recall, review remainder, and conflict/incomplete abstention. Calibration reasons are explicit: one independent group, insufficient score variation, and missing class. No threshold was selected or promoted.

Formal G5 remains `PENDING`, `inference_validity_mode=UNVALIDATED`, and automation `NOT_AUTHORIZED` because required Valentine/Splink task outputs remain unavailable. G4/G4A remain PASS. Step19 was not started.

## Step18 Offline Provider and Schema-Fusion Closure

This continuation used the complete local wheelhouse at `workspace/test-temp/step18-wheelhouse` with `--no-index --find-links`; no PyPI or network installation was used. Dedicated runtimes were provisioned and verified as `READY`: Valentine `1.0.0` in `matching-venv` and Splink `4.0.17` in `er-venv`, both on Python `3.10.11`, with import and module-location checks. Runtime metadata is stored only in evaluation bindings; provider receipts are written once by the underlying runners and contain no post-binding fields.

Valentine executed the full 11-group schema estate. Coma completed all 11 groups. Cupid completed 2 groups and failed 9 because its optional NLTK corpora were absent from the offline wheelhouse; downloads were disabled and no corpus was fetched. The immutable schema receipt therefore records `INCOMPLETE`, and schema candidate/ranking/Fusion quality is not promoted to an evaluated G5 task. All 22 actual DataProfiler, QualityAnalysisService, and Desbordante dependency producer results completed and were bound to the corresponding frozen schema sources, but producer completion cannot compensate for incomplete matcher evidence.

Splink imported and ran over all 24 records in `LINK_AND_DEDUPE`, including the same-source `crm-r9/crm-r10` pair. Its real training path failed closed because the frozen population did not provide an estimate for every required comparison level (`_is_fully_trained=False`). No default, hand-authored, heuristic, or truth-derived parameter was substituted; ER pairwise/cluster metrics and ER mutation control remain unavailable. The failure detail is retained without raw identity values.

The relationship result remains actual Desbordante evidence: candidate recall `5/7`, precision `5/17`, F1 `0.4167`; Fusion AP `0.5277`, MRR `0.6250`, Recall@1/@3 `0.5000/0.7500`, NDCG@1/@3 `0.5000/0.6250`, with semantic reproducibility `PASS` and unequal exact artifact bytes. The schema mutation control and other available controls execute, but G5 requires every provider/task and therefore remains `PENDING`, `UNVALIDATED`. No automation threshold was selected, and Step19 was not started.
