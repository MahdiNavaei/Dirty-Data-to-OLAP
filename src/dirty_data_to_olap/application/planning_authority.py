"""Server-owned authority for run-specific conditional stage selection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, PlatformError
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModelHypothesis, EntityResolutionRequirement
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanIntent, ExecutionPlanSelection, StageSelectionDecision
from dirty_data_to_olap.domain.contracts.platform import ArtifactIntegrityState, ArtifactPublicationState, ArtifactRef, RunRecord
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult, stable_digest, stable_id


@dataclass(frozen=True)
class ExecutionPlanningContext:
    """Trusted planning facts decoded from registered project artifacts."""

    run_id: str
    source_ids: tuple[str, ...]
    source_artifact_ids: tuple[str, ...]
    hypothesis_artifact_ids: tuple[str, ...]
    entity_resolution_requirements: dict[str, EntityResolutionRequirement]

    @property
    def cross_source_mapping_required(self) -> bool:
        return len(self.source_ids) > 1


@dataclass(frozen=True)
class SelectionResolution:
    selection: ExecutionPlanSelection | None = None
    unresolved_stage_ids: tuple[str, ...] = ()
    detail: str = ""


class TrustedExecutionPlanningState:
    """Read-only decoder for the trusted planning inputs already in the run."""

    _KINDS = {"SourceCatalog", "SourceSnapshotResult", "CanonicalModelHypothesis"}

    def __init__(self, control_store: ControlStorePort, artifact_store: ArtifactStorePort) -> None:
        self.control_store = control_store
        self.artifact_store = artifact_store

    def resolve(self, run: RunRecord) -> tuple[ExecutionPlanningContext | None, tuple[str, ...]]:
        source_ids: set[str] = set()
        source_artifacts: list[str] = []
        hypotheses: list[tuple[ArtifactRef, CanonicalModelHypothesis]] = []
        unresolved: set[str] = set()
        refs = self.control_store.list_artifacts(run_id=run.run_id, limit=10000)
        for ref in refs:
            if ref.artifact_kind not in self._KINDS:
                continue
            try:
                artifact, payload = self._read(ref, run.run_id)
            except (KeyError, OSError, PlatformError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                unresolved.add("SOURCE_SCOPE" if ref.artifact_kind in {"SourceCatalog", "SourceSnapshotResult"} else "ENTITY_RESOLUTION")
                continue
            if artifact.artifact_kind == "SourceCatalog" and isinstance(payload, SourceCatalog):
                source_ids.add(payload.source_id)
                source_artifacts.append(artifact.artifact_id)
            elif artifact.artifact_kind == "SourceSnapshotResult" and isinstance(payload, SourceSnapshotResult):
                source_ids.add(payload.snapshot.source_id)
                source_artifacts.append(artifact.artifact_id)
            elif artifact.artifact_kind == "CanonicalModelHypothesis" and isinstance(payload, CanonicalModelHypothesis):
                if payload.artifact_id != artifact.artifact_id or payload.run_id != run.run_id:
                    unresolved.add("ENTITY_RESOLUTION")
                else:
                    hypotheses.append((artifact, payload))

        if not source_ids:
            unresolved.add("SOURCE_SCOPE")
        if not hypotheses:
            unresolved.add("ENTITY_RESOLUTION")
        if unresolved:
            return None, tuple(sorted(unresolved))

        requirement_sets = {
            stable_digest(hypothesis.entity_resolution_requirements): hypothesis.entity_resolution_requirements
            for _artifact, hypothesis in hypotheses
        }
        if len(requirement_sets) != 1:
            return None, ("ENTITY_RESOLUTION",)
        requirements = dict(next(iter(requirement_sets.values())))
        return ExecutionPlanningContext(
            run_id=run.run_id,
            source_ids=tuple(sorted(source_ids)),
            source_artifact_ids=tuple(sorted(set(source_artifacts))),
            hypothesis_artifact_ids=tuple(sorted(artifact.artifact_id for artifact, _hypothesis in hypotheses)),
            entity_resolution_requirements=requirements,
        ), ()

    def _read(self, supplied: ArtifactRef, run_id: str) -> tuple[ArtifactRef, Any]:
        registered = self.control_store.get_artifact(supplied.artifact_id)
        if registered is None or registered != supplied or registered.run_id != run_id or registered.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise PlatformError("planning input is not the exact registered published artifact")
        if self.artifact_store.stat(registered) != registered or self.artifact_store.verify(registered).state is not ArtifactIntegrityState.VERIFIED:
            raise PlatformError("planning input failed artifact integrity verification")
        payload = json.loads(self.artifact_store.read(registered).decode("utf-8"))
        contract = {
            "SourceCatalog": SourceCatalog,
            "SourceSnapshotResult": SourceSnapshotResult,
            "CanonicalModelHypothesis": CanonicalModelHypothesis,
        }[registered.artifact_kind]
        return registered, contract.model_validate(payload)


class ServerOwnedExecutionPlanSelectionResolver:
    """Compile bounded intent into selection evidence owned by this project."""

    POLICY_REF = "execution-plan-authority-v2"

    def __init__(self, control_store: ControlStorePort, artifact_store: ArtifactStorePort, graph_root: Path) -> None:
        self.state = TrustedExecutionPlanningState(control_store, artifact_store)
        self.graph_root = Path(graph_root).resolve()

    def resolve(self, *, run: RunRecord, intent: ExecutionPlanIntent) -> SelectionResolution:
        context, unresolved = self.state.resolve(run)
        if context is None:
            return SelectionResolution(
                unresolved_stage_ids=unresolved,
                detail="trusted planning inputs are unavailable; selection authority will not guess",
            )
        graph_path = self.graph_root / "docs" / "architecture" / "specs" / "stage_graph.yml"
        try:
            import yaml

            graph = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
        except (ModuleNotFoundError, OSError, ValueError):
            return SelectionResolution(unresolved_stage_ids=("PLAN_GRAPH",), detail="authoritative execution graph could not be loaded")
        except yaml.YAMLError:
            return SelectionResolution(unresolved_stage_ids=("PLAN_GRAPH",), detail="authoritative execution graph could not be loaded")

        stages = tuple(graph.get("stages", ()))
        conditional_ids = tuple(str(raw["stage_id"]) for raw in stages if bool(raw.get("conditional", False)))
        scope_fingerprint = stable_digest({
            "run_id": run.run_id,
            "configuration_fingerprint": run.configuration_fingerprint,
            "source_ids": context.source_ids,
            "source_artifact_ids": context.source_artifact_ids,
            "hypothesis_artifact_ids": context.hypothesis_artifact_ids,
            "entity_resolution_requirements": {key: value.value for key, value in sorted(context.entity_resolution_requirements.items())},
            "policy": self.POLICY_REF,
        })
        scope = f"trusted-run-planning:{run.run_id}"
        decisions: list[StageSelectionDecision] = []
        for raw in stages:
            if not bool(raw.get("conditional", False)):
                continue
            stage_id = str(raw["stage_id"])
            selected, reason = self._decision_for(stage_id, raw, context, intent)
            evidence_ref = stable_id("selection-evidence", {
                "run_id": run.run_id,
                "stage_id": stage_id,
                "selected": selected,
                "scope_fingerprint": scope_fingerprint,
                "source_artifacts": context.source_artifact_ids,
                "hypothesis_artifacts": context.hypothesis_artifact_ids,
            })
            decisions.append(StageSelectionDecision(
                stage_id=stage_id,
                selected=selected,
                policy_ref=self.POLICY_REF,
                evidence_ref=evidence_ref,
                reason=reason,
                scope=scope,
                scope_fingerprint=scope_fingerprint,
            ))
        if set(conditional_ids) != {item.stage_id for item in decisions}:
            return SelectionResolution(unresolved_stage_ids=tuple(sorted(set(conditional_ids) - {item.stage_id for item in decisions})), detail="authoritative graph selection is incomplete")
        return SelectionResolution(selection=ExecutionPlanSelection(
            run_id=run.run_id,
            policy_ref=self.POLICY_REF,
            scope=scope,
            scope_fingerprint=scope_fingerprint,
            decisions=tuple(decisions),
        ), detail="selection was compiled from trusted run artifacts and bounded intent")

    @staticmethod
    def _decision_for(stage_id: str, raw: dict[str, Any], context: ExecutionPlanningContext, intent: ExecutionPlanIntent) -> tuple[bool, str]:
        if stage_id == "SCHEMA_MATCHING":
            selected = context.cross_source_mapping_required
            if selected:
                return True, "server authority: trusted multi-source scope requires SCHEMA_MATCHING"
            return False, "server authority: trusted single-source scope permits explicit SCHEMA_MATCHING exclusion"
        if stage_id == "ENTITY_RESOLUTION":
            required = tuple(sorted(family for family, requirement in context.entity_resolution_requirements.items() if requirement is EntityResolutionRequirement.ER_REQUIRED))
            if required:
                return True, "server authority: ER_REQUIRED families require ENTITY_RESOLUTION (" + ",".join(required) + ")"
            selected = bool(intent.entity_resolution_requested)
            return selected, "server authority: all trusted families are ER_NOT_REQUIRED; bounded intent " + ("enabled" if selected else "excluded") + " ENTITY_RESOLUTION"
        if stage_id == "OPTIONAL_SEMANTIC_EVIDENCE":
            selected = intent.optional_semantic_evidence_enabled
            return selected, "server authority: bounded optional semantic-evidence intent " + ("enabled" if selected else "excluded")
        if stage_id == "LEARNED_EVIDENCE":
            selected = intent.learned_evidence_enabled
            return selected, "server authority: bounded learned-evidence intent " + ("enabled" if selected else "excluded")
        if bool(raw.get("required", False)):
            return True, "server authority: architecture-required conditional stage is selected"
        return False, "server authority: optional conditional stage is excluded by policy"


__all__ = [
    "ExecutionPlanningContext",
    "SelectionResolution",
    "ServerOwnedExecutionPlanSelectionResolver",
    "TrustedExecutionPlanningState",
]
