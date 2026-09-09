# DataProfiler Integration

The official Capital One DataProfiler package is used through
`DataProfilerAdapter`. The reviewed source revision is
`4b5ab37bb28a2104d0898d21a8c9681b5c5deed1` and is Apache License 2.0. The
runtime dependency is the official PyPI package `DataProfiler==0.13.4`; no
source code was copied.

The adapter disables DataProfiler data labeling, correlation, chi-square,
row-statistics, null-replication metrics and multiprocessing for the bounded
baseline. Project accumulators own null accounting, sampling, privacy-safe
patterns and completeness. DataProfiler reports are used only as local engine
execution evidence and are not serialized.

The adapter feeds bounded pandas frames one batch at a time for full profiles
and one bounded deterministic sample frame for sampled profiles. Batch paths,
publication state, row counts and SHA-256 content hashes are checked before
iteration. A mismatch becomes `BATCH_INTEGRITY_FAILED`.

The research clone is temporary and must be absent from the final worktree.
