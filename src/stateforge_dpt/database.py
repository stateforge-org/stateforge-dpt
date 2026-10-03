"""SQLite persistence for DPT."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    release TEXT NOT NULL,
    generated_filename TEXT NOT NULL DEFAULT 'STATEFORGE_DPT.md',
    plan_source_branch TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    plan_fingerprint TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    repository_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    github_full_name TEXT NOT NULL,
    github_default_branch TEXT,
    clone_url TEXT,
    local_path TEXT,
    responsibility TEXT NOT NULL DEFAULT '',
    last_generated_digest TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, alias),
    UNIQUE(project_id, github_full_name)
);

CREATE TABLE IF NOT EXISTS steps (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    release TEXT NOT NULL,
    title TEXT NOT NULL,
    required_result TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    declared_state TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, step_id),
    FOREIGN KEY(repository_id)
        REFERENCES repositories(repository_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS step_dependencies (
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    depends_on_step_id TEXT NOT NULL,
    PRIMARY KEY(project_id, step_id, depends_on_step_id),
    FOREIGN KEY(project_id, step_id)
        REFERENCES steps(project_id, step_id) ON DELETE CASCADE,
    FOREIGN KEY(project_id, depends_on_step_id)
        REFERENCES steps(project_id, step_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS step_context_resources (
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id)
        ON DELETE CASCADE,
    path TEXT NOT NULL,
    selector_kind TEXT NOT NULL,
    selector_value TEXT,
    role TEXT NOT NULL,
    explanation TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1,
    authority TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, step_id, context_id),
    FOREIGN KEY(project_id, step_id)
        REFERENCES steps(project_id, step_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS step_verification_requirements (
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    verification_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    repository_id TEXT REFERENCES repositories(repository_id) ON DELETE SET NULL,
    target TEXT NOT NULL,
    explanation TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, step_id, verification_id),
    FOREIGN KEY(project_id, step_id)
        REFERENCES steps(project_id, step_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verification_observations (
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    verification_id TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    output_excerpt TEXT,
    exit_code INTEGER,
    observed_commit TEXT,
    recorded_by TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(project_id, step_id, verification_id),
    FOREIGN KEY(project_id, step_id, verification_id)
        REFERENCES step_verification_requirements(
            project_id, step_id, verification_id
        ) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_observations (
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    observed_commit TEXT,
    blob_hash TEXT,
    selector_status TEXT,
    status TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    PRIMARY KEY(project_id, step_id, context_id),
    FOREIGN KEY(project_id, step_id, context_id)
        REFERENCES step_context_resources(project_id, step_id, context_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_approvals (
    approval_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    approved_by TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    FOREIGN KEY(project_id, step_id)
        REFERENCES steps(project_id, step_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS implementation_packages (
    package_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    package_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id, step_id)
        REFERENCES steps(project_id, step_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS github_artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    repository_id TEXT NOT NULL,
    step_id TEXT,
    kind TEXT NOT NULL,
    number INTEGER NOT NULL,
    github_issue_id INTEGER,
    title TEXT NOT NULL,
    body TEXT,
    state TEXT NOT NULL,
    state_reason TEXT,
    html_url TEXT NOT NULL,
    author_login TEXT,
    timeline TEXT NOT NULL DEFAULT '[]',
    assignees TEXT NOT NULL DEFAULT '[]',
    reviewers TEXT NOT NULL DEFAULT '[]',
    labels TEXT NOT NULL DEFAULT '[]',
    head_branch TEXT,
    base_branch TEXT,
    is_draft INTEGER NOT NULL DEFAULT 0,
    is_merged INTEGER NOT NULL DEFAULT 0,
    checks_state TEXT,
    changed_files TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    UNIQUE(repository_id, kind, number),
    FOREIGN KEY(repository_id)
        REFERENCES repositories(repository_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS github_issue_dependency_edges (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    blocked_artifact_id TEXT NOT NULL
        REFERENCES github_artifacts(artifact_id) ON DELETE CASCADE,
    blocking_artifact_id TEXT NOT NULL
        REFERENCES github_artifacts(artifact_id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(blocked_artifact_id, blocking_artifact_id)
);

CREATE TABLE IF NOT EXISTS device_authorizations (
    authorization_digest TEXT PRIMARY KEY,
    encrypted_device_code BLOB NOT NULL,
    user_code TEXT NOT NULL,
    verification_uri TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    next_poll_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_digest TEXT PRIMARY KEY,
    github_user_id INTEGER NOT NULL,
    github_login TEXT NOT NULL,
    github_name TEXT,
    github_avatar_url TEXT,
    encrypted_access_token BLOB NOT NULL,
    encrypted_refresh_token BLOB,
    access_token_expires_at TEXT,
    refresh_token_expires_at TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    actor TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_plan_snapshots (
    repository_id TEXT PRIMARY KEY
        REFERENCES repositories(repository_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    source_branch TEXT NOT NULL,
    plan_json TEXT,
    revision INTEGER,
    fingerprint TEXT,
    file_digest TEXT,
    error TEXT,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_repository
    ON steps(repository_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_artifacts_step
    ON github_artifacts(project_id, step_id);
CREATE INDEX IF NOT EXISTS idx_issue_dependencies_project
    ON github_issue_dependency_edges(project_id, blocked_artifact_id);
CREATE INDEX IF NOT EXISTS idx_activities_project
    ON activities(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_step
    ON step_context_resources(project_id, step_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_verification_step
    ON step_verification_requirements(project_id, step_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_verification_observation_step
    ON verification_observations(project_id, step_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_approvals_step
    ON context_approvals(project_id, step_id, approved_at DESC);
"""


