# Ingestion and Staging

Step07 stages source-faithful rows under
`workspace/runs/<execution_context_id>/staging/<table_id>/` as Parquet. Raw
values are preserved; no semantic profiling, cleaning, relationship inference,
entity resolution or canonicalization occurs here.

Extraction is configuration-controlled and chunked. Each completed batch has a
content hash, schema fingerprint, ordered extraction ordinal range, adapter
provenance and `COMPLETE` publication state. Writes use a `.partial` file,
read-back row-count validation, hashing and atomic rename. Failed writes remove
the partial file and cannot produce a complete batch reference.

The extraction manifest and four catalog JSON artifacts are project-owned:
`sources.json`, `tables.json`, `columns.json` and
`declared_constraints.json`. Row accounting distinguishes observed, staged,
quarantined and unresolved records. Malformed CSV rows fail the affected
extraction explicitly; they are never silently dropped or coerced.

File snapshots use content fingerprints and `FILE_IMMUTABLE` consistency. The
SQLite dlt reference path is read-only and uses one SQLite engine/transaction
for the dlt resource extraction, so it reports tested `TRANSACTION_SCOPED`
consistency. This is not a cross-provider claim. Non-SQL database providers
are not live-verified in Step07.

Retries use content-derived batch IDs. Existing identical complete artifacts
are reused, while incomplete files are never consumed. Changed content creates
a different batch identity. General ArtifactStore, ControlStore persistence
and global orchestration remain later ownership.
