# 06 — Data Quality & Cleaning Taxonomy Report

## 1. Purpose

“Dirty data” is too vague to implement. DataFoundry needs a precise taxonomy that separates:

- what is observed;
- why it matters;
- whether it can be fixed safely;
- whether a human must decide.

This report defines V1 issue classes and cleaning policy.

---

## 2. Four-layer model

Every quality problem passes through four separate concepts:

```text
Observation
   ↓
Quality Issue
   ↓
Repair Proposal
   ↓
Repair Decision / Execution
```

Example:

```text
Observation: mobile column contains 4 common patterns
Issue: INCONSISTENT_PHONE_FORMAT
Repair Proposal: normalize to E.164-like canonical format
Decision: approve in analytical target
```

A detector must not silently mutate data.

---

## 3. Issue categories

# A. Structural quality

### A1 — Missing declared primary key

Signal:

- no PK in metadata;
- one or more near-unique candidate columns may exist.

Action:

- propose candidate business key;
- do not alter source.

### A2 — Missing declared foreign key

Signal:

- strong hidden relationship evidence.

Action:

- add relationship to canonical/analytical model, not source DB.

### A3 — Wrong or overly generic physical type

Examples:

- date stored as VARCHAR;
- amount stored as text;
- boolean stored as `Y/N/0/1`.

Action:

- propose typed transform in analytical target.

### A4 — Mixed logical types

Example:

```text
2026-01-10
not available
1404/10/20
```

in one date field.

Action:

- parsing plan + invalid-value quarantine/flag.

---

# B. Completeness

### B1 — Null/missing value

Not all nulls are errors.

Severity depends on semantic role:

- nullable middle_name: likely fine;
- missing order_id: critical;
- missing phone: maybe acceptable.

### B2 — Missing referenced record

Example:

```text
orders.customer_id = 1007
customers has no 1007
```

Classify as orphan reference.

### B3 — Missing historical coverage

If a table starts only in 2025 but related sales date back to 2020, this affects analytical readiness.

V1 can detect suspicious coverage gaps but should not invent missing history.

---

# C. Uniqueness and duplication

### C1 — Exact duplicate rows

Safe to detect automatically.

Do not automatically delete unless the target grain proves the duplication is invalid.

### C2 — Duplicate business key

Example:

```text
customer_code = C100 repeated 3 times
```

May represent:

- bad duplicate;
- valid history/SCD;
- source-system reuse.

Requires context.

### C3 — Probabilistic duplicate entity

Handled through entity resolution.

Do not call this “duplicate row”; it is a different problem.

---

# D. Validity

### D1 — Format invalidity

Examples:

- malformed email;
- impossible date;
- invalid phone length;
- non-numeric amount text.

### D2 — Domain invalidity

Example:

```text
status ∈ {1,2,3}
observed status = 17
```

Only call it invalid if the allowed domain is known or strongly inferred and reviewed.

### D3 — Range anomaly

Example:

```text
quantity = -5000
```

Could be return/correction. Therefore “anomaly” is not automatically “error”.

---

# E. Consistency

### E1 — Representation inconsistency

Example phone:

```text
09121234567
+989121234567
00989121234567
```

### E2 — Categorical alias inconsistency

```text
Active
ACTIVE
1
A
```

Potentially same state, but mapping needs evidence/config.

### E3 — Date/time inconsistency

- multiple date formats;
- timezone ambiguity;
- Persian/Gregorian calendar differences where relevant;
- naive vs aware timestamps.

### E4 — Currency/unit inconsistency

Example:

- Rial vs Toman;
- grams vs kilograms.

This is high risk. No automatic conversion without explicit unit evidence.

---

# F. Referential integrity

### F1 — Orphan foreign references

Measured as count/ratio.

### F2 — Non-unique referenced target

A proposed many-to-one FK points to a target key that is duplicated.

### F3 — Cardinality contradiction

Observed data does not match declared/proposed relationship cardinality.

---

# G. Semantic inconsistency

### G1 — Equivalent columns with different coding

Example:

```text
CRM.gender: M/F
ERP.gender: 1/2
```

### G2 — Same name, different meaning

Example:

```text
orders.status
customers.status
```

Name similarity must not imply semantic equivalence.

### G3 — Different name, same meaning

Handled through schema matching/evidence fusion.

---

# H. Temporal issues

### H1 — Conflicting update times

Different systems disagree and recency determines source authority.

### H2 — Overlapping validity intervals

Relevant for history/SCD candidates.

