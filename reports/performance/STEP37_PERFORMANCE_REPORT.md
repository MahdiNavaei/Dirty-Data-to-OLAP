# Step37 Performance Engineer Report

## Result

- Overall result: `PASS`
- Assessed content commit: `72a363a0a4fb30a165626eebbec848b7686de8ad`
- Scope: single-run/stage performance only; G12 capacity remains pending.

## Environment

```json
{
  "architecture": "AMD64",
  "cpu_logical_count": 6,
  "duckdb_version": "1.5.5",
  "memory_measurement": "python_tracemalloc_peak plus process RSS observation",
  "os": "Windows-10-10.0.26200-SP0",
  "python": "3.11.7",
  "working_directory": "repository-root"
}
```

## Existing benchmark estate reused

The report links existing relationship, schema, entity, evidence-fusion, validation, applied-ML and semantic-AI fixtures. No second truth model was created.

## Dataset and truth links

```json
[
  {
    "path": "benchmarks/inference_evaluation/relationship_truth.json",
    "schema_version": "1",
    "sha256": "cf29fa34e20b852877ccdb59c37c89a0adafb017479e52d7d72963ec8d459830",
    "shape": {
      "labels": 10
    },
    "truth_fixture_id": "step18-relationship-truth-v1"
  },
  {
    "path": "benchmarks/inference_evaluation/schema_truth.json",
    "schema_version": "1",
    "sha256": "aef9b170e1a814537b578c02aa909eff1a07b975c0439e291c1e62beabf18d8e",
    "shape": {
      "labels": 5
    },
    "truth_fixture_id": "step18-schema-truth-v1"
  },
  {
    "path": "benchmarks/inference_evaluation/entity_truth.json",
    "schema_version": "1",
    "sha256": "ef22b6a16941c8ec3c3210c07bd2c14e655d89c9d4f27f5b47bedc3410b979f6",
    "shape": {
      "evaluated_record_refs": 11,
      "negative_pairs": 2,
      "positive_pairs": 5,
      "truth_clusters": 3
    },
    "truth_fixture_id": "step18-entity-truth-v1"
  },
  {
    "path": "benchmarks/inference_evaluation/runtime_inputs.json",
    "schema_version": "1",
    "sha256": "97723566bd323345382fd87a6b83e01f59e7aa5c6e64c38dea6767dd82a234be",
    "shape": {
      "entity_records": 11,
      "relationship_queries": 10,
      "schema_queries": 5
    },
    "truth_fixture_id": "step18-runtime-inputs-v1"
  },
  {
    "path": "benchmarks/inference_evaluation/scenario_groups.json",
    "schema_version": "3",
    "sha256": "9141b358391cf9b7d9eae0dc9aef29c1eb41ce7a6f48f854becdd76e05e7e03c",
    "shape": {
      "groups": 35
    },
    "truth_fixture_id": "benchmarks/inference_evaluation/scenario_groups.json"
  },
  {
    "path": "benchmarks/inference_evaluation/scenario_groups_v4.json",
    "schema_version": "4",
    "sha256": "0d8e0072856912275cdf3de8daba3e559e7543e22ffa4898f8b27d000dfbeebc",
    "shape": {
      "groups": 35
    },
    "truth_fixture_id": "benchmarks/inference_evaluation/scenario_groups_v4.json"
  },
  {
    "path": "benchmarks/evidence_fusion/expected_control.json",
    "schema_version": "unspecified",
    "sha256": "5dd01415b565ee9b2b603fdc9fcd8753809b84f4e26bbd65701cdf92e130e82d",
    "shape": {},
    "truth_fixture_id": "step17-evidence-fusion-controls-v1"
  },
  {
    "path": "benchmarks/schema_matching/step13_labeled_fixture.json",
    "schema_version": "unspecified",
    "sha256": "f599b5d374c7fe290a870fd50a3e8d9c970856cced74905de860e68549e3e9a3",
    "shape": {
      "hard_negatives": 4,
      "positives": 2
    },
    "truth_fixture_id": "step13-cross-source-hard-negatives-v1"
  },
  {
    "path": "benchmarks/entity_resolution/step14_labeled_fixture.json",
    "schema_version": "unspecified",
    "sha256": "c32932de0730f64ccdc6f84050f0dde7e9cb72132aed1ff4b6e551de7425cec3",
    "shape": {
      "cases": 11,
      "ground_truth_entities": 8,
      "limitations": 3,
      "negative_pairs": 5,
      "positive_pairs": 12,
      "records": 24,
      "review_pairs": 2
    },
    "truth_fixture_id": "step14-entity-resolution-cases-v2"
  },
  {
    "path": "benchmarks/validation/step22_retail_source_truth.json",
    "schema_version": "unspecified",
    "sha256": "de1a7626d7173a27624bbb9f66e58f15f2e5f6dc8b4246598124567d3eaaecae",
    "shape": {
      "accounting_expectations": 23,
      "aggregate_expectations": 3,
      "duplicate_groups": 1,
      "entities": 11,
      "facts": 3,
      "provenance_refs": 4,
      "records": 12,
      "relationships": 7
    },
    "truth_fixture_id": "step22-retail-source-truth-v2"
  },
  {
    "path": "benchmarks/validation/step22_generic_source_truth.json",
    "schema_version": "unspecified",
    "sha256": "6cbbbd8717b5dab573a375bbf3c030646613c66e697d0d358532dec141291476",
    "shape": {
      "accounting_expectations": 14,
      "aggregate_expectations": 2,
      "duplicate_groups": 0,
      "entities": 7,
      "facts": 3,
      "provenance_refs": 4,
      "records": 7,
      "relationships": 6
    },
    "truth_fixture_id": "step22-generic-source-truth-v2"
  },
  {
    "path": "benchmarks/applied_ml/step15_relationship_ranker_fixture.json",
    "schema_version": "unspecified",
    "sha256": "d60e7e1440d483c90a3c1febd62ed791037bf167f2a6b5c7013e7765d9d04a20",
    "shape": {
      "limitations": 3,
      "rows": 6
    },
    "truth_fixture_id": "step15-relationship-ranker-v1"
  },
  {
    "path": "benchmarks/semantic_ai/step16_semantic_safety_fixture.json",
    "schema_version": "unspecified",
    "sha256": "b8ce35cd821a284b9cefc040866863a86842f0de799d187bd1f76cf83de1f021",
    "shape": {
      "cases": 11
    },
    "truth_fixture_id": "step16-semantic-safety-v2"
  }
]
```

