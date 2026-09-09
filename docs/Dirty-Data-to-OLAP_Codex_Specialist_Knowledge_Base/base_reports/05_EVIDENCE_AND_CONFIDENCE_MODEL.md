# 05 — Evidence & Confidence Model Report

## 1. Purpose

This report defines the central reasoning layer of Dirty Data to OLAP.

Third-party engines can answer narrow questions:

- DataProfiler: what does a column look like?
- Desbordante: what dependencies exist in values?
- Valentine: which columns appear similar?
- Splink: which records are likely the same entity?

None of these alone should decide:

> “`orders.client_no` is the foreign key to `customers.customer_code`.”

Dirty Data to OLAP must combine evidence, represent conflict and make a decision whose reasoning can be inspected.

---

## 2. Core principle

Confidence is not the same thing as raw similarity.

A schema matcher score of `0.94` does not mean a mapping is 94% true.

Therefore V1 distinguishes:

1. **evidence value** — output of an observation/algorithm;
2. **evidence reliability** — how much that evidence family should be trusted in this context;
3. **decision score** — combined ranking signal;
4. **confidence band** — operational category;
5. **decision state** — accept/review/reject.

The initial V1 score should be treated as a **calibratable ranking score**, not a statistically valid posterior probability until benchmark calibration proves otherwise.

This avoids a common and serious mistake: presenting heuristic scores as probabilities.

---

## 3. Evidence families for relationship inference

Consider candidate:

```text
orders.client_no → customers.customer_code
```

Potential evidence:

### E1 — Declared metadata

If a real DB FK is declared, this is very strong evidence, but the data may still violate it in extracted snapshots.

Fields:

- constraint exists;
- source engine;
- constraint columns.

### E2 — Inclusion coverage

```text
coverage = matched_non_null_source_values / distinct_non_null_source_values
```

Example: `0.974`.

### E3 — Orphan rate

```text
orphan_rate = source values with no target match / relevant source values
```

Relationship with 30% orphans should not receive the same score as 0.2% orphans.

### E4 — Target uniqueness

A many-to-one FK target should usually be unique or nearly unique on the referenced key.

### E5 — Type compatibility

Examples:

- UUID ↔ UUID: strong
- integer ↔ integer: compatible
- phone-like string ↔ customer ID integer: conflict

### E6 — Name similarity

Useful but weak alone.

Examples:

- `customer_id` ↔ `customer_id`: strong naming evidence
- `cust_code` ↔ `client_no`: moderate semantic evidence

### E7 — Instance/schema matching

Valentine output provides another signal.

### E8 — Cardinality compatibility

Observed distributions should support the proposed relationship type.

### E9 — Semantic type compatibility

If both columns look like customer identifiers, evidence increases.

### E10 — Human assertion

A reviewer can mark a relationship authoritative. This must remain visible as human evidence and not be disguised as algorithmic discovery.

---

## 4. Candidate generation vs candidate acceptance

To avoid combinatorial explosion, use a two-stage model.

### Stage A — Candidate generation

Cheap filters generate plausible pairs using:

- compatible physical types;
- names/tokens;
- table-size/cardinality heuristics;
- matcher top-K;
- declared metadata;
- IND engine outputs.

High recall is preferred here.

### Stage B — Evidence fusion and acceptance

More expensive checks compute:

- exact/approximate inclusion;
- target uniqueness;
- orphan rate;
- sample/full validation;
- semantic type compatibility;
- cardinality.

High precision is preferred here.

---

## 5. Initial scoring model

V1 should start with an explicit, inspectable weighted model rather than a learned black box.

Example normalized score:

```text
relationship_score =
    w_declared    * declared_fk_signal
  + w_inclusion   * inclusion_signal
  + w_unique      * target_uniqueness_signal
  + w_semantic    * semantic_match_signal
  + w_type        * type_compatibility_signal
  + w_cardinality * cardinality_signal
  + w_name        * name_signal
  - w_orphan      * orphan_penalty
  - w_conflict    * conflict_penalty
```

Initial weights are hypotheses and **must be calibrated on benchmark data**.

Recommended qualitative priority before calibration:

```text
Declared constraint / verified inclusion / uniqueness
    > instance-semantic matching
    > type compatibility / cardinality
    > name similarity alone
```

Do not hard-code a polished-looking set of decimal weights before evaluation. Store weights in a versioned policy file such as:

```text
policies/relationship_scoring_v1.yml
```

---

## 6. Reliability adjustment

Evidence reliability depends on scope.

Example:

```text
inclusion = 0.99 measured on full data
```

should be stronger than:

```text
inclusion = 0.99 measured on a 500-row sample
```

A simple V1 approach:

```text
adjusted_signal = normalized_value × reliability(scope, sample_size, algorithm)
```

Reliability factors should themselves be transparent and benchmarked.

---

## 7. Conflict representation

A decision must never hide contradictory evidence.

Example:

```text
Valentine similarity:       HIGH
Name similarity:            HIGH
Type compatibility:         HIGH
Inclusion coverage:         LOW (0.31)
Target uniqueness:          LOW (0.42)
```

This should become:

