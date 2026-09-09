# Open-Source Research and Reuse Ledger

This ledger records meaningful external research or reuse performed for Dirty Data to OLAP.

Research clones belong only under `research/oss/` and are never runtime dependencies. Direct code reuse requires inspection, license compliance, attribution where required, and adaptation behind project-owned contracts. Where direct reuse is not permitted or appropriate, implementation must be written independently from the studied behavior.

No external repository was cloned or inspected during Bootstrap / Prompt 0, so no project-specific reuse entry is asserted here.

## Entry fields

| Field | Required meaning |
|---|---|
| Project | External project name |
| Repository URL | Authoritative source URL |
| Reviewed version / commit | Exact inspected revision |
| License | License observed during review |
| Role | Intended role in Dirty Data to OLAP |
| Use type | Direct code reuse or conceptual research |
| Affected component | Project-owned component affected |
| Attribution | Required notices or attribution |
| Runtime clone required | Must always be `false` |