## Baseline measurements

Every measured record includes wall time, CPU time where reliable, Python allocation peak, cold/warm state and applicable I/O metadata.

```json
[
  {
    "benchmark_id": "PERF-MAT-001",
    "cold_warm": "COLD",
    "cpu_seconds": 0.21875,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "dimension_rows": {
        "dim_branch": 2,
        "dim_customer": 2,
        "dim_date": 2,
        "dim_product": 2
      },
      "fact_rows": 3,
      "materialization_mode": "PROJECT_DUCKDB_MATERIALIZER",
      "run": "first materialization",
      "semantic_artifact_content_hash": "85b9deed5bcee2d17f0a6748619f09fcd4ef7eba5c979980cd8b2f16745602d7",
      "table_count": 5,
      "target_sha256": "88ba6c6b4f223dfee9d49d8f5524dd50921a6e8ba85ecfcc4040aa5865bea794"
    },
    "family": "duckdb_materialization",
    "io": {
      "input_rows": 11,
      "output_db_bytes": 4993024,
      "output_files": 1,
      "status": "MEASURED",
      "temporary_bytes_observed": null
    },
    "peak_memory": {
      "bytes": 5456823,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [
      {
        "calls": 7,
        "cumulative_seconds": 0.0003503,
        "function": "pathlib.py:484:_parse_args"
      },
      {
        "calls": 150,
        "cumulative_seconds": 0.0002038,
        "function": "enum.py:193:__get__"
      },
      {
        "calls": 22,
        "cumulative_seconds": 0.0047834,
        "function": "encoder.py:183:encode"
      },
      {
        "calls": 22,
        "cumulative_seconds": 4.36e-05,
        "function": "encoder.py:105:__init__"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.7881418,
        "function": "run_step37_performance.py:210:materialize"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.7808183,
        "function": "materialization.py:47:materialize"
      },
      {
        "calls": 22,
        "cumulative_seconds": 0.0046677,
        "function": "encoder.py:205:iterencode"
      },
      {
        "calls": 150,
        "cumulative_seconds": 4.61e-05,
        "function": "enum.py:1255:value"
      },
      {
        "calls": 109,
        "cumulative_seconds": 0.0019037,
        "function": "main.py:253:__init__"
      },
      {
        "calls": 22,
        "cumulative_seconds": 0.0050119,
        "function": "__init__.py:183:dumps"
      },
      {
        "calls": 1,
        "cumulative_seconds": 3.23e-05,
        "function": "pathlib.py:667:with_name"
      },
      {
        "calls": 15,
        "cumulative_seconds": 6.27e-05,
        "function": "pathlib.py:147:splitroot"
      }
    ],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.7882069
  },
  {
    "benchmark_id": "PERF-MAT-002",
    "cold_warm": "WARM",
    "cpu_seconds": 0.09375,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "dimension_rows": {
        "dim_branch": 2,
        "dim_customer": 2,
        "dim_date": 2,
        "dim_product": 2
      },
      "fact_rows": 3,
      "materialization_mode": "PROJECT_DUCKDB_MATERIALIZER",
      "run": "identical repeated materialization",
      "semantic_artifact_content_hash": "85b9deed5bcee2d17f0a6748619f09fcd4ef7eba5c979980cd8b2f16745602d7",
      "table_count": 5,
      "target_sha256": "88ba6c6b4f223dfee9d49d8f5524dd50921a6e8ba85ecfcc4040aa5865bea794"
    },
    "family": "duckdb_materialization",
    "io": {
      "input_rows": 11,
      "output_db_bytes": 4993024,
      "output_files": 1,
      "status": "MEASURED",
      "temporary_bytes_observed": null
    },
    "peak_memory": {
      "bytes": 5082784,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.6056555
  },
  {
    "benchmark_id": "PERF-REG-001",
    "cold_warm": "COLD",
    "cpu_seconds": 0.21875,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "dimension_rows": {
        "dim_branch": 2,
        "dim_customer": 2,
        "dim_date": 2,
        "dim_product": 2
      },
      "fact_rows": 3,
      "materialization_mode": "PROJECT_DUCKDB_MATERIALIZER",
      "optimization_status": "NONE_ACCEPTED",
      "run": "first materialization",
      "same_fixture": true,
      "semantic_artifact_content_hash": "85b9deed5bcee2d17f0a6748619f09fcd4ef7eba5c979980cd8b2f16745602d7",
      "table_count": 5,
      "target_sha256": "88ba6c6b4f223dfee9d49d8f5524dd50921a6e8ba85ecfcc4040aa5865bea794"
    },
    "family": "regression_guard",
    "io": {
      "input_rows": 11,
      "output_db_bytes": 4993024,
      "output_files": 1,
      "status": "MEASURED",
      "temporary_bytes_observed": null
    },
    "peak_memory": {
      "bytes": 5456823,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [
      {
        "calls": 7,
        "cumulative_seconds": 0.0003503,
        "function": "pathlib.py:484:_parse_args"
      },
      {
        "calls": 150,
        "cumulative_seconds": 0.0002038,
        "function": "enum.py:193:__get__"
      },
      {
        "calls": 22,
        "cumulative_seconds": 0.0047834,
        "function": "encoder.py:183:encode"
      },
      {
        "calls": 22,
        "cumulative_seconds": 4.36e-05,
        "function": "encoder.py:105:__init__"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.7881418,
        "function": "run_step37_performance.py:210:materialize"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.7808183,
        "function": "materialization.py:47:materialize"
      },
      {
        "calls": 22,
        "cumulative_seconds": 0.0046677,
        "function": "encoder.py:205:iterencode"
      },
      {
        "calls": 150,
        "cumulative_seconds": 4.61e-05,
        "function": "enum.py:1255:value"
      },
      {
        "calls": 109,
        "cumulative_seconds": 0.0019037,
        "function": "main.py:253:__init__"
      },
      {
        "calls": 22,
        "cumulative_seconds": 0.0050119,
        "function": "__init__.py:183:dumps"
      },
      {
        "calls": 1,
        "cumulative_seconds": 3.23e-05,
        "function": "pathlib.py:667:with_name"
      },
      {
        "calls": 15,
        "cumulative_seconds": 6.27e-05,
        "function": "pathlib.py:147:splitroot"
      }
    ],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.7882069
  },
  {
    "benchmark_id": "PERF-QUERY-001",
    "cold_warm": "COLD",
    "cpu_seconds": 0.0,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "query_index": 0,
      "result_rows": 1,
      "sql_shape": "SELECT COUNT(*) AS fact_count"
    },
    "family": "duckdb_query",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 4435,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0005463
  },
  {
    "benchmark_id": "PERF-QUERY-001-WARM",
    "cold_warm": "WARM",
    "cpu_seconds": 0.0,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "query_index": 0,
      "result_rows": 1,
      "sql_shape": "SELECT COUNT(*) AS fact_count"
    },
    "family": "duckdb_query",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 4435,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0003316
  },
  {
    "benchmark_id": "PERF-QUERY-002",
    "cold_warm": "COLD",
    "cpu_seconds": 0.03125,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "query_index": 1,
      "result_rows": 1,
      "sql_shape": "SELECT p.category, SUM(f.quantity) AS units"
    },
    "family": "duckdb_query",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 4470,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0053641
  },
  {
    "benchmark_id": "PERF-QUERY-002-WARM",
    "cold_warm": "WARM",
    "cpu_seconds": 0.0,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "query_index": 1,
      "result_rows": 1,
      "sql_shape": "SELECT p.category, SUM(f.quantity) AS units"
    },
    "family": "duckdb_query",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 4435,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0028103
  },
  {
    "benchmark_id": "PERF-QUERY-003",
    "cold_warm": "COLD",
    "cpu_seconds": 0.0,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "query_index": 2,
      "result_rows": 1,
      "sql_shape": "SELECT d.year, SUM(f.quantity) AS units"
    },
    "family": "duckdb_query",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 4435,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0024467
  },
  {
    "benchmark_id": "PERF-QUERY-003-WARM",
    "cold_warm": "WARM",
    "cpu_seconds": 0.015625,
    "dataset_id": "step20-typed-reference-fixture-v1",
    "details": {
      "query_index": 2,
      "result_rows": 1,
      "sql_shape": "SELECT d.year, SUM(f.quantity) AS units"
    },
    "family": "duckdb_query",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 4435,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0013076
  },
  {
    "benchmark_id": "PERF-VAL-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.140625,
    "dataset_id": "step22-retail-source-truth",
    "details": {
      "checks": 25,
      "discrepancies": 0,
      "g6_eligible": true,
      "g6_status": "PASS"
    },
    "family": "validation_reconciliation",
    "io": {
      "status": "READ_ONLY_TARGET"
    },
    "peak_memory": {
      "bytes": 5023779,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.1613265
  },
  {
    "benchmark_id": "PERF-FUSION-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.109375,
    "dataset_id": "step17-evidence-fusion-runtime",
    "details": {
      "cases": 23,
      "control_fixture": "benchmarks/evidence_fusion/expected_control.json",
      "correctness": "existing Step17 control set executed"
    },
    "family": "evidence_fusion",
    "io": {
      "status": "NOT_APPLICABLE"
    },
    "peak_memory": {
      "bytes": 1291885,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [
      {
        "calls": 23,
        "cumulative_seconds": 0.0005957,
        "function": "pathlib.py:484:_parse_args"
      },
      {
        "calls": 2412,
        "cumulative_seconds": 0.0039759,
        "function": "enum.py:193:__get__"
      },
      {
        "calls": 407,
        "cumulative_seconds": 0.0279485,
        "function": "encoder.py:183:encode"
      },
      {
        "calls": 23,
        "cumulative_seconds": 0.0030424,
        "function": "decoder.py:332:decode"
      },
      {
        "calls": 407,
        "cumulative_seconds": 0.0007378,
        "function": "encoder.py:105:__init__"
      },
      {
        "calls": 71,
        "cumulative_seconds": 0.0003348,
        "function": "copy.py:243:_keep_alive"
      },
      {
        "calls": 23,
        "cumulative_seconds": 0.0026273,
        "function": "decoder.py:343:raw_decode"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.1232918,
        "function": "run_step37_performance.py:270:<dictcomp>"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.1232982,
        "function": "run_step37_performance.py:270:<lambda>"
      },
      {
        "calls": 387,
        "cumulative_seconds": 0.0260871,
        "function": "encoder.py:205:iterencode"
      },
      {
        "calls": 23,
        "cumulative_seconds": 0.0031876,
        "function": "__init__.py:299:loads"
      },
      {
        "calls": 1,
        "cumulative_seconds": 2e-06,
        "function": "enum.py:1093:__new__"
      }
    ],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.1233159
  },
  {
    "benchmark_id": "PERF-SCALE-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.140625,
    "dataset_id": "step24-orders-input-v1",
    "details": {
      "policy": "step24-test-policy-v1",
      "rows": 1024,
      "scale_class": "Tiny",
      "semantics": "existing Step24 reference reduction; not capacity evidence"
    },
    "family": "scale_reference",
    "io": {
      "status": "NOT_APPLICABLE"
    },
    "peak_memory": {
      "bytes": 273411,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [
      {
        "calls": 3072,
        "cumulative_seconds": 0.0781013,
        "function": "encoder.py:183:encode"
      },
      {
        "calls": 3072,
        "cumulative_seconds": 0.0055663,
        "function": "encoder.py:105:__init__"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.1605316,
        "function": "run_step37_performance.py:283:<lambda>"
      },
      {
        "calls": 3072,
        "cumulative_seconds": 0.0655389,
        "function": "encoder.py:205:iterencode"
      },
      {
        "calls": 4,
        "cumulative_seconds": 0.0011591,
        "function": "main.py:253:__init__"
      },
      {
        "calls": 3072,
        "cumulative_seconds": 0.0987471,
        "function": "__init__.py:183:dumps"
      },
      {
        "calls": 4,
        "cumulative_seconds": 0.001143,
        "function": "~:0:<method 'validate_python' of 'pydantic_core._pydantic_core.SchemaValidator' objects>"
      },
      {
        "calls": 1024,
        "cumulative_seconds": 0.0102691,
        "function": "~:0:<method 'to_python' of 'pydantic_core._pydantic_core.SchemaSerializer' objects>"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.0005652,
        "function": "distributed.py:438:_candidate_pairs"
      },
      {
        "calls": 1,
        "cumulative_seconds": 0.1224712,
        "function": "distributed.py:464:_output_for_rows"
      },
      {
        "calls": 1024,
        "cumulative_seconds": 0.0117133,
        "function": "main.py:427:model_dump"
      },
      {
        "calls": 1,
        "cumulative_seconds": 4.15e-05,
        "function": "distributed.py:23:_content_hash"
      }
    ],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.1605488
  },
  {
    "benchmark_id": "PERF-API-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.015625,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "endpoint": "GET /api/v1/product/configuration",
      "status_code": 200
    },
    "family": "api_hot_path",
    "io": {
      "status": "HTTP_LOCAL"
    },
    "peak_memory": {
      "bytes": 162497,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [],
    "wall_seconds": 0.021529
  },
  {
    "benchmark_id": "PERF-E2E-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 3.125,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "current_stage": "DEPENDENCY_DISCOVERY",
      "provider_boundary": "Desbordante optional provider",
      "provider_policy": "UNAVAILABLE is explicit; no G6/G7 upgrade",
      "run_id_present": true,
      "terminal_status": "FAILED"
    },
    "family": "real_product_path",
    "io": {
      "source_write": false,
      "status": "LOCAL_PRODUCT_RUNTIME"
    },
    "peak_memory": {
      "bytes": 3182014,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "UNAVAILABLE",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 6.7764333
  },
  {
    "benchmark_id": "PERF-API-002",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.046875,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "endpoint": "/api/v1/runs/run_c3ae4947faeba3f5a17b49bfbb554794/product-summary",
      "status_code": 200,
      "terminal_product_status": "FAILED"
    },
    "family": "api_hot_path",
    "io": {
      "status": "HTTP_LOCAL"
    },
    "peak_memory": {
      "bytes": 1383676,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [],
    "wall_seconds": 0.0775724
  },
  {
    "benchmark_id": "PERF-DIAG-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.03125,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "endpoint": "/api/v1/runs/run_c3ae4947faeba3f5a17b49bfbb554794/diagnostics",
      "status_code": 200,
      "terminal_product_status": "FAILED"
    },
    "family": "api_hot_path",
    "io": {
      "status": "HTTP_LOCAL"
    },
    "peak_memory": {
      "bytes": 1242384,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [],
    "wall_seconds": 0.0350113
  },
  {
    "benchmark_id": "PERF-STAGE-DEPENDENCY_DISCOVERY",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": null,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "source": "Step34 TelemetryClient",
      "stage_kind": "DEPENDENCY_DISCOVERY",
      "terminal_product_status": "FAILED"
    },
    "family": "runtime_stage_telemetry",
    "io": {
      "status": "TELEMETRY"
    },
    "peak_memory": {
      "bytes": null,
      "method": "telemetry_stage_duration_only"
    },
    "profile_top_functions": [],
    "status": "UNAVAILABLE",
    "truth_fixture_ids": [],
    "wall_seconds": 0.26599999994505197
  },
  {
    "benchmark_id": "PERF-STAGE-PROFILING",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": null,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "source": "Step34 TelemetryClient",
      "stage_kind": "PROFILING",
      "terminal_product_status": "FAILED"
    },
    "family": "runtime_stage_telemetry",
    "io": {
      "status": "TELEMETRY"
    },
    "peak_memory": {
      "bytes": null,
      "method": "telemetry_stage_duration_only"
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [],
    "wall_seconds": 1.375
  },
  {
    "benchmark_id": "PERF-STAGE-SOURCE_DISCOVERY",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": null,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "source": "Step34 TelemetryClient",
      "stage_kind": "SOURCE_DISCOVERY",
      "terminal_product_status": "FAILED"
    },
    "family": "runtime_stage_telemetry",
    "io": {
      "status": "TELEMETRY"
    },
    "peak_memory": {
      "bytes": null,
      "method": "telemetry_stage_duration_only"
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [],
    "wall_seconds": 0.20400000002700835
  },
  {
    "benchmark_id": "PERF-STAGE-SOURCE_SNAPSHOT_STAGE",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": null,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "source": "Step34 TelemetryClient",
      "stage_kind": "SOURCE_SNAPSHOT_STAGE",
      "terminal_product_status": "FAILED"
    },
    "family": "runtime_stage_telemetry",
    "io": {
      "status": "TELEMETRY"
    },
    "peak_memory": {
      "bytes": null,
      "method": "telemetry_stage_duration_only"
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [],
    "wall_seconds": 0.2970000000204891
  },
  {
    "benchmark_id": "PERF-TELEMETRY-001",
    "cold_warm": "NOT_APPLICABLE",
    "cpu_seconds": 0.046875,
    "dataset_id": "step29-orders-csv-4-rows",
    "details": {
      "disabled_wall_seconds": 0.0335596,
      "enabled_wall_seconds": 0.0518874,
      "method": "bounded 250 event/span emissions; diagnostic overhead only",
      "no_telemetry_disable_in_product": true
    },
    "family": "telemetry_overhead",
    "io": {
      "status": "NOT_APPLICABLE"
    },
    "peak_memory": {
      "bytes": 900789,
      "method": "python_tracemalloc_peak",
      "process_rss_after_bytes": null,
      "process_rss_before_bytes": null
    },
    "profile_top_functions": [],
    "status": "PASS",
    "truth_fixture_ids": [
      "step18-relationship-truth-v1",
      "step18-schema-truth-v1",
      "step18-entity-truth-v1",
      "step18-runtime-inputs-v1",
      "benchmarks/inference_evaluation/scenario_groups.json",
      "benchmarks/inference_evaluation/scenario_groups_v4.json",
      "step17-evidence-fusion-controls-v1",
      "step13-cross-source-hard-negatives-v1",
      "step14-entity-resolution-cases-v2",
      "step22-retail-source-truth-v2",
      "step22-generic-source-truth-v2",
      "step15-relationship-ranker-v1",
      "step16-semantic-safety-v2"
    ],
    "wall_seconds": 0.0518874
  }
]
```

