"""DPT application services and plan rendering."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from stateforge_dpt.database import Database
from stateforge_dpt.git import (
    CloneState,
    GitError,
    clone_identity,
    clone_repository,
    inspect_clone,
    is_committed_file,
    read_remote_file,
)
from stateforge_dpt.models import (
    PortablePlan,
    PortableProject,
    PortableRepository,
    PortableStep,
    ProjectCreate,
    RepositoryCreate,
    StepCreate,
    StepUpdate,
)
from stateforge_dpt.plan_file import (
    LoadedPlan,
    PlanFileError,
    canonical_plan_json,
    load_plan,
    parse_manifest,
    plan_fingerprint,
    render_manifest,
)
from stateforge_dpt.security import SecretStore, digest_token, new_token


def utc_now() -> str:
    """Return a stable UTC timestamp for persistence and rendering."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}  # noqa: SIM118


def _github_accounts(values: Any) -> list[dict[str, str | None]]:
    """Normalize GitHub users stored by current and earlier DPT versions."""
    if not isinstance(values, list):
        return []
    accounts: list[dict[str, str | None]] = []
    for value in values:
        login: object
        avatar_url: object
        html_url: object
        if isinstance(value, str):
            login = value
            avatar_url = None
            html_url = None
        elif isinstance(value, dict):
            login = value.get("login")
            avatar_url = value.get("avatar_url")
            html_url = value.get("html_url")
        else:
            continue
        if not isinstance(login, str) or not login:
            continue
        accounts.append(
            {
                "login": login,
                "avatar_url": (
                    avatar_url
                    if isinstance(avatar_url, str) and avatar_url
                    else f"https://github.com/{login}.png?size=64"
                ),
                "html_url": (
                    html_url
                    if isinstance(html_url, str) and html_url
                    else f"https://github.com/{login}"
                ),
            }
        )
    return accounts


def _github_timeline(
    events: Any,
    review_comments: Any,
) -> list[dict[str, Any]]:
    """Normalize GitHub timeline and inline review-comment records."""
    timeline: list[dict[str, Any]] = []
    source_events = events if isinstance(events, list) else []
    for index, value in enumerate(source_events):
        if not isinstance(value, dict):
            continue
        event = str(value.get("event") or "event")
        actor_values = _github_accounts([value.get("user") or value.get("actor")])
        created_at = value.get("created_at") or value.get("submitted_at")
        if event == "committed":
            author = value.get("author")
            if isinstance(author, dict):
                created_at = created_at or author.get("date")
        label = value.get("label")
        assignee = value.get("assignee")
        requested_reviewer = value.get("requested_reviewer")
        rename = value.get("rename")
        timeline.append(
            {
                "timeline_id": (
                    f"{event}:{value.get('id') or value.get('sha') or index}"
                ),
                "event": event,
                "actor": actor_values[0] if actor_values else None,
                "body": value.get("body"),
                "created_at": created_at,
                "html_url": value.get("html_url"),
                "state": value.get("state"),
                "commit_sha": value.get("sha") or value.get("commit_id"),
                "commit_message": value.get("message"),
                "label": label.get("name") if isinstance(label, dict) else None,
                "assignee": (
                    assignee.get("login") if isinstance(assignee, dict) else None
                ),
                "requested_reviewer": (
                    requested_reviewer.get("login")
                    if isinstance(requested_reviewer, dict)
                    else None
                ),
                "rename_from": (
                    rename.get("from") if isinstance(rename, dict) else None
                ),
                "rename_to": (rename.get("to") if isinstance(rename, dict) else None),
                "path": None,
                "line": None,
                "diff_hunk": None,
            }
        )

    source_comments = review_comments if isinstance(review_comments, list) else []
    for index, value in enumerate(source_comments):
        if not isinstance(value, dict):
            continue
        actor_values = _github_accounts([value.get("user")])
        timeline.append(
            {
                "timeline_id": (f"review_comment:{value.get('id') or index}"),
                "event": "review_comment",
                "actor": actor_values[0] if actor_values else None,
                "body": value.get("body"),
                "created_at": value.get("created_at"),
                "html_url": value.get("html_url"),
                "state": None,
                "commit_sha": value.get("commit_id"),
                "commit_message": None,
                "label": None,
                "assignee": None,
                "requested_reviewer": None,
                "rename_from": None,
                "rename_to": None,
                "path": value.get("path"),
                "line": value.get("line") or value.get("original_line"),
                "diff_hunk": value.get("diff_hunk"),
            }
        )

    return sorted(
        timeline,
        key=lambda item: str(item.get("created_at") or ""),
    )


def _pull_request_references_issue(
    body: object,
    repository: str,
    issue_number: int,
) -> bool:
    """Return whether a pull-request body closes or references an issue."""
    if not isinstance(body, str):
        return False
    escaped_repository = re.escape(repository)
    reference = rf"(?:{escaped_repository})?#{issue_number}(?!\d)"
    return bool(
        re.search(
            rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+{reference}",
            body,
            flags=re.IGNORECASE,
        )
    )


def _blocker_branch(release: str, title: str, issue_number: int) -> str:
    """Create a release-scoped, editable branch suggestion for a GitHub bug."""
    cleaned = re.sub(r"\[[^]]+]", " ", title)
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.casefold()).strip("-")
    if slug.startswith("fix-"):
        branch_name = slug
    elif slug:
        branch_name = f"fix-{slug}"
    else:
        branch_name = f"fix-issue-{issue_number}"
    return f"{release.casefold()}/{branch_name[:80].rstrip('-')}"


class DPTError(RuntimeError):
    """Raised when a requested DPT state transition is invalid."""


