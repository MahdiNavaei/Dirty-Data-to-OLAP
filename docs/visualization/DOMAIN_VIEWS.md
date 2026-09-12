# Domain view coverage

| Area | Representation | Integrity rule |
| --- | --- | --- |
| Source/schema | source, snapshot, table, view, column, constraint nodes | declared and inferred relationships are distinct |
| Schema matching | candidate nodes and candidate edges | candidate score retains metric semantics and provenance |
| Entity resolution | record, cluster, candidate/authorized link | cluster is not canonical identity |
| Canonical | canonical entity/event/attribute and source mapping | conflicts and survivorship remain explicit |
| OLAP | fact, dimension, grain, measure, materialization | grain is explicit; non-additive measures cannot default to SUM |
| Lineage | directed graph/focused path | direction is explicit and not causality |
| Quality | heatmap cells | denominator and observation scope are visible |
| Profile | justified bar/histogram/box/table/none | sampled/full and privacy state are visible |
| Validation | individual typed checks and derived summary | failure and NOT_EVALUATED cannot become PASS |

This table is a view contract, not a frontend implementation. Backend transport, review mutation, navigation, and renderer integration remain later specialist steps.
