"""Atomic persistence for project-owned profile JSON artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dirty_data_to_olap.domain.contracts.profiling import ProfileArtifactReference, ProfileResult


class ProfileArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _safe_root(self, root: Path) -> Path:
        resolved = root.resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise ValueError("profile artifacts must remain under the project root") from None
        return resolved

    def _write_json(self, path: Path, payload: object) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")
        partial = path.with_suffix(path.suffix + ".partial")
        try:
            partial.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            os.replace(partial, path)
            return digest
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def publish(self, result: ProfileResult, *, run_root: Path) -> ProfileResult:
        root = self._safe_root(run_root)
        profiles = root / "profiles"
        references: list[ProfileArtifactReference] = []
        for item, folder, kind, item_id in [
            *[(table, "tables", "TableProfile", table.profile_id) for table in result.tables],
            *[(column, "columns", "ColumnProfile", column.profile_id) for column in result.columns],
            *[(pattern, "patterns", "ValuePatternSummary", pattern.pattern_id) for pattern in result.patterns],
        ]:
            path = profiles / folder / f"{item_id}.json"
            digest = self._write_json(path, item.model_dump(mode="json"))
            references.append(ProfileArtifactReference(artifact_id=item_id, artifact_type=kind, artifact_location=path.relative_to(self.project_root).as_posix(), content_hash=digest))
        final_result = result.model_copy(update={"artifacts": tuple(references)})
        self._write_json(profiles / "manifests" / "profile_result.json", final_result.model_dump(mode="json"))
        return final_result
