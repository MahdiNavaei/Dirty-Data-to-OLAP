# Step18 Evaluation Contract

Step18 is an offline evaluation sidecar. It reads aggregate-safe runtime fixtures and joins them to benchmark truth only inside the evaluator. The runtime DAG does not import `dirty_data_to_olap.evaluation` and no evaluation label is accepted by the inference contracts.

The protocol is `step18-protocol-v1`, the dataset is `step18-inference-quality-v1`, and all results are bound to the frozen Step17 relationship and mapping policy hashes. A run must publish a protocol, dataset manifest, split manifest, task artifacts, error artifact, bootstrap artifact, and G5 assessment with byte hashes.

Scores retain their source semantics. Step17 fusion scores and Step15 learned ranking scores are uncalibrated decision/ranking scores, not probabilities or business confidence. Step18 cannot select a production threshold, authorize automation, accept a relationship, or assign canonical identity.
