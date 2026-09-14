import asyncio
from pathlib import Path

import httpx
import pytest

from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.database import Database
from stateforge_dpt.github import GitHubClient, GitHubError
from stateforge_dpt.models import ProjectCreate, RepositoryCreate, StepCreate
from stateforge_dpt.security import SecretStore
from stateforge_dpt.service import DPTService


def test_issue_body_is_available_in_step_evidence(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    service = DPTService(database, SecretStore(data_dir))

    project = service.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = service.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="agent",
            github_full_name="stateforge-org/stateforge-agent",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="A0",
            repository_id=repository["repository_id"],
            title="Implement discovery",
            required_result="The Agent discovers Providers.",
            branch_name="mvp/discovery",
        ),
    )

    service.record_github_artifacts(
        project["project_id"],
        repository["repository_id"],
        [
            {
                "id": 101,
                "number": 7,
                "title": "Implement discovery",
                "body": "## Required result\n\nDiscover every Provider endpoint.",
                "state": "open",
                "html_url": "https://github.com/stateforge-org/stateforge-agent/issues/7",
                "user": {"login": "octocat"},
                "timeline": [
                    {
                        "id": 201,
                        "event": "commented",
                        "user": {
                            "login": "reviewer",
                            "avatar_url": "https://avatars.githubusercontent.com/u/7?v=4",
                            "html_url": "https://github.com/reviewer",
                        },
                        "body": "This contract needs one more compatibility case.",
                        "created_at": "2026-09-14T10:05:00Z",
                        "html_url": "https://github.com/example/issues/7#issuecomment-201",
                    }
                ],
                "assignees": [
                    {
                        "login": "developer",
                        "avatar_url": "https://avatars.githubusercontent.com/u/42?v=4",
                        "html_url": "https://github.com/developer",
                    }
                ],
                "labels": [{"name": "dpt:step:A0"}],
                "updated_at": "2026-09-14T10:00:00Z",
            }
        ],
    )

    step = service.step(project["project_id"], "A0")

    assert step["artifacts"][0]["body"] == (
        "## Required result\n\nDiscover every Provider endpoint."
    )
    assert step["artifacts"][0]["assignees"] == [
        {
            "login": "developer",
            "avatar_url": "https://avatars.githubusercontent.com/u/42?v=4",
            "html_url": "https://github.com/developer",
        }
    ]
    assert step["artifacts"][0]["timeline"] == [
        {
            "timeline_id": "commented:201",
            "event": "commented",
            "actor": {
                "login": "reviewer",
                "avatar_url": "https://avatars.githubusercontent.com/u/7?v=4",
                "html_url": "https://github.com/reviewer",
            },
            "body": "This contract needs one more compatibility case.",
            "created_at": "2026-09-14T10:05:00Z",
            "html_url": "https://github.com/example/issues/7#issuecomment-201",
            "state": None,
            "commit_sha": None,
            "commit_message": None,
            "label": None,
            "assignee": None,
            "requested_reviewer": None,
            "rename_from": None,
            "rename_to": None,
            "path": None,
            "line": None,
            "diff_hunk": None,
        }
    ]


def test_same_github_artifact_can_be_cached_in_two_local_projects(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    service = DPTService(database, SecretStore(data_dir))
    artifact = {
        "id": 101,
        "number": 7,
        "title": "Implement discovery",
        "body": "Shared GitHub evidence",
        "state": "open",
        "html_url": "https://github.com/stateforge-org/stateforge-agent/issues/7",
        "user": {"login": "octocat"},
        "labels": [],
        "updated_at": "2026-09-14T10:00:00Z",
    }

    project_one = service.create_project(
        ProjectCreate(name="One", slug="one", release="MVP")
    )
    project_two = service.create_project(
        ProjectCreate(name="Two", slug="two", release="MVP")
    )
    repository_one = service.add_repository(
        project_one["project_id"],
        RepositoryCreate(
            alias="agent",
            github_full_name="stateforge-org/stateforge-agent",
        ),
    )
    repository_two = service.add_repository(
        project_two["project_id"],
        RepositoryCreate(
            alias="agent",
            github_full_name="stateforge-org/stateforge-agent",
        ),
    )

    service.record_github_artifacts(
        project_one["project_id"], repository_one["repository_id"], [artifact]
    )
    service.record_github_artifacts(
        project_two["project_id"], repository_two["repository_id"], [artifact]
    )

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT artifact_id FROM github_artifacts ORDER BY artifact_id"
        ).fetchall()
    assert [row["artifact_id"] for row in rows] == [
        f"{project_one['project_id']}:101",
        f"{project_two['project_id']}:101",
    ]


