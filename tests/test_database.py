import sqlite3
from pathlib import Path

import pytest

from stateforge_dpt.database import Database


def test_existing_database_moves_slug_uniqueness_to_a_migrated_index(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "dpt.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                release TEXT NOT NULL,
                generated_filename TEXT NOT NULL DEFAULT 'STATEFORGE_DPT.md',
                plan_source_branch TEXT,
                revision INTEGER NOT NULL DEFAULT 0,
                plan_fingerprint TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO projects(
                project_id, slug, name, release, plan_source_branch,
                created_at, updated_at
            ) VALUES ('one', 'stateforge', 'StateForge', 'MVP',
                      'mvp/implementation', 'now', 'now')
            """
        )

    database = Database(database_path)
    database.initialize()

    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO projects(
                    project_id, slug, name, release, plan_source_branch,
                    created_at, updated_at
                ) VALUES ('two', 'stateforge', 'StateForge', 'MVP',
                          'mvp/implementation', 'now', 'now')
                """
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
