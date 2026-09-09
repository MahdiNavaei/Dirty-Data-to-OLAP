# Dirty Data to OLAP — Null and Unknown Semantics

Absence-like values are distinct architectural states. The smallest required set is:

| State | Meaning | Analytical treatment |
|---|---|---|
| `SOURCE_NULL_MISSING` | Source explicitly has no value or a null marker. | Preserve source state and lineage; do not infer why it is absent. |
| `NOT_CAPTURED` | The source/process did not capture this attribute. | Distinct from a known unknown; may require review for required fields. |
| `UNKNOWN` | The concept exists or is expected, but its value is not known. | May be represented only through an explicit approved analytical policy. |
| `NOT_APPLICABLE` | The attribute does not apply to the subject/event. | Must not be counted as missingness without policy. |
| `INVALID_UNPARSEABLE` | A value exists but cannot be safely parsed or normalized. | Preserve raw value; quarantine or review; never silently coerce. |
| `WITHHELD_REDACTED` | A value is intentionally unavailable for privacy or policy reasons. | Preserve reason/state without exposing the value. |
| `QUARANTINED_UNACCEPTED` | A proposed transform or record is not accepted for analytical use. | Excluded only with record accounting and reason. |

Canonicalization or materialization may map states only through an explicit, versioned policy. It must not collapse all states to a silent NULL.

## Unknown members and orphans

There is no implicit `-1`, `0` or `UNKNOWN` dimension member. A later model policy must explicitly choose reject/quarantine, an approved UNKNOWN member, or nullable linkage for a given fact/dimension relationship.

For the reference benchmark, valid Orders require Customer and Branch and valid OrderLines require Order and Product. Injected orphan relationships are corruption/evaluation cases and must not be silently legitimized by an UNKNOWN member.
