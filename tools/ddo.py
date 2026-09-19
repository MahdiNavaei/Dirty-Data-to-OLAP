"""Source-checkout shim for the canonical ``ddo`` developer command."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dirty_data_to_olap.devx import main


if __name__ == "__main__":
    raise SystemExit(main())
