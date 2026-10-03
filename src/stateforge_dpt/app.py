"""FastAPI application for StateForge DPT."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from stateforge_dpt.config import Settings, load_settings
from stateforge_dpt.database import Database
from stateforge_dpt.git import GitError
from stateforge_dpt.github import GitHubClient, GitHubError
from stateforge_dpt.models import (
    AttachCloneRequest,
    BlockerCreate,
    BranchCreate,
    CloneRepositoryRequest,
    GenerateRequest,
    PlanImportRequest,
    PlanSourceBranchUpdate,
    ProjectCreate,
    ProjectImportRequest,
    PullRequestCreate,
    RepositoryCreate,
    StepCreate,
    StepUpdate,
)
from stateforge_dpt.security import SecretStore
from stateforge_dpt.service import DPTError, DPTService

SESSION_COOKIE = "stateforge_dpt_session"
ReconciliationReporter = Callable[[dict[str, object]], Awaitable[None]]


def _github_full_name_from_api_url(value: object) -> str | None:
    """Extract owner/repository from a GitHub repository API URL."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"https://api\.github\.com/repos/([^/]+/[^/]+)", value)
    return match.group(1) if match else None


def _github_issue_url(value: str) -> tuple[str, int] | None:
    """Parse a canonical GitHub issue URL."""
    match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)/?",
        value.strip(),
    )
    if not match:
        return None
    return match.group(1), int(match.group(2))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the local DPT API."""
    resolved = settings or load_settings()
    database = Database(resolved.database_path)
    database.initialize()
    service = DPTService(database, SecretStore(resolved.data_dir))
    github = GitHubClient(resolved)

    app = FastAPI(
        title="StateForge DPT",
        version="0.1.1",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = resolved
    app.state.service = service
    app.state.github = github

    async def authenticated_session(
        session_token: str | None,
    ) -> dict[str, object]:
        current = service.session(session_token)
        if current is None or session_token is None:
            raise HTTPException(
                status_code=401, detail="GitHub authentication required"
            )
        expires_at = current.get("access_token_expires_at")
        if expires_at:
            refresh_at = datetime.fromisoformat(str(expires_at)) - timedelta(minutes=1)
            if datetime.now(UTC) >= refresh_at:
                refresh_token = current.get("refresh_token")
                if not refresh_token:
                    service.delete_session(session_token)
                    raise HTTPException(
                        status_code=401,
                        detail="GitHub session expired; sign in again",
                    )
                try:
                    refreshed = await github.refresh_user_token(str(refresh_token))
                except (GitHubError, httpx.HTTPError) as error:
                    service.delete_session(session_token)
                    raise HTTPException(
                        status_code=401,
                        detail="GitHub session could not be refreshed; sign in again",
                    ) from error
                service.refresh_session(
                    session_token,
                    refreshed.access_token,
                    refreshed.refresh_token,
                    refreshed.expires_in,
                    refreshed.refresh_token_expires_in,
                )
                current = service.session(session_token)
                if current is None:
                    raise HTTPException(
                        status_code=401,
                        detail="GitHub authentication required",
                    )
        return current

    async def reconcile_github_evidence(
        project_id: str,
        current: dict[str, object],
        report: ReconciliationReporter | None = None,
    ) -> dict[str, object]:
        """Refresh GitHub evidence while optionally reporting real work."""
        initial_project = service.project(project_id)
        initial_repositories = service.repositories(project_id)
        source_branch = str(initial_project["plan_source_branch"])
        attached_count = sum(
            bool(repository.get("local_path")) for repository in initial_repositories
        )
        if report:
            await report(
                {
                    "phase": "plan_fetching",
                    "title": f"Checking the plan on {source_branch}",
                    "detail": (
                        f"Fetching only {initial_project['generated_filename']} "
                        f"from origin/{source_branch} in {attached_count} local "
                        "clones. Checked-out branches and working files are not "
                        "changed."
                    ),
                    "repository": None,
                    "repository_index": 0,
                    "repository_count": attached_count,
                    "artifacts": 0,
                }
            )
        plan_result = await asyncio.to_thread(
            service.refresh_remote_plan_sources,
            project_id,
            str(current["access_token"]),
        )
        if report:
            await report(
                {
                    "phase": "plan_applying",
                    "title": str(plan_result["message"]),
                    "detail": (
                        "The checksummed Git plan was compared with SQLite. "
                        "DPT imports only a strictly newer, conflict-free revision."
                    ),
                    "repository": None,
                    "repository_index": int(plan_result["sources_checked"]),
                    "repository_count": attached_count,
                    "artifacts": 0,
                }
            )
        project = service.project(project_id)
        repositories = service.repositories(project_id)
        total = 0
        repository_count = len(repositories)
        artifact_batches: dict[str, list[dict[str, Any]]] = {}
        repositories_by_name = {
            str(repository["github_full_name"]).casefold(): repository
            for repository in repositories
        }

        for index, repository in enumerate(repositories, start=1):
            full_name = str(repository["github_full_name"])
            if report:
                await report(
                    {
                        "phase": "fetching",
                        "title": f"Checking {full_name}",
                        "detail": (
                            "Requesting DPT-labelled issues and pull requests, then "
                            "loading their conversations, reviews, and merge state "
                            "from GitHub."
                        ),
                        "repository": full_name,
                        "repository_index": index,
                        "repository_count": repository_count,
                        "artifacts": total,
                    }
                )
            artifacts = await github.repository_artifacts(
                str(current["access_token"]),
                full_name,
                str(project["slug"]),
            )
            artifact_batches[str(repository["repository_id"])] = artifacts
            if report:
                await report(
                    {
                        "phase": "caching",
                        "title": f"Saving evidence from {full_name}",
                        "detail": (
                            f"Replacing the local cache with {len(artifacts)} current "
                            "GitHub items so the roadmap uses the latest delivery "
                            "state."
                        ),
                        "repository": full_name,
                        "repository_index": index,
                        "repository_count": repository_count,
                        "artifacts": total + len(artifacts),
                    }
                )
            total += len(artifacts)

        dependency_records: list[dict[str, object]] = []
        milestone_issues: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for repository in repositories:
            for artifact in artifact_batches.get(
                str(repository["repository_id"]), []
            ):
                labels = {
                    str(label.get("name"))
                    for label in artifact.get("labels", [])
                    if isinstance(label, dict)
                }
                if "pull_request" not in artifact and any(
                    label.startswith("dpt:step:") for label in labels
                ):
                    milestone_issues.append((repository, artifact))

        if report:
            await report(
                {
                    "phase": "dependencies",
                    "title": "Resolving GitHub blockers",
                    "detail": (
                        f"Checking native blocked-by relationships for "
                        f"{len(milestone_issues)} milestone issues. Blockers remain "
                        "GitHub-owned and are not written into the portable plan."
                    ),
                    "repository": None,
                    "repository_index": repository_count,
                    "repository_count": repository_count,
                    "artifacts": total,
                }
            )

        for blocked_repository, milestone_issue in milestone_issues:
            blocked_full_name = str(blocked_repository["github_full_name"])
            blocking_issues = await github.issue_dependencies(
                str(current["access_token"]),
                blocked_full_name,
                int(milestone_issue["number"]),
            )
            for blocking_issue in blocking_issues:
                if "pull_request" in blocking_issue:
                    continue
                blocking_full_name = _github_full_name_from_api_url(
                    blocking_issue.get("repository_url")
                )
                if blocking_full_name is None:
                    continue
                blocking_repository = repositories_by_name.get(
                    blocking_full_name.casefold()
                )
                if blocking_repository is None:
                    continue
                blocking_repository_id = str(blocking_repository["repository_id"])
                batch = artifact_batches[blocking_repository_id]
                if not any(
                    int(item["id"]) == int(blocking_issue["id"]) for item in batch
                ):
                    hydrated = await github.issue_artifact(
                        str(current["access_token"]),
                        blocking_full_name,
                        int(blocking_issue["number"]),
                    )
                    batch.append(hydrated)
                    total += 1
                dependency_records.append(
                    {
                        "blocked_repository": blocked_full_name,
                        "blocked_number": int(milestone_issue["number"]),
                        "blocking_repository": blocking_full_name,
                        "blocking_number": int(blocking_issue["number"]),
                    }
                )

        for repository in repositories:
            service.record_github_artifacts(
                project_id,
                str(repository["repository_id"]),
                artifact_batches.get(str(repository["repository_id"]), []),
            )
        service.record_issue_dependencies(project_id, dependency_records)

        if report:
            await report(
                {
                    "phase": "validating",
                    "title": "Rebuilding project state",
                    "detail": (
                        "Applying refreshed delivery evidence and GitHub blocker "
                        "relationships to every milestone, checking dependency "
                        "order, and recalculating findings."
                    ),
                    "repository": None,
                    "repository_index": repository_count,
                    "repository_count": repository_count,
                    "artifacts": total,
                }
            )
        service.dashboard(project_id)
        return {
            "repositories": repository_count,
            "artifacts": total,
            "plan_status": plan_result["status"],
            "plan_message": plan_result["message"],
            "plan_revision": plan_result["revision"],
            "plan_source_branch": plan_result["branch"],
            "plan_errors": plan_result["errors"],
        }

    @app.exception_handler(DPTError)
    async def handle_dpt_error(_request: Request, error: DPTError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    async def public_config() -> dict[str, object]:
        return {
            "github_authentication": "device_flow",
            "generated_filename": "STATEFORGE_DPT.md",
        }

    @app.get("/api/session")
    async def session(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        try:
            current = await authenticated_session(session_token)
        except HTTPException as error:
            if error.status_code != 401:
                raise
            return {"authenticated": False, "user": None}
        return {
            "authenticated": True,
            "user": {
                "id": current["github_user_id"],
                "login": current["github_login"],
                "name": current["github_name"],
                "avatar_url": current["github_avatar_url"],
            },
        }

    @app.post("/api/auth/github/device")
    async def start_github_device_authorization() -> dict[str, object]:
        try:
            authorization = await github.start_device_authorization()
        except (GitHubError, httpx.HTTPError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return service.create_device_authorization(
            authorization.device_code,
            authorization.user_code,
            authorization.verification_uri,
            authorization.expires_in,
            authorization.interval,
        )

    @app.post("/api/auth/github/device/{authorization_id}/poll")
    async def poll_github_device_authorization(
        authorization_id: str,
    ) -> JSONResponse:
        authorization = service.device_authorization(authorization_id)
        if authorization["retry_after"]:
            return JSONResponse(
                {
                    "status": "pending",
                    "retry_after": authorization["retry_after"],
                }
            )
        try:
            result = await github.poll_device_authorization(
                str(authorization["device_code"])
            )
        except (GitHubError, httpx.HTTPError) as error:
            service.finish_device_authorization(authorization_id)
            raise HTTPException(status_code=502, detail=str(error)) from error
        if result.status != "complete":
            retry_after = service.schedule_device_poll(
                authorization_id,
                slow_down=result.status == "slow_down",
            )
            return JSONResponse({"status": "pending", "retry_after": retry_after})
        identity = result.identity
        if identity is None:
            raise HTTPException(status_code=502, detail="GitHub identity is missing")
        service.finish_device_authorization(authorization_id)
        session_token = service.create_session(
            identity.user_id,
            identity.login,
            identity.name,
            identity.avatar_url,
            identity.access_token,
            identity.refresh_token,
            identity.expires_in,
            identity.refresh_token_expires_in,
        )
        response = JSONResponse(
            {
                "status": "complete",
                "user": {
                    "id": identity.user_id,
                    "login": identity.login,
                    "name": identity.name,
                    "avatar_url": identity.avatar_url,
                },
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            session_token,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 24 * 30,
        )
        return response

    @app.post("/api/auth/logout", status_code=204)
    async def logout(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Response:
        service.delete_session(session_token)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/api/github/repositories")
    async def github_repositories(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> list[dict[str, object]]:
        current = await authenticated_session(session_token)
        try:
            values = await github.repositories(str(current["access_token"]))
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return [
            {
                "id": value["id"],
                "full_name": value["full_name"],
                "private": value["private"],
                "default_branch": value["default_branch"],
                "clone_url": value["clone_url"],
                "updated_at": value["updated_at"],
                "owner_avatar_url": value["owner"]["avatar_url"],
            }
            for value in values
        ]

    @app.get("/api/projects")
    async def projects() -> list[dict[str, object]]:
        return service.projects()

    @app.post("/api/projects", status_code=201)
    async def create_project(request: ProjectCreate) -> dict[str, object]:
        return service.create_project(request)

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def remove_project(project_id: str) -> Response:
        service.remove_project(project_id)
        return Response(status_code=204)

    @app.post("/api/projects/import", status_code=201)
    async def import_project(request: ProjectImportRequest) -> dict[str, object]:
        return service.import_project_from_clone(request.local_path)

    @app.post("/api/projects/import/inspect")
    async def inspect_project_import(
        request: ProjectImportRequest,
    ) -> dict[str, object]:
        return service.inspect_project_import(request.local_path)

    @app.get("/api/projects/{project_id}")
    async def project_dashboard(project_id: str) -> dict[str, object]:
        return service.dashboard(project_id)

    @app.put("/api/projects/{project_id}/plan/source-branch")
    async def update_plan_source_branch(
        project_id: str,
        request: PlanSourceBranchUpdate,
    ) -> dict[str, object]:
        return service.update_plan_source_branch(
            project_id,
            request.plan_source_branch,
        )

    @app.get("/api/projects/{project_id}/repositories")
    async def project_repositories(project_id: str) -> list[dict[str, object]]:
        return service.repositories(project_id)

    @app.get("/api/projects/{project_id}/plan/sources")
    async def project_plan_sources(project_id: str) -> dict[str, object]:
        return service.project_plan_sources(project_id)

    @app.post("/api/projects/{project_id}/plan/import")
    async def import_project_plan(
        project_id: str,
        request: PlanImportRequest,
    ) -> dict[str, object]:
        return service.sync_project_from_repository(
            project_id,
            request.repository_id,
            request.force,
        )

    @app.post("/api/projects/{project_id}/repositories", status_code=201)
    async def add_repository(
        project_id: str,
        request: RepositoryCreate,
    ) -> dict[str, object]:
        return service.add_repository(project_id, request)

    @app.delete("/api/repositories/{repository_id}", status_code=204)
    async def remove_repository(repository_id: str) -> Response:
        service.remove_repository(repository_id)
        return Response(status_code=204)

    @app.post("/api/repositories/{repository_id}/attach")
    async def attach_repository(
        repository_id: str,
        request: AttachCloneRequest,
    ) -> dict[str, object]:
        try:
            return service.attach_clone(repository_id, request.local_path)
        except GitError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/repositories/{repository_id}/clone")
    async def clone_managed_repository(
        repository_id: str,
        request: CloneRepositoryRequest,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token) if session_token else None
        token = str(current["access_token"]) if current else None
        repository = service.repository(repository_id)
        destination_path = request.destination_path
        if destination_path is None and request.destination_root is not None:
            repository_name = str(repository["github_full_name"]).rsplit("/", 1)[-1]
            destination_path = str(Path(request.destination_root) / repository_name)
        if destination_path is None:
            raise HTTPException(status_code=422, detail="Clone destination is required")
        try:
            return service.clone(repository_id, destination_path, token)
        except GitError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/repositories/{repository_id}/clone-suggestion")
    async def repository_clone_suggestion(repository_id: str) -> dict[str, str]:
        repository = service.repository(repository_id)
        project = service.project(str(repository["project_id"]))
        project_name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(project["name"]))
        project_name = project_name.strip("-") or str(project["project_id"])
        repository_name = str(repository["github_full_name"]).rsplit("/", 1)[-1]
        destination = (
            resolved.working_directory / "projects" / project_name / repository_name
        )
        return {"destination_path": str(destination)}

    @app.post("/api/projects/{project_id}/steps", status_code=201)
    async def add_step(project_id: str, request: StepCreate) -> dict[str, object]:
        return service.create_step(project_id, request)

    @app.put("/api/projects/{project_id}/steps/{step_id}")
    async def update_step(
        project_id: str,
        step_id: str,
        request: StepUpdate,
    ) -> dict[str, object]:
        return service.update_step(project_id, step_id, request)

    @app.delete("/api/projects/{project_id}/steps/{step_id}", status_code=204)
    async def delete_step(project_id: str, step_id: str) -> Response:
        service.delete_step(project_id, step_id)
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/steps/{step_id}/github/issues")
    async def create_step_issue(
        project_id: str,
        step_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        project = service.project(project_id)
        step = service.step(project_id, step_id)
        if step["computed_state"] == "blocked":
            raise HTTPException(
                status_code=409,
                detail="Complete this step's dependencies before opening its Issue",
            )
        if any(artifact["kind"] == "issue" for artifact in step["artifacts"]):
            raise HTTPException(
                status_code=409,
                detail="A GitHub Issue is already linked to this step",
            )

        dependencies = step["depends_on"]
        issue_body = "\n".join(
            [
                "## Required result",
                "",
                step["required_result"],
                "",
                "## Planned branch",
                "",
                f"`{step['branch_name']}`",
                "",
                "## Dependencies",
                "",
                *(f"- {dependency}" for dependency in dependencies),
                *([] if dependencies else ["None."]),
            ]
        )
        labels = [
            f"dpt:project:{project['slug']}",
            f"dpt:release:{project['release'].lower()}",
            f"dpt:step:{step_id}",
        ]
        try:
            issue = await github.create_issue(
                str(current["access_token"]),
                step["github_full_name"],
                step["title"],
                issue_body,
                labels,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "number": issue["number"],
            "html_url": issue["html_url"],
        }

    @app.post(
        "/api/projects/{project_id}/steps/{step_id}/blockers",
        status_code=201,
    )
    async def create_or_link_blocker(
        project_id: str,
        step_id: str,
        request: BlockerCreate,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        project = service.project(project_id)
        step = service.step(project_id, step_id)
        milestone_issue = next(
            (
                artifact
                for artifact in step["artifacts"]
                if artifact["kind"] == "issue"
            ),
            None,
        )
        if milestone_issue is None:
            raise HTTPException(
                status_code=409,
                detail="Create the milestone Issue before adding a GitHub blocker",
            )
        repositories = service.repositories(project_id)
        repositories_by_name = {
            str(repository["github_full_name"]).casefold(): repository
            for repository in repositories
        }

        if request.issue_url:
            parsed = _github_issue_url(request.issue_url)
            if parsed is None:
                raise HTTPException(
                    status_code=422,
                    detail="Enter a GitHub Issue URL such as https://github.com/owner/repository/issues/12",
                )
            blocking_full_name, blocking_number = parsed
            blocking_repository = repositories_by_name.get(
                blocking_full_name.casefold()
            )
            if blocking_repository is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "A blocker must belong to a repository managed by this "
                        "project"
                    ),
                )
            try:
                blocking_issue = await github.issue_artifact(
                    str(current["access_token"]),
                    blocking_full_name,
                    blocking_number,
                )
            except GitHubError as error:
                raise HTTPException(status_code=502, detail=str(error)) from error
            if "pull_request" in blocking_issue:
                raise HTTPException(
                    status_code=422,
                    detail="A blocker must be a GitHub Issue, not a pull request",
                )
        else:
            blocking_repository = next(
                (
                    repository
                    for repository in repositories
                    if repository["repository_id"] == request.repository_id
                ),
                None,
            )
            if blocking_repository is None or request.title is None:
                raise HTTPException(status_code=404, detail="Repository not found")
            blocking_full_name = str(blocking_repository["github_full_name"])
            release = str(project["release"])
            title = request.title.strip()
            if not re.match(r"^\[bug]\[[^]]+]", title, flags=re.IGNORECASE):
                title = re.sub(r"^\[bug]\s*", "", title, flags=re.IGNORECASE)
                title = f"[BUG][{release.upper()}] {title}"
            labels = [
                f"dpt:project:{project['slug']}",
                f"dpt:release:{release.casefold()}",
                "bug",
            ]
            try:
                created = await github.create_issue(
                    str(current["access_token"]),
                    blocking_full_name,
                    title,
                    request.body,
                    labels,
                )
                blocking_issue = await github.issue_artifact(
                    str(current["access_token"]),
                    blocking_full_name,
                    int(created["number"]),
                )
            except GitHubError as error:
                raise HTTPException(status_code=502, detail=str(error)) from error

        if (
            blocking_full_name.casefold()
            == str(step["github_full_name"]).casefold()
            and int(blocking_issue["number"]) == int(milestone_issue["number"])
        ):
            raise HTTPException(
                status_code=422,
                detail="A milestone cannot block itself",
            )
        if any(
            str(blocker["github_full_name"]).casefold()
            == blocking_full_name.casefold()
            and int(blocker["number"]) == int(blocking_issue["number"])
            for blocker in step["blockers"]
        ):
            raise HTTPException(
                status_code=409,
                detail="This GitHub Issue already blocks the milestone",
            )

        try:
            await github.add_issue_dependency(
                str(current["access_token"]),
                str(step["github_full_name"]),
                int(milestone_issue["number"]),
                int(blocking_issue["id"]),
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        blocker_artifact_id = service.upsert_github_artifact(
            project_id,
            str(blocking_repository["repository_id"]),
            blocking_issue,
        )
        service.record_issue_dependency(
            project_id,
            {
                "blocked_repository": step["github_full_name"],
                "blocked_number": milestone_issue["number"],
                "blocking_repository": blocking_full_name,
                "blocking_number": blocking_issue["number"],
            },
        )
        return service.blocker(project_id, blocker_artifact_id)

    @app.delete(
        "/api/projects/{project_id}/steps/{step_id}/blockers/{blocker_id}",
        status_code=204,
    )
    async def unlink_blocker(
        project_id: str,
        step_id: str,
        blocker_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Response:
        current = await authenticated_session(session_token)
        step = service.step(project_id, step_id)
        blocker = service.blocker(project_id, blocker_id)
        if step_id not in blocker["blocked_step_ids"]:
            raise HTTPException(status_code=404, detail="Blocker link not found")
        milestone_issue = next(
            artifact for artifact in step["artifacts"] if artifact["kind"] == "issue"
        )
        try:
            await github.remove_issue_dependency(
                str(current["access_token"]),
                str(step["github_full_name"]),
                int(milestone_issue["number"]),
                int(blocker["github_issue_id"]),
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        service.remove_issue_dependency(
            project_id,
            str(milestone_issue["artifact_id"]),
            blocker_id,
        )
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/blockers/{blocker_id}/github/branches")
    async def blocker_repository_branches(
        project_id: str,
        blocker_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        project = service.project(project_id)
        blocker = service.blocker(project_id, blocker_id)
        repository = service.repository(str(blocker["repository_id"]))
        try:
            branches = await github.repository_branches(
                str(current["access_token"]), str(blocker["github_full_name"])
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        source_branch = str(project["plan_source_branch"])
        default_branch = repository.get("github_default_branch")
        if source_branch in branches:
            default_base = source_branch
        elif isinstance(default_branch, str) and default_branch in branches:
            default_base = default_branch
        else:
            default_base = branches[0] if branches else source_branch
        return {
            "branches": branches,
            "planned_branch": blocker["suggested_branch"],
            "default_base": default_base,
        }

    @app.get(
        "/api/projects/{project_id}/blockers/{blocker_id}/github/branch-comparison"
    )
    async def compare_blocker_branches(
        project_id: str,
        blocker_id: str,
        base_branch: str,
        head_branch: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        blocker = service.blocker(project_id, blocker_id)
        if base_branch == head_branch:
            raise HTTPException(
                status_code=422,
                detail="The base and compare branches must be different",
            )
        try:
            return await github.compare_branches(
                str(current["access_token"]),
                str(blocker["github_full_name"]),
                base_branch,
                head_branch,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post(
        "/api/projects/{project_id}/blockers/{blocker_id}/github/branches",
        status_code=201,
    )
    async def create_blocker_branch(
        project_id: str,
        blocker_id: str,
        request: BranchCreate,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        blocker = service.blocker(project_id, blocker_id)
        branch_name = str(blocker["suggested_branch"])
        if request.base_branch == branch_name:
            raise HTTPException(
                status_code=422,
                detail="The base and fix branches must be different",
            )
        try:
            await github.create_branch(
                str(current["access_token"]),
                str(blocker["github_full_name"]),
                request.base_branch,
                branch_name,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"name": branch_name, "base_branch": request.base_branch}

    @app.post(
        "/api/projects/{project_id}/blockers/{blocker_id}/github/pull-requests",
        status_code=201,
    )
    async def create_blocker_pull_request(
        project_id: str,
        blocker_id: str,
        request: PullRequestCreate,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        project = service.project(project_id)
        blocker = service.blocker(project_id, blocker_id)
        if not blocker["is_active"]:
            raise HTTPException(status_code=409, detail="This blocker is resolved")
        if request.base_branch == request.head_branch:
            raise HTTPException(
                status_code=422,
                detail="The base and compare branches must be different",
            )
        if any(
            artifact["kind"] == "pull_request"
            and (artifact["state"] == "open" or artifact["is_merged"])
            for artifact in blocker["artifacts"]
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "An active or merged pull request is already linked to this "
                    "blocker"
                ),
            )
        try:
            comparison = await github.compare_branches(
                str(current["access_token"]),
                str(blocker["github_full_name"]),
                request.base_branch,
                request.head_branch,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        if int(comparison["ahead_by"]) == 0:
            raise HTTPException(
                status_code=422,
                detail="The fix branch has no commits ahead of the base branch",
            )
        blocked_steps = ", ".join(blocker["blocked_step_ids"])
        body = "\n".join(
            [
                "## Blocker fix",
                "",
                f"Resolves a GitHub blocker affecting DPT milestones: {blocked_steps}.",
                "",
                f"Fixes {blocker['github_full_name']}#{blocker['number']}",
            ]
        )
        labels = [
            f"dpt:project:{project['slug']}",
            f"dpt:release:{str(project['release']).casefold()}",
            "bug",
        ]
        try:
            pull_request = await github.create_pull_request(
                str(current["access_token"]),
                str(blocker["github_full_name"]),
                str(blocker["title"]),
                body,
                request.base_branch,
                request.head_branch,
                labels,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "number": pull_request["number"],
            "html_url": pull_request["html_url"],
        }

    @app.get("/api/projects/{project_id}/steps/{step_id}/github/branches")
    async def step_repository_branches(
        project_id: str,
        step_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        project = service.project(project_id)
        step = service.step(project_id, step_id)
        repository = next(
            item
            for item in service.repositories(project_id)
            if item["repository_id"] == step["repository_id"]
        )
        try:
            branches = await github.repository_branches(
                str(current["access_token"]),
                step["github_full_name"],
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        integration_branch = f"{str(project['release']).lower()}/implementation"
        repository_default = repository.get("github_default_branch")
        if integration_branch in branches:
            default_base = integration_branch
        elif isinstance(repository_default, str) and repository_default in branches:
            default_base = repository_default
        else:
            default_base = branches[0] if branches else integration_branch
        return {
            "branches": branches,
            "planned_branch": step["branch_name"],
            "default_base": default_base,
        }

    @app.get("/api/projects/{project_id}/steps/{step_id}/github/branch-comparison")
    async def compare_step_branches(
        project_id: str,
        step_id: str,
        base_branch: str,
        head_branch: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        step = service.step(project_id, step_id)
        if base_branch == head_branch:
            raise HTTPException(
                status_code=422,
                detail="The base and compare branches must be different",
            )
        try:
            return await github.compare_branches(
                str(current["access_token"]),
                str(step["github_full_name"]),
                base_branch,
                head_branch,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post(
        "/api/projects/{project_id}/steps/{step_id}/github/branches",
        status_code=201,
    )
    async def create_step_branch(
        project_id: str,
        step_id: str,
        request: BranchCreate,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        step = service.step(project_id, step_id)
        if not step["dependencies_complete"]:
            raise HTTPException(
                status_code=409,
                detail="Complete this step's dependencies before creating its branch",
            )
        if not step["blockers_clear"]:
            raise HTTPException(
                status_code=409,
                detail="Resolve this step's GitHub blockers before creating its branch",
            )
        branch_name = str(step["branch_name"])
        if request.base_branch == branch_name:
            raise HTTPException(
                status_code=422,
                detail="The base and planned branches must be different",
            )
        try:
            await github.create_branch(
                str(current["access_token"]),
                str(step["github_full_name"]),
                request.base_branch,
                branch_name,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "name": branch_name,
            "base_branch": request.base_branch,
        }

    @app.post(
        "/api/projects/{project_id}/steps/{step_id}/github/pull-requests",
        status_code=201,
    )
    async def create_step_pull_request(
        project_id: str,
        step_id: str,
        request: PullRequestCreate,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        project = service.project(project_id)
        step = service.step(project_id, step_id)
        if not step["dependencies_complete"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Complete this step's dependencies before opening its pull request"
                ),
            )
        if not step["blockers_clear"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Resolve this step's GitHub blockers before opening its pull "
                    "request"
                ),
            )
        if request.base_branch == request.head_branch:
            raise HTTPException(
                status_code=422,
                detail="The base and compare branches must be different",
            )
        active_pull_request = next(
            (
                artifact
                for artifact in step["artifacts"]
                if artifact["kind"] == "pull_request"
                and (artifact["state"] == "open" or artifact["is_merged"])
            ),
            None,
        )
        if active_pull_request:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An active or merged pull request is already linked to this step"
                ),
            )
        issue = next(
            (artifact for artifact in step["artifacts"] if artifact["kind"] == "issue"),
            None,
        )
        if issue is None:
            raise HTTPException(
                status_code=409,
                detail="Create the milestone issue before opening its pull request",
            )

        try:
            comparison = await github.compare_branches(
                str(current["access_token"]),
                str(step["github_full_name"]),
                request.base_branch,
                request.head_branch,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        if int(comparison["ahead_by"]) == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The compare branch has no commits ahead of the base branch. "
                    "Push at least one implementation commit before opening the "
                    "pull request."
                ),
            )

        body = "\n".join(
            [
                "## Required result",
                "",
                step["required_result"],
                "",
                "## Implementation step",
                "",
                f"`{step_id}`",
                "",
                f"Closes #{issue['number']}",
            ]
        )
        labels = [
            f"dpt:project:{project['slug']}",
            f"dpt:release:{str(project['release']).lower()}",
            f"dpt:step:{step_id}",
        ]
        try:
            pull_request = await github.create_pull_request(
                str(current["access_token"]),
                step["github_full_name"],
                step["title"],
                body,
                request.base_branch,
                request.head_branch,
                labels,
            )
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "number": pull_request["number"],
            "html_url": pull_request["html_url"],
        }

    @app.get("/api/projects/{project_id}/plan/preview")
    async def preview_plan(project_id: str) -> Response:
        return Response(service.render_project(project_id), media_type="text/markdown")

    @app.post("/api/projects/{project_id}/plan/generate")
    async def generate_plan(
        project_id: str,
        request: GenerateRequest,
    ) -> dict[str, object]:
        return service.generate_project_files(project_id, request.force)

    @app.post("/api/projects/{project_id}/reconcile")
    async def reconcile_project(
        project_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        try:
            return await reconcile_github_evidence(project_id, current)
        except GitHubError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post("/api/projects/{project_id}/reconcile/stream")
    async def stream_project_reconciliation(
        project_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> StreamingResponse:
        current = await authenticated_session(session_token)

        async def events() -> AsyncIterator[bytes]:
            queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

            async def report(event: dict[str, object]) -> None:
                await queue.put({"type": "progress", **event})

            async def run() -> None:
                try:
                    result = await reconcile_github_evidence(
                        project_id,
                        current,
                        report,
                    )
                    await queue.put({"type": "complete", **result})
                except Exception as error:
                    await queue.put({"type": "error", "message": str(error)})

            task = asyncio.create_task(run())
            try:
                while True:
                    event = await queue.get()
                    yield f"{json.dumps(event)}\n".encode()
                    if event["type"] in {"complete", "error"}:
                        break
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/projects/{project_id}/activity")
    async def project_activity(project_id: str) -> list[dict[str, object]]:
        return service.activities(project_id)

    frontend = resolved.frontend_dir
    if (frontend / "index.html").is_file():
        assets = frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        async def frontend_fallback(path: str) -> FileResponse:
            candidate = frontend / path
            if path and candidate.is_file() and frontend in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")

    return app
