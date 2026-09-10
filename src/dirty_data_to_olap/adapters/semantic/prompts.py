"""Versioned prompt references; raw provider prompts are not persisted."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from dirty_data_to_olap.domain.contracts.semantic_ai import SemanticEvidenceRequest, SemanticPromptReference

ROOT = Path(__file__).resolve().parents[4] / "prompts" / "semantic-ai" / "v1"


@dataclass(frozen=True)
class PromptBundle:
    system_text: str
    task_text: str
    reference: SemanticPromptReference


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prompt_bundle(request: SemanticEvidenceRequest, *, context_builder_version: str) -> PromptBundle:
    system = ROOT / "system.txt"
    task = ROOT / "tasks" / f"{request.task.value}.txt"
    if not system.exists() or not task.exists():
        raise FileNotFoundError("versioned semantic prompt asset is missing")
    # Decode the exact bytes that are hashed; these strings are sent unchanged
    # to the provider so provenance covers the actual prompt payload.
    system_bytes, task_bytes = system.read_bytes(), task.read_bytes()
    system_text, task_text = system_bytes.decode("utf-8"), task_bytes.decode("utf-8")
    system_hash, task_hash = _hash(system), _hash(task)
    prompt_hash = hashlib.sha256(f"{system_hash}:{task_hash}:{request.schema_version}:{context_builder_version}".encode()).hexdigest()
    reference = SemanticPromptReference(prompt_version=request.prompt_version, system_prompt_hash=system_hash, task_template_hash=task_hash, schema_version=request.schema_version, context_builder_version=context_builder_version, prompt_hash=prompt_hash)
    return PromptBundle(system_text=system_text, task_text=task_text, reference=reference)


def prompt_reference(request: SemanticEvidenceRequest, *, context_builder_version: str) -> SemanticPromptReference:
    return load_prompt_bundle(request, context_builder_version=context_builder_version).reference
