# Dirty Data to OLAP — Conflict and Survivorship

## Scoped authority

Source authority is scoped by subject/entity, attribute or event/process, source, temporal scope, domain assertion/policy and validity conditions. There is no universal CRM, ERP or Sales winner for generic runtime data. The reference benchmark examples remain benchmark-only.

## Selection policies

The architecture permits versioned policies such as:

- `PREFERRED_SOURCE`;
- `MOST_RECENT_VALID`;
- `MOST_COMPLETE`;
- `CONSENSUS`;
- `REVIEW_REQUIRED`.

A policy selects a canonical representation; it does not delete losing values. The policy version, eligible evidence, rationale, authority scope, timestamps and reviewer decision remain traceable. `MOST_RECENT_VALID` cannot be used when timestamps do not support recency. An LLM cannot silently choose a survivor.

## Conflict classes

The logical model must represent at least:

- `DIFFERING_SOURCE_VALUES`;
- `AUTHORITY_CONFLICT`;
- `TEMPORAL_CONFLICT`;
- `NORMALIZATION_CONFLICT`;
- `IDENTITY_CONFLICT`;
- `SEMANTIC_MAPPING_CONFLICT`;
- `MISSING_AUTHORITY_CONFLICT`.

Conflicts retain all relevant source values/references and evidence. A higher uncalibrated score cannot make a conflict disappear. A resolution records actor/policy, rationale, evidence references, effective time, exceptions and supersession.

## Benchmark examples

In the reference benchmark only, CRM may be scoped for current customer contact attributes, Sales for order/line/payment events, ERP for product/branch master attributes and legacy files for historical/alias evidence. These examples do not authorize global source precedence in runtime customer datasets.
