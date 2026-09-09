# Dirty Data to OLAP V1 Scope Boundary

## MUST V1

- Evidence-first understanding of static/batch tabular sources.
- Required release source intent: PostgreSQL, MySQL/MariaDB, Microsoft SQL Server, CSV and Parquet.
- Required local reference/demo source: SQLite.
- Catalog discovery, profiling, candidate keys, hidden relationship evidence and quality diagnosis.
- Cross-source schema-match candidates with evidence and review state.
- Optional entity resolution for selected entity families with source-record preservation.
- Canonical model proposal with lineage and conflicts.
- Fact/dimension proposal with explicit grain, keys and measure semantics.
- Executable transformation plan and at least one materialized DuckDB analytical target.
- Post-materialization validation, reconciliation and record-loss accounting.
- Human review for ambiguous relationships, mappings, merges, repairs, canonical conflicts and analytical semantics.

## OPTIONAL / CONDITIONAL V1

- Excel/XLSX when an existing connector is inexpensive to support and compatibility evidence exists.
- Additional target engines such as ClickHouse are not required for G0 or the reference V1 target.
- Advanced outlier/drift detection, unit/currency inference and richer SCD behavior only when separately accepted and tested.

## DEFERRED

- Oracle source support.
- Continuous CDC and production streaming.
- Full incremental SCD2 maintenance.
- Enterprise scheduling/orchestration and large-scale distributed execution without measured need.
- Production-grade multi-tenant service operations.

## EXPLICITLY OUT OF SCOPE

- Destructive writes to operational sources.
- Kafka/event-stream ingestion as a V1 first-class input.
- PDFs, images, audio, video or free-form documents.
- Graph databases as primary sources.
- Arbitrary nested JSON as a first-class source type.
- Airflow, Dagster or dbt replacement behavior.
- Generic data lakehouse or enterprise warehouse orchestration.
- BI dashboard product or causal business-analysis engine.
- Autonomous authority for legal/business truth.
- Silent deduplication, silent correction, silent NULL coercion or unexplained data loss.

## Support-claim rule

“Supported” means the source path has implementation, contract tests, compatibility evidence and documented limitations. This product contract freezes intended scope; it does not claim that any source adapter exists before its owning specialist passes the relevant gate.
