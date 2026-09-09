"""Atomic, aggregate-only quality artifact publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dirty_data_to_olap.domain.contracts.quality import QualityResult


class QualityArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _root(self, run_root: Path) -> Path:
        root = run_root.resolve()
        try:
            root.relative_to(self.project_root)
        except ValueError:
            raise ValueError("quality artifacts must remain under the project root") from None
        return root

    @staticmethod
    def _write(path: Path, value: object) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")
        partial = path.with_suffix(path.suffix + ".partial")
        try:
            partial.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            os.replace(partial, path)
            return digest
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def publish(self, result: QualityResult, *, run_root: Path) -> QualityResult:
        root = self._root(run_root)
        quality = root / "quality"
        artifact_paths: list[str] = []
        for item, folder, item_id in (
            *((item, "issues", item.issue_id) for item in result.issues),
            *((item, "proposals", item.proposal_id) for item in result.repair_proposals),
            *((item, "evaluations", item.rule_id) for item in result.rule_evaluations),
            *((item, "summaries", item.dimension.value) for item in result.dimension_summaries),
        ):
            path = quality / folder / f"{item_id}.json"
            self._write(path, item.model_dump(mode="json"))
            artifact_paths.append(path.relative_to(self.project_root).as_posix())
        final = result.model_copy(update={"artifacts": tuple(artifact_paths)})
        self._write(quality / "manifests" / "quality_result.json", final.model_dump(mode="json"))
        return final
