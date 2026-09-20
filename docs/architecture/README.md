# Architecture

The runtime separates source adapters from internal contracts:

```text
source adapters -> discovery/catalog -> profiling/dependencies
  -> schema matching -> evidence fusion -> typed review
  -> canonical identity -> analytical plan -> DuckDB materialization
  -> validation/reconciliation -> bounded API/browser projections
```

External libraries are adapter dependencies, not the product contract. The
application owns authorization, scope, review compatibility, durable run/job
identity, artifact lineage, plan selection, materialization binding, and
validation receipts.

## Runtime boundaries

- Source access is read-only and bounded.
- The API is under `/api/v1`; local auth is an explicit test/integration
  boundary, not production identity management.
- The durable local control store is SQLite; the local worker is not claimed
  to be a production queue or exactly-once distributed executor.
- DuckDB is the reference analytical materialization target.
- Browser projections intentionally exclude raw storage locators, paths,
  generated SQL, credentials, and unrestricted artifact payloads.

Detailed contracts remain in [component model](COMPONENT_MODEL.md), [run and
stage lifecycle](RUN_AND_STAGE_LIFECYCLE.md), [runtime topology](RUNTIME_TOPOLOGY.md),
and the [software architecture contract](SOFTWARE_ARCHITECTURE_CONTRACT.md).
