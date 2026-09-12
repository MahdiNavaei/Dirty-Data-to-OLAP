# Step21 Semantic Query Trust Boundary Repair

## Scope

This was a surgical post-Step21 repair. Step22 was not implemented or started. The starting branch was `main` at `3aa0de9c54ebab882ba6f45f95bf350751162979`, matching `origin/main`; the existing Step21 semantic-layer content was `1d031b6815579b81c9a08b91d896dab12db4440c`.

The untracked `tests/quality_unit_artifacts/` directory was preserved and not edited.

## Finding

The Step21 executor accepted the caller-provided `sql_template` after checking that it looked like one `SELECT`, was semicolon-free, and did not contain a blacklist of dangerous tokens. That boundary was insufficient: a tampered template could name an undeclared physical table, add an undeclared join or arbitrary subquery, or invoke DuckDB file/table functions whose names were not covered by the blacklist. DuckDB `read_only=True` protects the target from writes, but it does not establish that the requested read is authorized by the semantic model.

## Repair

- Added a project-owned deterministic renderer that reconstructs SQL only from the bound `SemanticModel` and validated `SemanticQueryPlan` structure.
- Re-rendering validates exact model identity/content hash, one fact and grain, declared metrics/measures/dimensions/attributes, reviewed aggregation operations, exact physical bindings, reviewed relationship scope and join path, compatible time roles, exposed attributes, parameter shape, selected sort fields, and bounded limit.
- The executor now rejects any template that differs from the trusted structural rendering before opening DuckDB. Model/plan validation is repeated at this boundary, parameter count is checked, and execution remains against the path-contained SHA-bound `.duckdb` target with `read_only=True`.
- The plan stores filter shape metadata only; filter values remain bound parameters and are not rendered into SQL or persisted in the plan.
- V1 derived metrics are fail-closed. An `AVAILABLE` derived metric is rejected by the contract and service with `DERIVED_METRIC_NOT_EXECUTABLE_V1`; derived ratios cannot reach the executable compiler and cannot produce a `KeyError`/`IndexError` path.
- Extended the Step21 validator with legitimate retail and generic execution plus negative controls for tampered SQL, unknown/declaration-out-of-path tables, arbitrary subqueries, DuckDB file/table functions, undeclared join paths, and executable derived ratios.
- Corrected the Step21 specialist log filename to `17_ANALYTICAL_SEMANTIC_LAYER_ENGINEER.md`.

## Evidence

Security regression tests cover `read_csv`, `read_parquet`, `parquet_scan`, `csv_scan`, `glob`, `sqlite_scan`, `read_text`, and `read_blob`, as well as unknown table, declared-but-not-selected join, arbitrary subquery, forged join path, and read-only target controls. Legitimate retail and generic semantic queries continue to execute through the same bounded adapter.

Verification executed after the repair:

- focused Step21 suites: `20 passed`
- unit: `154 passed`
- contract: `25 passed`
- integration: `42 passed, 2 skipped` (optional Valentine/Splink runtimes unavailable)
- architecture: `14 passed`
- security: `33 passed`
- full regression: `268 passed, 2 skipped`
- Step21 behavioral validator: `38 checks PASS`
- all repository validators: `21/21 PASS`
- `python -m compileall -q src tools tests`: PASS
- `git diff --check`: PASS

The full run emitted existing dependency deprecation/compatibility warnings and the two documented optional-runtime skips; no test failed.

## Resulting state

The repair content commit is `e8933a5b07199acbd5ef6cf26c037375210f39b6` (`fix: close semantic query execution trust boundary`). G5 remains `PASS`; G6 remains `PENDING`; the repository handoff remains `last_completed_step=21`, `current_step=22`, and `step22_started=false`.

## Limitations

This closes the Step21 semantic query trust boundary for the project-owned bounded DuckDB path. It does not claim G6 data correctness, production deployment, source-to-canonical-to-OLAP reconciliation, or unrestricted SQL support. Optional Valentine/Splink integration tests remain skips in this environment.