### H3 — Impossible temporal ordering

Example:

```text
payment_time < order_time
```

Could be timezone/data issue; mark as anomaly unless domain rule is explicit.

---

## 4. Severity model

Recommended severity values:

```text
INFO
LOW
MEDIUM
HIGH
CRITICAL
```

Severity should depend on:

- semantic role;
- affected row percentage;
- analytical impact;
- whether key integrity is compromised;
- whether transformation would lose data.

Do not map “high affected count” directly to “critical”.

---

## 5. Repairability classes

```text
AUTO_SAFE
AUTO_WITH_VALIDATION
REVIEW_REQUIRED
MANUAL_BUSINESS_DECISION
NOT_REPAIRABLE
```

### AUTO_SAFE examples

- trim surrounding whitespace into analytical copy;
- canonicalize clearly equivalent null markers;
- deterministic case normalization for matching-only helper columns.

Even AUTO_SAFE modifies only the analytical/staging target, not source.

### AUTO_WITH_VALIDATION examples

- phone normalization;
- parse unambiguous ISO dates;
- numeric text conversion when parse success is high and failures are quarantined.

### REVIEW_REQUIRED examples

- merge customer aliases;
- map category codes;
- choose source of truth.

### MANUAL_BUSINESS_DECISION examples

- what counts as active customer;
- whether negative transactions are refunds or corruption;
- currency/unit ambiguity.

---

## 6. Cleaning architecture

Never overwrite raw extracted values.

Recommended layers in V1 workspace:

```text
raw_snapshot
    ↓
normalized_helpers
    ↓
clean_canonical
    ↓
analytical
```

Keep raw lineage for each transformed field.

Example:

```text
source_value = "00989121234567"
normalized_value = "+989121234567"
transform = normalize_ir_phone_v1
```

---

## 7. Normalization vs correction

These must be separated.

### Normalization

Changes representation without claiming the original fact was wrong.

Examples:

- whitespace trim;
- case normalization;
- phone canonical format;
- standard timestamp representation.

### Correction

Claims a source value is factually wrong.

Example:

```text
birth_year 1800 → 1980
```

V1 should almost never auto-correct factual values.

---

## 8. Duplicate handling policy

Three levels:

### Row duplicates

Exact physical duplicates.

### Key duplicates

Multiple records with same business identifier.

### Entity duplicates

Different identifiers/records representing one real-world entity.

Each needs a separate detector and repair strategy.

The target analytical model may preserve multiple source records but map them to one canonical entity key.

That is safer than deleting source-equivalent rows.

---

## 9. Quarantine strategy

When a transform cannot safely parse/normalize a value:

```text
valid target rows → analytical pipeline
invalid rows/fields → quarantine artifact
```

Quarantine stores:

- source record reference;
- failing column;
- raw value;
- transform attempted;
- failure reason.

No silent coercion to NULL unless policy explicitly allows it.

---

## 10. Data quality score

A single “78/100” score is useful for UI but dangerous if treated as objective truth.

V1 should first report a **vector**:

```yaml
completeness: 0.92
uniqueness: 0.88
validity: 0.84
consistency: 0.71
referential_integrity: 0.64
```

An overall score may be computed later using a documented weighting policy.

Never hide the vector behind the aggregate score.

---

## 11. Suggested V1 detectors

Priority 1:

- null ratio;
- distinct/duplicate ratios;
- candidate-key violations;
- hidden relationship/orphan rates;
- invalid parse rates;
- mixed string/numeric/date patterns;
- exact duplicate rows;
- common phone/email/date normalization opportunities;
- category inconsistency;
- referential integrity.

Priority 2:

- outliers;
- unusual temporal gaps;
- unit/currency inference;
- advanced drift detection.

---

## 12. Critical review applied before approval

### Problem A — “Clean” can destroy valid business events

Negative amounts, duplicated keys and unusual values may be legitimate.

**Correction:** anomaly detection and factual correction are separate; ambiguous cases require review.

### Problem B — Automatic deduplication could erase history

**Correction:** distinguish row/key/entity duplicates and prefer canonical mapping over deletion.

### Problem C — Overall data-health score creates false precision

**Correction:** preserve dimension-level quality scores; aggregate score is optional and documented.

### Problem D — Failed parse → NULL would hide data loss

**Correction:** quarantine failures with source lineage.

### Problem E — Source data should not be modified

**Correction:** all cleaning happens in extracted/analytical copies.

**Status: APPROVED AS V1 DATA-QUALITY TAXONOMY.**
