# Ingestion and Staging

Step07 stages source-faithful rows under
`workspace/runs/<execution_context_id>/staging/<table_id>/` as Parquet. Raw
values are preserved; no semantic profiling, cleaning, relationship inference,
entity resolution or canonicalization occurs here.

Extraction is configuration-controlled and chunked. `max_rows` is explicitly a
source-wide cap by default (`max_rows_scope=SOURCE_WIDE`); a bounded source can
fully observe one table, partially observe another, or not observe a later
table at all. `TableSnapshotObservation` records those states so a later
profiler cannot treat an unobserved table as empty. Each completed batch has a
content hash, schema fingerprint, ordered extraction ordinal range, adapter
provenance and `COMPLETE` publication state. Writes use a `.partial` file,
read-back row-count validation, hashing and atomic rename. Failed writes remove
the partial file and cannot produce a complete batch reference.

The extraction manifest and four catalog JSON artifacts are project-owned:
`sources.json`, `tables.json`, `columns.json` and
`declared_constraints.json`. Row accounting distinguishes observed, staged,
quarantined and unresolved records. Malformed CSV rows fail the affected
extraction explicitly; they are never silently dropped or coerced.

File snapshots use a discovery fingerprint and verify the same content
fingerprint immediately before and after extraction. `FILE_IMMUTABLE` means
content-fingerprint-pinned for the observed extraction; it does not claim that
the operating-system file can never change. A mismatch invalidates the
snapshot and publishes no complete snapshot result. The SQLite dlt reference
path is read-only, and the selected tables are loaded through one explicit
transaction connection, so the published snapshot reports
`TRANSACTION_SCOPED`. This is not a cross-provider claim. Non-SQL database
providers are not live-verified in Step07.

Retries use content-derived batch IDs. Existing identical complete artifacts
are reused, while incomplete files are never consumed. Changed content creates
a different batch identity. General ArtifactStore, ControlStore persistence
and global orchestration remain later ownership.
