# Prompt03 Generic Product Acceptance

## Result

Prompt03 local acceptance is PASS for the new telemetry/device product path on branch `codex/prompt03-generic-product`.

This evidence is local-first. No GitHub Actions workflow was started or modified, and Prompt04/Step42 was not started.

The accepted Prompt02 base was `7786ea301e3482b374e57447974b73d9a0176f8c`. The final post-commit rerun must replace the provisional run reference below with the run whose durable G6 evidence records the final candidate commit.

## Generic policy and durable binding

- Registered policies are bounded to `order` and `telemetry` and resolve by exact product ID, version, and optional content fingerprint.
- Unknown products, incompatible versions, and changed policy bytes are rejected before execution planning.
- The selected policy binding is persisted in run metadata, planning intent, execution plan, stage metadata, and artifact provenance.
- The telemetry policy fingerprint for this candidate is `baaecd58325f42c41187ee6ddadce83518175cf9e67925b06ef36dc347b3f42b`.
- Compiler, SQL, target-config, materialization, semantic, and validation artifact manifests carry the telemetry policy provenance and fingerprint.
- Telemetry uses the same backend, durable worker, stage registry, review boundary, canonical finalization, analytical planner, compiler, DuckDB materializer, semantic layer, and G6 validator as the accepted order path. It is not a parallel telemetry runtime.

## Real local pipeline evidence

The local test `tests/product_acceptance/test_prompt03_generic_product.py` executed the three heterogeneous CSV sources through the public backend boundary with the locally available Desbordante Docker provider image `dirty-data-to-olap-desbordante-step37:local`.

Provisional successful run before the final commit: `run_934c82dded706a04efa21e690dccba5c`.

The durable stage sequence completed through source discovery, snapshots, profiling, dependency discovery, schema matching, quality, evidence fusion, persisted evidence review, canonical hypothesis and identity review, canonical finalization, analytical planning and review, compilation and materialization review, materialization, semantic modeling, and validation reconciliation.

All stage jobs in that run succeeded. The run metadata recorded:

- `product_policy_id=telemetry`
- `product_policy_version=telemetry-product-v1`
- `product_policy_fingerprint=baaecd58325f42c41187ee6ddadce83518175cf9e67925b06ef36dc347b3f42b`

## Data correctness

The independent oracle is `tests/product_acceptance/oracle/telemetry_v1_truth.yml`. The runtime does not import or read that oracle; the test loads it only after execution and materialization.

The accepted telemetry slice contains:

- 9 source records across devices, locations, and readings;
- 3 device rows, including two `Pump A` records with different device IDs that remain separate;
- 2 location rows;
- 4 reading events at `reading_id` grain;
- 3 materialized fact rows;
- `R-004` retained in source/canonical accounting but quarantined from the fact table because `D-404` is unresolved;
- temperature in degrees Celsius with `MAX` within the reviewed observation slice and no cross-time total;
- global maximum `11.0`, with the date-scoped expectations retained in the independent oracle.

The durable G6 gate for the successful run was `PASS` and eligible. The test also checked source truth record count, fact count, quarantined reading behavior, duplicate-name device separation, run policy metadata, execution-plan binding, and compiler/materializer provenance.

## Negative controls

`tests/product_acceptance/test_prompt03_negative_controls.py` executes and passes NC-G01 through NC-G10:

| Control | Injected invalid condition | Expected result |
| --- | --- | --- |
| NC-G01 | Unknown product policy | Registry rejection |
| NC-G02 | Unregistered policy version | Version rejection |
| NC-G03 | Tampered policy fingerprint | Fingerprint rejection |
| NC-G04 | Telemetry binding paired with order planning intent | Durable plan rejection |
| NC-G05 | Telemetry source with no registered role columns | Role rejection |
| NC-G06 | Fact grain reference reused as a measure reference | Analytical contract rejection |
| NC-G07 | Numeric measure without a domain assertion | Measure contract rejection |
| NC-G08 | Accepted analytical planning request without the review boundary | Planning contract rejection |
| NC-G09 | Domain evidence bound to no selected fusion subject | Fusion remains incomplete and reports failures |
| NC-G10 | Runtime/oracle and telemetry direct-materialization isolation check | No oracle path or direct DuckDB connection in policy adapter |

The end-to-end fixture also provides actual heterogeneous-data negative controls for an unresolved device relationship and same-name/different-ID identity. Existing Prompt02 NC01-NC14 were not weakened.

## Local validation

- `python -m compileall -q src/dirty_data_to_olap config tests/product_acceptance`: PASS
- unit, contract, security, and Prompt03 negative-control tests: PASS (`379 passed`, 2 warnings)
- Prompt03 real local telemetry acceptance: PASS (`1 passed`, 12 warnings)
- Prompt02 live-provider acceptance harness: 3 auxiliary tests passed and the live multi-source test was skipped because the required PostgreSQL environment variable was not configured locally. This is reported as an environment limitation, not as a Prompt02 live PASS.
- `git diff --check`: required before commit
- No workflow or CI run was started.

## Architectural review and limitations

The generic adapter now owns policy-specific role detection, logical fields, relationship assertions, identity memberships, analytical dataset/planning semantics, truth/accounting expectations, and validation policy. The shared runtime owns durable execution, evidence fusion, persisted review, canonicalization, planner/compiler/materializer, semantic modeling, and G6.

Order-specific branches remain in the shared multi-source handler for backward-compatible Prompt02 execution. They are selected only for the registered `order` policy; telemetry reaches the same handler and stage IDs through the injected policy interface. This is an intentional bounded compatibility seam, not a claim that every historical order field has disappeared from the shared implementation.

The materializer is still the accepted shared DuckDB adapter. Telemetry does not open DuckDB or bypass the planner/compiler; its policy creates typed analytical inputs and specifications consumed by the existing planner, compiler, and materializer.

The evidence run is local and uses a disposable local Docker provider image. It is not external-CI confirmation and does not claim production deployment or Prompt04 completion.

## Protection and scope

Only Prompt03 source, configuration, test, oracle, and evidence-report files are in scope. The recovery branch, draft PR, RAR knowledge base, and `tests/quality_unit_artifacts/` are preserved and are not staged.
