# Masking and Pseudonymization

Masking is a one-way exposure transformation for logs, debug views and
exports. V1 uses category-specific full redaction (`<REDACTED_EMAIL>`,
`<REDACTED_PHONE>`, `<REDACTED_SECRET>`) and a generic replacement. Masking is
fail closed: type errors or transformation failures raise a privacy failure
and never return the original value.

Pseudonymization uses HMAC-SHA256 with a runtime-only key. The key is supplied
at the call boundary, while contracts carry only a key reference, scope and
version. The key is never serialized or logged. Scope and version bind output
meaning and permit planned rotation; an unavailable key is an explicit
failure. Plain SHA-256 of a value is not pseudonymization and is not used as a
fallback. This layer makes no anonymization claim: HMAC outputs remain
linkable by design.
