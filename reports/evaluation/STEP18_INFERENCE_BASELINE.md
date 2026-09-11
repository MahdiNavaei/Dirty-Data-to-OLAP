# Step18 Inference Baseline

Status: `PENDING`

Dataset: `step18-inference-quality-v2`; protocol: `step18-evaluation-protocol-v2`; seed: `20260911`. v1 remains historical/provisional and is superseded for G5 quality claims.

The corrected v2 chain is: provider execution -> normalized project-owned output -> frozen artifact -> topology truth join -> metric. Desbordante v2 executed over 13 provider scenarios and its candidate output is consumed by candidate-generation metrics. Valentine and Splink v2 outputs are absent in the current interpreter, so their metrics are not claimed. Fusion is also `INSUFFICIENT_EVIDENCE` because actual ProfileResult and QualityResult artifacts were not present; status-only producer evidence is not substituted.

Observed v2 relationship candidate-generation recall is `2/13 = 0.1538`; eleven topology-positive queries are retained as provider misses, including the zero-candidate cases. This is provider evidence, not a product-quality pass.

Provider binding status: Desbordante `EXECUTED`; Valentine `INSUFFICIENT_EVIDENCE`; Splink `INSUFFICIENT_EVIDENCE`. Calibration remains evidence-derived insufficient; the threshold study is calibration-only with `NO_AUTOMATION_THRESHOLD_SELECTED`; automation is `NOT_AUTHORIZED`.

This is an incomplete empirical closure over synthetic/aggregate-safe evidence. G4 and G4A remain PASS. Step19 implementation was not started.
