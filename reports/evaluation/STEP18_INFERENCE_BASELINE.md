# Step18 Inference Baseline

Status: `REVIEW_ONLY_VALIDATED`

Dataset: `step18-inference-quality-v1`; protocol: `step18-protocol-v1`; seed: `20260911`. TEST was group-held-out and executed after the protocol/policy manifest was written. Truth remained separate from runtime fixtures.

Results on the held-out synthetic scenario groups:

- Relationship fusion: precision `0.500`, recall `1.000`, F1 `0.667`, AP `0.500`; candidate-generation recall `0.667` with `rq-missing` exposed; MRR `0.500`, Recall@3 `1.000`.
- Schema matching: precision `0.333`, recall `1.000`, F1 `0.500`; no-match false-positive count `1`.
- Entity resolution: pairwise precision/recall/F1 `1.000/1.000/1.000`, false merges `0`, false splits `0` on the held-out anonymized-token cases.
- Applied ML: `EXECUTED_EXPERIMENTAL` with grouped scikit-learn evaluation and label-shuffle control; no learned probability or automation threshold was selected.

Real provider receipts completed for Desbordante Docker, Valentine 1.0.0, and Splink 4.0.17. Semantic AI remained optional and was not executed. Calibration is `INSUFFICIENT_CALIBRATION_DATA`; threshold study is `NO_AUTOMATION_THRESHOLD_SELECTED`; automation is `NOT_AUTHORIZED`.

This is review-only validation over synthetic/aggregate-safe evidence. It is not production performance, causal evidence, acceptance, canonical identity, repair authorization, or release evidence. Step19 implementation was not started.
