# Baseline and Ranking Policy

The baseline combines source-backed structural evidence: inclusion coverage,
target uniqueness, type compatibility, matcher support, unmatched ratio,
low-cardinality risk, and bounded-scope risk. It is deterministic and is
retained whenever the optional model is unavailable or insufficient.

The experimental model is a scikit-learn LogisticRegression estimator. Its
decision-function equivalent is persisted as an explicit linear score with
coefficients and intercept. Ranking metrics include Recall@1/3, MRR, NDCG,
candidate coverage, and hard-negative exposure. Metrics are reported by
grouped split; they are not acceptance thresholds.
