import json
from pathlib import Path

from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings, load_settings
from stateforge_dpt.github import (
    DeviceAuthorization,
    DevicePollResult,
    GitHubClient,
    GitHubIdentity,
)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
    )


def test_blank_environment_value_uses_stateforge_client_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STATEFORGE_DPT_GITHUB_CLIENT_ID", "")

    resolved = load_settings(tmp_path)

    assert resolved.github_client_id == DEFAULT_GITHUB_CLIENT_ID


def test_device_flow_creates_an_encrypted_local_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def start_device_authorization(
        _client: GitHubClient,
    ) -> DeviceAuthorization:
        return DeviceAuthorization(
            device_code="private-device-code",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=0,
        )

    async def poll_device_authorization(
        _client: GitHubClient,
        device_code: str,
    ) -> DevicePollResult:
        assert device_code == "private-device-code"
        return DevicePollResult(
            "complete",
            GitHubIdentity(
                user_id=42,
                login="octocat",
                name="The Octocat",
                avatar_url="https://github.com/images/error/octocat_happy.gif",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_in=28_800,
                refresh_token_expires_in=15_897_600,
            ),
        )

    monkeypatch.setattr(
        GitHubClient,
        "start_device_authorization",
        start_device_authorization,
    )
    monkeypatch.setattr(
        GitHubClient,
        "poll_device_authorization",
        poll_device_authorization,
    )

    with TestClient(create_app(settings(tmp_path))) as client:
        started = client.post("/api/auth/github/device")
        assert started.status_code == 200
        authorization = started.json()
        assert authorization["user_code"] == "ABCD-1234"
        assert "device_code" not in authorization

        completed = client.post(
            f"/api/auth/github/device/{authorization['authorization_id']}/poll"
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "complete"

        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["user"]["login"] == "octocat"

    database_bytes = (tmp_path / "data" / "dpt.sqlite3").read_bytes()
    assert b"private-device-code" not in database_bytes
    assert b"access-token" not in database_bytes
    assert b"refresh-token" not in database_bytes


def test_milestone_pull_request_uses_selected_github_branches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def start_device_authorization(
        _client: GitHubClient,
    ) -> DeviceAuthorization:
        return DeviceAuthorization(
            device_code="private-device-code",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=0,
        )

    async def poll_device_authorization(
        _client: GitHubClient,
        _device_code: str,
    ) -> DevicePollResult:
        return DevicePollResult(
            "complete",
            GitHubIdentity(
                user_id=42,
                login="octocat",
                name="The Octocat",
                avatar_url=None,
                access_token="access-token",
                refresh_token=None,
                expires_in=None,
                refresh_token_expires_in=None,
            ),
        )

    async def repository_artifacts(
        _client: GitHubClient,
        _access_token: str,
        _full_name: str,
        _project_slug: str,
    ) -> list[dict[str, object]]:
        return [
            {
                "id": 101,
                "number": 3,
                "title": "Define Provider Manifest Exchange Contract",
                "body": "Define the manifest contract.",
                "state": "open",
                "html_url": "https://github.com/example/issues/3",
                "user": {"login": "octocat"},
                "labels": [{"name": "dpt:step:P1"}],
                "updated_at": "2026-09-14T10:00:00Z",
            }
        ]

    async def repository_branches(
        _client: GitHubClient,
        _access_token: str,
        _full_name: str,
    ) -> list[str]:
        return ["main", "mvp/implementation", "mvp/provider-manifest-exchange"]

    async def compare_branches(
        _client: GitHubClient,
        _access_token: str,
        _full_name: str,
        base_branch: str,
        head_branch: str,
    ) -> dict[str, object]:
        captured.update(
            compared_base=base_branch,
            compared_head=head_branch,
        )
        configured_ahead_by = captured.get("ahead_by", 3)
        ahead_by = configured_ahead_by if isinstance(configured_ahead_by, int) else 3
        return {
            "status": "ahead" if ahead_by else "identical",
            "ahead_by": ahead_by,
            "behind_by": 0,
            "total_commits": ahead_by,
        }

    async def create_pull_request(
        _client: GitHubClient,
        _access_token: str,
        _full_name: str,
        title: str,
        body: str,
        base_branch: str,
        head_branch: str,
        labels: list[str],
    ) -> dict[str, object]:
        captured.update(
            title=title,
            body=body,
            base_branch=base_branch,
            head_branch=head_branch,
            labels=labels,
        )
        return {"number": 14, "html_url": "https://github.com/example/pull/14"}

    async def create_branch(
        _client: GitHubClient,
        _access_token: str,
        _full_name: str,
        base_branch: str,
        branch_name: str,
    ) -> dict[str, object]:
        captured.update(
            created_from=base_branch,
            created_branch=branch_name,
        )
        return {"ref": f"refs/heads/{branch_name}"}

    monkeypatch.setattr(
        GitHubClient,
        "start_device_authorization",
        start_device_authorization,
    )
    monkeypatch.setattr(
        GitHubClient,
        "poll_device_authorization",
        poll_device_authorization,
    )
    monkeypatch.setattr(
        GitHubClient,
        "repository_artifacts",
        repository_artifacts,
    )
    monkeypatch.setattr(
        GitHubClient,
        "repository_branches",
        repository_branches,
    )
    monkeypatch.setattr(GitHubClient, "compare_branches", compare_branches)
    monkeypatch.setattr(
        GitHubClient,
        "create_pull_request",
        create_pull_request,
    )
    monkeypatch.setattr(GitHubClient, "create_branch", create_branch)

    with TestClient(create_app(settings(tmp_path))) as client:
        authorization = client.post("/api/auth/github/device").json()
        client.post(f"/api/auth/github/device/{authorization['authorization_id']}/poll")
        project = client.post(
            "/api/projects",
            json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
        ).json()
        repository = client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": "protocol",
                "github_full_name": "stateforge-org/stateforge-protocol",
                "github_default_branch": "main",
            },
        ).json()
        client.post(
            f"/api/projects/{project['project_id']}/steps",
            json={
                "step_id": "P1",
                "repository_id": repository["repository_id"],
                "title": "Define Provider Manifest Exchange Contract",
                "required_result": "The manifest contract is defined.",
                "branch_name": "mvp/provider-manifest-exchange",
            },
        )
        client.post(f"/api/projects/{project['project_id']}/reconcile")
        streamed_reconciliation = client.post(
            f"/api/projects/{project['project_id']}/reconcile/stream"
        )
        reconciliation_events = [
            json.loads(line)
            for line in streamed_reconciliation.text.splitlines()
            if line
        ]

        branches = client.get(
            f"/api/projects/{project['project_id']}/steps/P1/github/branches"
        )
        comparison = client.get(
            f"/api/projects/{project['project_id']}/steps/P1/github/branch-comparison",
            params={
                "base_branch": "mvp/implementation",
                "head_branch": "mvp/provider-manifest-exchange",
            },
        )
        created_branch = client.post(
            f"/api/projects/{project['project_id']}/steps/P1/github/branches",
            json={"base_branch": "mvp/implementation"},
        )
        pull_request = client.post(
            f"/api/projects/{project['project_id']}/steps/P1/github/pull-requests",
            json={
                "base_branch": "mvp/implementation",
                "head_branch": "mvp/provider-manifest-exchange",
            },
        )
        captured["ahead_by"] = 0
        no_commits = client.post(
            f"/api/projects/{project['project_id']}/steps/P1/github/pull-requests",
            json={
                "base_branch": "mvp/implementation",
                "head_branch": "mvp/provider-manifest-exchange",
            },
        )

    assert streamed_reconciliation.status_code == 200
    assert [event["phase"] for event in reconciliation_events[:-1]] == [
        "plan_fetching",
        "plan_applying",
        "fetching",
        "caching",
        "validating",
    ]
    assert reconciliation_events[2]["repository"] == (
        "stateforge-org/stateforge-protocol"
    )
    assert reconciliation_events[-1]["type"] == "complete"
    assert reconciliation_events[-1]["repositories"] == 1
    assert reconciliation_events[-1]["artifacts"] == 1
    assert reconciliation_events[-1]["plan_source_branch"] == "mvp/implementation"
    assert branches.status_code == 200
    assert branches.json()["planned_branch"] == "mvp/provider-manifest-exchange"
    assert branches.json()["default_base"] == "mvp/implementation"
    assert comparison.status_code == 200
    assert comparison.json()["ahead_by"] == 3
    assert created_branch.status_code == 201
    assert created_branch.json() == {
        "name": "mvp/provider-manifest-exchange",
        "base_branch": "mvp/implementation",
    }
    assert captured["created_from"] == "mvp/implementation"
    assert captured["created_branch"] == "mvp/provider-manifest-exchange"
    assert pull_request.status_code == 201
    assert captured["base_branch"] == "mvp/implementation"
    assert captured["head_branch"] == "mvp/provider-manifest-exchange"
    assert captured["labels"] == [
        "dpt:project:stateforge",
        "dpt:release:mvp",
        "dpt:step:P1",
    ]
    assert "Closes #3" in str(captured["body"])
    assert no_commits.status_code == 422
    assert "no commits ahead" in no_commits.json()["detail"]
