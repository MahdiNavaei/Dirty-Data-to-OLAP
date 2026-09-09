# Dirty Data to OLAP — Reference Business Rules

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH only**. These are semantic rules for benchmark generation/evaluation, not universal customer rules.

Every rule has scope, rationale, exception handling, a verification path and a status. `DEFINED` means the benchmark meaning is fixed; `UNRESOLVED` means the generator or a later scoped decision must supply the missing detail.

## Physical validity versus business validity

- A syntactically valid phone is not a verified Customer identifier.
- A non-null status value is not a known valid business status until its source domain is defined.
- A structural inclusion dependency is not a business relationship without the relationship semantics and supporting evidence.
- In this reference benchmark, an unknown Order customer/branch or an orphan OrderLine/Payment is treated as corruption or incomplete snapshot evidence according to the relationship contract. For a real customer dataset, late-arriving data or intentionally anonymous activity requires a runtime domain assertion rather than reuse of this benchmark rule.
- A normalization that changes representation is not a factual correction; failed or ambiguous transformations remain reviewable/quarantined.

## BR-001 — Valid order has one customer

- scope: `REFERENCE BENCHMARK / Order.customer`
- statement: Every valid benchmark Order represents a commercial order placed by exactly one Customer.
- domain rationale: The reference relationship defines Order-to-Customer meaning and prevents orphan data from being silently treated as a new anonymous process.
- exceptions: A corrupted snapshot may contain null/orphan/mismatched customer references; classify and preserve the failure.
- verification path: Ground-truth relationship labels plus order/customer reconciliation.
- status: `DEFINED`

## BR-002 — OrderLine belongs to one Order

- scope: `REFERENCE BENCHMARK / OrderLine.order`
- statement: Every valid OrderLine is one product-level event within exactly one Order.
- domain rationale: Header and line events have different meanings and grains.
- exceptions: Injected orphan lines are retained as corruption cases, not reassigned.
- verification path: Relationship labels and `(order identity, line sequence)` grain checks.
- status: `DEFINED`

## BR-003 — OrderLine concerns one Product

- scope: `REFERENCE BENCHMARK / OrderLine.product`
- statement: Each valid OrderLine concerns exactly one Product; aliases are mapping evidence, not new products.
- domain rationale: Product-level analytical meaning cannot be established from a text alias alone.
- exceptions: Unknown/orphan product references remain unresolved or quarantined.
- verification path: Product relationship labels and alias-mapping evaluation.
- status: `DEFINED`

## BR-004 — Order has one Branch attribution

- scope: `REFERENCE BENCHMARK / Order.branch`
- statement: Each valid benchmark Order is attributed to one Branch.
- domain rationale: Branch attribution is a benchmark relationship, not a generic integer-code join.
- exceptions: Missing, aliased or orphan branch codes require the specified evidence/review path.
- verification path: Relationship labels and branch reconciliation.
- status: `DEFINED`

## BR-005 — Payment is an order-linked event

- scope: `REFERENCE BENCHMARK / Payment`
- statement: A valid Payment is one payment event associated with one Order; an Order may have zero or more Payment events.
- domain rationale: The benchmark does not define a one-payment-per-order rule.
- exceptions: Orphan payments remain referential failures; no refund, settlement or revenue meaning is inferred.
- verification path: Payment relationship labels and event-level accounting.
- status: `DEFINED`

## BR-006 — Quantity means ordered units

- scope: `REFERENCE BENCHMARK / OrderLine.quantity`
- statement: `quantity` represents the count of units on an OrderLine and is additive only at a valid line-event grain.
- domain rationale: It is a measure of line-level units, not an identifier or rate.
- exceptions: Negative/zero values require benchmark generator specification or quality review; they are not silently converted.
- verification path: Measure-semantic review and line-grain aggregate checks.
- status: `DEFINED` with value-domain details `UNRESOLVED`

## BR-007 — Unit price is per-unit and non-additive

- scope: `REFERENCE BENCHMARK / OrderLine.unit_price`
- statement: `unit_price` is a per-unit value and must not be summed across arbitrary rows.
- domain rationale: Summing prices across lines produces no defined business quantity.
- exceptions: Currency and unit metadata are unresolved; no conversion or revenue-recognition claim is made.
- verification path: Measure semantic contract and negative aggregation test.
- status: `DEFINED`

## BR-008 — Discount rate is a rate

- scope: `REFERENCE BENCHMARK / OrderLine.discount_rate`
- statement: `discount_rate` is a rate/ratio and is not additive.
- domain rationale: Rates require an explicit denominator or weighted calculation; simple summation is invalid.
- exceptions: Exact representation, bounds and currency interaction are generator-defined later.
- verification path: Measure semantic review and non-additivity test.
- status: `DEFINED`

## BR-009 — Status domains are source-specific

- scope: `REFERENCE BENCHMARK / status and category fields`
- statement: Source status/category domains are distinct until a versioned mapping is supplied; `orders.status` and `customers.status` do not share meaning merely because the column name matches.
- domain rationale: The benchmark intentionally includes same-name/different-meaning traps.
- exceptions: Known code mappings may be added by a generator specification or domain assertion with provenance.
- verification path: Column-semantic mapping tests and ambiguity review.
- status: `DEFINED`; actual code values `UNRESOLVED`

## BR-010 — Source representations remain distinct

- scope: `REFERENCE BENCHMARK / all source records`
- statement: Multiple source representations may map to one hidden canonical entity, but source records are not deleted or silently collapsed.
- domain rationale: Linkage and survivorship are separate from source-record identity and auditability.
- exceptions: None for the benchmark contract; analytical aggregation must account for its input records.
- verification path: Source-to-canonical labels, lineage and record-accounting checks.
- status: `DEFINED`

## BR-011 — Duplicate classes are not interchangeable

- scope: `REFERENCE BENCHMARK / quality semantics`
- statement: Exact row duplicates, duplicate business identifiers, multi-source representations, legitimate repeated events and erroneous repeated events are distinct issue classes.
- domain rationale: Order lines and payments can repeat legitimately; a duplicate label alone does not justify deletion.
- exceptions: Generator-specific erroneous-event labels are required before a repeated event is called erroneous.
- verification path: Quality fixture labels and event-grain review.
- status: `DEFINED`

## Explicitly outside current benchmark rules

No rule here defines recognized revenue, gross margin, tax, refunds, returns, settlement, inventory movement, currency conversion, fiscal calendars or full SCD history. Those are `OUTSIDE CURRENT BENCHMARK DOMAIN` or `UNRESOLVED` pending an explicit later specification.
