"""Atomic publication of aggregate dependency evidence and run metadata."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dirty_data_to_olap.domain.contracts.dependency import DependencyArtifactReference, DependencyResult


class DependencyArtifactStore:
    """Publish only project-owned contracts; raw staged values never enter these files."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _root(self, run_root: Path) -> Path:
        root = run_root.resolve()
        try:
            root.relative_to(self.project_root)
        except ValueError:
            raise ValueError("dependency artifacts must remain under the project root") from None
        return root

    @staticmethod
    def _write(path: Path, payload: object) -> str:
        raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")
        partial = path.with_suffix(path.suffix + ".partial")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            partial.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            os.replace(partial, path)
            return digest
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def publish(self, result: DependencyResult, *, run_root: Path) -> DependencyResult:
        root = self._root(run_root) / "dependencies"
        refs: list[DependencyArtifactReference] = []

        def publish_item(folder: str, artifact_type: str, artifact_id: str, value: object) -> None:
            path = root / folder / f"{artifact_id}.json"
            digest = self._write(path, value)
            refs.append(DependencyArtifactReference(artifact_id=artifact_id, artifact_type=artifact_type, artifact_location=path.relative_to(self.project_root).as_posix(), content_hash=digest))

        for item in result.key_candidates:
            publish_item("key_candidates", "dependency_key_candidate", item.candidate_id, item.model_dump(mode="json"))
        for item in result.ucc_evidence:
            publish_item("ucc_evidence", "dependency_ucc_evidence", item.evidence_id, item.model_dump(mode="json"))
        for item in result.functional_dependencies:
            publish_item("functional_dependencies", "dependency_functional_dependency", item.evidence_id, item.model_dump(mode="json"))
        for item in result.inclusion_dependencies:
            publish_item("inclusion_dependencies", "dependency_inclusion_dependency", item.evidence_id, item.model_dump(mode="json"))
        for item in result.relationship_candidates:
            publish_item("relationship_candidates", "dependency_relationship_candidate", item.candidate_id, item.model_dump(mode="json"))
        for item in result.failures:
            publish_item("failures", "dependency_failure", item.failure_id, item.model_dump(mode="json"))
        for item in result.capabilities:
            publish_item("capabilities", "dependency_capability", item.capability_id, item.model_dump(mode="json"))
        publish_item("scopes", "dependency_observation_scope", f"scope-{result.request.request_id}", result.observation_scope.model_dump(mode="json"))
        publish_item("stats", "dependency_search_stats", f"stats-{result.request.request_id}", result.search_stats.model_dump(mode="json"))
        final = result.model_copy(update={"artifacts": tuple(refs)})
        self._write(root / "manifests" / "dependency_result.json", final.model_dump(mode="json"))
        return final
