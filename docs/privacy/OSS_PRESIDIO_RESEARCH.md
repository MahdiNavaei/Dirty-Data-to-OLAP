# Presidio OSS Research Record

Repository: `https://github.com/microsoft/presidio`

Reviewed revision: `a7b17c75f3098b92b369f0b01855519f1cd5e8cc`

License: MIT, verified from the repository `LICENSE` file.

The Step10 review inspected `AnalyzerEngine`, `RecognizerResult`,
`PatternRecognizer`, `RecognizerRegistryProvider` and the anonymizer engine,
mask and redact operators. Tests inspected included analyzer-engine,
pattern-recognizer, recognizer-result, registry-provider, anonymizer-engine,
mask and redact tests. The source shows a registry/configuration boundary,
pattern recognizers producing span/score results, and operator-driven masking,
replacement and redaction. These are useful reference concepts, but their
native objects and score semantics do not become Dirty Data to OLAP contracts.

Decision: reference-only for Step10. The project uses its own deterministic
classification, fail-closed masking and HMAC-SHA256 pseudonymization boundary;
Presidio is not a runtime dependency and no vendor code is copied. The
temporary shallow clone under `research/oss/presidio` was deleted before final
regression.
