# Step40 Developer Experience Review

Status: `PENDING_G14_EVIDENCE`

Step40 adds one canonical project-local developer interface at
`src/dirty_data_to_olap/devx.py`, with a source-checkout shim at
`tools/ddo.py`. The interface owns bounded bootstrap, doctor, version, safe
configuration, deterministic demo, check, frontend diagnostics, OpenAPI
verification, and the existing local API serve entrypoint.

The implementation preserves the accepted product composition root. The demo
uses the real FastAPI control-plane boundary for health, configuration, run
creation, and run read; it does not write to an upstream source system. OpenAPI verification generates candidates
under `.ddo/tmp/` and compares them with the tracked contract. Optional
matching, profiling, entity-resolution, and ML profiles remain explicit.

The host-local attempt to bootstrap was intentionally fail-closed because the
available uv index did not provide the exact pinned CPython `3.11.16` artifact.
That is recorded as a local prerequisite blocker, not upgraded to a successful
bootstrap. G14 remains pending until the clean-checkout and remote CI evidence
is executed on the accepted Step40 content.

Required evidence before closure:

- clean checkout bootstrap and second-bootstrap idempotency;
- `doctor`, `demo`, CLI negative controls, and bounded npm diagnostics;
- deterministic OpenAPI verification with no tracked frontend mutation;
- focused and supported unfiltered test suites;
- clean-room G8 through G13 CI run on the accepted Step40 SHA;
- fresh-checkout Step40 validator result and protected-artifact preservation.
