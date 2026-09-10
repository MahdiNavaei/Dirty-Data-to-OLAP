# Ground Truth and Scenario Splits

Truth is stored separately from `benchmarks/inference_evaluation/runtime_inputs.json`: relationship labels, schema labels, and ER clusters live in separate truth artifacts. Runtime inputs contain candidate outputs and aggregate-safe anonymized tokens only.

The manifest freezes 19 scenario groups, seed `20260911`, and explicit DEVELOPMENT, CALIBRATION, and TEST roles. Groups, not rows or candidates, are the split unit. Reverse or logically related cases must remain in one group. The TEST groups are read only after protocol and policy hashes are written; they are excluded from threshold and calibration selection.

The current test contains low-cardinality and type-trap relationships, an incomplete/missing candidate case, schema no-match and multiple-target cases, plus Unicode and transitive ER cases. Small denominators remain visible in every metric artifact.
