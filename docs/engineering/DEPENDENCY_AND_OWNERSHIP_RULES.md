# Dependency and Ownership Rules

Project-owned contracts are the stable center. External engines are optional implementation details behind the eleven declared ports. A dependency is admitted only after license, security, compatibility, version-pinning, resource and deletion/replacement review.

Ownership is single-primary with explicit reviewers. Component, port and stage ownership is frozen in `specs/ownership_map.yml`. Shared code must be justified as a contract or infrastructure dependency; it must not become an unowned utility layer.

The ControlStore is not a raw-data warehouse. ArtifactStore is the immutable large-artifact plane. No stage may bypass the declared ports. Research material under `research/oss` is documentation only and must be deletable without changing runtime imports.