## Profiling findings

```json
[
  {
    "baseline_cost": 0.7882069,
    "benchmark_id": "PERF-MAT-001",
    "delta": null,
    "empirical_quality_result": "NOT_APPLICABLE_NO_OPTIMIZATION",
    "finding_id": "PERF-FIND-001",
    "hot_path": "DuckDBMaterializer.materialize",
    "measured_result": "baseline recorded; no implementation change",
    "proposed_change": "none accepted; preserve review, hashing, atomic publication and validation boundary",
    "resource_dimension": "wall_time_and_python_allocations",
    "root_cause": "reviewed V1 SQL loads and atomic DuckDB publication dominate the bounded reference path",
    "semantic_result": "PASS",
    "status": "OBSERVED_NO_CHANGE"
  },
  {
    "baseline_cost": 6.7764333,
    "benchmark_id": "PERF-E2E-001",
    "delta": null,
    "empirical_quality_result": "UNAVAILABLE",
    "finding_id": "PERF-FIND-002",
    "hot_path": "DEPENDENCY_DISCOVERY provider boundary",
    "measured_result": "UNAVAILABLE at the real product boundary",
    "proposed_change": "none; provider provisioning is outside Step37 algorithm optimization",
    "resource_dimension": "provider_availability",
    "root_cause": "Desbordante binding or provisioned local image is absent",
    "semantic_result": "PASS_NO_FALSE_SUCCESS",
    "status": "BLOCKED_BY_OPTIONAL_PROVIDER"
  }
]
```

