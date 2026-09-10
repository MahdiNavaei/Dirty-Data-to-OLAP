"""Versioned prompt references; raw provider prompts are not persisted."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dirty_data_to_olap.domain.contracts.semantic_ai import SemanticEvidenceRequest, SemanticPromptReference

ROOT = Path(__file__).resolve().parents[4] / "prompts" / "semantic-ai" / "v1"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_reference(request: SemanticEvidenceRequest, *, context_builder_version: str) -> SemanticPromptReference:
    system = ROOT / "system.txt"
    task = ROOT / "tasks" / f"{request.task.value}.txt"
    if not system.exists() or not task.exists():
        raise FileNotFoundError("versioned semantic prompt asset is missing")
    system_hash, task_hash = _hash(system), _hash(task)
    prompt_hash = hashlib.sha256(f"{system_hash}:{task_hash}:{request.schema_version}:{context_builder_version}".encode()).hexdigest()
    return SemanticPromptReference(prompt_version=request.prompt_version, system_prompt_hash=system_hash, task_template_hash=task_hash, schema_version=request.schema_version, context_builder_version=context_builder_version, prompt_hash=prompt_hash)
