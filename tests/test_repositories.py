import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from stateforge_dpt.app import SESSION_COOKIE, create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.git import read_remote_file
from stateforge_dpt.github import GitHubClient


def run_git(git: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the fixed Git executable with test-controlled arguments."""
    return subprocess.run(  # noqa: S603 - fixed executable and test arguments
        [git, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        working_directory=tmp_path / "dpt-workspace",
    )


def create_project_and_repository(client: TestClient) -> tuple[dict, dict]:
    project = client.post(
        "/api/projects",
        json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
    ).json()
    repository = client.post(
        f"/api/projects/{project['project_id']}/repositories",
        json={
            "alias": "sandbox",
            "github_full_name": "stateforge-org/sandbox",
        },
    ).json()
    return project, repository


def test_repository_can_be_removed_without_deleting_project(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_project_and_repository(client)

        response = client.delete(f"/api/repositories/{repository['repository_id']}")

        assert response.status_code == 204
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        assert dashboard["repositories"] == []
        activity = client.get(f"/api/projects/{project['project_id']}/activity").json()
        assert activity[0]["kind"] == "repository.removed"


def test_dashboard_fetches_latest_ci_success_for_managed_repositories(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def latest_successful_workflow_run(
        _client: GitHubClient,
        _access_token: str,
        full_name: str,
        branch: str,
    ) -> dict[str, object]:
        calls.append((full_name, branch))
        return {
            "name": "Provider CI",
            "run_number": 42,
            "run_attempt": 2,
            "event": "push",
            "head_sha": "abc123def456",
            "html_url": "https://github.com/stateforge-org/sandbox/actions/runs/42",
            "updated_at": "2026-10-11T09:15:00Z",
        }

    monkeypatch.setattr(
        GitHubClient,
        "latest_successful_workflow_run",
        latest_successful_workflow_run,
    )

    with TestClient(create_app(settings(tmp_path))) as client:
        project, _repository = create_project_and_repository(client)
        token = client.app.state.service.create_session(
            42,
            "octocat",
            "The Octocat",
            None,
            "access-token",
            None,
            None,
            None,
        )
        client.cookies.set(SESSION_COOKIE, token)

        response = client.get(f"/api/projects/{project['project_id']}")

    assert response.status_code == 200
    assert calls == [("stateforge-org/sandbox", "mvp/implementation")]
    assert response.json()["repositories"][0]["ci_cd"] == {
        "branch": "mvp/implementation",
        "state": "success",
        "workflow_name": "Provider CI",
        "run_number": 42,
        "run_attempt": 2,
        "event": "push",
        "head_sha": "abc123def456",
        "html_url": "https://github.com/stateforge-org/sandbox/actions/runs/42",
        "completed_at": "2026-10-11T09:15:00Z",
    }


def test_project_can_be_removed_without_touching_its_local_clone(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "existing-clone"
    clone.mkdir()
    marker = clone / "keep-me.txt"
    marker.write_text("local work", encoding="utf-8")
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_project_and_repository(client)
        with client.app.state.service.database.connect() as connection:
            connection.execute(
                "UPDATE repositories SET local_path = ? WHERE repository_id = ?",
                (str(clone), repository["repository_id"]),
            )

        response = client.delete(f"/api/projects/{project['project_id']}")

        assert response.status_code == 204
        assert client.get("/api/projects").json() == []
        assert marker.read_text(encoding="utf-8") == "local work"


def test_repository_with_implementation_steps_cannot_be_removed(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_project_and_repository(client)
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

        response = client.delete(f"/api/repositories/{repository['repository_id']}")

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Repository owns 1 implementation step and cannot be removed from DPT"
        )
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        assert len(dashboard["repositories"]) == 1
        assert len(dashboard["steps"]) == 1


def test_clone_suggestion_uses_the_dpt_working_directory(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        _project, repository = create_project_and_repository(client)

        response = client.get(
            f"/api/repositories/{repository['repository_id']}/clone-suggestion"
        )

        assert response.status_code == 200
        assert response.json() == {
            "destination_path": str(
                tmp_path / "dpt-workspace" / "projects" / "StateForge" / "sandbox"
            )
        }


def test_remote_file_fetch_does_not_change_the_checked_out_branch_or_worktree(
    tmp_path: Path,
) -> None:
    git = shutil.which("git")
    assert git is not None
    remote = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    run_git(git, "init", "--bare", str(remote))
    run_git(git, "init", "-b", "main", str(clone))
    run_git(git, "-C", str(clone), "config", "user.email", "dpt@example.test")
    run_git(git, "-C", str(clone), "config", "user.name", "DPT Test")
    run_git(git, "-C", str(clone), "remote", "add", "origin", str(remote))
    plan_path = clone / "STATEFORGE_DPT.md"
    plan_path.write_text("source branch plan\n", encoding="utf-8")
    run_git(git, "-C", str(clone), "add", "STATEFORGE_DPT.md")
    run_git(git, "-C", str(clone), "commit", "-m", "Add plan")
    run_git(git, "-C", str(clone), "branch", "mvp/implementation")
    run_git(git, "-C", str(clone), "push", "origin", "mvp/implementation")
    run_git(git, "-C", str(clone), "checkout", "-b", "feature/work")
    plan_path.write_text("uncommitted feature copy\n", encoding="utf-8")

    content = read_remote_file(
        str(clone),
        "mvp/implementation",
        "STATEFORGE_DPT.md",
    )

    assert content == "source branch plan"
    assert plan_path.read_text(encoding="utf-8") == "uncommitted feature copy\n"
    branch = run_git(
        git,
        "-C",
        str(clone),
        "branch",
        "--show-current",
    ).stdout.strip()
    assert branch == "feature/work"
