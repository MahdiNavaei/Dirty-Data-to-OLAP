# Privacy and Local Provider Policy

Semantic AI requires the explicit purpose `SEMANTIC_AI_LOCAL_ANALYSIS`, local-only processing, no external processing, no raw staging access and verified loopback transport. Authorization binds the request, task, manifest fingerprint, evidence references, provider, model digest and prompt version. A request is not authorization. Non-loopback and redirect paths fail closed.
