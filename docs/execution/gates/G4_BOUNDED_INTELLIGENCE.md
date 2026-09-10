# G4 Bounded Intelligence

G4 is PASS only when boundedness, privacy authorization, structured-output validation, failure containment, adversarial safety and a real local provider execution are evidenced together. Step16 closure satisfies those conditions using Ollama loopback runtime 0.31.1 with the installed `qwen2.5:7b` model and verified digest `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.

## Closure evidence

- Prompt provenance hashes the exact UTF-8 bytes sent in the provider system/task messages.
- Context is recursively minimized; requested, provided and provider-allowed references are separate and exact.
- Only the verified local model identity is allowed; non-loopback, redirect, proxy, timeout and model-change paths fail closed.
- All request budgets are enforced, including hypotheses and disabled retries. Provider statement, rationale and limitations are candidate-only and action-safe.
- Semantic artifacts are byte-hashed over their final nonrecursive envelope, written atomically, and referenced by project-relative locations under the project root.
- The executable aggregate-safe fixture contains 11 cases, without ground-truth answers: identifier, ambiguity, context conflict, structural/lexical conflict, Persian assertion/no-assertion, misleading metadata, injection and insufficient context controls.
- Live benchmark evaluation `step16-semantic-safety-v2`: schema-valid rate `10/11`; reference-valid rate `1.0`; forbidden actions `0`; hallucinated references `0`; privacy canaries `0`; prompt-injection escapes `0`; provider failures contained `1`.
- Live repeatability observation: exactly 3 calls; schema-valid rate `1.0`; exact structured response `1.0`; hypothesis-kind agreement `1.0`; reference agreement `1.0`. This is a repeatability observation, not a determinism claim.

G5 remains PENDING.
