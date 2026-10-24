"""FastAPI application for StateForge DPT."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from stateforge_dpt import __version__
from stateforge_dpt.config import Settings, load_settings
from stateforge_dpt.database import Database
from stateforge_dpt.git import GitError
from stateforge_dpt.github import (
    ArtifactDelta,
    GitHubAppPlanPublisher,
    GitHubClient,
    GitHubError,
)
from stateforge_dpt.mcp_server import create_mcp_server
from stateforge_dpt.models import (
    AttachCloneRequest,
    BlockerCreate,
    BranchCreate,
    CloneRepositoryRequest,
    ContextApprovalRequest,
    ContextContractUpdate,
    GenerateRequest,
    PlanImportRequest,
    PlanSourceBranchUpdate,
    ProjectCreate,
    ProjectImportRequest,
    PullRequestCreate,
    RepositoryCreate,
    StepCreate,
    StepUpdate,
    VerificationRecordRequest,
    VerificationRunRequest,
)
from stateforge_dpt.proposals import (
    PlanChangeProposalRequest,
    PlanProposalService,
    ProposalDecisionRequest,
)
from stateforge_dpt.security import SecretStore
from stateforge_dpt.security_evidence.service import SecurityEvidenceService
from stateforge_dpt.service import DPTError, DPTService

SESSION_COOKIE = "stateforge_dpt_session"
ReconciliationReporter = Callable[[dict[str, object]], Awaitable[None]]
LOGGER = logging.getLogger(__name__)


def _workflow_run_state(run: dict[str, Any]) -> str:
    """Normalize a GitHub workflow run into one dashboard state."""
    status = str(run.get("status") or "").lower()
    if status == "in_progress":
        return "in_progress"
    if status != "completed":
        return "queued"

    conclusion = str(run.get("conclusion") or "").lower()
    if conclusion == "success":
        return "success"
    if conclusion == "cancelled":
        return "cancelled"
    if conclusion in {"neutral", "skipped"}:
        return "skipped"
    return "failure"


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
    proposals = PlanProposalService(database, service)
    github = GitHubClient(resolved)
    security_evidence = SecurityEvidenceService(database, github)
    plan_publisher = GitHubAppPlanPublisher(resolved)
    reconciliation_tasks: dict[str, asyncio.Task[dict[str, object]]] = {}
    reconciliation_reporters: dict[str, set[ReconciliationReporter]] = {}
    reconciliation_progress: dict[str, dict[str, object]] = {}
    reconciliation_completed_at: dict[str, float] = {}
    reconciliation_full_at: dict[str, float] = {}
    reconciliation_failures: dict[str, int] = {}
    security_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
    background_session_token: str | None = None
    reconciliation_wakeup: asyncio.Event | None = None

    mcp_server = None
    mcp_application = None
    if resolved.mcp_enabled:
        # Keep the intentionally quiet DPT terminal banner free of dependency
        # lifecycle chatter; genuine errors remain visible.
        logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.ERROR)
        mcp_server = create_mcp_server(
            service,
            proposals,
            max_read_bytes=resolved.mcp_max_read_bytes,
        )
        loopback_hosts = {
            f"127.0.0.1:{resolved.port}",
            f"localhost:{resolved.port}",
            f"[::1]:{resolved.port}",
        }
        allowed_hosts = sorted(loopback_hosts | {f"{resolved.host}:{resolved.port}"})
        allowed_origins = sorted(
            {
                f"http://127.0.0.1:{resolved.port}",
                f"http://localhost:{resolved.port}",
                f"http://[::1]:{resolved.port}",
                f"http://{resolved.host}:{resolved.port}",
            }
        )
        mcp_application = mcp_server.streamable_http_app(
            streamable_http_path=resolved.mcp_path,
            json_response=True,
            host=resolved.host,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
            ),
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        nonlocal reconciliation_wakeup
        reconciliation_wakeup = asyncio.Event()
        background_task = (
            asyncio.create_task(automatic_reconciliation_loop())
            if resolved.auto_reconcile_enabled
            else None
        )
        try:
            if mcp_server is None:
                yield
            else:
                async with mcp_server.session_manager.run():
                    yield
        finally:
            reconciliation_wakeup = None
            if background_task is not None:
                background_task.cancel()
                await asyncio.gather(background_task, return_exceptions=True)
            for task in security_tasks.values():
                task.cancel()
            await asyncio.gather(*security_tasks.values(), return_exceptions=True)

    app = FastAPI(
        title="StateForge DPT",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.service = service
    app.state.github = github
    app.state.security_evidence = security_evidence
    app.state.plan_publisher = plan_publisher
    app.state.proposals = proposals
    app.state.mcp_server = mcp_server

    async def authenticated_session(
        session_token: str | None,
    ) -> dict[str, object]:
        nonlocal background_session_token
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
        background_session_token = session_token
        if reconciliation_wakeup is not None:
            reconciliation_wakeup.set()
        return current

    def human_actor(session_token: str | None) -> str:
        """Identify the local human without requiring GitHub authentication."""
        current = service.session(session_token)
        if current and current.get("github_login"):
            return f"github:{current['github_login']}"
        return "local-human"

    def require_same_origin(request: Request) -> None:
        """Reject browser decision requests submitted by another origin."""
        origin = request.headers.get("origin")
        if not origin:
            return
        parsed = urlsplit(origin)
        if parsed.scheme not in {
            "http",
            "https",
        } or parsed.netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="Same-origin request required")

    async def attach_repository_ci_status(
        dashboard: dict[str, Any],
        current: dict[str, object] | None,
    ) -> None:
        """Attach live GitHub Actions health without persisting telemetry."""
        project = dashboard["project"]
        branch = str(project.get("plan_source_branch") or "mvp/implementation")
        repositories = dashboard["repositories"]

        async def status(repository: dict[str, Any]) -> dict[str, object]:
            base: dict[str, object] = {"branch": branch}
            if current is None:
                return {**base, "state": "authentication_required"}
            try:
                run = await github.latest_workflow_run(
                    str(current["access_token"]),
                    str(repository["github_full_name"]),
                    branch,
                )
            except (GitHubError, httpx.HTTPError) as error:
                return {
                    **base,
                    "state": "unavailable",
                    "message": str(error),
                }
            if run is None:
                return {**base, "state": "not_found"}
            state = _workflow_run_state(run)
            updated_at = str(
                run.get("updated_at")
                or run.get("run_started_at")
                or run.get("created_at")
                or ""
            )
            return {
                **base,
                "state": state,
                "workflow_name": str(run.get("name") or "GitHub Actions"),
                "run_number": int(run.get("run_number") or 0),
                "run_attempt": int(run.get("run_attempt") or 1),
                "event": str(run.get("event") or ""),
                "status": str(run.get("status") or ""),
                "conclusion": str(run.get("conclusion") or ""),
                "head_sha": str(run.get("head_sha") or ""),
                "html_url": str(run.get("html_url") or ""),
                "updated_at": updated_at,
                "completed_at": (
                    updated_at if state not in {"queued", "in_progress"} else ""
                ),
            }

        statuses = await asyncio.gather(
            *(status(repository) for repository in repositories)
        )
        for repository, ci_status in zip(repositories, statuses, strict=True):
            repository["ci_cd"] = ci_status

    def attach_repository_security_status(dashboard: dict[str, Any]) -> None:
        """Attach persisted summaries without starting scans on a dashboard read."""
        project = dashboard["project"]
        branch = str(project.get("plan_source_branch") or "mvp/implementation")
        summaries = security_evidence.repository_summaries(
            str(project["project_id"]), branch
        )
        for repository in dashboard["repositories"]:
            repository["security"] = summaries[str(repository["repository_id"])]

    def schedule_security_refresh(
        project_id: str,
        current: dict[str, object],
        *,
        force: bool,
    ) -> asyncio.Task[dict[str, Any]]:
        """Share one background assessment across reconciliation and the UI."""
        existing = security_tasks.get(project_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(
            security_evidence.refresh_project(
                str(current["access_token"]),
                service.project(project_id),
                service.repositories(project_id),
                force=force,
            )
        )
        security_tasks[project_id] = task

        def finish(completed: asyncio.Task[dict[str, Any]]) -> None:
            if security_tasks.get(project_id) is completed:
                security_tasks.pop(project_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finish)
        return task

    async def reconcile_github_evidence(
        project_id: str,
        current: dict[str, object],
        report: ReconciliationReporter | None = None,
        *,
        force_full: bool = False,
    ) -> dict[str, object]:
        """Refresh GitHub evidence while optionally reporting real work."""
        started_at = asyncio.get_running_loop().time()
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
        repository_count = len(repositories)
        repositories_by_name = {
            str(repository["github_full_name"]).casefold(): repository
            for repository in repositories
        }
        cached_by_repository = {
            str(repository["repository_id"]): await asyncio.to_thread(
                service.github_artifact_metadata,
                project_id,
                str(repository["repository_id"]),
            )
            for repository in repositories
        }

        fetch_limit = asyncio.Semaphore(4)
        hydration_limit = asyncio.Semaphore(12)
        progress_lock = asyncio.Lock()
        artifacts_loaded = 0

        async def fetch_repository(
            index: int,
            repository: dict[str, Any],
            github_http: httpx.AsyncClient,
        ) -> tuple[str, Any]:
            nonlocal artifacts_loaded
            full_name = str(repository["github_full_name"])
            repository_id = str(repository["repository_id"])
            if report:
                await report(
                    {
                        "phase": "fetching",
                        "title": f"Checking {full_name}",
                        "detail": (
                            "Comparing DPT-labelled issues and pull requests with "
                            "the local cache. Only new or changed evidence is loaded "
                            "in full."
                        ),
                        "repository": full_name,
                        "repository_index": index,
                        "repository_count": repository_count,
                        "artifacts": artifacts_loaded,
                    }
                )
            async with fetch_limit:
                delta = await github.repository_artifact_delta(
                    str(current["access_token"]),
                    full_name,
                    str(project["slug"]),
                    {
                        artifact_id: str(metadata["updated_at"])
                        for artifact_id, metadata in cached_by_repository[
                            repository_id
                        ].items()
                    },
                    force_full=force_full,
                    client=github_http,
                    hydration_limit=hydration_limit,
                )
            async with progress_lock:
                artifacts_loaded += len(delta.listed_items)
                current_total = artifacts_loaded
            if report:
                await report(
                    {
                        "phase": "caching",
                        "title": f"Saving evidence from {full_name}",
                        "detail": (
                            f"Found {len(delta.listed_items)} current items; "
                            f"{len(delta.changed_artifacts)} require an update and "
                            f"{delta.unchanged} remain cached."
                        ),
                        "repository": full_name,
                        "repository_index": index,
                        "repository_count": repository_count,
                        "artifacts": current_total,
                    }
                )
            return repository_id, delta

        async with httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
        ) as github_http:
            fetched_deltas: dict[str, ArtifactDelta] = dict(
                await asyncio.gather(
                    *(
                        fetch_repository(index, repository, github_http)
                        for index, repository in enumerate(repositories, start=1)
                    )
                )
            )

            artifact_updates = {
                repository_id: list(delta.changed_artifacts)
                for repository_id, delta in fetched_deltas.items()
            }
            updated_ids_by_repository = {
                repository_id: {int(item["id"]) for item in delta.changed_artifacts}
                for repository_id, delta in fetched_deltas.items()
            }
            current_ids_by_repository = {
                repository_id: {int(item["id"]) for item in delta.listed_items}
                for repository_id, delta in fetched_deltas.items()
            }

            dependency_records: list[dict[str, object]] = []
            milestone_issues: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for repository in repositories:
                delta = fetched_deltas[str(repository["repository_id"])]
                for artifact in delta.listed_items:
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
                            f"{len(milestone_issues)} milestone issues concurrently."
                        ),
                        "repository": None,
                        "repository_index": repository_count,
                        "repository_count": repository_count,
                        "artifacts": artifacts_loaded,
                    }
                )

            dependency_limit = asyncio.Semaphore(10)

            async def fetch_dependencies(
                blocked_repository: dict[str, Any],
                milestone_issue: dict[str, Any],
            ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
                async with dependency_limit:
                    blockers = await github.issue_dependencies(
                        str(current["access_token"]),
                        str(blocked_repository["github_full_name"]),
                        int(milestone_issue["number"]),
                        client=github_http,
                    )
                return blocked_repository, milestone_issue, blockers

            dependency_batches = await asyncio.gather(
                *(
                    fetch_dependencies(blocked_repository, milestone_issue)
                    for blocked_repository, milestone_issue in milestone_issues
                )
            )
            blockers_to_hydrate: dict[
                tuple[str, int], tuple[dict[str, Any], dict[str, Any]]
            ] = {}
            for (
                blocked_repository,
                milestone_issue,
                blocking_issues,
            ) in dependency_batches:
                blocked_full_name = str(blocked_repository["github_full_name"])
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
                    blocking_id = int(blocking_issue["id"])
                    current_ids_by_repository[blocking_repository_id].add(blocking_id)
                    cached = cached_by_repository[blocking_repository_id].get(
                        blocking_id
                    )
                    if blocking_id not in updated_ids_by_repository[
                        blocking_repository_id
                    ] and (
                        force_full
                        or cached is None
                        or str(cached["updated_at"])
                        != str(blocking_issue.get("updated_at") or "")
                    ):
                        blockers_to_hydrate[(blocking_repository_id, blocking_id)] = (
                            blocking_repository,
                            blocking_issue,
                        )
                    dependency_records.append(
                        {
                            "blocked_repository": blocked_full_name,
                            "blocked_number": int(milestone_issue["number"]),
                            "blocking_repository": blocking_full_name,
                            "blocking_number": int(blocking_issue["number"]),
                        }
                    )

            async def hydrate_blocker(
                repository_id: str,
                repository: dict[str, Any],
                issue: dict[str, Any],
            ) -> tuple[str, dict[str, Any]]:
                artifact = await github.issue_artifact(
                    str(current["access_token"]),
                    str(repository["github_full_name"]),
                    int(issue["number"]),
                    client=github_http,
                    hydration_limit=hydration_limit,
                )
                return repository_id, artifact

            hydrated_blockers = await asyncio.gather(
                *(
                    hydrate_blocker(repository_id, repository, issue)
                    for (repository_id, _blocking_id), (
                        repository,
                        issue,
                    ) in blockers_to_hydrate.items()
                )
            )
            for repository_id, artifact in hydrated_blockers:
                artifact_updates[repository_id].append(artifact)

        changed_total = sum(len(items) for items in artifact_updates.values())
        total = sum(len(items) for items in current_ids_by_repository.values())

        for repository in repositories:
            repository_id = str(repository["repository_id"])
            await asyncio.to_thread(
                service.reconcile_github_artifacts,
                project_id,
                repository_id,
                current_ids_by_repository[repository_id],
                artifact_updates[repository_id],
            )
        await asyncio.to_thread(
            service.record_issue_dependencies,
            project_id,
            dependency_records,
        )

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
        context_steps = [
            step
            for step in service.dashboard(project_id)["steps"]
            if step.get("context_resources")
        ]
        if report and context_steps:
            await report(
                {
                    "phase": "context_integrity",
                    "title": "Checking implementation context",
                    "detail": (
                        f"Resolving portable paths and selectors for "
                        f"{len(context_steps)} milestones without modifying files."
                    ),
                    "repository": None,
                    "repository_index": repository_count,
                    "repository_count": repository_count,
                    "artifacts": total,
                }
            )
        context_limit = asyncio.Semaphore(4)

        async def inspect_context(step: dict[str, Any]) -> None:
            async with context_limit:
                await asyncio.to_thread(
                    service.inspect_step_context,
                    project_id,
                    str(step["step_id"]),
                )

        await asyncio.gather(*(inspect_context(step) for step in context_steps))
        await security_evidence.refresh_project(
            str(current["access_token"]),
            project,
            repositories,
            report=report,
            force=False,
        )
        dashboard = service.dashboard(project_id)
        service.record_project_status_snapshot(project_id, dashboard)
        return {
            "repositories": repository_count,
            "artifacts": total,
            "changed_artifacts": changed_total,
            "duration_ms": round(
                (asyncio.get_running_loop().time() - started_at) * 1000
            ),
            "plan_status": plan_result["status"],
            "plan_message": plan_result["message"],
            "plan_revision": plan_result["revision"],
            "plan_source_branch": plan_result["branch"],
            "plan_errors": plan_result["errors"],
        }

    async def coordinated_reconciliation(
        project_id: str,
        current: dict[str, object],
        report: ReconciliationReporter | None = None,
        *,
        force_full: bool = False,
    ) -> dict[str, object]:
        """Share one in-flight reconciliation across tabs and refresh triggers."""
        reporters = reconciliation_reporters.setdefault(project_id, set())
        if report is not None:
            reporters.add(report)

        async def broadcast(event: dict[str, object]) -> None:
            reconciliation_progress[project_id] = event
            active_reporters = tuple(reconciliation_reporters.get(project_id, ()))
            if active_reporters:
                await asyncio.gather(
                    *(reporter(event) for reporter in active_reporters),
                    return_exceptions=True,
                )

        task = reconciliation_tasks.get(project_id)
        if task is None:
            task = asyncio.create_task(
                reconcile_github_evidence(
                    project_id,
                    current,
                    broadcast,
                    force_full=force_full,
                )
            )
            reconciliation_tasks[project_id] = task

            def finish(completed: asyncio.Task[dict[str, object]]) -> None:
                if reconciliation_tasks.get(project_id) is completed:
                    reconciliation_tasks.pop(project_id, None)
                if completed.cancelled():
                    return
                error = completed.exception()
                if error is None:
                    reconciliation_completed_at[project_id] = (
                        asyncio.get_running_loop().time()
                    )
                    if force_full:
                        reconciliation_full_at[project_id] = (
                            asyncio.get_running_loop().time()
                        )
                    reconciliation_failures[project_id] = 0
                else:
                    reconciliation_failures[project_id] = (
                        reconciliation_failures.get(project_id, 0) + 1
                    )
                if reconciliation_wakeup is not None:
                    reconciliation_wakeup.set()
                reconciliation_progress.pop(project_id, None)
                reconciliation_reporters.pop(project_id, None)

            task.add_done_callback(finish)
        elif report:
            latest = reconciliation_progress.get(project_id)
            await report(
                latest
                or {
                    "phase": "fetching",
                    "title": "Joining the active refresh",
                    "detail": (
                        "DPT is attaching this view to the reconciliation already "
                        "in progress instead of duplicating GitHub requests."
                    ),
                    "repository": None,
                    "repository_index": 0,
                    "repository_count": len(service.repositories(project_id)),
                    "artifacts": 0,
                }
            )
        try:
            return await asyncio.shield(task)
        finally:
            if report is not None:
                active = reconciliation_reporters.get(project_id)
                if active is not None:
                    active.discard(report)

    def automatic_reconciliation_interval(dashboard: dict[str, Any]) -> float:
        """Choose a faster cadence while GitHub delivery work is in motion."""
        metrics = dashboard["metrics"]
        has_active_delivery = bool(
            metrics["in_review"] or metrics["ready"] or metrics["pending"]
        )
        return (
            resolved.auto_reconcile_active_seconds
            if has_active_delivery
            else resolved.auto_reconcile_idle_seconds
        )

    async def automatic_reconciliation_loop() -> None:
        """Keep authenticated projects current without requiring an open tab."""
        nonlocal background_session_token
        project_limit = asyncio.Semaphore(2)

        async def refresh_project(
            project_id: str,
            current: dict[str, object],
            force_full: bool,
        ) -> None:
            async with project_limit:
                try:
                    await coordinated_reconciliation(
                        project_id,
                        current,
                        force_full=force_full,
                    )
                except Exception as error:
                    LOGGER.error(
                        "Automatic reconciliation failed for project %s: %s",
                        project_id,
                        error,
                    )

        while True:
            wakeup = reconciliation_wakeup
            if wakeup is None:
                return
            session_token = background_session_token
            if session_token is None:
                wakeup.clear()
                await wakeup.wait()
                continue

            try:
                current = await authenticated_session(session_token)
            except HTTPException as error:
                if error.status_code != 401:
                    raise
                background_session_token = None
                continue

            projects = await asyncio.to_thread(service.projects)
            now = asyncio.get_running_loop().time()
            due_projects: list[tuple[str, bool]] = []
            next_due_in = resolved.auto_reconcile_idle_seconds
            for project in projects:
                project_id = str(project["project_id"])
                dashboard = await asyncio.to_thread(service.dashboard, project_id)
                failures = reconciliation_failures.get(project_id, 0)
                interval = (
                    min(300.0, 15.0 * (2**failures))
                    if failures
                    else automatic_reconciliation_interval(dashboard)
                )
                elapsed = now - reconciliation_completed_at.get(project_id, 0.0)
                remaining = interval - elapsed
                if remaining <= 0:
                    last_full = reconciliation_full_at.setdefault(project_id, now)
                    due_projects.append(
                        (
                            project_id,
                            now - last_full >= resolved.auto_reconcile_full_seconds,
                        )
                    )
                else:
                    next_due_in = min(next_due_in, remaining)

            if due_projects:
                await asyncio.gather(
                    *(
                        refresh_project(project_id, current, force_full)
                        for project_id, force_full in due_projects
                    )
                )
                continue

            wakeup.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    wakeup.wait(),
                    timeout=max(0.25, min(next_due_in, 30.0)),
                )

    @app.exception_handler(DPTError)
    async def handle_dpt_error(_request: Request, error: DPTError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mcp": {
                "enabled": resolved.mcp_enabled,
                "path": resolved.mcp_path if resolved.mcp_enabled else None,
            },
        }

    @app.get("/api/config")
    async def public_config() -> dict[str, object]:
        return {
            "github_authentication": "device_flow",
            "generated_filename": "STATEFORGE_DPT.md",
            "automatic_reconciliation": resolved.auto_reconcile_enabled,
            "mcp": {
                "enabled": resolved.mcp_enabled,
                "path": resolved.mcp_path if resolved.mcp_enabled else None,
            },
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
        nonlocal background_session_token
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
        background_session_token = session_token
        if reconciliation_wakeup is not None:
            reconciliation_wakeup.set()
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
        nonlocal background_session_token
        service.delete_session(session_token)
        if session_token == background_session_token:
            background_session_token = None
            if reconciliation_wakeup is not None:
                reconciliation_wakeup.set()
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
    async def project_dashboard(
        project_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        dashboard = service.dashboard(project_id)
        current: dict[str, object] | None = None
        if session_token:
            try:
                current = await authenticated_session(session_token)
            except HTTPException as error:
                if error.status_code != 401:
                    raise
        await attach_repository_ci_status(dashboard, current)
        attach_repository_security_status(dashboard)
        return dashboard

    @app.get("/api/projects/{project_id}/security")
    async def project_security(project_id: str) -> dict[str, Any]:
        """Return persisted repository findings and evidence provenance."""
        project = service.project(project_id)
        result = security_evidence.details(
            project_id,
            str(project.get("plan_source_branch") or "mvp/implementation"),
        )
        task = security_tasks.get(project_id)
        result["refresh"] = {
            "state": "running" if task is not None and not task.done() else "idle"
        }
        return result

    @app.post("/api/projects/{project_id}/security/refresh", status_code=202)
    async def refresh_project_security(
        project_id: str,
        request: Request,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        """Start one user-authenticated, exact-head security refresh."""
        require_same_origin(request)
        current = await authenticated_session(session_token)
        running = security_tasks.get(project_id)
        if running is not None and not running.done():
            return {"state": "running", "joined": True}
        schedule_security_refresh(project_id, current, force=True)
        return {"state": "started", "joined": False}

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

    @app.get("/api/repositories/{repository_id}/tree")
    async def repository_tree(
        repository_id: str,
        query: str = "",
        limit: int = 250,
    ) -> dict[str, object]:
        return service.repository_tree(repository_id, query, limit)

    @app.get("/api/repositories/{repository_id}/resource-preview")
    async def repository_resource_preview(
        repository_id: str,
        path: str,
    ) -> dict[str, object]:
        return service.repository_resource_preview(repository_id, path)

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

    @app.put("/api/projects/{project_id}/steps/{step_id}/context")
    async def update_step_context(
        project_id: str,
        step_id: str,
        request: ContextContractUpdate,
    ) -> dict[str, object]:
        return service.update_context_contract(project_id, step_id, request)

    @app.post("/api/projects/{project_id}/steps/{step_id}/context/inspect")
    async def inspect_step_context(project_id: str, step_id: str) -> dict[str, object]:
        return service.inspect_step_context(project_id, step_id)

    @app.post("/api/projects/{project_id}/steps/{step_id}/context/approve")
    async def approve_step_context(
        project_id: str,
        step_id: str,
        request: ContextApprovalRequest,
    ) -> dict[str, object]:
        return service.approve_step_context(project_id, step_id, request.approved_by)

    @app.post("/api/projects/{project_id}/steps/{step_id}/implementation-package")
    async def create_implementation_package(
        project_id: str, step_id: str
    ) -> dict[str, object]:
        return service.implementation_package(project_id, step_id)

    @app.post(
        "/api/projects/{project_id}/steps/{step_id}/verification/"
        "{verification_id}/record"
    )
    async def record_step_verification(
        project_id: str,
        step_id: str,
        verification_id: str,
        request: VerificationRecordRequest,
    ) -> dict[str, object]:
        return service.record_verification(
            project_id,
            step_id,
            verification_id,
            request.status,
            request.recorded_by,
            request.evidence,
        )

    @app.post(
        "/api/projects/{project_id}/steps/{step_id}/verification/{verification_id}/run"
    )
    async def run_step_verification(
        project_id: str,
        step_id: str,
        verification_id: str,
        request: VerificationRunRequest,
    ) -> dict[str, object]:
        return service.run_verification(
            project_id,
            step_id,
            verification_id,
            request.approved_by,
            request.approved_target,
        )

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
            (artifact for artifact in step["artifacts"] if artifact["kind"] == "issue"),
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
                        "A blocker must belong to a repository managed by this project"
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

        if blocking_full_name.casefold() == str(
            step["github_full_name"]
        ).casefold() and int(blocking_issue["number"]) == int(
            milestone_issue["number"]
        ):
            raise HTTPException(
                status_code=422,
                detail="A milestone cannot block itself",
            )
        if any(
            str(blocker["github_full_name"]).casefold() == blocking_full_name.casefold()
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
                    "An active or merged pull request is already linked to this blocker"
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

    @app.post("/api/projects/{project_id}/plan/publish-git")
    async def publish_plan_through_git(
        project_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        return await asyncio.to_thread(
            service.publish_project_plan_to_git,
            project_id,
            plan_publisher,
            str(current["github_login"]),
        )

    @app.get("/api/projects/{project_id}/plan-proposals")
    async def plan_change_proposals(project_id: str) -> list[dict[str, Any]]:
        """Return immutable AI plan proposals for optional browser review."""
        return proposals.list(project_id)

    @app.post("/api/projects/{project_id}/plan-proposals", status_code=201)
    async def create_plan_change_proposal(
        project_id: str,
        proposal: PlanChangeProposalRequest,
        request: Request,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        require_same_origin(request)
        if proposal.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail="Proposal project_id must match the route project",
            )
        return proposals.create(
            proposal,
            proposer_id=f"browser:{human_actor(session_token)}",
        )

    @app.get("/api/plan-proposals/{proposal_id}")
    async def plan_change_proposal(proposal_id: str) -> dict[str, Any]:
        return proposals.get(proposal_id)

    @app.post("/api/plan-proposals/{proposal_id}/decision")
    async def decide_plan_change_proposal(
        proposal_id: str,
        decision: ProposalDecisionRequest,
        request: Request,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        require_same_origin(request)
        actor = human_actor(session_token)
        if decision.decision == "apply":
            return proposals.apply(
                proposal_id,
                decision,
                decided_by=actor,
                channel="browser",
            )
        return proposals.reject(
            proposal_id,
            decision,
            decided_by=actor,
            channel="browser",
        )

    @app.post("/api/projects/{project_id}/reconcile")
    async def reconcile_project(
        project_id: str,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, object]:
        current = await authenticated_session(session_token)
        try:
            return await coordinated_reconciliation(project_id, current)
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
                    result = await coordinated_reconciliation(
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

    if mcp_application is not None:
        # Reuse the shared FastAPI origin so the advertised MCP endpoint is
        # exactly /mcp rather than a separately managed process or port.
        app.router.routes.extend(mcp_application.routes)

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
