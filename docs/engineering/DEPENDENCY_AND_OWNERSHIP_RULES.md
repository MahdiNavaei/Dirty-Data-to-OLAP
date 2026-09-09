# Dependency and Ownership Rules

Project-owned contracts are the stable center. External engines are optional implementation details behind the eleven declared ports. A dependency is admitted only after license, security, compatibility, version-pinning, resource and deletion/replacement review.

Ownership is single-primary with explicit reviewers. Component, port and stage ownership is frozen in `specs/ownership_map.yml`; component implementation fields in `architecture/specs/components.yml` must match it. `domain.contracts` and `composition.root` are Step06 bootstrap-only concerns, not permanent DBA ownership. Semantic contract families remain with their named later specialists.

The ControlStore is not a raw-data warehouse. Step06 may establish the minimal metadata/control schema and port; Step23 owns comprehensive platform hardening. ArtifactStore is the immutable large-artifact plane. No stage may bypass the declared ports. Research material under `research/oss` is documentation only and must be deletable without changing runtime imports.
