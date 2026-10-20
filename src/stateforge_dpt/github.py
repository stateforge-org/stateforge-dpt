"""GitHub App authentication and API client."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from stateforge_dpt.config import Settings
from stateforge_dpt.git import GitError, clone_identity, fetch_remote_branch


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


@dataclass(frozen=True, slots=True)
class ArtifactDelta:
    """Lightweight repository index plus the artifacts that need rehydration."""

    listed_items: list[dict[str, Any]]
    changed_artifacts: list[dict[str, Any]]
    unchanged: int


class GitHubAppPlanPublisher:
    """Publish the generated plan as the installed StateForge DPT App."""

    api_url = "https://api.github.com"

    def __init__(self, settings: Settings) -> None:
        """Use server-side App credentials, separate from the human session."""
        self.settings = settings

    def publish(
        self,
        local_path: str,
        full_name: str,
        target_branch: str,
        filename: str,
        content: str,
        commit_message: str,
    ) -> dict[str, str | bool | int]:
        """Create one verified bot commit and advance the protected branch."""
        repository = self._repository_name(full_name)
        relative = PurePosixPath(filename)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise GitError("Generated plan filename must remain inside the repository")
        root, clone_name, _origin = clone_identity(local_path)
        if clone_name != full_name.casefold():
            raise GitError(f"Origin points to {clone_name}, expected {full_name}")

        app_token = self._app_jwt()
        with httpx.Client(timeout=30) as client:
            installation = self._request(
                client,
                app_token,
                "GET",
                f"/repos/{full_name}/installation",
            )
            installation_id = int(installation["id"])
            token_payload = self._request(
                client,
                app_token,
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                json={
                    "repositories": [repository],
                    "permissions": {"contents": "write"},
                },
            )
            installation_token = str(token_payload.get("token") or "")
            if not installation_token:
                raise GitError("GitHub did not return an App installation token")

            encoded_branch = quote(target_branch, safe="")
            reference = self._request(
                client,
                installation_token,
                "GET",
                f"/repos/{full_name}/git/ref/heads/{encoded_branch}",
            )
            previous_commit = str(reference["object"]["sha"])
            base_commit = self._request(
                client,
                installation_token,
                "GET",
                f"/repos/{full_name}/git/commits/{previous_commit}",
            )
            base_tree = str(base_commit["tree"]["sha"])
            blob = self._request(
                client,
                installation_token,
                "POST",
                f"/repos/{full_name}/git/blobs",
                json={
                    "content": base64.b64encode(content.encode("utf-8")).decode(
                        "ascii"
                    ),
                    "encoding": "base64",
                },
            )
            tree = self._request(
                client,
                installation_token,
                "POST",
                f"/repos/{full_name}/git/trees",
                json={
                    "base_tree": base_tree,
                    "tree": [
                        {
                            "path": relative.as_posix(),
                            "mode": "100644",
                            "type": "blob",
                            "sha": str(blob["sha"]),
                        }
                    ],
                },
            )
            tree_sha = str(tree["sha"])
            if tree_sha == base_tree:
                return {
                    "publisher": "stateforge-dpt-app",
                    "installation_id": installation_id,
                    "target_branch": target_branch,
                    "previous_commit": previous_commit,
                    "target_commit": previous_commit,
                    "changed": False,
                    "signature_verified": bool(
                        base_commit.get("verification", {}).get("verified")
                    ),
                    **self._sync_local_tracking_branch(
                        str(root),
                        target_branch,
                        installation_token,
                        previous_commit,
                    ),
                }

            commit = self._request(
                client,
                installation_token,
                "POST",
                f"/repos/{full_name}/git/commits",
                json={
                    "message": commit_message,
                    "tree": tree_sha,
                    "parents": [previous_commit],
                },
            )
            target_commit = str(commit["sha"])
            verified_commit = self._request(
                client,
                installation_token,
                "GET",
                f"/repos/{full_name}/git/commits/{target_commit}",
            )
            verification = verified_commit.get("verification") or {}
            if verification.get("verified") is not True:
                reason = str(verification.get("reason") or "unknown")
                raise GitError(
                    "GitHub did not verify the StateForge DPT App commit "
                    f"{target_commit}: {reason}"
                )

            self._request(
                client,
                installation_token,
                "PATCH",
                f"/repos/{full_name}/git/refs/heads/{encoded_branch}",
                json={"sha": target_commit, "force": False},
            )
        return {
            "publisher": "stateforge-dpt-app",
            "installation_id": installation_id,
            "target_branch": target_branch,
            "previous_commit": previous_commit,
            "target_commit": target_commit,
            "changed": True,
            "signature_verified": True,
            **self._sync_local_tracking_branch(
                str(root),
                target_branch,
                installation_token,
                target_commit,
            ),
        }

    @staticmethod
    def _sync_local_tracking_branch(
        local_path: str,
        target_branch: str,
        installation_token: str,
        expected_commit: str,
    ) -> dict[str, str | bool]:
        """Refresh local remote state without misreporting a completed publish."""
        try:
            fetched = fetch_remote_branch(
                local_path,
                target_branch,
                installation_token,
            )
        except GitError as error:
            return {
                "local_tracking_updated": False,
                "local_tracking_error": str(error),
            }
        return {
            "local_tracking_updated": fetched == expected_commit,
            "local_tracking_commit": fetched,
        }

    def _app_jwt(self) -> str:
        """Create the short-lived JWT used only to mint installation tokens."""
        app_id = self.settings.github_app_id
        private_key_path = self.settings.github_app_private_key_path
        if app_id is None or private_key_path is None:
            raise GitError(
                "Review & publish requires STATEFORGE_DPT_GITHUB_APP_ID and "
                "STATEFORGE_DPT_GITHUB_APP_PRIVATE_KEY_PATH"
            )
        try:
            key_data = private_key_path.read_bytes()
            private_key = serialization.load_pem_private_key(
                key_data,
                password=None,
            )
        except (OSError, TypeError, ValueError) as error:
            raise GitError(
                f"Cannot load the GitHub App private key: {private_key_path}"
            ) from error
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise GitError("The GitHub App private key must be an RSA private key")

        issued_at = int(time.time()) - 60
        header = self._jwt_part({"alg": "RS256", "typ": "JWT"})
        payload = self._jwt_part(
            {"iat": issued_at, "exp": issued_at + 600, "iss": str(app_id)}
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        signature = private_key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return f"{header}.{payload}.{encoded_signature}"

    @staticmethod
    def _jwt_part(value: dict[str, object]) -> str:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()

    @staticmethod
    def _repository_name(full_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
            raise GitError(f"Invalid GitHub repository name: {full_name}")
        return full_name.split("/", 1)[1]

    def _request(
        self,
        client: httpx.Client,
        access_token: str,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        try:
            response = client.request(
                method,
                f"{self.api_url}{path}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
                **kwargs,
            )
        except httpx.HTTPError as error:
            raise GitError(f"GitHub request failed: {error}") from error
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = str(payload.get("message", response.text))
            raise GitError(f"GitHub returned {response.status_code}: {message}")
        return response.json()


class GitHubClient:
    """Small typed boundary around the GitHub REST API."""

    api_url = "https://api.github.com"

    def __init__(self, settings: Settings) -> None:
        """Create a GitHub client using the StateForge GitHub App."""
        self.settings = settings
        self._get_cache: OrderedDict[tuple[str, str, str], tuple[str, Any]] = (
            OrderedDict()
        )

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
        delta = await self.repository_artifact_delta(
            access_token,
            full_name,
            project_slug,
            {},
            force_full=True,
        )
        return delta.changed_artifacts

    async def repository_artifact_delta(
        self,
        access_token: str,
        full_name: str,
        project_slug: str,
        cached_updated_at: dict[int, str],
        *,
        force_full: bool = False,
        client: httpx.AsyncClient | None = None,
        hydration_limit: asyncio.Semaphore | None = None,
    ) -> ArtifactDelta:
        """Hydrate only new or changed artifacts from a lightweight issue index."""
        if client is None:
            async with httpx.AsyncClient(timeout=30) as owned_client:
                return await self.repository_artifact_delta(
                    access_token,
                    full_name,
                    project_slug,
                    cached_updated_at,
                    force_full=force_full,
                    client=owned_client,
                    hydration_limit=hydration_limit,
                )

        listed_items: list[dict[str, Any]] = []
        limit = hydration_limit or asyncio.Semaphore(6)

        async def hydrate(
            item: dict[str, Any],
        ) -> dict[str, Any]:
            async with limit:
                return await self._hydrate_issue(
                    client,
                    access_token,
                    full_name,
                    dict(item),
                )

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
            listed_items.extend(dict(item) for item in items)
            if len(items) < 100:
                break

        changed_items = [
            item
            for item in listed_items
            if force_full
            or cached_updated_at.get(int(item["id"]))
            != str(item.get("updated_at") or "")
            # Commit checks can settle without changing the PR's updated_at.
            or ("pull_request" in item and item.get("state") == "open")
        ]
        changed_artifacts = list(
            await asyncio.gather(*(hydrate(item) for item in changed_items))
        )
        return ArtifactDelta(
            listed_items=listed_items,
            changed_artifacts=changed_artifacts,
            unchanged=len(listed_items) - len(changed_items),
        )

    async def issue_artifact(
        self,
        access_token: str,
        full_name: str,
        issue_number: int,
        *,
        client: httpx.AsyncClient | None = None,
        hydration_limit: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        """Load one issue and the same conversation data used by reconciliation."""
        if client is None:
            async with httpx.AsyncClient(timeout=30) as owned_client:
                return await self.issue_artifact(
                    access_token,
                    full_name,
                    issue_number,
                    client=owned_client,
                    hydration_limit=hydration_limit,
                )
        limit = hydration_limit or asyncio.Semaphore(1)
        async with limit:
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
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        """Return the native GitHub issues that block an issue."""
        if client is None:
            async with httpx.AsyncClient(timeout=30) as owned_client:
                return await self.issue_dependencies(
                    access_token,
                    full_name,
                    issue_number,
                    client=owned_client,
                )
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
        timeline_request = self._paginated_items(
            client,
            access_token,
            f"/repos/{full_name}/issues/{item['number']}/timeline",
        )
        review_comments: list[dict[str, Any]] = []
        changed_files: list[dict[str, Any]] = []
        checks_state: str | None = None
        if "pull_request" in item:
            timeline, pull, review_comments, changed_files = await asyncio.gather(
                timeline_request,
                self._request(
                    client,
                    access_token,
                    "GET",
                    f"/repos/{full_name}/pulls/{item['number']}",
                ),
                self._paginated_items(
                    client,
                    access_token,
                    f"/repos/{full_name}/pulls/{item['number']}/comments",
                ),
                self._paginated_items(
                    client,
                    access_token,
                    f"/repos/{full_name}/pulls/{item['number']}/files",
                ),
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
        else:
            timeline = await timeline_request
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
        method = method.upper()
        supplied_headers = kwargs.pop("headers", {})
        request_headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": "2026-03-10",
            **supplied_headers,
        }
        cache_key: tuple[str, str, str] | None = None
        cached: tuple[str, Any] | None = None
        if method == "GET":
            cache_key = (
                hashlib.sha256(access_token.encode()).hexdigest(),
                path,
                json.dumps(kwargs.get("params"), sort_keys=True, default=str),
            )
            cached = self._get_cache.get(cache_key)
            if cached is not None:
                request_headers["If-None-Match"] = cached[0]
        response = await client.request(
            method,
            f"{self.api_url}{path}",
            headers=request_headers,
            **kwargs,
        )
        if response.status_code == 304 and cached is not None and cache_key is not None:
            # Another concurrent request can advance the bounded LRU while this
            # request is in flight. Restore the validated entry before touching
            # its recency so an eviction cannot turn a valid 304 into KeyError.
            self._get_cache[cache_key] = cached
            self._get_cache.move_to_end(cache_key)
            while len(self._get_cache) > 512:
                self._get_cache.popitem(last=False)
            return copy.deepcopy(cached[1])
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
        payload = response.json()
        etag = response.headers.get("etag")
        if cache_key is not None and etag:
            self._get_cache[cache_key] = (etag, copy.deepcopy(payload))
            self._get_cache.move_to_end(cache_key)
            while len(self._get_cache) > 512:
                self._get_cache.popitem(last=False)
        return payload

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
