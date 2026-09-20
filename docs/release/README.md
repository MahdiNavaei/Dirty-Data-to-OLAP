# V1 release documentation

This release documentation describes the current repository at version
`0.1.0`. It is a bounded local/reference release record, not a deployment
approval or universal production-readiness statement.

- [Claim/evidence matrix](CLAIM_EVIDENCE_MATRIX.md)
- [Limitations](LIMITATIONS.md)
- [Changelog](CHANGELOG.md)
- [OSS reuse and license status](OSS_AND_LICENSE.md)
- [Gate map](../engineering/RELEASE_GATE_MAP.md)
- [G15 receipt](../execution/gates/G15_RELEASE.md)
- [Step41 review](../execution/STEP41_TECHNICAL_WRITER_REVIEW.md)

## Release boundary

The released documentation covers the implemented local product path, the
tested source/adapter boundary, review-gated canonical and OLAP behavior,
bounded API/auth contracts, project-local developer experience, and the
evidence classes behind G0-G14. G15 is PASS only after the final evidence
receipt and exact-head CI succeed.

No tag, deployment, package publication, or external release action is
performed by this workflow.
