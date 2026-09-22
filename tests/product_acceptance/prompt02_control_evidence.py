"""Helpers for recording observations made by Prompt02 negative controls.

The acceptance runner supplies ``DDO_PROMPT02_CONTROL_EVIDENCE_DIR`` only when
it is collecting durable evidence.  Ordinary focused test runs remain
side-effect free, while the acceptance run receives an assertion-produced
observation instead of a value copied from the expected control definition.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def record_control_observation(control_id: str, observed_rejection: str, assertion_reference: str) -> None:
    """Persist the value established by the test's actual assertion."""

    evidence_root = os.environ.get("DDO_PROMPT02_CONTROL_EVIDENCE_DIR")
    if not evidence_root:
        return
    if not observed_rejection.strip() or not assertion_reference.strip():
        raise AssertionError("Prompt02 control observations require a non-empty observation and assertion reference")
    root = Path(evidence_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"PROMPT02_NEGATIVE_CONTROL_{control_id}.observed.json"
    path.write_text(
        json.dumps(
            {
                "control_id": control_id,
                "observed_rejection": observed_rejection,
                "assertion_reference": assertion_reference,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
