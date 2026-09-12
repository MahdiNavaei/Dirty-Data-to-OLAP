# Performance evidence

The executed Step26 benchmark constructs a deterministic synthetic graph with 220 tables, 3,000+ columns/nodes, 3,000+ edges, and a high-degree table. It builds an overview capped at 250 nodes/500 edges and a one-hop neighborhood capped at 120 nodes/180 edges. The assertions verify elapsed local projection time, bounded output, focus/hop metadata, and hidden counts.

The result is evidence for bounded in-process projection only. It does not measure a browser, renderer, GPU, network, API, database, multi-node execution, production workload, or user-perceived latency. Step37/38 own broader performance/load evidence.