class Database:
    """Open short-lived SQLite connections with a shared schema."""

    def __init__(self, path: Path) -> None:
        """Create a database handle for `path`."""
        self.path = path

    def initialize(self) -> None:
        """Create the database and apply the current schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            session_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "encrypted_refresh_token" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN encrypted_refresh_token BLOB"
                )
            if "access_token_expires_at" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN access_token_expires_at TEXT"
                )
            if "refresh_token_expires_at" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN refresh_token_expires_at TEXT"
                )
            project_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "plan_fingerprint" not in project_columns:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN plan_fingerprint TEXT"
                )
            if "plan_source_branch" not in project_columns:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN plan_source_branch TEXT"
                )
            project_schema = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'projects'"
            ).fetchone()["sql"]
            if "slug TEXT NOT NULL UNIQUE" in project_schema:
                # Remove the inline constraint so an accidental duplicate created
                # by an older DPT build can still be opened and deleted separately.
                connection.commit()
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.executescript(
                    """
                    BEGIN;
                    CREATE TABLE projects_without_slug_uniqueness (
                        project_id TEXT PRIMARY KEY,
                        slug TEXT NOT NULL,
                        name TEXT NOT NULL,
                        release TEXT NOT NULL,
                        generated_filename TEXT NOT NULL DEFAULT 'STATEFORGE_DPT.md',
                        plan_source_branch TEXT,
                        revision INTEGER NOT NULL DEFAULT 0,
                        plan_fingerprint TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO projects_without_slug_uniqueness
                    SELECT project_id, slug, name, release, generated_filename,
                           plan_source_branch, revision, plan_fingerprint,
                           created_at, updated_at
                    FROM projects;
                    DROP TABLE projects;
                    ALTER TABLE projects_without_slug_uniqueness RENAME TO projects;
                    COMMIT;
                    """
                )
                connection.execute("PRAGMA foreign_keys = ON")
                foreign_key_errors = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if foreign_key_errors:
                    raise sqlite3.DatabaseError(
                        "Project identity migration left invalid foreign keys"
                    )
            duplicate_slug = connection.execute(
                """
                SELECT slug FROM projects
                GROUP BY slug HAVING COUNT(*) > 1
                LIMIT 1
                """
            ).fetchone()
            if duplicate_slug is None:
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_slug "
                    "ON projects(slug)"
                )
            connection.execute(
                """
                UPDATE projects
                SET plan_source_branch = lower(release) || '/implementation'
                WHERE plan_source_branch IS NULL
                """
            )
            artifact_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(github_artifacts)"
                ).fetchall()
            }
            if "body" not in artifact_columns:
                connection.execute("ALTER TABLE github_artifacts ADD COLUMN body TEXT")
            if "timeline" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE github_artifacts "
                    "ADD COLUMN timeline TEXT NOT NULL DEFAULT '[]'"
                )
            if "github_issue_id" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE github_artifacts ADD COLUMN github_issue_id INTEGER"
                )
            if "state_reason" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE github_artifacts ADD COLUMN state_reason TEXT"
                )
            if "changed_files" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE github_artifacts "
                    "ADD COLUMN changed_files TEXT NOT NULL DEFAULT '[]'"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured SQLite connection."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