class DPTService:
    """Coordinate projects, repositories, plans, and GitHub evidence."""

    def __init__(self, database: Database, secrets: SecretStore) -> None:
        """Create a service backed by SQLite and a local secret store."""
        self.database = database
        self.secrets = secrets

    def create_project(self, request: ProjectCreate) -> dict[str, Any]:
        """Create an empty DPT project."""
        project_id = str(uuid.uuid4())
        timestamp = utc_now()
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE slug = ?",
                (request.slug,),
            ).fetchone():
                raise DPTError(f"Project slug already exists: {request.slug}")
            try:
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, slug, name, release, plan_source_branch,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        request.slug,
                        request.name,
                        request.release,
                        request.plan_source_branch
                        or f"{request.release.lower()}/implementation",
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DPTError(
                    f"Project slug already exists: {request.slug}"
                ) from error
            self._activity(
                connection,
                project_id,
                "project.created",
                f"Created project {request.name}",
            )
        return self.project(project_id)

    def projects(self) -> list[dict[str, Any]]:
        """Return every DPT project with summary counts."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*,
                    COUNT(DISTINCT r.repository_id) AS repository_count,
                    COUNT(DISTINCT s.step_id) AS step_count
                FROM projects p
                LEFT JOIN repositories r ON r.project_id = p.project_id
                LEFT JOIN steps s ON s.project_id = p.project_id
                GROUP BY p.project_id
                ORDER BY p.updated_at DESC
                """
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def remove_project(self, project_id: str) -> None:
        """Remove one local DPT working copy without touching Git or GitHub."""
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise DPTError("Project not found")
            connection.execute(
                "DELETE FROM projects WHERE project_id = ?",
                (project_id,),
            )

    def project(self, project_id: str) -> dict[str, Any]:
        """Return one project or raise a domain error."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise DPTError("Project not found")
        return _row_dict(row)

    def _project_is_uninitialized(self, project_id: str) -> bool:
        """Return whether Git may safely establish this local project's identity."""
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT p.revision, p.plan_fingerprint, COUNT(s.step_id) AS step_count
                FROM projects AS p
                LEFT JOIN steps AS s ON s.project_id = p.project_id
                WHERE p.project_id = ?
                GROUP BY p.project_id
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            raise DPTError("Project not found")
        return (
            int(row["revision"]) == 0
            and row["plan_fingerprint"] is None
            and int(row["step_count"]) == 0
        )

    def update_plan_source_branch(
        self,
        project_id: str,
        plan_source_branch: str,
    ) -> dict[str, Any]:
        """Change the remote parent branch used to discover published plans."""
        self.project(project_id)
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET plan_source_branch = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (plan_source_branch, timestamp, project_id),
            )
            connection.execute(
                "DELETE FROM remote_plan_snapshots WHERE project_id = ?",
                (project_id,),
            )
            self._activity(
                connection,
                project_id,
                "plan.source_branch.changed",
                f"Changed the plan source branch to {plan_source_branch}",
            )
        return self.project(project_id)

    def add_repository(
        self,
        project_id: str,
        request: RepositoryCreate,
    ) -> dict[str, Any]:
        """Register a GitHub repository before attaching its local clone."""
        self.project(project_id)
        repository_id = str(uuid.uuid4())
        timestamp = utc_now()
        with self.database.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO repositories(
                        repository_id, project_id, alias, github_full_name,
                        github_default_branch, clone_url, responsibility,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repository_id,
                        project_id,
                        request.alias,
                        request.github_full_name,
                        request.github_default_branch,
                        request.clone_url,
                        request.responsibility,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DPTError(
                    "Repository alias or GitHub repository already exists"
                ) from error
            self._activity(
                connection,
                project_id,
                "repository.added",
                f"Added {request.github_full_name}",
            )
        return self.repository(repository_id)

    def repository(self, repository_id: str) -> dict[str, Any]:
        """Return one managed repository."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        if row is None:
            raise DPTError("Repository not found")
        return self._repository_with_clone_state(_row_dict(row))

    def repositories(self, project_id: str) -> list[dict[str, Any]]:
        """Return project repositories and their current local Git state."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM repositories
                WHERE project_id = ?
                ORDER BY created_at, alias
                """,
                (project_id,),
            ).fetchall()
        return [self._repository_with_clone_state(_row_dict(row)) for row in rows]

    def remove_repository(self, repository_id: str) -> None:
        """Stop managing a repository without changing GitHub or its local clone."""
        with self.database.connect() as connection:
            repository = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
            if repository is None:
                raise DPTError("Repository not found")

            step_count = connection.execute(
                "SELECT COUNT(*) FROM steps WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()[0]
            if step_count:
                noun = "step" if step_count == 1 else "steps"
                raise DPTError(
                    f"Repository owns {step_count} implementation {noun} and "
                    "cannot be removed from DPT"
                )

            project_id = str(repository["project_id"])
            github_full_name = str(repository["github_full_name"])
            connection.execute(
                "DELETE FROM repositories WHERE repository_id = ?",
                (repository_id,),
            )
            self._touch_project(connection, project_id)
            self._activity(
                connection,
                project_id,
                "repository.removed",
                f"Removed {github_full_name} from DPT",
            )

    def attach_clone(self, repository_id: str, local_path: str) -> dict[str, Any]:
        """Attach a validated existing local clone."""
        repository = self.repository(repository_id)
        state = inspect_clone(local_path, repository["github_full_name"])
        project = self.project(str(repository["project_id"]))
        plan_path = Path(state.path) / str(project["generated_filename"])
        if (
            self._project_is_uninitialized(str(repository["project_id"]))
            and plan_path.is_file()
        ):
            try:
                loaded = load_plan(str(plan_path))
            except PlanFileError as error:
                raise DPTError(str(error)) from error
            if loaded.plan.project.slug != project["slug"]:
                raise DPTError(
                    f"This clone belongs to project {loaded.plan.project.name} "
                    f"({loaded.plan.project.slug}), not {project['name']} "
                    f"({project['slug']}). Import it as an existing project instead."
                )
            repository_names = {
                item.github_full_name.casefold() for item in loaded.plan.repositories
            }
            if str(repository["github_full_name"]).casefold() not in repository_names:
                raise DPTError(
                    "This repository is not listed in its embedded DPT manifest"
                )
            with self.database.connect() as connection:
                self._replace_project_plan(
                    connection,
                    str(repository["project_id"]),
                    loaded,
                    state.path,
                    str(repository["github_full_name"]),
                )
                self._activity(
                    connection,
                    repository["project_id"],
                    "plan.imported",
                    (
                        f"Imported revision {loaded.plan.project.revision} "
                        "while attaching a clone"
                    ),
                    {
                        "repository": repository["github_full_name"],
                        "fingerprint": loaded.fingerprint,
                    },
                )
            return self.repository(repository_id)

        trusted_generated_digest: str | None = None
        if plan_path.is_file():
            try:
                attached_plan = load_plan(str(plan_path))
            except PlanFileError as error:
                filename = project["generated_filename"]
                raise DPTError(
                    f"The clone contains an unreadable {filename}: {error}"
                ) from error
            if attached_plan.plan.project.slug != project["slug"]:
                raise DPTError(
                    f"This clone contains project "
                    f"{attached_plan.plan.project.slug}, not {project['slug']}"
                )
            repository_names = {
                item.github_full_name.casefold()
                for item in attached_plan.plan.repositories
            }
            if str(repository["github_full_name"]).casefold() not in repository_names:
                raise DPTError(
                    "This repository is not listed in its embedded DPT manifest"
                )
            published_fingerprint = project.get("plan_fingerprint")
            if (
                attached_plan.fingerprint == published_fingerprint
                or attached_plan.plan.project.revision < int(project["revision"])
            ):
                # This is a checksummed DPT revision that can safely be advanced by
                # the next publish. Newer or same-revision divergent plans remain
                # untrusted until the user resolves them explicitly.
                trusted_generated_digest = attached_plan.file_digest

        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE repositories
                SET local_path = ?,
                    last_generated_digest = COALESCE(?, last_generated_digest),
                    updated_at = ?
                WHERE repository_id = ?
                """,
                (
                    state.path,
                    trusted_generated_digest,
                    timestamp,
                    repository_id,
                ),
            )
            self._activity(
                connection,
                repository["project_id"],
                "repository.attached",
                f"Attached local clone for {repository['github_full_name']}",
                {"path": state.path},
            )
        return self.repository(repository_id)

    def clone(
        self,
        repository_id: str,
        destination_path: str,
        access_token: str | None,
    ) -> dict[str, Any]:
        """Clone and attach a registered GitHub repository."""
        repository = self.repository(repository_id)
        state = clone_repository(
            repository["github_full_name"],
            destination_path,
            access_token,
        )
        return self.attach_clone(repository_id, state.path)

    def create_step(self, project_id: str, request: StepCreate) -> dict[str, Any]:
        """Add a delivery step to the canonical project plan."""
        timestamp = utc_now()
        with self.database.connect() as connection:
            repository = connection.execute(
                """
                SELECT 1 FROM repositories
                WHERE repository_id = ? AND project_id = ?
                """,
                (request.repository_id, project_id),
            ).fetchone()
            if repository is None:
                raise DPTError("Repository does not belong to this project")
            missing = self._missing_dependencies(
                connection, project_id, request.depends_on
            )
            if missing:
                raise DPTError(f"Unknown dependencies: {', '.join(missing)}")
            order = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 1
                FROM steps WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()[0]
            try:
                connection.execute(
                    """
                    INSERT INTO steps(
                        project_id, step_id, repository_id, release, title,
                        required_result, branch_name, declared_state, sort_order,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        request.step_id,
                        request.repository_id,
                        request.release,
                        request.title,
                        request.required_result,
                        request.branch_name,
                        "planned",
                        order,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DPTError(f"Step already exists: {request.step_id}") from error
            for dependency in request.depends_on:
                connection.execute(
                    """
                    INSERT INTO step_dependencies(
                        project_id, step_id, depends_on_step_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (project_id, request.step_id, dependency),
                )
            self._assert_acyclic(connection, project_id)
            self._touch_project(connection, project_id)
            self._activity(
                connection,
                project_id,
                "step.created",
                f"Added step {request.step_id}: {request.title}",
            )
        return self.step(project_id, request.step_id)

    def update_step(
        self,
        project_id: str,
        step_id: str,
        request: StepUpdate,
    ) -> dict[str, Any]:
        """Update a delivery step and replace its dependency set."""
        if step_id in request.depends_on:
            raise DPTError("A step cannot depend on itself")
        timestamp = utc_now()
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT repository_id FROM steps
                WHERE project_id = ? AND step_id = ?
                """,
                (project_id, step_id),
            ).fetchone()
            if existing is None:
                raise DPTError("Step not found")
            repository_id = request.repository_id or existing["repository_id"]
            repository = connection.execute(
                """
                SELECT 1 FROM repositories
                WHERE repository_id = ? AND project_id = ?
                """,
                (repository_id, project_id),
            ).fetchone()
            if repository is None:
                raise DPTError("Repository does not belong to this project")
            missing = self._missing_dependencies(
                connection, project_id, request.depends_on
            )
            if missing:
                raise DPTError(f"Unknown dependencies: {', '.join(missing)}")
            result = connection.execute(
                """
                UPDATE steps
                SET repository_id = ?, title = ?, required_result = ?,
                    branch_name = ?, updated_at = ?
                WHERE project_id = ? AND step_id = ?
                """,
                (
                    repository_id,
                    request.title,
                    request.required_result,
                    request.branch_name,
                    timestamp,
                    project_id,
                    step_id,
                ),
            )
            if result.rowcount != 1:
                raise DPTError("Step not found")
            connection.execute(
                "DELETE FROM step_dependencies WHERE project_id = ? AND step_id = ?",
                (project_id, step_id),
            )
            for dependency in request.depends_on:
                connection.execute(
                    """
                    INSERT INTO step_dependencies(
                        project_id, step_id, depends_on_step_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (project_id, step_id, dependency),
                )
            self._assert_acyclic(connection, project_id)
            self._touch_project(connection, project_id)
            self._activity(
                connection,
                project_id,
                "step.updated",
                f"Updated step {step_id}",
            )
        return self.step(project_id, step_id)

    def delete_step(self, project_id: str, step_id: str) -> None:
        """Delete an unused plan step without orphaning dependencies or evidence."""
        with self.database.connect() as connection:
            step = connection.execute(
                """
                SELECT title FROM steps
                WHERE project_id = ? AND step_id = ?
                """,
                (project_id, step_id),
            ).fetchone()
            if step is None:
                raise DPTError("Step not found")

            dependents = connection.execute(
                """
                SELECT step_id FROM step_dependencies
                WHERE project_id = ? AND depends_on_step_id = ?
                ORDER BY step_id
                """,
                (project_id, step_id),
            ).fetchall()
            if dependents:
                dependent_ids = ", ".join(row["step_id"] for row in dependents)
                raise DPTError(
                    f"Step is required by {dependent_ids} and cannot be deleted"
                )

            artifact_count = connection.execute(
                """
                SELECT COUNT(*) FROM github_artifacts
                WHERE project_id = ? AND step_id = ?
                """,
                (project_id, step_id),
            ).fetchone()[0]
            if artifact_count:
                raise DPTError(
                    "Step has GitHub delivery evidence and cannot be deleted"
                )

            connection.execute(
                "DELETE FROM steps WHERE project_id = ? AND step_id = ?",
                (project_id, step_id),
            )
            self._touch_project(connection, project_id)
            self._activity(
                connection,
                project_id,
                "step.deleted",
                f"Deleted step {step_id}: {step['title']}",
            )

    def step(self, project_id: str, step_id: str) -> dict[str, Any]:
        """Return a step with dependencies and GitHub evidence."""
        data = self.dashboard(project_id)
        for step in data["steps"]:
            if step["step_id"] == step_id:
                return cast(dict[str, Any], step)
        raise DPTError("Step not found")

    def blocker(self, project_id: str, blocker_id: str) -> dict[str, Any]:
        """Return one reconciled GitHub blocker projection."""
        for blocker in self.dashboard(project_id)["blockers"]:
            if blocker["blocker_id"] == blocker_id:
                return cast(dict[str, Any], blocker)
        raise DPTError("Blocker not found")

    def dashboard(self, project_id: str) -> dict[str, Any]:
        """Return the complete project graph and observed delivery state."""
        project = self.project(project_id)
        with self.database.connect() as connection:
            repository_rows = connection.execute(
                """
                SELECT * FROM repositories
                WHERE project_id = ? ORDER BY created_at, alias
                """,
                (project_id,),
            ).fetchall()
            step_rows = connection.execute(
                """
                SELECT s.*, r.alias AS repository_alias,
                    r.github_full_name AS github_full_name
                FROM steps s
                JOIN repositories r ON r.repository_id = s.repository_id
                WHERE s.project_id = ?
                ORDER BY s.sort_order, s.step_id
                """,
                (project_id,),
            ).fetchall()
            dependency_rows = connection.execute(
                """
                SELECT step_id, depends_on_step_id
                FROM step_dependencies WHERE project_id = ?
                """,
                (project_id,),
            ).fetchall()
            artifact_rows = connection.execute(
                """
                SELECT a.*, r.alias AS repository_alias,
                    r.github_full_name AS github_full_name
                FROM github_artifacts a
                JOIN repositories r ON r.repository_id = a.repository_id
                WHERE a.project_id = ? ORDER BY a.updated_at DESC
                """,
                (project_id,),
            ).fetchall()
            dependency_edge_rows = connection.execute(
                """
                SELECT blocked_artifact_id, blocking_artifact_id
                FROM github_issue_dependency_edges
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchall()

        dependencies: dict[str, list[str]] = {}
        for row in dependency_rows:
            dependencies.setdefault(row["step_id"], []).append(
                row["depends_on_step_id"]
            )
        artifacts: dict[str, list[dict[str, Any]]] = {}
        artifact_items: list[dict[str, Any]] = []
        artifacts_by_id: dict[str, dict[str, Any]] = {}
        for row in artifact_rows:
            item = _row_dict(row)
            item["timeline"] = json.loads(item["timeline"])
            item["assignees"] = _github_accounts(json.loads(item["assignees"]))
            item["reviewers"] = _github_accounts(json.loads(item["reviewers"]))
            item["labels"] = json.loads(item["labels"])
            artifact_items.append(item)
            artifacts_by_id[item["artifact_id"]] = item
            if item["step_id"]:
                artifacts.setdefault(item["step_id"], []).append(item)

        blockers_by_id: dict[str, dict[str, Any]] = {}
        blocker_ids_by_step: dict[str, list[str]] = {}
        for edge in dependency_edge_rows:
            blocked = artifacts_by_id.get(edge["blocked_artifact_id"])
            blocking = artifacts_by_id.get(edge["blocking_artifact_id"])
            if (
                blocked is None
                or blocking is None
                or blocked["kind"] != "issue"
                or blocking["kind"] != "issue"
                or not blocked.get("step_id")
            ):
                continue
            blocker_id = str(blocking["artifact_id"])
            step_id = str(blocked["step_id"])
            if blocker_id not in blockers_by_id:
                state_reason = blocking.get("state_reason")
                if blocking["state"] == "open":
                    blocker_status = "active"
                    active = True
                elif state_reason == "not_planned":
                    blocker_status = "dismissed"
                    active = False
                else:
                    blocker_status = "resolved"
                    active = False
                related_pull_requests = [
                    artifact
                    for artifact in artifact_items
                    if artifact["kind"] == "pull_request"
                    and artifact["github_full_name"].casefold()
                    == blocking["github_full_name"].casefold()
                    and _pull_request_references_issue(
                        artifact.get("body"),
                        str(blocking["github_full_name"]),
                        int(blocking["number"]),
                    )
                ]
                blockers_by_id[blocker_id] = {
                    **blocking,
                    "blocker_id": blocker_id,
                    "status": blocker_status,
                    "is_active": active,
                    "blocked_step_ids": [],
                    "suggested_branch": _blocker_branch(
                        str(project["release"]),
                        str(blocking["title"]),
                        int(blocking["number"]),
                    ),
                    "artifacts": [blocking, *related_pull_requests],
                }
            blocker = blockers_by_id[blocker_id]
            if step_id not in blocker["blocked_step_ids"]:
                blocker["blocked_step_ids"].append(step_id)
            step_blocker_ids = blocker_ids_by_step.setdefault(step_id, [])
            if blocker_id not in step_blocker_ids:
                step_blocker_ids.append(blocker_id)

        result_steps: list[dict[str, Any]] = []
        completed = {
            row["step_id"]
            for row in step_rows
            if self._observed_state(artifacts.get(row["step_id"], [])) == "completed"
        }
        for row in step_rows:
            item = _row_dict(row)
            item.pop("declared_state", None)
            item["depends_on"] = sorted(dependencies.get(item["step_id"], []))
            item["artifacts"] = artifacts.get(item["step_id"], [])
            item["observed_state"] = self._observed_state(item["artifacts"])
            dependencies_complete = all(
                dependency in completed for dependency in item["depends_on"]
            )
            item["dependencies_complete"] = dependencies_complete
            item["blockers"] = [
                blockers_by_id[blocker_id]
                for blocker_id in blocker_ids_by_step.get(item["step_id"], [])
            ]
            blockers_clear = not any(
                blocker["is_active"] for blocker in item["blockers"]
            )
            item["blockers_clear"] = blockers_clear
            item["computed_state"] = self._computed_state(
                item["observed_state"], dependencies_complete, blockers_clear
            )
            result_steps.append(item)

        findings = self._reconciliation_findings(result_steps)

        repositories = [
            self._repository_with_clone_state(_row_dict(row)) for row in repository_rows
        ]
        metrics = {
            state: sum(step["computed_state"] == state for step in result_steps)
            for state in (
                "planned",
                "blocked",
                "ready",
                "pending",
                "in_review",
                "completed",
                "cancelled",
            )
        }
        metrics["actionable"] = sum(
            step["dependencies_complete"]
            and step["blockers_clear"]
            and step["computed_state"] in {"ready", "pending"}
            for step in result_steps
        )
        metrics["findings"] = len(findings)
        metrics["active_blockers"] = sum(
            blocker["is_active"] for blocker in blockers_by_id.values()
        )
        metrics["blocked_milestones"] = sum(
            any(blocker["is_active"] for blocker in step["blockers"])
            for step in result_steps
        )
        return {
            "project": project,
            "repositories": repositories,
            "steps": result_steps,
            "blockers": sorted(
                blockers_by_id.values(),
                key=lambda blocker: (
                    not blocker["is_active"],
                    blocker["github_full_name"],
                    blocker["number"],
                ),
            ),
            "metrics": metrics,
            "findings": findings,
        }

    def portable_plan(
        self,
        project_id: str,
        revision: int | None = None,
    ) -> PortablePlan:
        """Return the lossless project plan stored in Git repositories."""
        dashboard = self.dashboard(project_id)
        project = dashboard["project"]
        return PortablePlan(
            project=PortableProject(
                slug=project["slug"],
                name=project["name"],
                release=project["release"],
                generated_filename=project["generated_filename"],
                revision=(int(project["revision"]) if revision is None else revision),
                plan_source_branch=project["plan_source_branch"],
            ),
            repositories=[
                PortableRepository(
                    alias=repository["alias"],
                    github_full_name=repository["github_full_name"],
                    github_default_branch=repository["github_default_branch"],
                    responsibility=repository["responsibility"],
                )
                for repository in sorted(
                    dashboard["repositories"], key=lambda item: item["alias"]
                )
            ],
            steps=[
                PortableStep(
                    step_id=step["step_id"],
                    repository_alias=step["repository_alias"],
                    release=step["release"],
                    title=step["title"],
                    required_result=step["required_result"],
                    branch_name=step["branch_name"],
                    depends_on=step["depends_on"],
                    sort_order=step["sort_order"],
                )
                for step in dashboard["steps"]
            ],
        )

    def import_project_from_clone(self, local_path: str) -> dict[str, Any]:
        """Create a local project from a Git-tracked DPT plan."""
        loaded, clone_path, source_full_name = self._validated_plan_source(local_path)
        plan = loaded.plan
        timestamp = utc_now()
        project_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM projects WHERE slug = ?",
                (plan.project.slug,),
            ).fetchone()
            if existing:
                raise DPTError(
                    f"Project {plan.project.name} already exists in DPT. Open that "
                    "project instead of importing a duplicate local working copy."
                )
            connection.execute(
                """
                INSERT INTO projects(
                    project_id, slug, name, release, generated_filename,
                    plan_source_branch, revision, plan_fingerprint, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    plan.project.slug,
                    plan.project.name,
                    plan.project.release,
                    plan.project.generated_filename,
                    plan.project.plan_source_branch
                    or f"{plan.project.release.lower()}/implementation",
                    plan.project.revision,
                    loaded.fingerprint,
                    timestamp,
                    timestamp,
                ),
            )
            self._replace_project_plan(
                connection,
                project_id,
                loaded,
                clone_path,
                source_full_name,
            )
            self._activity(
                connection,
                project_id,
                "plan.imported",
                f"Imported revision {plan.project.revision} from Git",
                {
                    "repository": source_full_name,
                    "fingerprint": loaded.fingerprint,
                },
            )
        return self.dashboard(project_id)

    def inspect_project_import(self, local_path: str) -> dict[str, Any]:
        """Describe a portable Git project before creating local state."""
        loaded, clone_path, source_full_name = self._validated_plan_source(local_path)
        plan = loaded.plan
        return {
            "project": plan.project.model_dump(mode="json"),
            "repositories": len(plan.repositories),
            "steps": len(plan.steps),
            "source_repository": source_full_name,
            "source_path": clone_path,
            "fingerprint": loaded.fingerprint,
        }

    def refresh_remote_plan_sources(
        self,
        project_id: str,
        access_token: str | None,
    ) -> dict[str, Any]:
        """Fetch only the portable plan from the configured remote parent branch."""
        project = self.project(project_id)
        repositories = self.repositories(project_id)
        branch = str(project["plan_source_branch"])
        filename = str(project["generated_filename"])
        timestamp = utc_now()
        snapshots: list[dict[str, Any]] = []

        for repository in repositories:
            local_path = repository.get("local_path")
            if not local_path:
                continue
            plan_json: str | None = None
            revision: int | None = None
            fingerprint: str | None = None
            file_digest: str | None = None
            error_message: str | None = None
            try:
                content = read_remote_file(
                    str(local_path),
                    branch,
                    filename,
                    access_token,
                )
                plan, fingerprint = parse_manifest(content)
                if plan.project.slug != project["slug"]:
                    raise PlanFileError(
                        f"The plan on origin/{branch} belongs to "
                        f"{plan.project.name} ({plan.project.slug}), not "
                        f"{project['name']} ({project['slug']}). Import that plan "
                        "as an existing project instead."
                    )
                repository_names = {
                    item.github_full_name.casefold() for item in plan.repositories
                }
                if (
                    str(repository["github_full_name"]).casefold()
                    not in repository_names
                ):
                    raise PlanFileError(
                        "Remote plan does not list the repository that supplied it"
                    )
                plan_json = canonical_plan_json(plan)
                revision = plan.project.revision
                file_digest = hashlib.sha256(content.encode()).hexdigest()
            except (GitError, PlanFileError) as error:
                error_message = str(error)
            snapshots.append(
                {
                    "repository_id": repository["repository_id"],
                    "project_id": project_id,
                    "source_branch": branch,
                    "plan_json": plan_json,
                    "revision": revision,
                    "fingerprint": fingerprint,
                    "file_digest": file_digest,
                    "error": error_message,
                    "fetched_at": timestamp,
                }
            )

        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM remote_plan_snapshots WHERE project_id = ?",
                (project_id,),
            )
            connection.executemany(
                """
                INSERT INTO remote_plan_snapshots(
                    repository_id, project_id, source_branch, plan_json,
                    revision, fingerprint, file_digest, error, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot["repository_id"],
                        snapshot["project_id"],
                        snapshot["source_branch"],
                        snapshot["plan_json"],
                        snapshot["revision"],
                        snapshot["fingerprint"],
                        snapshot["file_digest"],
                        snapshot["error"],
                        snapshot["fetched_at"],
                    )
                    for snapshot in snapshots
                ],
            )

        status = self.project_plan_sources(project_id)
        recommended = status["recommended_repository_id"]
        imported = False
        if isinstance(recommended, str):
            self.sync_project_from_repository(project_id, recommended)
            imported = True
            status = self.project_plan_sources(project_id)
        elif not status["conflict"]:
            matching = next(
                (
                    source
                    for source in status["sources"]
                    if source["status"] == "current"
                    and source["fingerprint"] == status["working_fingerprint"]
                ),
                None,
            )
            if (
                matching
                and status["published_fingerprint"] != status["working_fingerprint"]
            ):
                self.sync_project_from_repository(
                    project_id,
                    str(matching["repository_id"]),
                )
                status = self.project_plan_sources(project_id)

        valid_sources = [
            source for source in status["sources"] if source["fingerprint"]
        ]
        errors = [
            {
                "repository": source["github_full_name"],
                "message": source["error"],
            }
            for source in status["sources"]
            if source["error"]
        ]
        if imported:
            result_status = "imported"
            message = f"Imported plan revision {status['local_revision']} from {branch}"
        elif status["conflict"]:
            result_status = "conflict"
            message = "Plan changes need review before SQLite can be updated"
        elif not valid_sources:
            result_status = "unavailable"
            message = f"No portable plan could be read from origin/{branch}"
        elif status["has_local_changes"]:
            result_status = "local_changes"
            message = "Kept unpublished local plan edits"
        elif all(source["status"] == "older" for source in valid_sources):
            result_status = "local_ahead"
            message = "The local DPT plan is newer than the source branch"
        else:
            result_status = "current"
            message = f"Plan revision {status['local_revision']} is current"
        return {
            "status": result_status,
            "message": message,
            "branch": branch,
            "revision": status["local_revision"],
            "sources_checked": len(snapshots),
            "errors": errors,
        }

    def sync_project_from_repository(
        self,
        project_id: str,
        repository_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Replace a local project plan with a selected Git-tracked revision."""
        project = self.project(project_id)
        repository = self.repository(repository_id)
        if repository["project_id"] != project_id:
            raise DPTError("Repository does not belong to this project")
        local_path = repository.get("local_path")
        if not local_path:
            raise DPTError("Attach or clone this repository before importing its plan")
        loaded, source_file_is_worktree = self._repository_plan_source(
            project,
            repository,
        )
        plan = loaded.plan
        if plan.project.slug != project["slug"]:
            raise DPTError(
                f"This clone belongs to project {plan.project.name} "
                f"({plan.project.slug}), not {project['name']} ({project['slug']}). "
                "Import it as an existing project instead."
            )
        source_names = {item.github_full_name.casefold() for item in plan.repositories}
        if str(repository["github_full_name"]).casefold() not in source_names:
            raise DPTError("The selected repository is not part of its DPT manifest")

        working_fingerprint = plan_fingerprint(self.portable_plan(project_id))
        base_fingerprint = project.get("plan_fingerprint")
        has_local_changes = bool(
            base_fingerprint and working_fingerprint != base_fingerprint
        ) or (not base_fingerprint and bool(self.dashboard(project_id)["steps"]))
        local_revision = int(project["revision"])
        source_revision = plan.project.revision

        if not force:
            if loaded.fingerprint == base_fingerprint:
                return {
                    "status": "current",
                    "project": self.project(project_id),
                }
            if loaded.fingerprint == working_fingerprint:
                with self.database.connect() as connection:
                    connection.execute(
                        """
                        UPDATE projects
                        SET plan_fingerprint = ?, updated_at = ?
                        WHERE project_id = ?
                        """,
                        (loaded.fingerprint, utc_now(), project_id),
                    )
                return {
                    "status": "current",
                    "project": self.project(project_id),
                }
            if source_revision < local_revision:
                raise DPTError(
                    f"Git revision {source_revision} is older than local revision "
                    f"{local_revision}"
                )
            if source_revision == local_revision:
                raise DPTError(
                    "Git and DPT contain different plans at the same revision. "
                    "Review the Git changes before choosing which copy to keep."
                )
            if has_local_changes:
                raise DPTError(
                    "A newer Git revision exists, but this computer also has "
                    "unpublished plan edits. Publish or discard those edits before "
                    "importing Git."
                )

        clone_path, source_full_name, _origin = clone_identity(str(local_path))
        with self.database.connect() as connection:
            self._replace_project_plan(
                connection,
                project_id,
                loaded,
                str(clone_path),
                source_full_name,
                source_file_is_worktree=source_file_is_worktree,
            )
            self._activity(
                connection,
                project_id,
                "plan.synced",
                f"Synced revision {source_revision} from Git",
                {
                    "repository": source_full_name,
                    "fingerprint": loaded.fingerprint,
                },
            )
        return {"status": "imported", "project": self.project(project_id)}

    def _repository_plan_source(
        self,
        project: dict[str, Any],
        repository: dict[str, Any],
    ) -> tuple[LoadedPlan, bool]:
        """Prefer the last fetched parent-branch plan, then the worktree copy."""
        with self.database.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT * FROM remote_plan_snapshots
                WHERE repository_id = ? AND source_branch = ?
                """,
                (repository["repository_id"], project["plan_source_branch"]),
            ).fetchone()
        if snapshot is not None and snapshot["plan_json"]:
            try:
                plan = PortablePlan.model_validate_json(snapshot["plan_json"])
            except ValueError as error:
                raise DPTError("The cached remote plan is invalid") from error
            return (
                LoadedPlan(
                    path=Path(str(repository["local_path"]))
                    / str(project["generated_filename"]),
                    plan=plan,
                    fingerprint=str(snapshot["fingerprint"]),
                    file_digest=str(snapshot["file_digest"]),
                ),
                False,
            )
        if snapshot is not None and snapshot["error"]:
            raise DPTError(str(snapshot["error"]))
        try:
            return (
                load_plan(
                    str(
                        Path(str(repository["local_path"]))
                        / str(project["generated_filename"])
                    )
                ),
                True,
            )
        except PlanFileError as error:
            raise DPTError(str(error)) from error

    def project_plan_sources(self, project_id: str) -> dict[str, Any]:
        """Compare attached Git plan copies with the local working plan."""
        project = self.project(project_id)
        dashboard = self.dashboard(project_id)
        working_fingerprint = plan_fingerprint(self.portable_plan(project_id))
        base_fingerprint = project.get("plan_fingerprint")
        has_local_changes = bool(
            base_fingerprint and working_fingerprint != base_fingerprint
        ) or (not base_fingerprint and bool(dashboard["steps"]))
        sources: list[dict[str, Any]] = []

        for repository in dashboard["repositories"]:
            local_path = repository.get("local_path")
            source: dict[str, Any] = {
                "repository_id": repository["repository_id"],
                "alias": repository["alias"],
                "github_full_name": repository["github_full_name"],
                "path": None,
                "source_kind": "worktree",
                "source_branch": None,
                "revision": None,
                "fingerprint": None,
                "status": "unavailable",
                "error": None,
            }
            if not local_path:
                source["error"] = "No local clone attached"
                sources.append(source)
                continue
            try:
                loaded, source_file_is_worktree = self._repository_plan_source(
                    project,
                    repository,
                )
            except DPTError as error:
                plan_path = Path(local_path) / str(project["generated_filename"])
                source["path"] = str(plan_path)
                source["status"] = "missing" if not plan_path.is_file() else "invalid"
                source["error"] = str(error)
                sources.append(source)
                continue
            if source_file_is_worktree:
                source["path"] = str(loaded.path)
            else:
                source["source_kind"] = "remote"
                source["source_branch"] = project["plan_source_branch"]
                source["path"] = (
                    f"origin/{project['plan_source_branch']}:"
                    f"{project['generated_filename']}"
                )
            source["revision"] = loaded.plan.project.revision
            source["fingerprint"] = loaded.fingerprint
            if loaded.plan.project.slug != project["slug"]:
                source["status"] = "conflict"
                source["error"] = (
                    f"Contains project {loaded.plan.project.slug}, not "
                    f"{project['slug']}"
                )
            elif loaded.fingerprint == base_fingerprint:
                source["status"] = "current"
            elif loaded.plan.project.revision > int(project["revision"]):
                source["status"] = "conflict" if has_local_changes else "newer"
                if has_local_changes:
                    source["error"] = "Newer Git plan conflicts with local draft edits"
            elif loaded.plan.project.revision < int(project["revision"]):
                source["status"] = "older"
            elif loaded.fingerprint == working_fingerprint:
                source["status"] = "current"
            else:
                source["status"] = "conflict"
                source["error"] = "Different plan content uses the same revision"
            sources.append(source)

        valid_sources = [
            source
            for source in sources
            if source["fingerprint"] and source["revision"] is not None
        ]
        newest_revision = max(
            (int(source["revision"]) for source in valid_sources),
            default=None,
        )
        newest = [
            source for source in valid_sources if source["revision"] == newest_revision
        ]
        newest_fingerprints = {source["fingerprint"] for source in newest}
        conflict = len(newest_fingerprints) > 1 or any(
            source["status"] == "conflict" for source in sources
        )
        recommended = next(
            (
                source["repository_id"]
                for source in newest
                if source["status"] == "newer"
            ),
            None,
        )
        return {
            "local_revision": int(project["revision"]),
            "working_fingerprint": working_fingerprint,
            "published_fingerprint": base_fingerprint,
            "has_local_changes": has_local_changes,
            "conflict": conflict,
            "recommended_repository_id": None if conflict else recommended,
            "sources": sources,
        }

    def _validated_plan_source(self, local_path: str) -> tuple[LoadedPlan, str, str]:
        """Load a plan and prove that its containing clone belongs to it."""
        try:
            loaded = load_plan(local_path)
            clone_path, source_full_name, _origin = clone_identity(
                str(loaded.path.parent)
            )
        except (GitError, PlanFileError) as error:
            raise DPTError(str(error)) from error
        repository_names = {
            repository.github_full_name.casefold()
            for repository in loaded.plan.repositories
        }
        if source_full_name.casefold() not in repository_names:
            raise DPTError(
                f"Git origin {source_full_name} is not listed in this DPT project"
            )
        return loaded, str(clone_path), source_full_name

    def _replace_project_plan(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        loaded: LoadedPlan,
        source_clone_path: str,
        source_full_name: str,
        source_file_is_worktree: bool = True,
    ) -> None:
        """Atomically replace portable project data while preserving local clones."""
        plan = loaded.plan
        timestamp = utc_now()
        existing_rows = connection.execute(
            "SELECT * FROM repositories WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        existing_by_name = {
            str(row["github_full_name"]).casefold(): row for row in existing_rows
        }

        connection.execute(
            "DELETE FROM github_artifacts WHERE project_id = ?",
            (project_id,),
        )
        connection.execute(
            "DELETE FROM step_dependencies WHERE project_id = ?",
            (project_id,),
        )
        connection.execute("DELETE FROM steps WHERE project_id = ?", (project_id,))
        connection.execute(
            """
            UPDATE repositories
            SET alias = '__dpt_import__' || repository_id
            WHERE project_id = ?
            """,
            (project_id,),
        )

        repository_ids: dict[str, str] = {}
        retained_ids: set[str] = set()
        for repository in plan.repositories:
            existing = existing_by_name.get(repository.github_full_name.casefold())
            repository_id = (
                str(existing["repository_id"]) if existing else str(uuid.uuid4())
            )
            retained_ids.add(repository_id)
            repository_ids[repository.alias] = repository_id
            is_source = (
                repository.github_full_name.casefold() == source_full_name.casefold()
            )
            local_clone = (
                source_clone_path
                if is_source
                else (existing["local_path"] if existing else None)
            )
            generated_digest = (
                loaded.file_digest
                if is_source and source_file_is_worktree
                else (existing["last_generated_digest"] if existing else None)
            )
            clone_url = (
                existing["clone_url"]
                if existing and existing["clone_url"]
                else f"https://github.com/{repository.github_full_name}.git"
            )
            if existing:
                connection.execute(
                    """
                    UPDATE repositories
                    SET alias = ?, github_full_name = ?, github_default_branch = ?,
                        clone_url = ?, local_path = ?, responsibility = ?,
                        last_generated_digest = ?, updated_at = ?
                    WHERE repository_id = ?
                    """,
                    (
                        repository.alias,
                        repository.github_full_name,
                        repository.github_default_branch,
                        clone_url,
                        local_clone,
                        repository.responsibility,
                        generated_digest,
                        timestamp,
                        repository_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO repositories(
                        repository_id, project_id, alias, github_full_name,
                        github_default_branch, clone_url, local_path,
                        responsibility, last_generated_digest, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repository_id,
                        project_id,
                        repository.alias,
                        repository.github_full_name,
                        repository.github_default_branch,
                        clone_url,
                        local_clone,
                        repository.responsibility,
                        generated_digest,
                        timestamp,
                        timestamp,
                    ),
                )

        for existing in existing_rows:
            repository_id = str(existing["repository_id"])
            if repository_id not in retained_ids:
                connection.execute(
                    "DELETE FROM repositories WHERE repository_id = ?",
                    (repository_id,),
                )

        for step in sorted(
            plan.steps, key=lambda item: (item.sort_order, item.step_id)
        ):
            connection.execute(
                """
                INSERT INTO steps(
                    project_id, step_id, repository_id, release, title,
                    required_result, branch_name, declared_state, sort_order,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    step.step_id,
                    repository_ids[step.repository_alias],
                    step.release,
                    step.title,
                    step.required_result,
                    step.branch_name,
                    "planned",
                    step.sort_order,
                    timestamp,
                    timestamp,
                ),
            )
        for step in plan.steps:
            connection.executemany(
                """
                INSERT INTO step_dependencies(
                    project_id, step_id, depends_on_step_id
                ) VALUES (?, ?, ?)
                """,
                [
                    (project_id, step.step_id, dependency)
                    for dependency in step.depends_on
                ],
            )
        self._assert_acyclic(connection, project_id)
        connection.execute(
            """
            UPDATE projects
            SET name = ?, release = ?, generated_filename = ?,
                plan_source_branch = ?, revision = ?, plan_fingerprint = ?,
                updated_at = ?
            WHERE project_id = ?
            """,
            (
                plan.project.name,
                plan.project.release,
                plan.project.generated_filename,
                plan.project.plan_source_branch
                or f"{plan.project.release.lower()}/implementation",
                plan.project.revision,
                loaded.fingerprint,
                timestamp,
                project_id,
            ),
        )

    def record_github_artifacts(
        self,
        project_id: str,
        repository_id: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        """Replace cached GitHub evidence for one repository."""
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM github_artifacts WHERE repository_id = ?",
                (repository_id,),
            )
            for item in artifacts:
                self._write_github_artifact(
                    connection, project_id, repository_id, item, timestamp
                )
            self._activity(
                connection,
                project_id,
                "github.reconciled",
                f"Reconciled {len(artifacts)} GitHub artifacts",
                {"repository_id": repository_id},
            )

    def upsert_github_artifact(
        self,
        project_id: str,
        repository_id: str,
        artifact: dict[str, Any],
    ) -> str:
        """Cache one freshly created or linked GitHub artifact immediately."""
        with self.database.connect() as connection:
            artifact_id = self._write_github_artifact(
                connection, project_id, repository_id, artifact, utc_now()
            )
        return artifact_id

    @staticmethod
    def _write_github_artifact(
        connection: sqlite3.Connection,
        project_id: str,
        repository_id: str,
        item: dict[str, Any],
        timestamp: str,
    ) -> str:
        """Normalize and persist one GitHub issue or pull request."""
        labels = [
            str(label["name"])
            for label in item.get("labels", [])
            if isinstance(label, dict) and label.get("name")
        ]
        step_id = next(
            (
                label.removeprefix("dpt:step:")
                for label in labels
                if label.startswith("dpt:step:")
            ),
            None,
        )
        pull = item.get("pull_details")
        kind = "pull_request" if pull else "issue"
        artifact_id = f"{project_id}:{item['id']}"
        values = (
            artifact_id,
            project_id,
            repository_id,
            step_id,
            kind,
            item["number"],
            item.get("id"),
            item["title"],
            item.get("body"),
            item["state"],
            item.get("state_reason"),
            item["html_url"],
            item.get("user", {}).get("login"),
            json.dumps(
                _github_timeline(
                    item.get("timeline", []),
                    item.get("review_comments", []),
                )
            ),
            json.dumps(_github_accounts(item.get("assignees", []))),
            json.dumps(
                _github_accounts((pull or {}).get("requested_reviewers", []))
            ),
            json.dumps(labels),
            (pull or {}).get("head", {}).get("ref"),
            (pull or {}).get("base", {}).get("ref"),
            int(bool((pull or {}).get("draft"))),
            int(bool((pull or {}).get("merged_at"))),
            None,
            item.get("updated_at", timestamp),
        )
        connection.execute(
            """
            INSERT INTO github_artifacts(
                artifact_id, project_id, repository_id, step_id, kind,
                number, github_issue_id, title, body, state, state_reason,
                html_url, author_login, timeline, assignees, reviewers,
                labels, head_branch, base_branch, is_draft, is_merged,
                checks_state, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(artifact_id) DO UPDATE SET
                repository_id = excluded.repository_id,
                step_id = excluded.step_id,
                kind = excluded.kind,
                number = excluded.number,
                github_issue_id = excluded.github_issue_id,
                title = excluded.title,
                body = excluded.body,
                state = excluded.state,
                state_reason = excluded.state_reason,
                html_url = excluded.html_url,
                author_login = excluded.author_login,
                timeline = excluded.timeline,
                assignees = excluded.assignees,
                reviewers = excluded.reviewers,
                labels = excluded.labels,
                head_branch = excluded.head_branch,
                base_branch = excluded.base_branch,
                is_draft = excluded.is_draft,
                is_merged = excluded.is_merged,
                checks_state = excluded.checks_state,
                updated_at = excluded.updated_at
            """,
            values,
        )
        return artifact_id

    def record_issue_dependencies(
        self,
        project_id: str,
        dependencies: list[dict[str, Any]],
    ) -> None:
        """Replace the local projection of GitHub blocked-by relationships."""
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM github_issue_dependency_edges WHERE project_id = ?",
                (project_id,),
            )
            for dependency in dependencies:
                self._insert_issue_dependency(connection, project_id, dependency)

    def record_issue_dependency(
        self,
        project_id: str,
        dependency: dict[str, Any],
    ) -> None:
        """Cache one dependency immediately after GitHub accepts it."""
        with self.database.connect() as connection:
            self._insert_issue_dependency(connection, project_id, dependency)

    @staticmethod
    def _insert_issue_dependency(
        connection: sqlite3.Connection,
        project_id: str,
        dependency: dict[str, Any],
    ) -> None:
        blocked = connection.execute(
            """
            SELECT a.artifact_id
            FROM github_artifacts a
            JOIN repositories r ON r.repository_id = a.repository_id
            WHERE a.project_id = ? AND a.kind = 'issue'
              AND lower(r.github_full_name) = lower(?) AND a.number = ?
            """,
            (
                project_id,
                dependency["blocked_repository"],
                dependency["blocked_number"],
            ),
        ).fetchone()
        blocking = connection.execute(
            """
            SELECT a.artifact_id
            FROM github_artifacts a
            JOIN repositories r ON r.repository_id = a.repository_id
            WHERE a.project_id = ? AND a.kind = 'issue'
              AND lower(r.github_full_name) = lower(?) AND a.number = ?
            """,
            (
                project_id,
                dependency["blocking_repository"],
                dependency["blocking_number"],
            ),
        ).fetchone()
        if blocked is None or blocking is None:
            return
        connection.execute(
            """
            INSERT OR REPLACE INTO github_issue_dependency_edges(
                project_id, blocked_artifact_id, blocking_artifact_id, observed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (project_id, blocked["artifact_id"], blocking["artifact_id"], utc_now()),
        )

    def remove_issue_dependency(
        self,
        project_id: str,
        blocked_artifact_id: str,
        blocking_artifact_id: str,
    ) -> None:
        """Remove one cached dependency after GitHub accepts its removal."""
        with self.database.connect() as connection:
            connection.execute(
                """
                DELETE FROM github_issue_dependency_edges
                WHERE project_id = ? AND blocked_artifact_id = ?
                  AND blocking_artifact_id = ?
                """,
                (project_id, blocked_artifact_id, blocking_artifact_id),
            )

    def render_project(self, project_id: str, revision: int | None = None) -> str:
        """Render the complete canonical plan as human-readable Markdown."""
        dashboard = self.dashboard(project_id)
        project = dashboard["project"]
        plan_revision = revision if revision is not None else project["revision"]
        manifest = render_manifest(self.portable_plan(project_id, plan_revision))
        lines = [
            manifest,
            "",
            "# StateForge Development Progress",
            "",
            "> Generated by StateForge DPT. Edit the plan through DPT so every",
            "> managed repository remains synchronized.",
            "",
            f"**Project:** {project['name']}  ",
            f"**Release:** {project['release']}  ",
            f"**Revision:** {plan_revision}",
            "",
            "## Repositories",
            "",
            "| Alias | GitHub repository | Responsibility |",
            "| --- | --- | --- |",
        ]
        lines.extend(
            (
                f"| `{repository['alias']}` | "
                f"[{repository['github_full_name']}](https://github.com/"
                f"{repository['github_full_name']}) | "
                f"{repository['responsibility'] or '—'} |"
            )
            for repository in dashboard["repositories"]
        )

        lines.extend(
            [
                "",
                "## Implementation roadmap",
                "",
                "| Step | Repository | Required implementation | Depends on | "
                "State | GitHub |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for step in dashboard["steps"]:
            dependencies = (
                ", ".join(f"`{value}`" for value in step["depends_on"]) or "—"
            )
            links = (
                " · ".join(
                    f"[{artifact['kind'].replace('_', ' ').title()} "
                    f"#{artifact['number']}]({artifact['html_url']})"
                    for artifact in step["artifacts"]
                )
                or "—"
            )
            required = step["required_result"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{step['step_id']}` | `{step['repository_alias']}` | "
                f"**{step['title']}**<br>{required} | {dependencies} | "
                f"`{step['computed_state'].upper()}` | {links} |"
            )

        lines.extend(
            [
                "",
                "## Delivery rules",
                "",
                "- Every issue and pull request uses the labels "
                "`dpt:project:<project>`, `dpt:release:<release>`, and "
                "`dpt:step:<step-id>`.",
                "- An open issue represents pending work.",
                "- An open pull request represents work in review.",
                "- A merged pull request represents completed work.",
                "- A closed, unmerged pull request is cancelled, not completed.",
                "- A dependent step becomes ready only when all prerequisites "
                "are complete.",
                "- Human review remains the trust boundary for accepted work.",
                "",
                "## Agent instructions",
                "",
                "Before starting work, locate the requested step in this roadmap "
                "and verify",
                "that every dependency is complete. Implement only the required "
                "result for",
                "that step. Do not begin a downstream step merely because its "
                "repository is",
                "available locally. Update the plan through StateForge DPT rather "
                "than editing",
                "this generated file manually.",
                "",
            ]
        )
        return "\n".join(lines)

    def _validate_publish_snapshot(
        self,
        project: dict[str, Any],
        repository: dict[str, Any],
        loaded: LoadedPlan,
        working_fingerprint: str,
        location: str,
    ) -> None:
        """Reject foreign, newer, or same-revision divergent published plans."""
        source_project = loaded.plan.project
        if (
            source_project.slug != project["slug"]
            or source_project.release.casefold() != str(project["release"]).casefold()
            or source_project.generated_filename != project["generated_filename"]
        ):
            raise DPTError(
                f"The DPT file at {location} belongs to a different project or "
                "release. Review its plan before publishing."
            )
        repository_names = {
            item.github_full_name.casefold() for item in loaded.plan.repositories
        }
        if str(repository["github_full_name"]).casefold() not in repository_names:
            raise DPTError(
                f"The DPT manifest at {location} does not include "
                f"{repository['github_full_name']}. Review it before publishing."
            )
        local_revision = int(project["revision"])
        if source_project.revision > local_revision:
            raise DPTError(
                f"A newer Git plan (revision {source_project.revision}) exists at "
                f"{location}; DPT is at revision {local_revision}. Reconcile and "
                "resolve the newer plan before publishing."
            )
        if source_project.revision == local_revision and loaded.fingerprint not in (
            project.get("plan_fingerprint"),
            working_fingerprint,
        ):
            raise DPTError(
                f"Git and DPT contain different plans at revision {local_revision} "
                f"({location}). Review and resolve the conflict before publishing."
            )

    def _validate_publish_target(
        self,
        project: dict[str, Any],
        repository: dict[str, Any],
        target: Path,
        current_digest: str,
        next_digest: str,
        working_fingerprint: str,
    ) -> None:
        """Accept known DPT output or a compatible, unchanged Git snapshot."""
        if current_digest == next_digest:
            return
        previous = repository["last_generated_digest"]
        try:
            loaded = load_plan(str(target))
        except (PlanFileError, UnicodeError) as error:
            if current_digest == previous:
                # An unchanged output from a pre-manifest DPT release can still
                # be upgraded. Unknown or edited legacy files remain protected.
                return
            raise DPTError(
                f"Generated file changed outside DPT: {target}. Its embedded "
                f"plan cannot be validated: {error}. Review it before publishing."
            ) from error
        self._validate_publish_snapshot(
            project, repository, loaded, working_fingerprint, str(target)
        )
        if current_digest == previous:
            return
        if not is_committed_file(str(repository["local_path"]), target.name):
            raise DPTError(
                f"Generated file changed outside DPT: {target}. DPT could not "
                "verify it as an unchanged committed Git snapshot; it may have "
                "uncommitted or staged edits. Review those changes before publishing."
            )

    def generate_project_files(
        self, project_id: str, force: bool = False
    ) -> dict[str, Any]:
        """Write one identical generated plan into every attached repository."""
        project = self.project(project_id)
        repositories = self.repositories(project_id)
        attached = [
            repository for repository in repositories if repository["local_path"]
        ]
        if not attached:
            raise DPTError("Attach at least one local repository before generating")

        working_fingerprint = plan_fingerprint(self.portable_plan(project_id))
        if not force:
            for repository in attached:
                try:
                    loaded, source_is_worktree = self._repository_plan_source(
                        project, repository
                    )
                except (DPTError, UnicodeError):
                    continue
                if not source_is_worktree:
                    location = (
                        f"{repository['github_full_name']} "
                        f"origin/{project['plan_source_branch']}:"
                        f"{project['generated_filename']}"
                    )
                    self._validate_publish_snapshot(
                        project,
                        repository,
                        loaded,
                        working_fingerprint,
                        location,
                    )

        revision = int(project["revision"]) + 1
        content = self.render_project(project_id, revision)
        digest = hashlib.sha256(content.encode()).hexdigest()
        fingerprint = plan_fingerprint(self.portable_plan(project_id, revision))
        targets: list[tuple[dict[str, Any], Path]] = []
        for repository in attached:
            target = Path(repository["local_path"]) / project["generated_filename"]
            if target.exists() and not force:
                current_digest = hashlib.sha256(target.read_bytes()).hexdigest()
                self._validate_publish_target(
                    project,
                    repository,
                    target,
                    current_digest,
                    digest,
                    working_fingerprint,
                )
            targets.append((repository, target))

        temporary_files: list[tuple[dict[str, Any], Path, Path]] = []
        try:
            for repository, target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{project['generated_filename']}.",
                    dir=target.parent,
                    text=True,
                )
                temporary = Path(temporary_name)
                with os.fdopen(
                    descriptor, "w", encoding="utf-8", newline="\n"
                ) as stream:
                    stream.write(content)
                temporary_files.append((repository, target, temporary))
            for _, target, temporary in temporary_files:
                os.replace(temporary, target)
        finally:
            for _, _, temporary in temporary_files:
                temporary.unlink(missing_ok=True)

        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET revision = ?, plan_fingerprint = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (revision, fingerprint, timestamp, project_id),
            )
            for repository, _ in targets:
                connection.execute(
                    """
                    UPDATE repositories SET last_generated_digest = ?, updated_at = ?
                    WHERE repository_id = ?
                    """,
                    (digest, timestamp, repository["repository_id"]),
                )
            self._activity(
                connection,
                project_id,
                "plan.generated",
                f"Generated revision {revision} into {len(targets)} repositories",
                {
                    "digest": digest,
                    "fingerprint": fingerprint,
                    "repositories": len(targets),
                },
            )
        return {"revision": revision, "digest": digest, "repositories": len(targets)}

    def activities(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent project activity."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM activities WHERE project_id = ?
                ORDER BY activity_id DESC LIMIT ?
                """,
                (project_id, min(limit, 500)),
            ).fetchall()
        result = []
        for row in rows:
            item = _row_dict(row)
            item["details"] = json.loads(item["details"])
            result.append(item)
        return result

    def create_device_authorization(
        self,
        device_code: str,
        user_code: str,
        verification_uri: str,
        expires_in: int,
        interval: int,
    ) -> dict[str, Any]:
        """Persist a pending GitHub device authorization securely."""
        authorization_id = new_token()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=expires_in)
        next_poll_at = now + timedelta(seconds=interval)
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM device_authorizations WHERE expires_at < ?",
                (now.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO device_authorizations(
                    authorization_digest, encrypted_device_code, user_code,
                    verification_uri, interval_seconds, next_poll_at,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest_token(authorization_id),
                    self.secrets.encrypt(device_code),
                    user_code,
                    verification_uri,
                    interval,
                    next_poll_at.isoformat(),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return {
            "authorization_id": authorization_id,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_at": expires_at.isoformat(),
            "interval": interval,
        }

    def device_authorization(self, authorization_id: str) -> dict[str, Any]:
        """Resolve a pending device authorization for one controlled poll."""
        now = datetime.now(UTC)
        digest = digest_token(authorization_id)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM device_authorizations
                WHERE authorization_digest = ?
                """,
                (digest,),
            ).fetchone()
            if row and row["expires_at"] <= now.isoformat():
                connection.execute(
                    "DELETE FROM device_authorizations WHERE authorization_digest = ?",
                    (digest,),
                )
                row = None
        if row is None:
            raise DPTError("GitHub authorization expired; start again")
        next_poll_at = datetime.fromisoformat(row["next_poll_at"])
        retry_after = max(0, int((next_poll_at - now).total_seconds() + 0.999))
        return {
            "device_code": self.secrets.decrypt(row["encrypted_device_code"]),
            "interval": int(row["interval_seconds"]),
            "retry_after": retry_after,
        }

    def schedule_device_poll(
        self,
        authorization_id: str,
        slow_down: bool = False,
    ) -> int:
        """Schedule the earliest allowed next GitHub device-flow poll."""
        digest = digest_token(authorization_id)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT interval_seconds FROM device_authorizations
                WHERE authorization_digest = ?
                """,
                (digest,),
            ).fetchone()
            if row is None:
                raise DPTError("GitHub authorization expired; start again")
            interval = int(row["interval_seconds"]) + (5 if slow_down else 0)
            next_poll_at = datetime.now(UTC) + timedelta(seconds=interval)
            connection.execute(
                """
                UPDATE device_authorizations
                SET interval_seconds = ?, next_poll_at = ?
                WHERE authorization_digest = ?
                """,
                (interval, next_poll_at.isoformat(), digest),
            )
        return interval

    def finish_device_authorization(self, authorization_id: str) -> None:
        """Remove a completed or rejected device authorization."""
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM device_authorizations WHERE authorization_digest = ?",
                (digest_token(authorization_id),),
            )

    def create_session(
        self,
        github_user_id: int,
        login: str,
        name: str | None,
        avatar_url: str | None,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
        refresh_token_expires_in: int | None,
    ) -> str:
        """Persist an encrypted GitHub session and return its cookie token."""
        token = new_token()
        now = datetime.now(UTC)
        timestamp = now.replace(microsecond=0).isoformat()
        access_token_expires_at = (
            (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None
        )
        refresh_token_expires_at = (
            (now + timedelta(seconds=refresh_token_expires_in)).isoformat()
            if refresh_token_expires_in
            else None
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_digest, github_user_id, github_login, github_name,
                    github_avatar_url, encrypted_access_token,
                    encrypted_refresh_token, access_token_expires_at,
                    refresh_token_expires_at, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest_token(token),
                    github_user_id,
                    login,
                    name,
                    avatar_url,
                    self.secrets.encrypt(access_token),
                    self.secrets.encrypt(refresh_token) if refresh_token else None,
                    access_token_expires_at,
                    refresh_token_expires_at,
                    timestamp,
                    timestamp,
                ),
            )
        return token

    def refresh_session(
        self,
        session_token: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
        refresh_token_expires_in: int | None,
    ) -> None:
        """Replace the expiring credentials for an authenticated session."""
        now = datetime.now(UTC)
        access_token_expires_at = (
            (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None
        )
        refresh_token_expires_at = (
            (now + timedelta(seconds=refresh_token_expires_in)).isoformat()
            if refresh_token_expires_in
            else None
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET encrypted_access_token = ?,
                    encrypted_refresh_token = COALESCE(?, encrypted_refresh_token),
                    access_token_expires_at = ?,
                    refresh_token_expires_at = COALESCE(?, refresh_token_expires_at),
                    last_seen_at = ?
                WHERE session_digest = ?
                """,
                (
                    self.secrets.encrypt(access_token),
                    self.secrets.encrypt(refresh_token) if refresh_token else None,
                    access_token_expires_at,
                    refresh_token_expires_at,
                    now.replace(microsecond=0).isoformat(),
                    digest_token(session_token),
                ),
            )

    def session(self, token: str | None) -> dict[str, Any] | None:
        """Resolve an authenticated session cookie."""
        if not token:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_digest = ?",
                (digest_token(token),),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE session_digest = ?",
                    (utc_now(), digest_token(token)),
                )
        if row is None:
            return None
        item = _row_dict(row)
        item["access_token"] = self.secrets.decrypt(item.pop("encrypted_access_token"))
        encrypted_refresh_token = item.pop("encrypted_refresh_token", None)
        item["refresh_token"] = (
            self.secrets.decrypt(encrypted_refresh_token)
            if encrypted_refresh_token
            else None
        )
        item.pop("session_digest")
        return item

    def delete_session(self, token: str | None) -> None:
        """Delete an authenticated session."""
        if not token:
            return
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE session_digest = ?",
                (digest_token(token),),
            )

    @staticmethod
    def _observed_state(artifacts: list[dict[str, Any]]) -> str | None:
        pulls = [item for item in artifacts if item["kind"] == "pull_request"]
        if any(item["is_merged"] for item in pulls):
            return "completed"
        if any(item["state"] == "open" for item in pulls):
            return "in_review"
        if pulls and all(item["state"] == "closed" for item in pulls):
            return "cancelled"
        issues = [item for item in artifacts if item["kind"] == "issue"]
        if any(item["state"] == "open" for item in issues):
            return "pending"
        return None

    @staticmethod
    def _computed_state(
        observed: str | None,
        dependencies_complete: bool,
        blockers_clear: bool = True,
    ) -> str:
        if observed in {"completed", "cancelled"}:
            return observed
        if not blockers_clear:
            return "blocked"
        if observed:
            return observed
        return "ready" if dependencies_complete else "blocked"

    @staticmethod
    def _reconciliation_findings(
        steps: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        findings: list[dict[str, str]] = []
        for step in steps:
            if step["dependencies_complete"]:
                continue
            pull_requests = [
                artifact
                for artifact in step["artifacts"]
                if artifact["kind"] == "pull_request"
                and (artifact["state"] == "open" or artifact["is_merged"])
            ]
            if pull_requests:
                findings.append(
                    {
                        "kind": "Dependency violation",
                        "severity": "error",
                        "step_id": step["step_id"],
                        "message": (
                            "A pull request exists while at least one prerequisite "
                            "is incomplete."
                        ),
                    }
                )
        return findings

    @staticmethod
    def _repository_with_clone_state(repository: dict[str, Any]) -> dict[str, Any]:
        path = repository.get("local_path")
        repository["clone_state"] = None
        repository["clone_error"] = None
        if path:
            try:
                state: CloneState = inspect_clone(path, repository["github_full_name"])
                repository["clone_state"] = {
                    "path": state.path,
                    "branch": state.branch,
                    "origin": state.origin,
                    "dirty": state.dirty,
                    "ahead": state.ahead,
                    "behind": state.behind,
                }
            except RuntimeError as error:
                repository["clone_error"] = str(error)
        return repository

    @staticmethod
    def _missing_dependencies(
        connection: sqlite3.Connection,
        project_id: str,
        dependencies: list[str],
    ) -> list[str]:
        if not dependencies:
            return []
        placeholders = ",".join("?" for _ in dependencies)
        query = (
            "SELECT step_id FROM steps WHERE project_id = ? "
            f"AND step_id IN ({placeholders})"
        )
        rows = connection.execute(
            query,
            (project_id, *dependencies),
        ).fetchall()
        found = {row["step_id"] for row in rows}
        return sorted(set(dependencies) - found)

    @staticmethod
    def _assert_acyclic(connection: sqlite3.Connection, project_id: str) -> None:
        rows = connection.execute(
            """
            SELECT step_id, depends_on_step_id FROM step_dependencies
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
        graph: dict[str, list[str]] = {}
        for row in rows:
            graph.setdefault(row["step_id"], []).append(row["depends_on_step_id"])

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise DPTError(f"Dependency cycle detected at {step_id}")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph.get(step_id, []):
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)

    @staticmethod
    def _touch_project(connection: sqlite3.Connection, project_id: str) -> None:
        connection.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ?",
            (utc_now(), project_id),
        )

    @staticmethod
    def _activity(
        connection: sqlite3.Connection,
        project_id: str | None,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO activities(
                project_id, kind, summary, details, actor, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                kind,
                summary,
                json.dumps(details or {}, sort_keys=True),
                actor,
                utc_now(),
            ),
        )
