# G3A — Source Usability

Status: `PASS` as an intermediate Step07 milestone. Formal G3 Source Safety
remains `PENDING`.

Evidence is the executed representative pipeline in
`tests/integration/sources/test_step07_source_pipelines.py`:

```text
SQLite -> registry -> discovery -> SourceCatalog -> dlt extraction
       -> SourceSnapshot -> chunked Parquet batches -> record references
       -> catalog JSON / extraction manifest -> row accounting
```

The same test also exercises CSV, Parquet and optional XLSX paths. SQLite
produced eleven staged rows across multiple chunked batches, declared PK/FK
metadata, readable Parquet output, deterministic complete batch references,
distinct snapshot-bound references for duplicate no-key rows, and a rejected
write attempt. CSV and Parquet representative tests verify raw-value
preservation, malformed-row failure, projection, row groups and restart
repeatability. Partial-file cleanup and all required contract layers pass.

This milestone does not claim provider-wide SQL compatibility, final privacy or
security-gate completion, transactionally atomic cross-table snapshots, or
formal G3.
