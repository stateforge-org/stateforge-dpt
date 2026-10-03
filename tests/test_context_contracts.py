import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.plan_file import parse_manifest


def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
    )


def git_repository(tmp_path: Path) -> Path:
    git = shutil.which("git")
    assert git is not None
    repository = tmp_path / "protocol"
    (repository / "src").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "src" / "manifest_handler.py").write_text(
        "class ManifestHandler:\n    pass\n", encoding="utf-8"
    )
    (repository / "tests" / "test_manifest.py").write_text(
        "def test_manifest_contract():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(  # noqa: S603 - fixed Git executable and test paths
        [git, "init", str(repository)], check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603 - fixed Git executable and test values
        [git, "-C", str(repository), "config", "user.email", "dpt@example.com"],
        check=True,
    )
    subprocess.run(  # noqa: S603 - fixed Git executable and test values
        [git, "-C", str(repository), "config", "user.name", "DPT Test"],
        check=True,
    )
    subprocess.run(  # noqa: S603 - fixed Git executable and test paths
        [git, "-C", str(repository), "add", "."], check=True
    )
    subprocess.run(  # noqa: S603 - fixed Git executable and test values
        [git, "-C", str(repository), "commit", "-m", "Initial context"],
        check=True,
        capture_output=True,
    )
    return repository


def create_milestone(client: TestClient, repository_path: Path) -> tuple[dict, dict]:
    project = client.post(
        "/api/projects",
        json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
    ).json()
    repository = client.post(
        f"/api/projects/{project['project_id']}/repositories",
        json={
            "alias": "protocol",
            "github_full_name": "stateforge-org/stateforge-protocol",
        },
    ).json()
    with client.app.state.service.database.connect() as connection:
        connection.execute(
            "UPDATE repositories SET local_path = ? WHERE repository_id = ?",
            (str(repository_path), repository["repository_id"]),
        )
    response = client.post(
        f"/api/projects/{project['project_id']}/steps",
        json={
            "step_id": "P1",
            "repository_id": repository["repository_id"],
            "title": "[TASK][MVP] Implement Manifest Handler",
            "required_result": "ManifestHandler enforces the manifest contract.",
            "branch_name": "mvp/manifest-handler",
        },
    )
    assert response.status_code == 201
    return project, repository


def context_contract(repository_id: str) -> dict:
    return {
        "resources": [
            {
                "context_id": "manifest-handler",
                "repository_id": repository_id,
                "path": "src/manifest_handler.py",
                "selector_kind": "symbol",
                "selector_value": "ManifestHandler",
                "role": "expected_to_change",
                "explanation": "This is the canonical implementation surface.",
                "required": True,
                "authority": "canonical",
            },
            {
                "context_id": "manifest-test",
                "repository_id": repository_id,
                "path": "tests/test_manifest.py",
                "selector_kind": "test_case",
                "selector_value": "test_manifest_contract",
                "role": "must_verify",
                "explanation": "This test proves the public behavior remains intact.",
                "required": True,
                "authority": "canonical",
            },
        ],
        "verification": [
            {
                "verification_id": "manifest-tests",
                "kind": "test",
                "repository_id": repository_id,
                "target": "pytest tests/test_manifest.py",
                "explanation": "The focused contract test must pass.",
                "required": True,
            }
        ],
    }


