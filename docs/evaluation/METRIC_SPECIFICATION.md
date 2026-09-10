# Metric Specification

Classification uses `TP`, `FP`, `FN`, and `TN` over the declared evaluated universe. Precision is `TP/(TP+FP)`, recall is `TP/(TP+FN)`, and F1 is `2TP/(2TP+FP+FN)`. Average precision sorts by score with candidate-ID tie breaking. Undefined denominators are emitted as `value: null` with an explicit reason.

Ranking reports Recall@1/3/5, MRR, NDCG@1/3/5, eligible-query count, single-candidate count, no-positive count, mean candidate count, and exposed hard-negative count. Queries with no positive truth are not silently treated as zero-quality eligible queries.

ER reports pairwise precision/recall/F1, false merges, false-merge rate, contaminated clusters, largest contaminated cluster, split truth clusters, false-split rate, cluster purity, completeness, and evaluated record/pair denominators. Bootstrap resamples scenario groups with a fixed seed; fewer than two groups yields an explicit insufficient-interval status.
