# Logging, Debug and Export Safety

Structured values must pass through the recursive privacy sanitizer before
logging. Nested mappings and sequences are traversed; secret-like keys are
fully redacted, and email, phone and credential-like string forms are masked.
Raw values are not an acceptable debug field.

Debug bundles reject raw-staging artifacts. Exports are aggregate/metadata
only unless a later, explicit policy provides a safe transformed representation.
The Step10 canary test scans sensitive values in staging, profile/quality-like
payloads, sanitized logs, debug and external payload decisions. A clean scan
means no value was observed outside the allowed restricted-staging exception;
it is not a claim of complete repository-wide DLP.
