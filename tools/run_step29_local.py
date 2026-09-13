"""Run the local Step29 API and durable product worker."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn

from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.application.product_runtime import build_local_product


def main() -> None:
    parser = argparse.ArgumentParser(description="Dirty Data to OLAP Step29 local product")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--graph-root", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    platform, backend, runtime = build_local_product(args.root, graph_root=args.graph_root)
    app = create_app(backend)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        runtime.close()
        platform.close()


if __name__ == "__main__":
    main()