def test_repository_artifacts_include_timeline_and_review_comments(
    monkeypatch,
    tmp_path: Path,
) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )
    requests: list[str] = []

    async def request(_client, _token, method, path, **_kwargs):
        assert method == "GET"
        requests.append(path)
        if path.endswith("/issues"):
            return [
                {
                    "id": 101,
                    "number": 7,
                    "pull_request": {"url": "https://api.github.com/pulls/7"},
                }
            ]
        if path.endswith("/issues/7/timeline"):
            return [{"id": 201, "event": "commented"}]
        if path.endswith("/pulls/7/comments"):
            return [{"id": 301, "body": "Please rename this symbol."}]
        if path.endswith("/pulls/7"):
            return {"merged_at": None}
        raise AssertionError(f"Unexpected GitHub path: {path}")

    monkeypatch.setattr(github, "_request", request)

    artifacts = asyncio.run(
        github.repository_artifacts(
            "access-token",
            "stateforge-org/stateforge-agent",
            "stateforge",
        )
    )

    assert artifacts[0]["timeline"] == [{"id": 201, "event": "commented"}]
    assert artifacts[0]["review_comments"] == [
        {"id": 301, "body": "Please rename this symbol."}
    ]
    assert requests == [
        "/repos/stateforge-org/stateforge-agent/issues",
        "/repos/stateforge-org/stateforge-agent/issues/7/timeline",
        "/repos/stateforge-org/stateforge-agent/pulls/7",
        "/repos/stateforge-org/stateforge-agent/pulls/7/comments",
    ]


def test_issue_creation_adds_every_missing_dpt_label(
    monkeypatch,
    tmp_path: Path,
) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )
    requests = []

    async def request(_client, _token, method, path, **kwargs):
        requests.append((method, path, kwargs))
        if method == "GET":
            return [{"name": "dpt:project:stateforge"}]
        if path.endswith("/issues"):
            return {"number": 12, "html_url": "https://github.com/example/issues/12"}
        return kwargs["json"]

    monkeypatch.setattr(github, "_request", request)
    labels = [
        "dpt:project:stateforge",
        "dpt:release:mvp",
        "dpt:step:P2",
    ]

    issue = asyncio.run(
        github.create_issue(
            "access-token",
            "stateforge-org/stateforge-protocol",
            "Define capability assessment",
            "Required result",
            labels,
        )
    )

    created_labels = [
        call[2]["json"]["name"]
        for call in requests
        if call[0] == "POST" and call[1].endswith("/labels")
    ]
    issue_request = next(
        call for call in requests if call[0] == "POST" and call[1].endswith("/issues")
    )

    assert created_labels == ["dpt:release:mvp", "dpt:step:P2"]
    assert issue_request[2]["json"]["labels"] == labels
    assert issue["number"] == 12


def test_repository_branches_returns_every_available_branch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )

    async def request(_client, _token, method, path, **kwargs):
        assert method == "GET"
        assert path.endswith("/branches")
        assert kwargs["params"]["page"] == 1
        return [{"name": "mvp/implementation"}, {"name": "mvp/manifest"}]

    monkeypatch.setattr(github, "_request", request)

    branches = asyncio.run(
        github.repository_branches(
            "access-token",
            "stateforge-org/stateforge-protocol",
        )
    )

    assert branches == ["mvp/implementation", "mvp/manifest"]


