# Structured Output and Reference Validation

Ollama is called with `stream=false`, a JSON schema, temperature zero, an explicit seed and a bounded output token count. Provider JSON is parsed strictly, normalized deterministically, checked for candidate-only language and validated against the exact manifest evidence references. Native provider responses are not persisted.
