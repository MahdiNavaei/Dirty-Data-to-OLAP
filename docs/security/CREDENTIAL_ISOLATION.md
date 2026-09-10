# Credential Isolation

Persisted contracts carry only a credential reference. Runtime credentials
are short-lived adapter-local values and are never placed in source models,
catalogs, snapshots, exceptions, `repr`/`str`, logs or security artifacts.

`SOURCE_READ_ONLY`, `TARGET_WRITE`, `ADMINISTRATION` and `TEST_ONLY` are
distinct purposes. A resolver call is scoped by profile, required purpose and
source ID; wrong-purpose or cross-source credentials fail closed. A target or
administration credential can never satisfy a source assurance.
