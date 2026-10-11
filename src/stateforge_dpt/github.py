"""GitHub App authentication and API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx

from stateforge_dpt.config import Settings


class GitHubError(RuntimeError):
    """Raised for an unsuccessful GitHub operation."""


@dataclass(frozen=True, slots=True)
class GitHubIdentity:
    """Authenticated GitHub user identity."""

    user_id: int
    login: str
    name: str | None
    avatar_url: str | None
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    refresh_token_expires_in: int | None


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    """GitHub device authorization awaiting confirmation by a user."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True, slots=True)
class DevicePollResult:
    """Result of one rate-limited device authorization poll."""

    status: Literal["pending", "slow_down", "complete"]
    identity: GitHubIdentity | None = None


@dataclass(frozen=True, slots=True)
class RefreshedToken:
    """Replacement credentials for an existing GitHub user session."""

    access_token: str
    refresh_token: str | None
    expires_in: int | None
    refresh_token_expires_in: int | None


class GitHubClient:
    """Small typed boundary around the GitHub REST API."""

    api_url = "https://api.github.com"

    def __init__(self, settings: Settings) -> None:
        """Create a GitHub client using the StateForge GitHub App."""
        self.settings = settings

    async def start_device_authorization(self) -> DeviceAuthorization:
        """Request a verification code for the GitHub App device flow."""
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://github.com/login/device/code",
                headers={"Accept": "application/json"},
                data={"client_id": self.settings.github_client_id},
            )
        response.raise_for_status()
        payload = response.json()
        if "device_code" not in payload:
            message = payload.get("error_description", "Device authorization failed")
            raise GitHubError(str(message))
        return DeviceAuthorization(
            device_code=str(payload["device_code"]),
            user_code=str(payload["user_code"]),
            verification_uri=str(payload["verification_uri"]),
            expires_in=int(payload["expires_in"]),
            interval=int(payload.get("interval", 5)),
        )

    async def poll_device_authorization(
        self,
        device_code: str,
    ) -> DevicePollResult:
        """Poll GitHub once for completion of a device authorization."""
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.settings.github_client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            response.raise_for_status()
            payload = response.json()
            error = payload.get("error")
            if error == "authorization_pending":
                return DevicePollResult("pending")
            if error == "slow_down":
                return DevicePollResult("slow_down")
            if error:
                message = payload.get("error_description", error)
                raise GitHubError(str(message))
            token = self._token_from_payload(payload)
            user = await self._request(client, token.access_token, "GET", "/user")

        identity = GitHubIdentity(
            user_id=int(user["id"]),
            login=str(user["login"]),
            name=user.get("name"),
            avatar_url=user.get("avatar_url"),
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
            refresh_token_expires_in=token.refresh_token_expires_in,
        )
        return DevicePollResult("complete", identity)

    async def refresh_user_token(self, refresh_token: str) -> RefreshedToken:
        """Refresh an expiring GitHub App user access token."""
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.settings.github_client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            message = payload.get("error_description", payload["error"])
            raise GitHubError(str(message))
        return self._token_from_payload(payload)

    async def repositories(self, access_token: str) -> list[dict[str, Any]]:
        """Return repositories visible to the authenticated user."""
        repositories: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(1, 11):
                page_items = await self._request(
                    client,
                    access_token,
                    "GET",
                    "/user/repos",
                    params={
                        "affiliation": "owner,collaborator,organization_member",
                        "sort": "updated",
                        "per_page": 100,
                        "page": page,
                    },
                )
                repositories.extend(page_items)
                if len(page_items) < 100:
                    break
        return repositories

    async def repository_artifacts(
        self,
        access_token: str,
        full_name: str,
        project_slug: str,
    ) -> list[dict[str, Any]]:
        """Load DPT-labelled issues and pull requests for a repository."""
        artifacts: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(1, 11):
                items = await self._request(
                    client,
                    access_token,
                    "GET",
                    f"/repos/{full_name}/issues",
                    params={
                        "state": "all",
                        "labels": f"dpt:project:{project_slug}",
                        "per_page": 100,
                        "page": page,
                    },
                )
                artifacts.extend(
                    [
                        await self._hydrate_issue(
                            client, access_token, full_name, dict(item)
                        )
                        for item in items
                    ]
                )
                if len(items) < 100:
                    break
        return artifacts

    async def issue_artifact(
        self,
        access_token: str,
        full_name: str,
        issue_number: int,
    ) -> dict[str, Any]:
        """Load one issue and the same conversation data used by reconciliation."""
        async with httpx.AsyncClient(timeout=30) as client:
            issue = await self._request(
                client,
                access_token,
                "GET",
                f"/repos/{full_name}/issues/{issue_number}",
            )
            return await self._hydrate_issue(
                client, access_token, full_name, dict(issue)
            )

    async def issue_dependencies(
        self,
        access_token: str,
        full_name: str,
        issue_number: int,
    ) -> list[dict[str, Any]]:
        """Return the native GitHub issues that block an issue."""
        async with httpx.AsyncClient(timeout=30) as client:
            return await self._paginated_items(
                client,
                access_token,
                f"/repos/{full_name}/issues/{issue_number}/dependencies/blocked_by",
            )

    async def add_issue_dependency(
        self,
        access_token: str,
        full_name: str,
        issue_number: int,
        blocking_issue_id: int,
    ) -> dict[str, Any]:
        """Make an issue natively blocked by another GitHub issue."""
        async with httpx.AsyncClient(timeout=30) as client:
            result = await self._request(
                client,
                access_token,
                "POST",
                f"/repos/{full_name}/issues/{issue_number}/dependencies/blocked_by",
                json={"issue_id": blocking_issue_id},
            )
        return dict(result)

    async def remove_issue_dependency(
        self,
        access_token: str,
        full_name: str,
        issue_number: int,
        blocking_issue_id: int,
    ) -> None:
        """Remove one native blocked-by relationship from a GitHub issue."""
        async with httpx.AsyncClient(timeout=30) as client:
            await self._request(
                client,
                access_token,
                "DELETE",
                (
                    f"/repos/{full_name}/issues/{issue_number}/dependencies/"
                    f"blocked_by/{blocking_issue_id}"
                ),
            )

    async def _hydrate_issue(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        full_name: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach timeline, review, and pull-request details to an issue object."""
        timeline = await self._paginated_items(
            client,
            access_token,
            f"/repos/{full_name}/issues/{item['number']}/timeline",
        )
        review_comments: list[dict[str, Any]] = []
        changed_files: list[dict[str, Any]] = []
        checks_state: str | None = None
        if "pull_request" in item:
            pull = await self._request(
                client,
                access_token,
                "GET",
                f"/repos/{full_name}/pulls/{item['number']}",
            )
            review_comments = await self._paginated_items(
                client,
                access_token,
                f"/repos/{full_name}/pulls/{item['number']}/comments",
            )
            changed_files = await self._paginated_items(
                client,
                access_token,
                f"/repos/{full_name}/pulls/{item['number']}/files",
            )
            head_sha = pull.get("head", {}).get("sha")
            if head_sha:
                status = await self._request(
                    client,
                    access_token,
                    "GET",
                    f"/repos/{full_name}/commits/{head_sha}/status",
                )
                checks_state = str(status.get("state") or "pending")
            item = {**item, "pull_details": pull}
        return {
            **item,
            "timeline": timeline,
            "review_comments": review_comments,
            "changed_files": changed_files,
            "checks_state": checks_state,
        }

    async def _paginated_items(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        path: str,
    ) -> list[dict[str, Any]]:
        """Return up to one thousand ordered items from a GitHub endpoint."""
        items: list[dict[str, Any]] = []
        for page in range(1, 11):
            page_items = await self._request(
                client,
                access_token,
                "GET",
                path,
                params={"per_page": 100, "page": page},
            )
            items.extend(page_items)
            if len(page_items) < 100:
                break
        return items

    async def repository_branches(
        self,
        access_token: str,
        full_name: str,
    ) -> list[str]:
        """Return every branch available for pull-request selection."""
        branches: list[str] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(1, 11):
                page_items = await self._request(
                    client,
                    access_token,
                    "GET",
                    f"/repos/{full_name}/branches",
                    params={"per_page": 100, "page": page},
                )
                branches.extend(str(item["name"]) for item in page_items)
                if len(page_items) < 100:
                    break
        return branches

    async def latest_workflow_run(
        self,
        access_token: str,
        full_name: str,
        branch: str,
    ) -> dict[str, Any] | None:
        """Return the newest Actions run for one repository branch."""
        async with httpx.AsyncClient(timeout=30) as client:
            payload = await self._request(
                client,
                access_token,
                "GET",
                f"/repos/{full_name}/actions/runs",
                params={
                    "branch": branch,
                    "per_page": 1,
                },
            )
        runs = payload.get("workflow_runs", [])
        if not isinstance(runs, list) or not runs:
            return None
        run = runs[0]
        return dict(run) if isinstance(run, dict) else None

    async def compare_branches(
        self,
        access_token: str,
        full_name: str,
        base_branch: str,
        head_branch: str,
    ) -> dict[str, Any]:
        """Return GitHub's comparison of two repository branches."""
        encoded_base = quote(base_branch, safe="")
        encoded_head = quote(head_branch, safe="")
        async with httpx.AsyncClient(timeout=30) as client:
            comparison = await self._request(
                client,
                access_token,
                "GET",
                f"/repos/{full_name}/compare/{encoded_base}...{encoded_head}",
            )
        return {
            "status": str(comparison["status"]),
            "ahead_by": int(comparison["ahead_by"]),
            "behind_by": int(comparison["behind_by"]),
            "total_commits": int(comparison["total_commits"]),
        }

    async def create_issue(
        self,
        access_token: str,
        full_name: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Create missing DPT labels and open a labelled GitHub Issue."""
        async with httpx.AsyncClient(timeout=30) as client:
            await self._ensure_labels(client, access_token, full_name, labels)
            issue = await self._request(
                client,
                access_token,
                "POST",
                f"/repos/{full_name}/issues",
                json={"title": title, "body": body, "labels": labels},
            )
        return dict(issue)

    async def create_pull_request(
        self,
        access_token: str,
        full_name: str,
        title: str,
        body: str,
        base_branch: str,
        head_branch: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Open a pull request and attach its DPT tracking labels."""
        async with httpx.AsyncClient(timeout=30) as client:
            await self._ensure_labels(client, access_token, full_name, labels)
            pull_request = await self._request(
                client,
                access_token,
                "POST",
                f"/repos/{full_name}/pulls",
                json={
                    "title": title,
                    "body": body,
                    "base": base_branch,
                    "head": head_branch,
                },
            )
            await self._request(
                client,
                access_token,
                "PATCH",
                f"/repos/{full_name}/issues/{pull_request['number']}",
                json={"labels": labels},
            )
        return dict(pull_request)

    async def create_branch(
        self,
        access_token: str,
        full_name: str,
        base_branch: str,
        branch_name: str,
    ) -> dict[str, Any]:
        """Create a repository branch from the current base-branch commit."""
        encoded_base = quote(base_branch, safe="")
        async with httpx.AsyncClient(timeout=30) as client:
            base_reference = await self._request(
                client,
                access_token,
                "GET",
                f"/repos/{full_name}/git/ref/heads/{encoded_base}",
            )
            reference = await self._request(
                client,
                access_token,
                "POST",
                f"/repos/{full_name}/git/refs",
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "sha": base_reference["object"]["sha"],
                },
            )
        return dict(reference)

    async def _ensure_labels(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        full_name: str,
        labels: list[str],
    ) -> None:
        """Create requested DPT and bug labels when a repository lacks them."""
        label_definitions: list[tuple[str, str, str]] = []
        for label in labels:
            if label.startswith("dpt:project:"):
                definition = (label, "2563eb", "StateForge DPT project tracking")
            elif label.startswith("dpt:release:"):
                definition = (label, "7c3aed", "StateForge DPT release tracking")
            elif label.startswith("dpt:step:"):
                definition = (label, "0e7490", "StateForge DPT implementation step")
            elif label.casefold() == "bug":
                definition = (label, "d73a4a", "Something is not working")
            else:
                definition = (label, "64748b", "StateForge DPT GitHub tracking")
            label_definitions.append(definition)
        existing_labels: set[str] = set()
        for page in range(1, 11):
            page_items = await self._request(
                client,
                access_token,
                "GET",
                f"/repos/{full_name}/labels",
                params={"per_page": 100, "page": page},
            )
            existing_labels.update(str(item["name"]).casefold() for item in page_items)
            if len(page_items) < 100:
                break

        for name, color, description in label_definitions:
            if name.casefold() in existing_labels:
                continue
            await self._request(
                client,
                access_token,
                "POST",
                f"/repos/{full_name}/labels",
                json={
                    "name": name,
                    "color": color,
                    "description": description,
                },
            )

    async def _request(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        response = await client.request(
            method,
            f"{self.api_url}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            **kwargs,
        )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = str(payload.get("message", response.text))
            validation_details = [
                str(item.get("message", ""))
                for item in payload.get("errors", [])
                if isinstance(item, dict) and item.get("message")
            ]
            if validation_details:
                message = f"{message} — {'; '.join(validation_details)}"
            raise GitHubError(f"GitHub returned {response.status_code}: {message}")
        return response.json()

    @staticmethod
    def _token_from_payload(payload: dict[str, Any]) -> RefreshedToken:
        access_token = payload.get("access_token")
        if not access_token:
            raise GitHubError("GitHub did not return an access token")
        return RefreshedToken(
            access_token=str(access_token),
            refresh_token=(
                str(payload["refresh_token"]) if payload.get("refresh_token") else None
            ),
            expires_in=(
                int(payload["expires_in"]) if payload.get("expires_in") else None
            ),
            refresh_token_expires_in=(
                int(payload["refresh_token_expires_in"])
                if payload.get("refresh_token_expires_in")
                else None
            ),
        )
