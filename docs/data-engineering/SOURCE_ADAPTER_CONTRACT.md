# Source Adapter Contract

Step07 owns the read-only source boundary. `SourceAdapter` exposes only
`discover_source` and `create_bounded_snapshot`; both accept and return
project-owned Pydantic contracts.

The lifecycle is:

```text
SourceSelection -> registry -> SourceAdapter discovery -> SourceCatalog
SourceCatalog -> bounded/chunked extraction -> SourceSnapshot
SourceSnapshot -> atomic Parquet batches -> BatchReference + SourceRecordReference
```

Discovery does not require a snapshot. Source descriptors retain only safe
connection-profile references or normalized file locators. Runtime SQL
credentials are injected into the concrete SQL adapter and never enter source
contracts, paths, logs, manifests or normalized errors.

Physical IDs use stable SHA-256-derived serialization. Table and column IDs are
source-scoped. Primary-key record references retain ordered key components;
records without a declared stable key use snapshot-bound extraction ordinals.
A declared foreign key remains declared metadata and is not a business
relationship decision.

Adapters use dlt for SQL extraction, Python's streaming CSV reader for CSV,
PyArrow for Parquet, and optional read-only openpyxl for XLSX. Vendor objects
do not cross the adapter or application boundary.
