# Repair Proposals

Step09 may propose safe, reversible transformations, but does not approve or
execute them. Proposal target layers are controlled derived copies or explicit
quarantine artifacts; operational source data is outside the write boundary.

Current proposal types are trim-whitespace normalization, explicit expected-type
parsing where the rule supplies the operation, and exact-duplicate handling.
Duplicate handling is always review-required because deletion or survivor choice
can change business meaning. Domain violations, requiredness failures, FK
orphans and ambiguous patterns require manual business decisions or have no
automatic proposal.

Every proposal carries affected record references, a transform ID/version,
preconditions, risk notes, lineage requirements, row-accounting requirements and
a `RepairValidationPlan`. Proposal status is `PROPOSED`; there are deliberately
no approved/applied/executed fields in this contract.

No rule receives an automatic quarantine proposal by default. A proposal is
created only when the rule explicitly authorizes a known deterministic
operation or an explicit quarantine policy, and manual or non-repairable rules
never receive one.
