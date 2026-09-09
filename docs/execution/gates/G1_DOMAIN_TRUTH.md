# G1 — Domain Truth

Status: `PASS`

Owning specialist: `Step 02 — Business / Domain Data Expert`

## Evidence

The following artifacts establish the domain contract without universalizing the synthetic benchmark:

- [Domain contract](../../domain/DOMAIN_CONTRACT.md)
- [Reference glossary](../../domain/GLOSSARY.md)
- [Source-system map](../../domain/SOURCE_SYSTEM_MAP.md)
- [Business rules](../../domain/BUSINESS_RULES.md)
- [Identity and keys](../../domain/IDENTITY_AND_KEYS.md)
- [Relationship semantics](../../domain/RELATIONSHIP_SEMANTICS.md)
- [Ambiguity catalogue](../../domain/AMBIGUITY_CATALOGUE.md)
- [Domain assertion model](../../domain/DOMAIN_ASSERTION_MODEL.md)
- [Reference benchmark domain](../../domain/REFERENCE_BENCHMARK_DOMAIN.md)
- [Semantic walkthrough](../../domain/SEMANTIC_WALKTHROUGH.md)
- [Benchmark entities](../../../benchmarks/labels/domain-reviewed/entities.yml)
- [Benchmark relationships](../../../benchmarks/labels/domain-reviewed/relationships.yml)
- [Benchmark source authority](../../../benchmarks/labels/domain-reviewed/source_authority.yml)
- [Benchmark business rules](../../../benchmarks/labels/domain-reviewed/business_rules.yml)
- [Benchmark ambiguities](../../../benchmarks/labels/domain-reviewed/ambiguities.yml)
- [Deterministic domain validator](../../../tools/validate_domain_docs.py)

## G1 conditions

| Condition | Evidence | Result |
|---|---|---|
| Critical terms have explicit definitions | Glossary and entity specification define Customer, Customer Address, Campaign Membership, Product, Product Alias, Branch, Order, OrderLine, Payment, Date, and source-record representations. | PASS |
| Ambiguous concepts are marked rather than guessed | AMB-001 through AMB-014 identify false-semantic traps, required evidence, status and downstream risk. | PASS |
| Reference benchmark truth is sufficient for later generation/evaluation | Versioned entities, five ground-truth relationships, source authority expectations, business rules, event meanings, grain and measure semantics are defined without fabricated rows. | PASS |
| Benchmark semantics are not universalized | Domain artifacts separate `REFERENCE BENCHMARK DOMAIN TRUTH` from `RUNTIME CUSTOMER DOMAIN ASSERTION`; no source is globally authoritative. | PASS |
| Runtime assertion framework exists | Assertion types, provenance, actor, scope, effective time, status, conflict retention and supersession are defined. | PASS |
| Domain truth remains distinct from measured/inferred evidence | The contract separates declared metadata, observations, hypotheses, domain decisions and materialized outputs; IND/matcher scores cannot define meaning. | PASS |

## Validation

Executed from the project root:

```text
python tools/validate_domain_docs.py
PASS: domain_docs=10 benchmark_specs=5 entities=6 relationships=5 business_rules=11 ambiguities=14
git diff --check
PASS
```

The validator also checked YAML parsing, ID uniqueness, glossary coverage, relationship fields, source authority fields, business-rule fields, ambiguity fields, relative links, no blanket authority claims, no fabricated record-level labels, no application source directory and no unexpected OSS clone content.

## Known unresolved semantics

These are explicit and do not block G1 because the benchmark meaning needed by downstream specialists is defined independently of them:

- exact generator column names and row schema;
- status/category code values and cross-source mappings;
- currency and unit metadata;
- complete valid-from/valid-to history and full SCD behavior;
- record-level hidden canonical IDs and clusters before generation;
- inventory or adjustment scenarios not present in the current benchmark.

They do block claims about generated records, code conversion, currency conversion and historical accounting semantics.

G0 remains `PASS`. This gate does not claim application implementation and does not pass G2-G15.
