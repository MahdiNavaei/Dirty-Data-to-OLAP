# Dirty Data to OLAP — Identity and Key Semantics

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH only**. Exact physical columns and record-level labels are deferred to the benchmark generator.

## 1. Distinct identity layers

| Layer | Meaning | Safe use |
|---|---|---|
| Physical source key | Key or row locator in one source table/file. | Identifies a source record only; never assumed globally stable. |
| Business identifier | A domain-facing code intended to identify an entity/event within a source or business context. | Candidate evidence whose scope and uniqueness must be measured. |
| Canonical identity | Hidden benchmark identity for the real-world entity/event represented across sources. | Ground-truth cluster membership is generated later; it is not inferred from a source column spelling. |
| Candidate key | A measured hypothesis about uniqueness or reference behavior. | Must retain violation/null evidence and status `CANDIDATE`; not domain truth by itself. |
| Surrogate analytical key | A later warehouse/OLAP implementation identifier. | Owned by downstream architecture/OLAP work; not defined here. |
| Entity-linkage evidence | Observed or asserted support for linking source records. | Supports a decision while preserving source records; does not itself define identity. |

## 2. Benchmark entity identity

| Entity | Same real-world entity/event means | Business-key candidates / semantic roles | Does not prove identity |
|---|---|---|---|
| Customer | Source records refer to the same synthetic buyer/person or organization under the hidden generator truth. | CRM customer identifier; ERP `client_no`; legacy customer identifier; exact physical names are `UNRESOLVED — benchmark generator specification required`. | Same name, phone, address, email, or one matching identifier across systems alone. |
| Product | Source representations refer to the same sellable product concept under hidden benchmark mapping. | ERP product code/SKU; alias-to-product mapping; exact physical names are generator-defined. | Same description, category or similar code domain alone. |
| Branch | Source representations refer to the same selling location under hidden benchmark mapping. | ERP branch code; legacy branch-code alias; exact physical names are generator-defined. | Same numeric code, address text or name alone. |
| Order | Records refer to the same commercial order event. | Sales order identifier; source-record identity must be stable in the generator. | Same customer, date, amount or line contents alone. |
| OrderLine | Records refer to the same product-level line event within an Order. | Conceptual key: Order identity + line sequence; generator must define the physical sequence field. | Same Order identity alone; different products/lines are legitimate distinct events. |
| Payment | Records refer to the same payment event associated with an Order. | Payment event identifier if present, otherwise a generator-defined stable event key; exact physical field is unresolved. | One Order reference, amount or timestamp alone. One-payment-per-order is not assumed. |

## 3. Required generator contract

The later benchmark generator must emit stable source-record references and hidden canonical IDs for the entity families above, plus the intended business identifiers and any injected duplicate/corruption labels. This pass deliberately does not fabricate record-level clusters, row IDs or status-code tables.

## 4. Unsafe assumptions rejected

- `crm.customer_id == erp.client_no` is not assumed.
- Same phone, name, address or product description is not identity proof.
- A physical key is not automatically a business key or canonical identity.
- A matcher score or linkage probability is not a domain assertion.
- Entity resolution maps records; it does not delete source records.
