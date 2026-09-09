# Rule Catalog

Rules are versioned project-owned YAML and validate into `QualityRule`.

| Type | Evidence required | Detection boundary |
|---|---|---|
| REQUIRED_VALUE | explicit rule and observed rows | missing physical nulls or configured markers only |
| UNIQUE_VALUES | explicit column/key rule | duplicate non-missing value tuples; never promotes a key |
| EXACT_ROW_DUPLICATION | staged full row projection | exact row equality on the observed scope |
| EXPECTED_PATTERN | explicit pattern rule | deterministic pattern matcher; no semantic label inference |
| EXPECTED_PRIMITIVE_TYPE | explicit primitive type | observed value type mismatch; ambiguous coercion remains invalid/review |
| ALLOWED_DOMAIN | explicit finite domain | value outside the declared domain |
| NUMERIC_RANGE | explicit minimum/maximum | numeric value outside declared bounds |
| DECLARED_REFERENTIAL_INTEGRITY | Step07 declared FK + full target coverage | orphan key only when target coverage is complete |
| NORMALIZATION_OPPORTUNITY | explicit reversible operation | representation difference such as surrounding whitespace |

Scopes distinguish `GENERIC_TECHNICAL`, `SOURCE_DECLARED`, `DOMAIN_ASSERTION`,
`REFERENCE_BENCHMARK` and `USER_POLICY`. Benchmark rules are not in the
runtime-default set. Null rate never creates a requiredness rule, observed
uniqueness never creates a key, and pattern matches never create business
meaning.

The detector extension point is the project-owned rule type/configuration
contract. A new detector must declare its input prerequisites, scope,
measurement semantics, failure behavior, severity basis and privacy behavior;
native engine objects may remain inside an adapter only.
