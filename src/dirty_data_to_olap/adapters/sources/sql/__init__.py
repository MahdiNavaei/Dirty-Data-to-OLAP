"""Safe SQL database foundation for Specialist Step06.

This is intentionally not the Step07 SourceAdapter or ingestion layer.
"""

from .sqlite import SQLiteReadOnlySession, SQLiteReadOnlySource, normalize_sqlite_failure

__all__ = ["SQLiteReadOnlySession", "SQLiteReadOnlySource", "normalize_sqlite_failure"]
