# Step16 LLM / Semantic AI Review

Step16 implements an optional local Ollama semantic-evidence producer. The implementation is aggregate-only, candidate-only and fail-closed at the privacy, provider, schema and reference boundaries. No raw values, staged readers, native provider responses, chain-of-thought, executable actions or acceptance decisions are emitted.

## Evidence

- local runtime and model identity are captured in `SemanticProviderReference`;
- prompt, schema and context-builder hashes are captured;
- context manifests are raw-value-free and bounded;
- provider failure, invalid JSON, unknown references, non-loopback endpoints and model identity changes are explicit failures;
- the real integration test executes against loopback Ollama; repeatability is reported as observation.

## Step16 Upstream Evidence Hardening

Before semantic evidence was enabled, Step15 was hardened to separate matcher families, add explicit missing indicators, remove the weighted baseline, use grouped ranking splits, expose held-out metrics, retain label-shuffle and reverse-pair leakage controls, avoid one-fit stability claims, use rank-based active-learning disagreement, require held-out calibration sufficiency, bind model identity to configuration/dataset/split/library fingerprints, support unlabeled JSON-artifact inference, and keep artifacts under the project test-temp boundary. Step14 term-frequency reporting is now fail-closed unless the official Splink comparison configuration actually succeeds.

## Post-Step16 G4 Integrity Closure

- starting_head: `b2a780e4219bbd7a8e375090342b267bd844a3e8`
- repaired: exact prompt-byte provenance; recursive generic-description privacy minimization; requested/provided/allowed reference binding; verified local-model identity; all semantic budgets and truthful retry semantics; statement/rationale/limitations safety; provider-independent hypothesis IDs; byte-accurate artifact hashes and project-relative artifact locations; executable 11-case aggregate-safe benchmark; behavioral validators; Step15 matcher, split, stability, model-identity, label-free artifact inference, schema and tamper hardening
- adversarial_controls: 20 required local behavioral cases executed; malformed JSON/schema, unknown references, digest changes, non-loopback, redirect, proxy, timeout, injection, permutation and artifact-tamper paths are covered
- real_provider: Ollama 0.31.1, `qwen2.5:7b`, verified digest above; benign, injection and ambiguity cases executed; one provider failure remained explicitly contained with no evidence or authority output
- evaluation: aggregate `SemanticSafetyEvaluation` actually produced; no forbidden actions, hallucinated references, privacy canaries or prompt-injection escapes; repeatability exactly 3 calls with all reported agreement rates `1.0`
- state: Step16 complete; G4 PASS; G4A PASS; Step17 is the next handoff and its implementation was not started
