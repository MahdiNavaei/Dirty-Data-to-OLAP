# Architecture Enforcement

Enforcement is split into checks that are active now and checks that activate when implementation exists. The current repository validators enforce frozen domain, data and software contracts. Future checks enforce Python imports, vendor boundaries, port usage, lifecycle transitions, review compatibility, read-only source access, record accounting, grain guards, secrets and bounded memory.

`specs/architecture_enforcement.yml` is the enforcement register. A violation is blocking when it can change contract meaning, bypass a review guard, write to a source, lose provenance, or misstate release evidence. Static success never upgrades absent runtime evidence.
