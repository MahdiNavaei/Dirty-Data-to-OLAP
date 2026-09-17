"""Controlled DuckDB materializer for reviewed analytical V1 targets."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import duckdb

from dirty_data_to_olap.application.compiler import AnalyticalCompilerService
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputDataset,
    CompiledPlan,
    GeneratedSQL,
    MaterializationArtifact,
    MaterializationStatus,
    TargetConfig,
    materialization_artifact_id,
)
from dirty_data_to_olap.domain.contracts.source import utc_now


class DuckDBMaterializer:
    """Publish a reviewed plan only inside its injected controlled root."""

    def __init__(self, controlled_root: Path, *, repository_root: Path):
        self.controlled_root = controlled_root.resolve()
        self.repository_root = repository_root.resolve()

    def _target_path(self, target_config: TargetConfig) -> Path:
        target = (self.controlled_root / target_config.relative_path).resolve()
        try:
            target.relative_to(self.controlled_root)
        except ValueError as exc:
            raise ValueError("DuckDB target escapes the controlled root") from exc
        if target.suffix.casefold() != ".duckdb":
            raise ValueError("DuckDB target must use the .duckdb extension")
        return target

    def _target_relative_path(self, target: Path) -> str:
        try:
            return target.relative_to(self.repository_root).as_posix()
        except ValueError as exc:
            raise ValueError("controlled target must be project-local") from exc

    def materialize(
        self,
        compiled_plan: CompiledPlan,
        generated_sql: GeneratedSQL,
        input_data: AnalyticalInputDataset,
        target_config: TargetConfig,
        *,
        run_id: str,
    ) -> MaterializationArtifact:
        attempted_at = utc_now()
        target = self._target_path(target_config)
        target_relative_path = self._target_relative_path(target)
        artifact_id = materialization_artifact_id({
            "run_id": run_id,
            "compiled_plan_id": compiled_plan.compiled_plan_id,
            "compiled_plan_hash": compiled_plan.content_hash,
            "sql_hash": generated_sql.sql_hash,
            "target": target_config.config_fingerprint,
        })
        temp = target.with_name("." + target.name + "." + artifact_id[-16:] + ".tmp")
        connection = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            runtime_sql = generated_sql
            if not input_data.allow_literal_sql and any("?" in statement for statement in (generated_sql.load_date_sql, generated_sql.load_dimensions_sql, generated_sql.load_facts_sql)):
                runtime_sql = AnalyticalCompilerService()._generate_sql(
                    compiled_plan.dimension_specs,
                    compiled_plan.fact_specs,
                    compiled_plan.grain_specs,
                    compiled_plan.measure_specs,
                    input_data.model_copy(update={"allow_literal_sql": True}),
                )
            if temp.exists():
                temp.unlink()
            connection = duckdb.connect(str(temp))
            connection.execute("BEGIN TRANSACTION")
            for statement in (generated_sql.create_schema_sql, runtime_sql.load_date_sql, runtime_sql.load_dimensions_sql, runtime_sql.load_facts_sql):
                if statement.strip():
                    connection.execute(statement)
            connection.execute("COMMIT")
            table_names = tuple(sorted(row[0] for row in connection.execute("SHOW TABLES").fetchall()))
            if table_names != tuple(sorted(compiled_plan.table_names)):
                raise RuntimeError("materialized table set does not match the reviewed plan")
            row_counts = {
                table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in table_names
            }
            connection.close()
            connection = None
            os.replace(temp, target)
            file_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            return MaterializationArtifact(
                artifact_id=artifact_id,
                run_id=run_id,
                plan_id=compiled_plan.plan_id,
                plan_content_hash=compiled_plan.plan_content_hash,
                compiled_plan_id=compiled_plan.compiled_plan_id,
                compiled_plan_content_hash=compiled_plan.content_hash,
                target_type=target_config.target_type,
                target_relative_path=target_relative_path,
                target_config_fingerprint=target_config.config_fingerprint,
                generated_sql_hash=generated_sql.sql_hash,
                table_names=table_names,
                row_counts=row_counts,
                status=MaterializationStatus.SUCCEEDED,
                usable=True,
                attempted_at=attempted_at,
                provenance_refs=("adapter:duckdb", "target:injected-controlled-root", "publication:atomic-replace"),
                target_file_sha256=file_hash,
                quarantined_record_count=len(generated_sql.quarantine_records),
                quarantine_records=generated_sql.quarantine_records,
            )
        except Exception as exc:
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    pass
                connection.close()
            if temp.exists():
                temp.unlink()
            return MaterializationArtifact(
                artifact_id=artifact_id,
                run_id=run_id,
                plan_id=compiled_plan.plan_id,
                plan_content_hash=compiled_plan.plan_content_hash,
                compiled_plan_id=compiled_plan.compiled_plan_id,
                compiled_plan_content_hash=compiled_plan.content_hash,
                target_type=target_config.target_type,
                target_relative_path=target_relative_path,
                target_config_fingerprint=target_config.config_fingerprint,
                generated_sql_hash=generated_sql.sql_hash,
                table_names=tuple(sorted(compiled_plan.table_names)) or ("invalid_target",),
                row_counts={},
                status=MaterializationStatus.FAILED,
                usable=False,
                attempted_at=attempted_at,
                provenance_refs=("adapter:duckdb", "target:injected-controlled-root", "publication:not-published"),
                failure_reason=type(exc).__name__ + ": " + str(exc)[:240],
                quarantined_record_count=len(generated_sql.quarantine_records),
                quarantine_records=generated_sql.quarantine_records,
            )
