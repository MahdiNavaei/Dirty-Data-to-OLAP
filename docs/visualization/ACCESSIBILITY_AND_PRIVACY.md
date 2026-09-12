# Accessibility and privacy

Every rendered graph node has one `AccessibleGraphRow` with a label, description, and related visual IDs. Legends explain shape, line style, state, evidence, reliability, scope, review, and conflict fields in text. This is a nonvisual semantic equivalent for a future renderer; it is not a browser accessibility audit.

`VisualLabel` rejects email-shaped values, secret-like key/value material, and long numeric identifiers. Sensitive or redacted labels must be explicit, and profile points are not transferred for redacted profiles. The contracts contain only safe references and aggregate/observational metadata, not raw PII, raw values, raw SQL, credentials, or secrets.
