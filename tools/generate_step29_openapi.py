"""Generate the browser contract from the executable FastAPI application."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dirty_data_to_olap.application.product_runtime import build_local_product
from dirty_data_to_olap.entrypoints.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    platform, backend, _runtime = build_local_product(root)
    try:
        document = create_app(backend).openapi()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    finally:
        platform.close()


if __name__ == "__main__":
    main()
