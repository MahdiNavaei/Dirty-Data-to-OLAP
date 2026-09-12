"""Read-only DuckDB adapter for project-generated semantic SELECT plans."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb

from dirty_data_to_olap.domain.contracts.semantic import (
    SemanticModel,
    SemanticQueryCompilation,
    SemanticQueryResult,
)


class SemanticQueryExecutionError(ValueError):
    """A semantic query cannot be executed against its bound target."""


class DuckDBSemanticQueryExecutor:
    """Execute only a validated, generated SELECT against a read-only target."""

    def __init__(self, repository_root: Path):
        self.repository_root = repository_root.resolve()

    def _target_path(self, model: SemanticModel) -> Path:
        target = (self.repository_root / model.target_relative_path).resolve()
        try:
            target.relative_to(self.repository_root)
        except ValueError as exc:
            raise SemanticQueryExecutionError("semantic target escapes repository root") from exc
        if target.suffix.casefold() != ".duckdb":
            raise SemanticQueryExecutionError("semantic target must be a DuckDB file")
        if not target.is_file():
            raise SemanticQueryExecutionError("semantic target does not exist")
        if model.target_file_sha256:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != model.target_file_sha256:
                raise SemanticQueryExecutionError("STALE_TARGET: target file hash differs from semantic model")
        return target

    def execute(self, model: SemanticModel, compilation: SemanticQueryCompilation) -> SemanticQueryResult:
        query_plan = compilation.query_plan
        if query_plan.semantic_model_id != model.semantic_model_id or query_plan.semantic_model_content_hash != model.content_hash:
            raise SemanticQueryExecutionError("STALE_SEMANTIC_MODEL: query plan is not bound to this model")
        statement = query_plan.sql_template.strip()
        upper = statement.upper()
        if not upper.startswith("SELECT ") or ";" in statement:
            raise SemanticQueryExecutionError("semantic executor accepts one generated SELECT only")
        if any(token in upper for token in (" INSERT ", " UPDATE ", " DELETE ", " DROP ", " ALTER ", " CREATE ", " PRAGMA", " ATTACH ", " INSTALL ", " LOAD ", " READ_CSV", " READ_PARQUET", " HTTPFS")):
            raise SemanticQueryExecutionError("unsafe semantic statement")
        target = self._target_path(model)
        connection = duckdb.connect(str(target), read_only=True)
        try:
            cursor = connection.execute(statement, list(compilation.parameters))
            columns = tuple(item[0] for item in cursor.description)
            rows = tuple({column: row[index] for index, column in enumerate(columns)} for row in cursor.fetchall())
        finally:
            connection.close()
        return SemanticQueryResult(
            query_plan_id=query_plan.query_plan_id,
            semantic_model_id=model.semantic_model_id,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            provenance_refs=("adapter:duckdb-read-only", "semantic:generated-select", model.semantic_model_id),
        )
