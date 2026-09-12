"""Read-only target inspection adapter for Step22 validation.

The adapter accepts a reviewed relative target path and an allowlisted table
set supplied by the application contract.  It does not accept SQL from the
caller and it never exposes a DuckDB connection outside this module.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import duckdb

from dirty_data_to_olap.domain.contracts.validation import TargetSnapshot, TargetTableSnapshot


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ValidationTargetError(ValueError):
    """The target cannot be safely inspected or does not match its binding."""


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValidationTargetError(f"unsafe validation table identifier: {value!r}")
    return '"' + value + '"'


class DuckDBValidationTargetReader:
    """Inspect only project-generated tables through a read-only connection."""

    def __init__(self, repository_root: Path):
        self.repository_root = repository_root.resolve()

    def _target_path(self, relative_path: str) -> Path:
        target = (self.repository_root / relative_path).resolve()
        try:
            target.relative_to(self.repository_root)
        except ValueError as exc:
            raise ValidationTargetError("validation target escapes repository root") from exc
        if target.suffix.casefold() != ".duckdb" or not target.is_file():
            raise ValidationTargetError("validation target must be an existing DuckDB file")
        return target

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def snapshot(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        allowed_table_names: tuple[str, ...],
    ) -> TargetSnapshot:
        """Return a bounded snapshot of the exact target tables.

        SHOW TABLES and DESCRIBE are fixed adapter queries.  Row reads are
        generated only from validated identifier names, never caller SQL.
        """

        target = self._target_path(relative_path)
        before = self._sha256(target)
        if before != expected_sha256:
            raise ValidationTargetError("STALE_TARGET: target hash differs from validation binding")
        table_names = tuple(sorted(set(allowed_table_names)))
        if not table_names or any(not _IDENTIFIER.fullmatch(name) for name in table_names):
            raise ValidationTargetError("validation requires a non-empty safe table allowlist")
        connection = duckdb.connect(str(target), read_only=True)
        try:
            actual_names = tuple(sorted(row[0] for row in connection.execute("SHOW TABLES").fetchall()))
            missing = set(table_names) - set(actual_names)
            if missing:
                raise ValidationTargetError("BOUND_TARGET_TABLE_MISSING:" + ",".join(sorted(missing)))
            tables: list[TargetTableSnapshot] = []
            for table_name in table_names:
                quoted = _quote_identifier(table_name)
                columns = tuple(row[0] for row in connection.execute(f"DESCRIBE {quoted}").fetchall())
                cursor = connection.execute(f"SELECT * FROM {quoted}")
                names = tuple(item[0] for item in cursor.description)
                rows = tuple({name: row[index] for index, name in enumerate(names)} for row in cursor.fetchall())
                tables.append(TargetTableSnapshot(table_name=table_name, columns=columns, rows=rows))
        finally:
            connection.close()
        after = self._sha256(target)
        if after != before:
            raise ValidationTargetError("TARGET_CHANGED_DURING_VALIDATION")
        return TargetSnapshot(
            target_relative_path=relative_path,
            target_file_sha256=after,
            available_table_names=actual_names,
            tables=tuple(tables),
            provenance_refs=("adapter:duckdb-read-only", "validation:fixed-query-allowlist"),
        )