## Optimizations and before/after

No optimization was accepted: the measured safe core path is bounded and optional inference providers are unavailable in this environment. Existing implementations were left unchanged; no unmeasured speedup is claimed.

```json
[
  {
    "benchmark_id": "PERF-REG-001",
    "optimization_status": "NONE_ACCEPTED",
    "quality_regression": "NOT_APPLICABLE_NO_OPTIMIZATION",
    "reason": "No material safe bottleneck was changed in Step37.",
    "same_fixture": true,
    "same_truth_links": true,
    "semantic_equivalence": "PASS",
    "status": "PASS"
  }
]
```

## Correctness, semantic equivalence and empirical quality

```json
{
  "empirical_quality_before_after": {
    "ER": {
      "fixture": "entity_truth.json",
      "status": "UNAVAILABLE"
    },
    "evidence_fusion": {
      "control": "expected_control.json",
      "status": "PASS"
    },
    "relationship": {
      "fixture": "relationship_truth.json",
      "status": "NOT_APPLICABLE_NO_OPTIMIZATION"
    },
    "schema_matching": {
      "fixture": "schema_truth.json",
      "status": "UNAVAILABLE"
    }
  },
  "semantic_equivalence": {
    "checks": [
      "identical reviewed fixture",
      "materialization semantic content hash stable",
      "target table set and row counts stable",
      "G6 validation PASS",
      "no inference optimization accepted"
    ],
    "materialization_target_sha256": "88ba6c6b4f223dfee9d49d8f5524dd50921a6e8ba85ecfcc4040aa5865bea794",
    "query_outputs": {
      "PERF-QUERY-001": {
        "cold_rows": 1,
        "expected_columns": 3,
        "result_digest": "a9ae641b7f052c0994d31111f1bb92a40340e57b57c984bf99f03949206426a1",
        "warm_rows": 1
      },
      "PERF-QUERY-002": {
        "cold_rows": 1,
        "expected_columns": 1,
        "result_digest": "5b8d3f4dd81155619c9ab2afd6bf724f1e38c7e514bc4be5965b89fe4d39b0b3",
        "warm_rows": 1
      },
      "PERF-QUERY-003": {
        "cold_rows": 1,
        "expected_columns": 1,
        "result_digest": "bfae578ed017f6f10ad167533d8d20f098684a684c99f88b98552e082193a9f3",
        "warm_rows": 1
      }
    },
    "status": "PASS"
  }
}
```

