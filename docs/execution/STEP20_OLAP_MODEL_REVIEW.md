# Step20 Data Warehouse / OLAP Engineer Review

## GOAL RESULT

PASS for the bounded Step20 scope. The repository now has project-owned
analytical contracts, an explicit fact/dimension/grain/measure plan, exact
stage-scoped review guards, deterministic DuckDB SQL, controlled atomic
materialization, and an independently validated synthetic/domain-reviewed
reference package. Step21 was not implemented and G6 remains PENDING.

## REPOSITORY BASELINE

- Starting branch: `main`
- Starting HEAD and `origin/main`:
  `a182d59a030130db5cbb2a21f3dddcb5be9e4889`
- Protected untracked path `tests/quality_unit_artifacts/` was preserved and was
  not staged or modified.
- Step19 repair-2 content remains the canonical starting point.

## AUTHORITATIVE INPUTS

The Step20 playbook, shared execution protocol, domain-reviewed benchmark
labels, canonical/OLAP modeling report, evidence/confidence report,
benchmark/validation plan, architecture specifications, engineering
specifications, and existing canonical implementation were inspected before
implementation. The domain labels remain the authority for Customer, Product,
Branch, Order, OrderLine and Payment concepts and the reviewed relationships.
Physical benchmark column names remain unresolved; the reference run uses an
explicit typed fixture-local binding.

## IMPLEMENTED PROJECT-OWNED CONTRACTS

`domain.contracts.analytical` contains:

- `AnalyticalPlan`, `DimensionSpec`, `FactSpec`, `GrainSpec` and `MeasureSpec`;
- explicit warehouse-key, SCD and unknown-member policies;
- typed `AnalyticalInputBinding` and `AnalyticalInputFixture` rows;
- `CompiledPlan` and `GeneratedSQL` with plan, compiler, dialect, operation,
  target and SQL bindings;
- `MaterializationArtifact` with status/usability, target path, table/row
  counts, hashes, provenance and failure semantics.

The contracts are Pydantic project-owned models. No DuckDB connection, native
provider object, Step18 evaluation object or semantic-layer type crosses the
domain contract boundary.

## ANALYTICAL MODEL DECISIONS

The reference plan materializes:

- `dim_customer`, `dim_product`, `dim_branch` and bounded Gregorian `dim_date`;
- `fact_order_line` at the mandatory grain
  `one product line in one order event, identified by
  (order_event_id, line_sequence)`;
- degenerate `order_event_id` on the fact;
- conformed Customer, Product and Branch dimensions;
- V1 `TYPE1_SNAPSHOT` behavior, with no fabricated SCD2 history;
- `QUARANTINE_FACT` for unresolved dimension references;
- additive `quantity` only for `SUM` semantics;
- non-additive `unit_price` and `discount_rate` preserved at line grain;
- no revenue, gross amount, currency or tax inference.

Payment remains an explicit deferred candidate rather than an automatically
materialized fact. Canonical/source record references remain in the plan,
fixture and target lineage columns. Canonical IDs, ER IDs and source keys are
not warehouse surrogate keys.

## KEY AND DETERMINISM POLICY

Warehouse keys use the project-owned
`SHA256_NAMESPACE_INT64_V1` strategy, are positive BIGINT values, are
namespace-separated from canonical IDs, reject collisions, and do not depend
on Python hash randomization or UUID4. SQL row order and input fixture order
are normalized. The reference flow was run twice: analytical-plan,
compiled-plan, generated-SQL and DuckDB file hashes remained identical.

## REVIEW AND EXECUTION FLOW

The implemented flow is:

`ANALYTICAL_PLANNING`
→ exact `REVIEW_ANALYTICAL_PLAN`
→ `COMPILATION`
→ exact `REVIEW_MATERIALIZATION_PLAN`
→ controlled `MATERIALIZATION`.

The existing `ReviewDecision` and `ReviewCompatibilityContext` are reused.
Analytical review binds the plan ID/content hash/schema, canonical-model ID,
canonical-model content hash/fingerprint, policy, source schema fingerprints,
domain scope, semantic subject and applicability. Materialization review binds
the compiled-plan hash, generated-SQL hash, target configuration fingerprint,
compiler, dialect, canonical model and domain scope. Changed grain, measure,
plan, compiled SQL, target or binding data cannot reuse the old decision.

The compiler consumes only a compatible accepted analytical review. The
materializer consumes only a compatible accepted materialization review. A
failed build leaves no newly published target and returns a non-usable failed
artifact; publication uses same-directory atomic replacement.

## REFERENCE ARTIFACTS

The generated package is under the ignored, controlled path
`workspace/runs/step20-reference-run/olap/` and includes the analytical plan,
fact/dimension/grain/measure specs, both review decisions, input binding,
compiled plan, generated SQL and manifest, validation manifest, canonical and
lineage references, target inspection and `target.duckdb`.

Verified identifiers and hashes are recorded in `run_manifest.json`:

- canonical model: `cmodel_f971451cfa29865b38cac0b7d5af4ac5`;
- analytical plan: `aplan_8b084fc5014b3bb782f18d5f1f39811d`;
- analytical plan content hash:
  `3c40d9b54fcad6970e936c1f25e64af1c10d67a505e2895a1f50a20ab614d69c`;
- compiled plan: `cplan_7e2ce865b7c1c05aa60db5acf927bc29`;
- generated SQL hash:
  `5aa1457c758ecd814f8bfa06f9563d06ae6946803a6ea51bf30a67fa88342626`;
- materialization artifact:
  `mat_a7ead944ef6577c9a5fe033b93301dff`;
- target: `workspace/runs/step20-reference-run/olap/target.duckdb`.

Target inspection found five expected tables, two rows each in Customer,
Product, Branch and Date, three order-line rows, zero duplicate fact-grain
keys, zero unresolved fact FKs and `SUM(quantity) = 6`.

## NEGATIVE CONTROLS

Focused tests and the Step20 validator cover duplicate composite grain,
missing dimension references, unsafe identifiers, target traversal, stale
input binding, rejected analytical review, changed review context, non-additive
measure protection, SCD policy completeness, warehouse-key separation,
controlled target publication and no Step21/evaluation runtime boundary.

## VALIDATION STATUS

The focused Step20 suite passed `14` tests and the independent Step20 validator
passed `24` checks. Final matrix results were unit `148 passed`, contract `12
passed`, integration `38 passed, 2 skipped`, architecture `11 passed`,
security `27 passed`, and full regression `236 passed, 2 skipped`. All existing
validators passed, including the Step20 validator; compileall and diff-check
also passed. Verified content commit:
`f7c04b0366ecaeb3433a28efc48ee5aaaede3be6`.

## LIMITATIONS AND NON-CLAIMS

The reference fixture is synthetic and domain-reviewed, not production data.
This Step20 result does not claim G6, source-to-OLAP reconciliation, physical
benchmark-column compatibility, production incremental SCD2, Payment fact
acceptance, semantic/KPI layer support, revenue recognition, public deployment
or Step22 Data QA completion. No source data was written. No Step18 evaluation
truth was used as runtime input. Step21 and later gates remain pending.

## HANDOFF TO STEP21

Step21 may consume the reviewed `AnalyticalPlan`, explicit specs, review
decisions, compiled SQL and materialization artifact by their recorded hashes.
It must preserve the fact grain and aggregation classes, keep
`unit_price`/`discount_rate` non-additive, retain canonical/source lineage,
and not promote the synthetic target to G6 acceptance. The next state is
`current_step=21`, `current_role=analytical_semantic_layer_engineer`, with G5
PASS and G6 PENDING.