def test_branch_comparison_encodes_branch_names(
    monkeypatch,
    tmp_path: Path,
) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )

    async def request(_client, _token, method, path, **_kwargs):
        assert method == "GET"
        assert path.endswith("/compare/mvp%2Fimplementation...mvp%2Fprovider-manifest")
        return {
            "status": "ahead",
            "ahead_by": 3,
            "behind_by": 0,
            "total_commits": 3,
        }

    monkeypatch.setattr(github, "_request", request)

    comparison = asyncio.run(
        github.compare_branches(
            "access-token",
            "stateforge-org/stateforge-protocol",
            "mvp/implementation",
            "mvp/provider-manifest",
        )
    )

    assert comparison["ahead_by"] == 3
    assert comparison["status"] == "ahead"


def test_github_validation_error_includes_specific_details(tmp_path: Path) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "PullRequest",
                        "code": "custom",
                        "message": "No commits between the selected branches",
                    }
                ],
            },
            request=request,
        )

    async def make_request() -> None:
        transport = httpx.MockTransport(respond)
        async with httpx.AsyncClient(transport=transport) as client:
            await github._request(client, "access-token", "POST", "/pulls")

    with pytest.raises(
        GitHubError,
        match="Validation Failed — No commits between the selected branches",
    ):
        asyncio.run(make_request())


def test_branch_creation_uses_the_selected_base_commit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )
    requests = []

    async def request(_client, _token, method, path, **kwargs):
        requests.append((method, path, kwargs))
        if method == "GET":
            return {"object": {"sha": "base-commit-sha"}}
        return {"ref": kwargs["json"]["ref"]}

    monkeypatch.setattr(github, "_request", request)

    reference = asyncio.run(
        github.create_branch(
            "access-token",
            "stateforge-org/stateforge-protocol",
            "mvp/implementation",
            "mvp/provider-manifest-exchange",
        )
    )

    base_call, create_call = requests
    assert base_call[1].endswith("/git/ref/heads/mvp%2Fimplementation")
    assert create_call[1].endswith("/git/refs")
    assert create_call[2]["json"] == {
        "ref": "refs/heads/mvp/provider-manifest-exchange",
        "sha": "base-commit-sha",
    }
    assert reference["ref"] == "refs/heads/mvp/provider-manifest-exchange"


def test_pull_request_creation_uses_selected_branches_and_dpt_labels(
    monkeypatch,
    tmp_path: Path,
) -> None:
    github = GitHubClient(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path,
            frontend_dir=tmp_path,
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )
    labels = [
        "dpt:project:stateforge",
        "dpt:release:mvp",
        "dpt:step:P1",
    ]
    requests = []

    async def request(_client, _token, method, path, **kwargs):
        requests.append((method, path, kwargs))
        if method == "GET":
            return [{"name": label} for label in labels]
        if path.endswith("/pulls"):
            return {"number": 14, "html_url": "https://github.com/example/pull/14"}
        return {"labels": kwargs["json"]["labels"]}

    monkeypatch.setattr(github, "_request", request)

    pull_request = asyncio.run(
        github.create_pull_request(
            "access-token",
            "stateforge-org/stateforge-protocol",
            "Define Provider Manifest Exchange Contract",
            "Closes #3",
            "mvp/implementation",
            "mvp/provider-manifest-exchange",
            labels,
        )
    )

    pull_request_call = next(
        call for call in requests if call[0] == "POST" and call[1].endswith("/pulls")
    )
    label_call = next(call for call in requests if call[0] == "PATCH")

    assert pull_request_call[2]["json"]["base"] == "mvp/implementation"
    assert pull_request_call[2]["json"]["head"] == "mvp/provider-manifest-exchange"
    assert label_call[1].endswith("/issues/14")
    assert label_call[2]["json"]["labels"] == labels
    assert pull_request["number"] == 14


