# Model Card: Experimental Relationship Ranker

## Intended use

Rank relationship candidates for review in the Dirty Data to OLAP benchmark
workflow. The model is not an acceptance classifier, a canonical identity
system, or a calibrated production probability model.

## Data and features

The model uses project-owned normalized aggregate evidence from dependency
discovery, schema matching, profiling, quality, and declared metadata.
Labels are explicit synthetic benchmark labels. Grouped base scenarios and
reverse pairs are held out together. No raw rows or raw identity values are
persisted.

## Method

scikit-learn 1.7.2 LogisticRegression with a fixed seed, balanced class
weights, and the versioned project feature schema. The JSON artifact stores
only the linear parameters and provenance fingerprints.

## Limitations and risks

The score is uncalibrated. Small synthetic benchmarks do not establish
production performance, patient/entity independence, temporal validity, or
business acceptance criteria. Missing, bounded, incomplete, low-cardinality,
and conflicting evidence can change rankings. Human review remains required.

## Monitoring

Retain grouped Recall@k, MRR, NDCG, candidate coverage, hard-negative
exposure, label-shuffle/source-permutation leakage negatives, rank stability,
calibration sufficiency, and active-learning provenance for every experiment.
