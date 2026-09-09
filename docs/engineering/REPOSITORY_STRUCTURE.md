# Repository Structure

The following is the planned implementation topology. Paths marked future do not exist yet and are not represented as implemented functionality.

```text
src/dirty_data_to_olap/                 # future runtime namespace
  domain/contracts/                     # future project-owned schemas and IDs; Step06 only bootstraps primitives
  application/                          # future stage services and lifecycle
  adapters/                             # future dlt/DataProfiler/etc. boundaries
    sources/sql/                        # future Step06 DB substrate; internal to source adapter boundary
  persistence/                          # future ControlStore/ArtifactStore ports
  runtime/                              # future executor and capability registry
  entrypoints/                          # future CLI/API
  composition/                          # future dependency composition
tests/{unit,contract,architecture,...}/ # future test tiers
migrations/                             # future control-store migrations; Step06 bootstrap only, Step23 hardening
docs/product/                           # frozen product truth
docs/domain/                            # frozen domain truth
docs/data-architecture/                # frozen logical data architecture
docs/architecture/                     # frozen software architecture
docs/engineering/                      # Step 05 engineering governance
benchmarks/labels/domain-reviewed/     # reviewed benchmark labels
research/oss/                          # research pointers only; never runtime
tools/                                 # deterministic repository validators
```

Import direction is domain → application → ports/adapters/persistence, with entrypoints and composition at the edge. Domain contracts must remain independent of vendor libraries and storage implementations.
