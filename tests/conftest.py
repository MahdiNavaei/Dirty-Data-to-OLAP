from __future__ import annotations

import sqlite3
from uuid import uuid4
from pathlib import Path

import pytest


@pytest.fixture()
def sqlite_fixture_path() -> Path:
    runtime_dir = Path(__file__).resolve().parents[1] / "workspace" / "tests"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    database_path = runtime_dir / f"step06_reference_{uuid4().hex}.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE parent (
                parent_id INTEGER PRIMARY KEY,
                parent_code TEXT NOT NULL UNIQUE,
                nullable_note TEXT
            );
            CREATE TABLE child (
                parent_id INTEGER NOT NULL,
                child_seq INTEGER NOT NULL,
                label TEXT,
                unique_value TEXT UNIQUE,
                PRIMARY KEY (parent_id, child_seq),
                FOREIGN KEY (parent_id) REFERENCES parent(parent_id)
            );
            CREATE TABLE "odd table" (
                "odd column" TEXT NOT NULL,
                value INTEGER
            );
            CREATE VIEW child_view AS
                SELECT parent_id, child_seq, label FROM child;
            INSERT INTO parent(parent_id, parent_code, nullable_note) VALUES
                (1, 'P-001', NULL),
                (2, 'P-002', 'present');
            INSERT INTO child(parent_id, child_seq, label, unique_value) VALUES
                (1, 1, 'first', 'U-001'),
                (1, 2, 'second', 'U-002'),
                (2, 1, 'third', 'U-003');
            INSERT INTO "odd table"("odd column", value) VALUES ('quoted', 7);
            """
        )
        connection.commit()
    finally:
        connection.close()
    try:
        yield database_path
    finally:
        database_path.unlink(missing_ok=True)
