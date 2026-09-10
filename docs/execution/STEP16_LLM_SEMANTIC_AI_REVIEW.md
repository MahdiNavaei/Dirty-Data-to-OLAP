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
