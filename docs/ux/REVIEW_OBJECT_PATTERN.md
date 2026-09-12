# Reusable Review Object Pattern

Each review card and detail panel uses the same field order. A card may summarize, but never omit the presence of a required field.

## Required fields

| Field | Required meaning |
|---|---|
| Subject | exact subject kind, subject ID, stage, endpoints/record set/plan/target |
| State | lifecycle state plus whether it is actionable, blocked, stale, or invalidated |
| Meaning | observation, hypothesis, human decision, accepted canonical semantics, analytical plan, materialized output, or validation |
| Risk | impact if accepted, rejected, deferred, stale, or exposed |
| Evidence | supporting refs, contradicting refs, missing refs, unavailable/failed/privacy-blocked producer refs |
| Conflicts | explicit conflict IDs, type, severity, explanation, and resolution state |
| Missing | required evidence not observed or not configured, with reason and scope |
| Scope | run, snapshot, table/column, observation mode, full/bounded/sample and counts/denominators |
| Provenance | source IDs, snapshot IDs, schema fingerprints, producer/policy/model version, lineage refs |
| Downstream | guarded stage, dependents, what is blocked or invalidated by each action |
| Technical details | raw metric name/semantics, normalized signal, spec hash, SQL hash or validation check only when relevant |
| Actions | available action, required rationale, confirmation and exact consequence |
| Freshness | content hash, reviewed-at, stale trigger, superseded-by/invalidation link |

## Evidence ledger

Evidence is rendered as typed rows, not as an undifferentiated confidence paragraph:

- **Supports** — references and producer state.
- **Contradicts** — references, conflict type, severity and explanation.
- **Missing** — required but not observed, not configured or insufficient.
- **Unavailable / failed / incomplete / privacy-blocked** — distinct producer states and recovery path.
- **Scope** — full snapshot, observed scope, bounded sample, estimated method or unmeasured.
- **Human assertion** — actor, actor source, rationale and timestamp, visibly separate from model/producer evidence.

Reference IDs resolve to a safe evidence detail view. Raw PII is masked or withheld under the privacy policy.

## Exact review envelope

The review envelope mirrors `ReviewDecision`: checkpoint, subject stage, artifact ID, content hash, schema version, model version, source schema fingerprints, policy version, domain assertion refs, subject semantic ID, applicability fingerprint, decision, reviewed-at, actor, actor source, rationale, supersession and invalidation metadata. A reviewer cannot submit a decision while the exact subject context is missing.