def test_context_contract_is_portable_approved_and_staleness_sensitive(
    tmp_path: Path,
) -> None:
    repository_path = git_repository(tmp_path)
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_milestone(client, repository_path)
        base = f"/api/projects/{project['project_id']}/steps/P1"

        saved = client.put(
            f"{base}/context", json=context_contract(repository["repository_id"])
        )
        assert saved.status_code == 200
        assert len(saved.json()["context_resources"]) == 2

        inspected = client.post(f"{base}/context/inspect")
        assert inspected.status_code == 200
        assert {item["status"] for item in inspected.json()["observations"]} == {
            "current"
        }

        approved = client.post(
            f"{base}/context/approve", json={"approved_by": "maintainer"}
        )
        assert approved.status_code == 200
        assert approved.json()["context_readiness"] == {
            "state": "ready",
            "ready": True,
            "reasons": [],
        }

        unchanged = client.put(
            f"{base}/context", json=context_contract(repository["repository_id"])
        )
        assert unchanged.status_code == 200
        assert (
            unchanged.json()["context_approval"]["approval_id"]
            == (approved.json()["context_approval"]["approval_id"])
        )

        client.app.state.service.record_github_artifacts(
            project["project_id"],
            repository["repository_id"],
            [
                {
                    "id": 101,
                    "number": 5,
                    "title": "[TASK][MVP] Implement Manifest Handler",
                    "body": "Build the manifest handler from the declared contract.",
                    "state": "open",
                    "html_url": (
                        "https://github.com/stateforge-org/stateforge-protocol/issues/5"
                    ),
                    "user": {"login": "maintainer"},
                    "assignees": [{"login": "developer"}],
                    "timeline": [
                        {
                            "id": 102,
                            "event": "commented",
                            "user": {"login": "reviewer"},
                            "body": "Preserve the existing manifest revision behavior.",
                            "created_at": "2026-10-03T08:55:00+00:00",
                            "html_url": (
                                "https://github.com/stateforge-org/"
                                "stateforge-protocol/issues/5#issuecomment-102"
                            ),
                        }
                    ],
                    "labels": [
                        {"name": "dpt:step:P1"},
                        {"name": "dpt:release:MVP"},
                    ],
                    "updated_at": "2026-10-03T09:00:00+00:00",
                }
            ],
        )

        package = client.post(f"{base}/implementation-package")
        assert package.status_code == 200
        assert package.json()["package"]["context"][0]["content"].startswith(
            "class ManifestHandler"
        )
        assert "Implement only this milestone" in package.json()["markdown"]
        assert package.json()["package"]["github_issue"] == {
            "repository": "stateforge-org/stateforge-protocol",
            "number": 5,
            "title": "[TASK][MVP] Implement Manifest Handler",
            "body": "Build the manifest handler from the declared contract.",
            "state": "open",
            "url": "https://github.com/stateforge-org/stateforge-protocol/issues/5",
            "author": "maintainer",
            "assignees": [
                {
                    "avatar_url": "https://github.com/developer.png?size=64",
                    "html_url": "https://github.com/developer",
                    "login": "developer",
                }
            ],
            "labels": ["dpt:step:P1", "dpt:release:MVP"],
            "timeline": [
                {
                    "timeline_id": "commented:102",
                    "event": "commented",
                    "actor": {
                        "avatar_url": "https://github.com/reviewer.png?size=64",
                        "html_url": "https://github.com/reviewer",
                        "login": "reviewer",
                    },
                    "body": "Preserve the existing manifest revision behavior.",
                    "created_at": "2026-10-03T08:55:00+00:00",
                    "html_url": (
                        "https://github.com/stateforge-org/"
                        "stateforge-protocol/issues/5#issuecomment-102"
                    ),
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
            ],
            "updated_at": "2026-10-03T09:00:00+00:00",
        }
        assert "## GitHub issue" in package.json()["markdown"]
        assert "stateforge-protocol/issues/5" in package.json()["markdown"]
        assert "Build the manifest handler" in package.json()["markdown"]
        assert (
            "Preserve the existing manifest revision behavior"
            in package.json()["markdown"]
        )

        preview = client.get(f"/api/projects/{project['project_id']}/plan/preview").text
        portable, _fingerprint = parse_manifest(preview)
        assert portable.steps[0].context_resources[0].path == (
            "src/manifest_handler.py"
        )
        assert portable.steps[0].verification_requirements[0].kind == "test"

        (repository_path / "src" / "manifest_handler.py").write_text(
            "class ManifestHandler:\n    changed = True\n", encoding="utf-8"
        )
        client.post(f"{base}/context/inspect")
        stale = client.get(f"/api/projects/{project['project_id']}").json()
        step = stale["steps"][0]
        assert step["context_resources"][0]["observation"]["status"] == "changed"
        assert step["context_readiness"]["state"] == "needs_context_review"
        assert client.post(f"{base}/implementation-package").status_code == 409


def test_visual_tree_discovery_and_unsafe_paths(tmp_path: Path) -> None:
    repository_path = git_repository(tmp_path)
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_milestone(client, repository_path)

        tree = client.get(
            f"/api/repositories/{repository['repository_id']}/tree",
            params={"query": "manifest"},
        )
        assert tree.status_code == 200
        assert tree.json()["paths"] == [
            "src/manifest_handler.py",
            "tests/test_manifest.py",
        ]
        assert tree.json()["directories"] == ["src", "tests"]

        preview = client.get(
            f"/api/repositories/{repository['repository_id']}/resource-preview",
            params={"path": "src/manifest_handler.py"},
        )
        assert preview.status_code == 200
        assert preview.json()["content"].startswith("class ManifestHandler")
        assert {
            (selector["kind"], selector["value"])
            for selector in preview.json()["selectors"]
        } == {("symbol", "ManifestHandler")}

        contract = context_contract(repository["repository_id"])
        contract["resources"][0]["path"] = "../outside.py"
        unsafe = client.put(
            f"/api/projects/{project['project_id']}/steps/P1/context", json=contract
        )
        assert unsafe.status_code == 409
        assert "inside their managed repository" in unsafe.json()["detail"]


def test_pull_request_impact_is_reported_without_a_separate_dpt_acceptance(
    tmp_path: Path,
) -> None:
    repository_path = git_repository(tmp_path)
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_milestone(client, repository_path)
        base = f"/api/projects/{project['project_id']}/steps/P1"
        client.put(
            f"{base}/context", json=context_contract(repository["repository_id"])
        )
        client.post(f"{base}/context/approve", json={"approved_by": "maintainer"})

        client.app.state.service.record_github_artifacts(
            project["project_id"],
            repository["repository_id"],
            [
                {
                    "id": 301,
                    "number": 8,
                    "title": "Implement Manifest Handler",
                    "body": "Implements P1.",
                    "state": "open",
                    "html_url": "https://github.com/stateforge-org/stateforge-protocol/pull/8",
                    "user": {"login": "developer"},
                    "labels": [{"name": "dpt:step:P1"}],
                    "pull_details": {
                        "merged_at": None,
                        "head": {"ref": "mvp/manifest-handler"},
                        "base": {"ref": "mvp/implementation"},
                        "requested_reviewers": [],
                    },
                    "changed_files": [
                        {"filename": "src/manifest_handler.py"},
                        {"filename": "src/unexpected_global.py"},
                    ],
                    "checks_state": "success",
                    "updated_at": "2026-10-03T10:00:00+00:00",
                }
            ],
        )

        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        step = dashboard["steps"][0]
        assert step["impact_analysis"]["expected_changes"] == [
            "src/manifest_handler.py"
        ]
        assert step["impact_analysis"]["unexpected_changes"] == [
            "src/unexpected_global.py"
        ]
        assert step["context_readiness"]["ready"] is True
        assert step["risk_assessment"]["level"] == "red"
        assert any(
            factor["kind"] == "unexpected_change_surface"
            for factor in step["risk_assessment"]["factors"]
        )


def test_verification_can_be_run_or_recorded_with_human_identity(
    tmp_path: Path,
) -> None:
    repository_path = git_repository(tmp_path)
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_milestone(client, repository_path)
        base = f"/api/projects/{project['project_id']}/steps/P1"
        contract = context_contract(repository["repository_id"])
        contract["verification"][0].update(
            {"kind": "command", "target": "git status --short"}
        )
        client.put(f"{base}/context", json=contract)

        mismatch = client.post(
            f"{base}/verification/manifest-tests/run",
            json={"approved_by": "maintainer", "approved_target": "git status"},
        )
        assert mismatch.status_code == 409

        executed = client.post(
            f"{base}/verification/manifest-tests/run",
            json={
                "approved_by": "maintainer",
                "approved_target": "git status --short",
            },
        )
        assert executed.status_code == 200
        observation = executed.json()["verification_requirements"][0]["observation"]
        assert observation["status"] == "passed"
        assert observation["recorded_by"] == "maintainer"
        assert observation["exit_code"] == 0

        recorded = client.post(
            f"{base}/verification/manifest-tests/record",
            json={
                "status": "unavailable",
                "recorded_by": "reviewer",
                "evidence": "CI service was unavailable.",
            },
        )
        assert recorded.status_code == 200
        observation = recorded.json()["verification_requirements"][0]["observation"]
        assert observation["status"] == "unavailable"
        assert observation["evidence"] == "CI service was unavailable."


def test_repository_removal_reports_context_references(tmp_path: Path) -> None:
    repository_path = git_repository(tmp_path)
    second_path = tmp_path / "consumer"
    shutil.copytree(repository_path, second_path)
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_milestone(client, repository_path)
        consumer = client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": "consumer",
                "github_full_name": "stateforge-org/stateforge-consumer",
            },
        ).json()
        with client.app.state.service.database.connect() as connection:
            connection.execute(
                "UPDATE repositories SET local_path = ? WHERE repository_id = ?",
                (str(second_path), consumer["repository_id"]),
            )
        contract = context_contract(repository["repository_id"])
        contract["resources"][0]["repository_id"] = consumer["repository_id"]
        saved = client.put(
            f"/api/projects/{project['project_id']}/steps/P1/context", json=contract
        )
        assert saved.status_code == 200

        removed = client.delete(f"/api/repositories/{consumer['repository_id']}")
        assert removed.status_code == 409
        assert "P1:src/manifest_handler.py" in removed.json()["detail"]
