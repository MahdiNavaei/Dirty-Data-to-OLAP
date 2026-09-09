# Profiling Contract

Step08 publishes project-owned `ProfileRequest`, `ProfileObservationScope`,
`ColumnProfile`, `TableProfile`, `ValuePatternSummary`, `ProfileFailure` and
`ProfileResult` models. DataProfiler, pandas, NumPy and PyArrow objects are
adapter-local implementation details and never cross the application/domain
boundary or enter persisted artifacts.

Every result is bound to one source snapshot, table, input batch IDs and batch
content hashes. Only `COMPLETE` staged batches are accepted. A failed table or
column is represented explicitly; a partial result is never upgraded to
complete.

The project owns null semantics. Physical nulls and configured markers are
separate counts. Empty strings, whitespace, `NULL`, `None`, `nan` and other
tokens are not missing unless the request explicitly configures them.

The optional `profiling` extra pins the official `DataProfiler==0.13.4`
package. Expensive correlation and chi-square statistics are disabled by
default and require an explicit column bound when enabled.
