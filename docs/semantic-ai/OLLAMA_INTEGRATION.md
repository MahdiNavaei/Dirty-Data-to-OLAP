# Ollama Integration

The Step16 adapter uses the installed local Ollama HTTP API only. The verified runtime is 0.31.1 and the G4 model is `qwen2.5:7b`, digest `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`. The adapter resolves the digest before and after generation and emits explicit model-identity failure if it changes.
