# Step40 Developer Experience Review

Status: `PASS_G14`

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
The DX bootstrap was then repaired to reuse an already-active exact interpreter
from CI/host when available, while retaining the fail-closed uv provisioning
path for hosts that do not have the pin.

G14 closure evidence:

- final Step40 content head: `3b3d11276989f8bea05d83124de02e651fb86d4a`;
- exact clean-checkout CI run: `35448760078`;
- remote result: `SUCCESS`; all upstream G8-G13 jobs and the Step40 job passed;
- Step40 job `105922343522`: pinned Python/Node setup, locked tooling,
  bootstrap, Step40 validator, and clean-worktree verification all passed;
- bootstrap was executed twice in the clean checkout, proving the intended
  idempotent path; the second run reused the exact active project interpreter;
- the validator covered version/help/config, doctor, CLI negative controls,
  deterministic demo, compile checks, OpenAPI verification, npm diagnostics,
  project-local filesystem boundaries, optional profiles, and protected-path
  preservation;
- the supported clean-room regression passed after the historical Step32/33
  fixture repairs; the protected quality-artifact path was not read or changed.

Required evidence before closure:

- clean checkout bootstrap and second-bootstrap idempotency;
- `doctor`, `demo`, CLI negative controls, and bounded npm diagnostics;
- deterministic OpenAPI verification with no tracked frontend mutation;
- focused and supported unfiltered test suites;
- clean-room G8 through G13 CI run on the accepted Step40 SHA: PASS;
- fresh-checkout Step40 validator result and protected-artifact preservation:
  PASS.

Final state: `G14=PASS`, `G15=PENDING`, `step40_started=true`,
`step40_status=COMPLETED_DEVELOPER_EXPERIENCE_G14_PASS`,
`step41_started=false`, `step41_status=NOT_STARTED`.

## Targeted post-Step40 filesystem-boundary repair

The accepted Step40 CLI had one independently audited defect: an explicit
`--state-root` could make the real demo backend and `demo-result.json` outside
the project-local `.ddo/` authority. The repair resolves both the authority
and requested path, applies Path containment semantics, and rejects external
absolute paths, traversal, symlink escapes, invalid path types, and non-directory
targets before backend construction or runtime writes.

Repair evidence:

- repaired content head: `354030a90fd4148c74151426bd9102dc4f2a20ec`;
- default state: `<repository>/.ddo/demo/`;
- valid custom state: only beneath `<repository>/.ddo/`;
- focused Step40 suite: `10 passed`;
- actual CLI negative controls: external absolute path, traversal, symlink escape,
  actionable failure, and no external artifact creation;
- supported unit tests: `245 passed` excluding the protected quality-engine test;
- security/red-team tests: `65 passed`;
- default repository validators: `37/39`; the two host-only failures were
  Step30/Step31 OpenAPI checks requiring unavailable local Python `3.11.16`;
- exact pinned remote CI run: `35464747467`, result `SUCCESS`;
- Step40 job: `105965189041`; bootstrap, validator, boundary controls and
  clean-worktree verification passed;
- protected `tests/quality_unit_artifacts/` remained unread and untouched;
- G14 remains `PASS`, G15 remains `PENDING`, and Step41 remains `NOT_STARTED`.