```text
state = REVIEW_REQUIRED
conflict = SEMANTIC_STRUCTURAL_CONFLICT
```

not an average score that happens to equal `0.76`.

Define conflict classes:

- `SEMANTIC_STRUCTURAL_CONFLICT`
- `DECLARED_DATA_CONFLICT`
- `TYPE_SEMANTIC_CONFLICT`
- `SAMPLE_FULLSCAN_CONFLICT`
- `MULTIPLE_TARGET_AMBIGUITY`

---

## 8. Confidence bands

Initial bands are operational, not probabilistic:

```text
HIGH        → candidate may auto-accept if no conflict
MEDIUM      → human review recommended/required
LOW         → reject or keep as weak candidate
CONFLICTED  → human review required regardless of score
```

Numerical thresholds should be chosen after baseline benchmark results.

If temporary thresholds are needed for early development, mark them explicitly as `UN CALIBRATED` in configuration and UI.

---

## 9. Auto-accept policy

A relationship may auto-accept only if:

1. score is in HIGH band;
2. no hard conflict exists;
3. target key uniqueness is compatible with proposed cardinality;
4. source/target type semantics are compatible;
5. observed orphan rate is below policy threshold or explained;
6. evidence scope meets minimum quality requirements.

For example, a high semantic match alone must never auto-accept.

---

## 10. Schema-equivalence evidence

For:

```text
crm.customer_code ≈ erp.client_no
```

signals may include:

- Valentine matcher scores;
- normalized name embeddings/tokens;
- semantic type match;
- value overlap after normalization;
- relationship neighborhood similarity;
- candidate key behavior;
- cross-source entity-resolution coherence.

An important higher-order signal is **relationship neighborhood**.

If both columns participate in structurally similar customer→order relationships, semantic equivalence becomes more plausible.

This is a Dirty Data to OLAP-owned feature and worth implementing after the baseline.

---

## 11. Entity-resolution confidence

Splink match probabilities should be stored as Splink evidence, not copied directly into Dirty Data to OLAP relationship confidence.

Entity-resolution decisions need separate policies:

- pairwise match threshold;
- clustering behavior;
- transitive-link risk;
- minimum strong identifiers;
- conflict rules for impossible attributes.

Example hard conflict:

- same normalized phone but clearly different national IDs where both IDs are trusted.

Such records should not merge automatically even if fuzzy-name similarity is high.

---

## 12. Canonical attribute source selection

After records are linked, Dirty Data to OLAP still needs to choose values.

Do not simply choose the most common value.

Candidate authority score may use:

- source priority configured by user;
- recency;
- non-nullness;
- validation quality;
- agreement among sources;
- trusted-source flags.

Example:

```text
CRM phone updated yesterday
ERP phone updated 4 years ago
```

A source-policy may prefer the CRM value.

The reason must be stored.

---

## 13. Evidence provenance

Every evidence item stores:

- engine;
- version/commit where applicable;
- config hash;
- source/snapshot scope;
- run ID;
- sample information;
- raw metric;
- normalized metric if transformed.

This makes decisions replayable and debuggable.

---

## 14. Calibration strategy

Once the benchmark suite exists:

1. generate candidate relationships;
2. label true/false from ground truth;
3. evaluate each signal independently;
4. inspect false positives/negatives;
5. tune policy weights/thresholds;
6. plot reliability/calibration if score is to be called probability;
7. freeze a versioned policy.

Possible later upgrade:

- logistic regression / gradient boosting evidence combiner.

But only after a meaningful labeled benchmark exists. A learned model with 50 synthetic relationships would create false sophistication.

---

## 15. Explainable decision output

Example final output:

```yaml
relationship:
  from: sales.orders.client_no
  to: crm.customers.customer_code
  cardinality: MANY_TO_ONE
state: ACCEPTED
confidence_band: HIGH
score: 0.94
score_semantics: uncalibrated_ranking_score
supporting_evidence:
  inclusion_coverage: 0.974
  target_uniqueness: 0.9998
  semantic_match: 0.89
  type_compatibility: 1.0
contradicting_evidence:
  orphan_rate: 0.026
explanation:
  - nearly all source identifiers occur in the target key
  - target behaves as a unique customer identifier
  - schema matcher independently links both columns
```

This is much more trustworthy than:

```text
AI says relationship = 94%
```

---

## 16. Critical review applied before approval

### Problem A — Treating heuristic score as probability

**Correction:** V1 score is explicitly called a ranking/decision score until calibrated.

### Problem B — Weighted averages can hide contradictory evidence

**Correction:** hard conflict classes override score bands.

### Problem C — Sampling can create false confidence

**Correction:** evidence reliability incorporates observation scope and full validation can be required before auto-accept.

### Problem D — Name similarity is dangerously seductive

**Correction:** name/LLM semantic evidence is weak unless supported by values/structure.

### Problem E — Learned fusion model too early would be fake rigor

**Correction:** start transparent; learn only after enough labeled benchmark data exists.

### Problem F — Human overrides could become invisible magic

**Correction:** human assertions are explicit evidence with provenance.

**Status: APPROVED AS V1 EVIDENCE-FUSION DESIGN.**
