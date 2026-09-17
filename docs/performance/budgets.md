# Step37 Performance Budgets and Evidence Classes

Step37 records single-run and stage-level measurements. It does not establish
concurrency, saturation, soak, breakpoint, overload recovery or a capacity
envelope; those are owned by Step38 and G12 remains `PENDING`.

## Evidence classes

| Class | Meaning |
| --- | --- |
| `MEASURED_BASELINE` | Executed in the named environment with wall time, CPU time where reliable, memory method and applicable I/O metadata. |
| `REGRESSION_GUARD` | Same fixture/truth links and semantic-equivalence assertions used to detect an unintended change. |
| `REFERENCE_TARGET` | A planning target or bounded reference fixture, not a production SLO. |
| `UNVERIFIED_LARGE_SCALE_TARGET` | A canonical V1 scale that was not executed and is explicitly not measured. |

## Current bounded budgets

- Core Step20/22 DuckDB materialization and G6 validation must remain executable
  on the existing typed reference fixture.
- Every performance record must carry a wall-time unit, a declared memory
  measurement method and the applicable truth fixture links.
- Candidate-growth records must retain considered and retained counts. An
  unavailable optional provider is recorded as `UNAVAILABLE`, never as PASS.
- No optimization is accepted without a concrete `PERF-FIND-*` finding and an
  identical-fixture rerun. Inference-affecting changes additionally require
  empirical truth-backed quality metrics; semantic hashes alone are not enough.
- CI uses bounded reference evidence and does not generate 10M or 100M rows.

## V1 scale interpretation

Base Report 08 defines Tiny as approximately 1k–10k rows/table, Medium as
approximately 100k–1M rows in major fact tables, and Large-local as several
million fact rows. Step37 executes only safe bounded reference evidence; an
unexecuted scale remains unverified and is handed to the appropriate future
specialist.
