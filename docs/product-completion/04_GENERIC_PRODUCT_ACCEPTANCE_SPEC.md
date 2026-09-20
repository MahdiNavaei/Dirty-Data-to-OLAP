# Generic Product Acceptance Specification

Status: `SPECIFICATION ONLY — NOT YET EXECUTED`

## Purpose

The product must be generic at the orchestration boundary. “Generic” means a
domain policy and typed configuration supply source/table semantics, identity,
quality rules, analytical grain, dimensions, measures, and validation
expectations. It does not mean removing the accepted order policy or allowing
arbitrary unreviewed SQL.

## Required policy boundary

The runtime must depend on a product/domain policy port, not directly on
`OrderProductPolicy`, `build_order_dataset`, or
`build_order_truth_and_accounting`. The order implementation becomes one
registered policy/plugin. A second policy must be executable without changing
the orchestration stages, artifact integrity rules, review gate, or validation
engine.

The policy contract must provide, at minimum:

- source/table selection and source-specific schema interpretation;
- profile and quality requests;
- identity candidate and entity-resolution requirements;
- canonical entity types and record-accounting rules;
- analytical input bindings, dimensions, fact, grain, and measure semantics;
- materialization target configuration;
- validation and reconciliation policy;
- safe output labels and deferred/unsupported concepts.

Policy data must be versioned, content-hashed, and included in plan and
artifact provenance. A policy cannot make a score a probability or invent a
business measure by naming it.

## Second-domain acceptance scenario

Use the existing generic telemetry/device truth shape as a starting point, but
run it through the product boundary rather than calling its stage services
directly. The fixture should contain `devices`, `locations`, and `readings`:

- `reading_id` is the event grain;
- `device` and `location` are canonical entities;
- `observed_on` is the date dimension;
- `temperature` is a numeric measure with degrees-Celsius unit semantics;
- the output must not be called an order, revenue, GMV, or customer model.

The scenario must include at least one missing/ambiguous relationship and one
explicitly rejected hard negative, with all source rows accounted for. Its
truth remains independent of runtime and must be compared only after verified
execution.

## Generic acceptance gates

The second-domain run passes only when:

1. the same server-owned stage graph executes without order-specific branches;
2. no order column name is required by the generic policy;
3. identity and relationships are selected by policy and evidence, not by a
   hardcoded `customer_id` or `order_id` assumption;
4. the plan contains explicit fact grain, at least three dimensions where the
   domain supports them, and a declared temperature measure;
5. the output row and aggregate checks match the independent truth;
6. the existing order acceptance test still passes through the order policy;
7. no runtime or UI code imports the second domain's oracle;
8. unsupported concepts are surfaced as deferred or not applicable rather than
   silently synthesized.

## Current implementation verdict

`NOT_ACCEPTED`. `OrderProductPolicy`, `order_v1.json`, the order input builder,
and the order truth/accounting builder are direct product dependencies today.
The typed lower-level contracts are a sound starting point, but their presence
is not generic product acceptance.

