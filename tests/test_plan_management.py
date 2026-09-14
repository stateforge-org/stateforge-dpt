from pathlib import Path

from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
    )


def create_plan(client: TestClient) -> tuple[dict, dict, dict]:
    project = client.post(
        "/api/projects",
        json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
    ).json()
    protocol = client.post(
        f"/api/projects/{project['project_id']}/repositories",
        json={
            "alias": "protocol",
            "github_full_name": "stateforge-org/stateforge-protocol",
        },
    ).json()
    provider = client.post(
        f"/api/projects/{project['project_id']}/repositories",
        json={
            "alias": "provider",
            "github_full_name": "stateforge-org/stateforge-provider",
        },
    ).json()
    return project, protocol, provider


def create_step(
    client: TestClient,
    project: dict,
    repository: dict,
    step_id: str,
    depends_on: list[str] | None = None,
) -> dict:
    response = client.post(
        f"/api/projects/{project['project_id']}/steps",
        json={
            "step_id": step_id,
            "repository_id": repository["repository_id"],
            "title": f"[TASK][MVP] Build {step_id}",
            "required_result": f"The result for {step_id} exists.",
            "branch_name": f"mvp/build-{step_id.lower()}",
            "depends_on": depends_on or [],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_step_can_be_visually_reassigned_and_reconnected(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, protocol, provider = create_plan(client)
        create_step(client, project, protocol, "P0")
        create_step(client, project, protocol, "P1", ["P0"])

        response = client.put(
            f"/api/projects/{project['project_id']}/steps/P1",
            json={
                "repository_id": provider["repository_id"],
                "title": "[TASK][MVP] Build Provider P1",
                "required_result": "The provider result exists.",
                "branch_name": "mvp/build-provider-p1",
                "depends_on": [],
            },
        )

        assert response.status_code == 200
        step = response.json()
        assert step["repository_id"] == provider["repository_id"]
        assert step["repository_alias"] == "provider"
        assert step["depends_on"] == []


def test_step_deletion_protects_the_dependency_graph(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, protocol, _provider = create_plan(client)
        create_step(client, project, protocol, "P0")
        create_step(client, project, protocol, "P1", ["P0"])

        blocked = client.delete(f"/api/projects/{project['project_id']}/steps/P0")
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == (
            "Step is required by P1 and cannot be deleted"
        )

        assert (
            client.delete(f"/api/projects/{project['project_id']}/steps/P1").status_code
            == 204
        )
        assert (
            client.delete(f"/api/projects/{project['project_id']}/steps/P0").status_code
            == 204
        )
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        assert dashboard["steps"] == []


def test_step_reassignment_requires_a_repository_in_the_same_project(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, protocol, _provider = create_plan(client)
        create_step(client, project, protocol, "P0")

        response = client.put(
            f"/api/projects/{project['project_id']}/steps/P0",
            json={
                "repository_id": "missing-repository",
                "title": "[TASK][MVP] Build P0",
                "required_result": "The result exists.",
                "branch_name": "mvp/build-p0",
                "depends_on": [],
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Repository does not belong to this project"
        )


def test_step_update_rejects_a_dependency_cycle(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, protocol, _provider = create_plan(client)
        create_step(client, project, protocol, "P0")
        create_step(client, project, protocol, "P1", ["P0"])

        response = client.put(
            f"/api/projects/{project['project_id']}/steps/P0",
            json={
                "repository_id": protocol["repository_id"],
                "title": "[TASK][MVP] Build P0",
                "required_result": "The result exists.",
                "branch_name": "mvp/build-p0",
                "depends_on": ["P1"],
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Dependency cycle detected at P0"
