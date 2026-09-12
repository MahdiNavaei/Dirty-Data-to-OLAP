# Large graph strategy

The default is a bounded overview. It may expose top-level source/table/fact/dimension/canonical nodes and reports the complete input totals plus hidden counts. A neighborhood or focused path requires a focus reference and uses deterministic breadth-first traversal with a declared hop limit. Node and edge caps are explicit request fields.

Edges are emitted only when both endpoint nodes are rendered. This prevents dangling or misleading links. Hidden nodes and edges are counted, `show_more_available` is explicit, and truncation has a reason. No content is silently discarded and no synthetic aggregate edge is invented.

The Step26 test uses 220 tables, more than 3,000 columns/nodes, thousands of relationships, and one high-degree table. It measures local projection elapsed time and checks the caps and reconciliation fields. It does not claim browser rendering performance, multi-node capacity, or a target FPS.
