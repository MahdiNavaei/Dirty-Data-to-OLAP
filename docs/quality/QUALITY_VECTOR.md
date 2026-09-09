# Quality Vector

QualityResult publishes five independent dimensions:

`completeness`, `uniqueness`, `validity`, `consistency` and
`referential_integrity`.

Each dimension reports `MEASURED`, `UNMEASURED` or `INCONCLUSIVE`, measurement
semantics, applicable/evaluated rule counts, measured rows, affected records,
affected ratio when meaningful, and issue references. A dimension with no
explicit rule is `UNMEASURED`; an incomplete prerequisite is not `MEASURED`.

There is no overall quality score, weighted grade or gate derived from this
vector. Consumers must preserve the vector and inspect the rule-level evidence,
failures and scope before taking policy action.