## Candidate growth and large-scale status

```json
{
  "candidate_growth": {
    "dependency": [
      {
        "candidate_pairs_considered": 2500,
        "candidates_retained": 0,
        "columns": 50,
        "reason": "Desbordante binding/Docker image is not provisioned; only the bounded pre-provider search shape is recorded",
        "status": "UNAVAILABLE"
      },
      {
        "candidate_pairs_considered": 10000,
        "candidates_retained": 0,
        "columns": 100,
        "reason": "Desbordante binding/Docker image is not provisioned; only the bounded pre-provider search shape is recorded",
        "status": "UNAVAILABLE"
      },
      {
        "candidate_pairs_considered": 62500,
        "candidates_retained": 0,
        "columns": 250,
        "reason": "Desbordante binding/Docker image is not provisioned; only the bounded pre-provider search shape is recorded",
        "status": "UNAVAILABLE"
      },
      {
        "candidate_pairs_considered": 250000,
        "candidates_retained": 0,
        "columns": 500,
        "reason": "Desbordante binding/Docker image is not provisioned; only the bounded pre-provider search shape is recorded",
        "status": "UNAVAILABLE"
      }
    ],
    "entity_resolution": [
      {
        "candidate_reduction_ratio": null,
        "generated_candidate_pairs": 0,
        "naive_pair_upper_bound": 10000,
        "reason": "Splink optional runtime is not provisioned in the locked profiling environment",
        "records": 100,
        "status": "UNAVAILABLE"
      },
      {
        "candidate_reduction_ratio": null,
        "generated_candidate_pairs": 0,
        "naive_pair_upper_bound": 250000,
        "reason": "Splink optional runtime is not provisioned in the locked profiling environment",
        "records": 500,
        "status": "UNAVAILABLE"
      },
      {
        "candidate_reduction_ratio": null,
        "generated_candidate_pairs": 0,
        "naive_pair_upper_bound": 1000000,
        "reason": "Splink optional runtime is not provisioned in the locked profiling environment",
        "records": 1000,
        "status": "UNAVAILABLE"
      }
    ],
    "schema_matching": [
      {
        "candidate_pairs": 2500,
        "reason": "Valentine optional runtime is not provisioned in the locked profiling environment",
        "retained_candidates": 0,
        "source_columns": 50,
        "status": "UNAVAILABLE",
        "target_columns": 50
      },
      {
        "candidate_pairs": 10000,
        "reason": "Valentine optional runtime is not provisioned in the locked profiling environment",
        "retained_candidates": 0,
        "source_columns": 100,
        "status": "UNAVAILABLE",
        "target_columns": 100
      },
      {
        "candidate_pairs": 62500,
        "reason": "Valentine optional runtime is not provisioned in the locked profiling environment",
        "retained_candidates": 0,
        "source_columns": 250,
        "status": "UNAVAILABLE",
        "target_columns": 250
      },
      {
        "candidate_pairs": 250000,
        "reason": "Valentine optional runtime is not provisioned in the locked profiling environment",
        "retained_candidates": 0,
        "source_columns": 500,
        "status": "UNAVAILABLE",
        "target_columns": 500
      }
    ]
  },
  "large_scale_execution_status": {
    "100M": "FEASIBILITY_DESIGNED",
    "10M": "NOT_EXECUTED_OPTIONAL",
    "1M": "NOT_EXECUTED",
    "Tiny": "EXECUTED_REFERENCE_ONLY",
    "several-million": "NOT_EXECUTED"
  }
}
```

## Step38 handoff

Single-run cost, stage timing, memory and disk observations are handed to Step38. No concurrency, saturation, breakpoint or capacity claim is made.

## Limitations

- Optional Desbordante, Valentine and Splink runtimes were unavailable; their status is UNAVAILABLE, not PASS.
- Tiny reference and typed OLAP fixtures are single-run evidence only; Medium, Large-local, 1M and several-million full product scales were not executed.
- No concurrency, arrival-rate, saturation, soak, breakpoint, overload-recovery or capacity claim is made; those belong to Step38.
- Local benchmark timings are environment-specific and do not establish production SLOs.

## Upstream gates

`G6=PASS`, `G7=PASS`, `G8=PASS`, `G9=PASS`, `G10=PASS`, `G11=PASS`; `G12-G15=PENDING`.

