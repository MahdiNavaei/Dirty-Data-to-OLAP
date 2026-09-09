# Dirty Data to OLAP — Domain Contract

Status: `DEFINED` for the synthetic reference benchmark; runtime customer semantics remain assertion-driven.

## 1. Scope and authority boundary

Dirty Data to OLAP is a generic product. Its benchmark semantics are **not universal product semantics**: the product has no universal meaning for `Customer`, `Order`, `Payment`, source authority or business status. This contract defines two deliberately separate semantic layers:

1. **REFERENCE BENCHMARK DOMAIN TRUTH** — the controlled synthetic retail/e-commerce estate owned by this project. The versioned specifications under `benchmarks/labels/domain-reviewed/` are authoritative for benchmark generation and evaluation.
2. **RUNTIME CUSTOMER DOMAIN ASSERTION** — organization-specific meaning supplied by an attributable human/domain authority for a real customer dataset. Benchmark truth must never be copied into this layer without a new assertion.

The benchmark specifications define intended entities, processes, relationships, identity semantics and business rules. They do not contain generated rows or record-level clusters. Those belong to a later benchmark-generator step.

## 2. What counts as domain truth

For the reference benchmark, a statement is domain truth only when it is present in this contract or its linked versioned label/specification and is explicitly scoped to the reference benchmark. For a runtime customer dataset, a domain assertion is evidence with a named actor, scope, time, provenance and lifecycle state. Neither a column name, matcher score, inclusion dependency, LLM output nor a plausible aggregate is domain truth by itself.

The following remain distinct:

- source-declared metadata;
- measured observation or estimate;
- inferred relationship or mapping hypothesis;
- benchmark-domain decision;
- runtime human/domain assertion;
- materialized analytical output.

## 3. Authority boundaries

Authority is scoped per entity property, attribute, event/process and time period. No source is globally authoritative. The reference authority expectations are listed in `SOURCE_SYSTEM_MAP.md` and `benchmarks/labels/domain-reviewed/source_authority.yml`.

An authority expectation selects a survivor for a specified benchmark property; it does not erase conflicting source values or source records. Conflicting observations, rejected assertions and superseded decisions remain auditable.

## 4. Downstream consumption

Later specialists may consume a benchmark decision only with its scope, version, provenance and status. They must retain source lineage and distinguish the decision from measured evidence. A runtime assertion may be used only when its status and scope permit it; `REVIEW_REQUIRED`, `CONFLICTED`, `REJECTED` and `SUPERSEDED` assertions cannot silently drive canonicalization or materialization.

The domain contract supplies meaning for relationship and grain evaluation. It does not implement discovery, entity resolution, evidence fusion, SQL generation, analytical schemas or validation code.

## 5. Conflict policy

Human/domain assertions do not silently overwrite measured evidence. If an assertion conflicts with observations, both are retained and the subject becomes `CONFLICTED` or `REVIEW_REQUIRED`. A later decision may resolve the conflict only by recording the actor, rationale, evidence references, effective time and supersession relationship. Declared metadata, measured evidence, inferred hypotheses and domain assertions are never collapsed into one undifferentiated score.

## 6. Defined benchmark boundary

The benchmark defines Customer, Product, Branch, Order, OrderLine and Payment; the supporting records and processes are defined in `GLOSSARY.md` and `REFERENCE_BENCHMARK_DOMAIN.md`. It includes customer profile representation, order creation, order-line creation, payment events, product representation and branch attribution.

Recognized revenue, gross margin, tax accounting, refunds, returns, settlement, inventory movement and full historical SCD behavior are **OUTSIDE CURRENT BENCHMARK DOMAIN** unless a later versioned generator specification explicitly adds them.

## 7. Explicit unresolved items

The following are intentionally unresolved rather than guessed:

- exact physical column names and row-level generator schema;
- source status/category code values and cross-source code mappings;
- currency and unit metadata for monetary measures;
- full valid-from/valid-to history and SCD behavior;
- record-level hidden canonical IDs and entity clusters before generation;
- whether a future scenario adds inventory snapshots or adjustment events.

These do not block the current benchmark semantic contract because the required concepts, relationships, identity rules, event meanings and measure meanings are defined independently of column spelling and generated rows. They do block claims about uncreated records, code mappings, currency conversion or historical semantics.

## 8. Non-universalization rule

No artifact may phrase a benchmark choice as a universal Dirty Data to OLAP rule. Correct form: “In the REFERENCE BENCHMARK, Sales owns the order event.” For a real customer: “A runtime domain assertion scoped to this source and period states who owns the order event.”
