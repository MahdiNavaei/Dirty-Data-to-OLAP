# V1 Data Handling Policy

Status: implemented by Specialist Step10. This is an engineering control
policy, not a legal or regulatory compliance certification.

Raw source values may be read only through the existing bounded source/staging
path. Source-faithful staging is restricted and may retain raw values only as
the pinned input needed for reproducible profiling and quality work. Raw values
are not copied into catalog, profile, quality, debug, export or external
payloads. Source systems remain read-only.

The project-owned privacy policy service classifies fields without persisting
the inspected value. A missing detector match is `UNKNOWN`, never public or
explicitly non-sensitive. Explicit rules can confirm a category; deterministic
patterns produce potential sensitivity. Artifact sensitivity is conservative.

This pass does not implement encryption, authentication, authorization,
database grants, KMS, or an ArtifactStore. Those controls remain later work.
# Step11 hardening note

Unknown scan values fail closed outside restricted source-faithful staging;
numeric identifiers are not aggregate-safe without the explicit metric
contract; and runtime behavior is loaded from the versioned privacy config.