def test_github_evidence_derives_progress_and_unlocks_dependencies(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    service = DPTService(database, SecretStore(data_dir))

    project = service.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = service.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="protocol",
            github_full_name="stateforge-org/stateforge-protocol",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="P0",
            repository_id=repository["repository_id"],
            title="Define handshake",
            required_result="The handshake contract is defined.",
            branch_name="mvp/handshake",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="P1",
            repository_id=repository["repository_id"],
            title="Define manifest exchange",
            required_result="The manifest contract is defined.",
            branch_name="mvp/manifest",
            depends_on=["P0"],
        ),
    )

    service.record_github_artifacts(
        project["project_id"],
        repository["repository_id"],
        [
            {
                "id": 100,
                "number": 2,
                "title": "Define handshake",
                "body": "Implemented and reviewed.",
                "state": "closed",
                "html_url": "https://github.com/example/pull/2",
                "user": {"login": "reviewer"},
                "labels": [{"name": "dpt:step:P0"}],
                "pull_details": {
                    "merged_at": "2026-09-14T10:00:00Z",
                    "head": {"ref": "mvp/handshake"},
                    "base": {"ref": "mvp/implementation"},
                    "requested_reviewers": [],
                },
                "updated_at": "2026-09-14T10:00:00Z",
            },
            {
                "id": 101,
                "number": 3,
                "title": "Define manifest exchange",
                "body": "Ready for implementation.",
                "state": "open",
                "html_url": "https://github.com/example/issues/3",
                "user": {"login": "maintainer"},
                "labels": [{"name": "dpt:step:P1"}],
                "updated_at": "2026-09-14T11:00:00Z",
            },
        ],
    )

    dashboard = service.dashboard(project["project_id"])
    steps = {step["step_id"]: step for step in dashboard["steps"]}

    assert steps["P0"]["computed_state"] == "completed"
    assert steps["P1"]["computed_state"] == "pending"
    assert steps["P1"]["dependencies_complete"] is True
    assert dashboard["metrics"]["actionable"] == 1
    assert dashboard["findings"] == []


def test_pull_request_before_dependencies_is_a_reconciliation_finding(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    service = DPTService(database, SecretStore(data_dir))

    project = service.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = service.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="provider",
            github_full_name="stateforge-org/stateforge-provider-implementation",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="P1",
            repository_id=repository["repository_id"],
            title="Define manifest exchange",
            required_result="The manifest contract is defined.",
            branch_name="mvp/manifest",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="R1",
            repository_id=repository["repository_id"],
            title="Implement manifest exchange",
            required_result="The Provider exchanges its manifest.",
            branch_name="mvp/provider-manifest",
            depends_on=["P1"],
        ),
    )
    service.record_github_artifacts(
        project["project_id"],
        repository["repository_id"],
        [
            {
                "id": 201,
                "number": 4,
                "title": "Implement manifest exchange",
                "body": "Implementation under review.",
                "state": "open",
                "html_url": "https://github.com/example/pull/4",
                "user": {"login": "contributor"},
                "labels": [{"name": "dpt:step:R1"}],
                "pull_details": {
                    "merged_at": None,
                    "head": {"ref": "mvp/provider-manifest"},
                    "base": {"ref": "mvp/implementation"},
                    "requested_reviewers": [],
                },
                "updated_at": "2026-09-14T12:00:00Z",
            }
        ],
    )

    dashboard = service.dashboard(project["project_id"])
    step = next(step for step in dashboard["steps"] if step["step_id"] == "R1")

    assert step["computed_state"] == "in_review"
    assert step["dependencies_complete"] is False
    assert dashboard["findings"] == [
        {
            "kind": "Dependency violation",
            "severity": "error",
            "step_id": "R1",
            "message": (
                "A pull request exists while at least one prerequisite is incomplete."
            ),
        }
    ]
