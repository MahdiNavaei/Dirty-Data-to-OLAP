# Source Database Security Matrix

| Provider | Security posture in V1 | Verification status |
|---|---|---|
| SQLite | URI `mode=ro`, query-only, authorizer, guard, narrow API, source hash/semantic immutability | LIVE reference fixture tested |
| PostgreSQL | dedicated role policy, CONNECT/USAGE/SELECT, verifier required, RLS/PUBLIC/security-definer review | NOT LIVE VERIFIED |
| MySQL | dedicated SELECT/metadata role policy, verifier required | NOT LIVE VERIFIED |
| MariaDB | dedicated SELECT/metadata role policy, verifier required | NOT LIVE VERIFIED |
| SQL Server | dedicated CONNECT/SELECT role policy, verifier required | NOT LIVE VERIFIED |
| Oracle | deferred by V1 scope | DEFERRED |

No provider row is upgraded to live verification by static policy checks.
