# Critical Post-Step20 Analytical Integrity and Generic V1 OLAP Repair

## GOAL RESULT

PASS for the bounded Step20 repair. The analytical review boundary now binds
the exact semantic contents of every `FactSpec`, `DimensionSpec`, `GrainSpec`
and `MeasureSpec`, and the V1 planner/compiler/materializer execute a generic
typed analytical input path rather than a retail-only implementation. Step21
was not started. `G5_INFERENCE_VALIDITY` remains `PASS`; `G6_DATA_CORRECTNESS`
remains `PENDING`.

## FINDINGS REPAIRED

The prior Step20 implementation had two integrity gaps:

- `REVIEW_ANALYTICAL_PLAN` bound child specification IDs without binding their
  exact semantic contents. A same-ID mutation could therefore evade the review
  compatibility boundary.
- The runtime path was coupled to the reference retail fixture, fixed retail
  table names and one fixed target path. The declarative unknown-member and
  unresolved-FK policies were not sufficiently demonstrated as generic runtime
  behavior.

The original Step20 report remains historical. This report records the repair
closure and supersedes only those findings, not the valid Step20 scope limits.

## REPAIRS

- Added semantic content hashes for all four child-spec families and exact
  per-family hash maps plus a package hash to `AnalyticalPlan`.
- Extended analytical review applicability, subject identity and schema
  fingerprints with the exact spec package. Compilation recomputes every child
  hash and fails closed on same-ID mutation, changed request semantics,
  changed grain evidence, changed policy or changed binding.
- Added typed `AnalyticalInputDataset`, `AnalyticalInputTable`,
  `AnalyticalInputRow`, column bindings and planning-request contracts. Rows
  carry typed values, row references, canonical references and source lineage;
  arbitrary dictionary rows are not the application contract.
- Generalized planning, grain validation, warehouse-key generation, DDL,
  date-range handling, measures, foreign keys, lineage, SQL generation and
  atomic materialization to the reviewed input/specs. No Customer/Product/
  Branch/Order/OrderLine/Payment assumption remains in the production
  planner/compiler/materializer/adapter path.
- Kept the retail fixture conversion and retail reference plan in `tools/` as
  compatibility/reference code. The application layer does not import fixture
  row classes.
- Made `QUARANTINE_FACT`, `NULLABLE_FK` and `EXPLICIT_UNKNOWN_MEMBER`
  behavioral policies. Quarantined rows are typed artifact records, nullable
  foreign keys are permitted only under an explicit nullable policy, and the
  explicit unknown-member path materializes a reserved dimension member and
  records the fact lineage marker.
- Added a second non-retail Device/Location/Reading domain through the same
  planner, review, compiler and materializer path. Generic generated SQL is
  parameterized and contains no synthetic device literals.
- Generalized the controlled target path. An alternate `.duckdb` target inside
  the approved root succeeds; traversal outside that root fails closed.

## REVIEW AND RUNTIME INVARIANTS

The compiler accepts only an accepted, compatible analytical review whose
context matches the canonical model, source dataset, policy, exact child-spec
maps, package hash and target-independent plan semantics. Materialization
accepts only the compatible materialization review and the exact compiled
target binding. Generated SQL artifacts for generic input contain parameter
placeholders; the adapter performs controlled in-memory binding for execution
and does not persist raw generic row values in the SQL artifact. The synthetic
retail compatibility run may use its explicitly marked literal fixture mode.

The V1 runtime retains deterministic positive namespaced warehouse keys,
composite grain validation, bounded Gregorian date dimensions, declared
attribute derivations, Type 1 snapshot behavior, explicit measure aggregation
classes, source/canonical lineage and atomic same-directory publication.

## EXECUTED EVIDENCE

Reference retail run:

- plan `aplan_cfdce761b46fec7b697808a4a0050a67`, plan hash
  `442a0f4075804941b6f34f11500e872887bbeceb77cc13829e9e47eab0882671`;
- compiled plan `cplan_0ecede81aae094681afe5b55fb1f1c93`, SQL hash
  `a79a4f66f6c97084acfe65cd123ae11c6f2c2ba1cf717fe5800d6eb8500795fe`;
- materialization `mat_cfc7ac8cea8952cd24927c2b2eacce34`;
- five generic-by-contract warehouse tables, three fact rows, zero duplicate
  grain keys, zero unresolved foreign keys and `SUM(quantity)=6`.

Non-retail Device/Location/Reading run:

- plan `aplan_78c722224a989b62ec8d571b736e0e27`, plan hash
  `6b7418c4ad5630987f54d5417be501a209808d5f8f7920f312cca21c79d640c`;
- compiled plan `cplan_44bc04c075f4f6b9e61479c45a77375e`, SQL hash
  `2030732db084eeefc2eb8d0876bf14081f5a1b8375d56a7f71cae989d2fee0d6`;
- materialization `mat_682dfbe19fee009ce021290861b081cf`;
- three rows materialized in the reviewed target, zero unresolved foreign keys,
  and the generic load SQL was parameterized (`?`) with no `Pump A` or device
  value literals.

Focused mutation and policy tests passed `26`. The complete matrix passed:
unit `148`, contract `22`, integration `39` with `2` optional skips,
architecture `12`, security `27`, and full regression `248` with `2` optional
skips. The optional skips are the unavailable official Splink and Valentine
runtime tests. `compileall`, `git diff --check`, the Step20 validator
(`49` checks), the engineering-plan post-gate validator (`73` checks), and all
`20` repository `validate_*.py` validators passed. Verified content commit:
`b8cebf561addbc4a9e21c8ce1da9792293cec64c`.

## LIMITATIONS AND NON-CLAIMS

Both executed domains are synthetic/domain-reviewed reference inputs, not
production source data. This repair does not claim G6 source-to-canonical-
to-OLAP reconciliation, production incremental SCD2, semantic/KPI layer
support, Payment fact acceptance, physical benchmark-column compatibility,
public deployment or Step22 Data QA completion. No source data was written.
The generic parameter binding is a controlled local execution mechanism, not a
production database ingestion adapter. Step21 remains not implemented.

## HANDOFF STATE

The repository remains at `current_step=21` with
`current_role=analytical_semantic_layer_engineer`, `last_completed_step=20`,
`G5=PASS` and `G6=PENDING`. The next specialist may consume the exact reviewed
package and compiled artifact by hash, but may not reinterpret the reviewed
grain, measures, policies or lineage.
