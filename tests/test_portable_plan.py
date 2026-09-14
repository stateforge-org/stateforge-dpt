import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.plan_file import (
    MANIFEST_END,
    MANIFEST_START,
    PlanFileError,
    load_plan,
    parse_manifest,
)


def settings(root: Path, machine: str) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=root / machine / "data",
        frontend_dir=root / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        working_directory=root / machine,
    )


def git_clone(root: Path, name: str, full_name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    git = shutil.which("git")
    assert git is not None
    subprocess.run(  # noqa: S603 - fixed test executable and arguments
        [git, "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603 - fixed test executable and arguments
        [
            git,
            "-C",
            str(path),
            "remote",
            "add",
            "origin",
            f"https://github.com/{full_name}.git",
        ],
        check=True,
        capture_output=True,
    )
    return path


def create_step(
    client: TestClient,
    project_id: str,
    repository_id: str,
    step_id: str,
    dependencies: list[str] | None = None,
) -> None:
    response = client.post(
        f"/api/projects/{project_id}/steps",
        json={
            "step_id": step_id,
            "repository_id": repository_id,
            "title": f"[TASK][MVP] Build {step_id}",
            "required_result": f"The complete result for {step_id} exists.",
            "branch_name": f"mvp/build-{step_id.lower()}",
            "depends_on": dependencies or [],
        },
    )
    assert response.status_code == 201


def test_legacy_manifest_without_source_branch_remains_valid() -> None:
    raw_plan = {
        "schema_version": 1,
        "project": {
            "slug": "stateforge",
            "name": "StateForge",
            "release": "MVP",
            "generated_filename": "STATEFORGE_DPT.md",
            "revision": 5,
        },
        "repositories": [],
        "steps": [],
    }
    canonical = json.dumps(
        raw_plan,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    envelope = {
        "format": "stateforge-dpt-plan",
        "format_version": 1,
        "fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
        "plan": raw_plan,
    }
    content = (
        f"{MANIFEST_START}\n"
        f"{json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        f"{MANIFEST_END}"
    )

    plan, fingerprint = parse_manifest(content)

    assert plan.project.plan_source_branch == "mvp/implementation"
    assert len(fingerprint) == 64


def test_git_plan_can_recreate_and_update_a_project_on_another_machine(
    tmp_path: Path,
) -> None:
    protocol_path = git_clone(
        tmp_path,
        "stateforge-protocol",
        "stateforge-org/stateforge-protocol",
    )
    provider_path = git_clone(
        tmp_path,
        "stateforge-provider",
        "stateforge-org/stateforge-provider",
    )
    machine_a = settings(tmp_path, "machine-a")
    machine_b = settings(tmp_path, "machine-b")

    with TestClient(create_app(machine_a)) as client:
        project = client.post(
            "/api/projects",
            json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
        ).json()
        protocol = client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": "protocol",
                "github_full_name": "stateforge-org/stateforge-protocol",
                "github_default_branch": "main",
                "responsibility": "Wire contracts",
            },
        ).json()
        provider = client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": "provider",
                "github_full_name": "stateforge-org/stateforge-provider",
                "github_default_branch": "main",
                "responsibility": "Provider runtime",
            },
        ).json()
        assert (
            client.post(
                f"/api/repositories/{protocol['repository_id']}/attach",
                json={"local_path": str(protocol_path)},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/repositories/{provider['repository_id']}/attach",
                json={"local_path": str(provider_path)},
            ).status_code
            == 200
        )
        create_step(client, project["project_id"], protocol["repository_id"], "P0")
        create_step(
            client,
            project["project_id"],
            provider["repository_id"],
            "R0",
            ["P0"],
        )
        published = client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )
        assert published.status_code == 200
        assert published.json()["revision"] == 1

    protocol_file = protocol_path / "STATEFORGE_DPT.md"
    provider_file = provider_path / "STATEFORGE_DPT.md"
    assert protocol_file.read_bytes() == provider_file.read_bytes()
    portable = load_plan(str(protocol_path)).plan
    assert portable.project.revision == 1
    assert portable.project.plan_source_branch == "mvp/implementation"
    assert portable.steps[1].branch_name == "mvp/build-r0"
    assert portable.steps[1].depends_on == ["P0"]

    with TestClient(create_app(machine_b)) as client:
        inspection = client.post(
            "/api/projects/import/inspect",
            json={"local_path": str(protocol_path)},
        )
        assert inspection.status_code == 200
        assert inspection.json()["project"]["revision"] == 1
        assert inspection.json()["repositories"] == 2
        assert inspection.json()["steps"] == 2

        imported = client.post(
            "/api/projects/import",
            json={"local_path": str(protocol_path)},
        )
        assert imported.status_code == 201
        dashboard = imported.json()
        imported_project_id = dashboard["project"]["project_id"]
        assert dashboard["project"]["revision"] == 1
        assert [step["step_id"] for step in dashboard["steps"]] == ["P0", "R0"]
        assert dashboard["steps"][1]["depends_on"] == ["P0"]
        imported_protocol = next(
            repository
            for repository in dashboard["repositories"]
            if repository["alias"] == "protocol"
        )
        imported_provider = next(
            repository
            for repository in dashboard["repositories"]
            if repository["alias"] == "provider"
        )
        assert imported_protocol["local_path"] == str(protocol_path)
        assert imported_provider["local_path"] is None

    with TestClient(create_app(machine_a)) as client:
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        create_step(
            client,
            project["project_id"],
            dashboard["repositories"][0]["repository_id"],
            "P1",
            ["P0"],
        )
        published = client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )
        assert published.status_code == 200
        assert published.json()["revision"] == 2

    with TestClient(create_app(machine_b)) as client:
        sources = client.get(f"/api/projects/{imported_project_id}/plan/sources").json()
        assert sources["has_local_changes"] is False
        assert (
            sources["recommended_repository_id"] == imported_protocol["repository_id"]
        )
        assert sources["sources"][0]["status"] == "newer"
        synced = client.post(
            f"/api/projects/{imported_project_id}/plan/import",
            json={"repository_id": imported_protocol["repository_id"]},
        )
        assert synced.status_code == 200
        dashboard = client.get(f"/api/projects/{imported_project_id}").json()
        assert dashboard["project"]["revision"] == 2
        assert [step["step_id"] for step in dashboard["steps"]] == [
            "P0",
            "R0",
            "P1",
        ]


def test_attaching_a_portable_clone_populates_an_empty_project(
    tmp_path: Path,
) -> None:
    clone_path = git_clone(
        tmp_path,
        "stateforge-protocol",
        "stateforge-org/stateforge-protocol",
    )

    with TestClient(create_app(settings(tmp_path, "publisher"))) as client:
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
        client.post(
            f"/api/repositories/{repository['repository_id']}/attach",
            json={"local_path": str(clone_path)},
        )
        create_step(client, project["project_id"], repository["repository_id"], "P0")
        client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )

    with TestClient(create_app(settings(tmp_path, "consumer"))) as client:
        project = client.post(
            "/api/projects",
            json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
        ).json()
        repository = client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": "temporary-alias",
                "github_full_name": "stateforge-org/stateforge-protocol",
            },
        ).json()
        attached = client.post(
            f"/api/repositories/{repository['repository_id']}/attach",
            json={"local_path": str(clone_path)},
        )
        assert attached.status_code == 200
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        assert dashboard["project"]["revision"] == 1
        assert dashboard["project"]["plan_fingerprint"]
        assert dashboard["repositories"][0]["alias"] == "protocol"
        assert dashboard["repositories"][0]["local_path"] == str(clone_path)
        assert [step["step_id"] for step in dashboard["steps"]] == ["P0"]


def test_portable_manifest_rejects_changed_plan_content(tmp_path: Path) -> None:
    clone_path = git_clone(
        tmp_path,
        "stateforge-protocol",
        "stateforge-org/stateforge-protocol",
    )
    with TestClient(create_app(settings(tmp_path, "publisher"))) as client:
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
        client.post(
            f"/api/repositories/{repository['repository_id']}/attach",
            json={"local_path": str(clone_path)},
        )
        create_step(client, project["project_id"], repository["repository_id"], "P0")
        client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )

    plan_path = clone_path / "STATEFORGE_DPT.md"
    content = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(
        content.replace("mvp/build-p0", "mvp/tampered-p0", 1),
        encoding="utf-8",
    )
    with pytest.raises(PlanFileError, match="checksum"):
        load_plan(str(plan_path))


def test_reconciliation_imports_a_newer_plan_from_the_remote_parent_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone_path = git_clone(
        tmp_path,
        "stateforge-protocol",
        "stateforge-org/stateforge-protocol",
    )
    publisher = create_app(settings(tmp_path, "publisher"))
    consumer = create_app(settings(tmp_path, "consumer"))

    with TestClient(publisher) as client:
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
        client.post(
            f"/api/repositories/{repository['repository_id']}/attach",
            json={"local_path": str(clone_path)},
        )
        create_step(client, project["project_id"], repository["repository_id"], "P0")
        client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )

    with TestClient(consumer) as client:
        dashboard = client.post(
            "/api/projects/import",
            json={"local_path": str(clone_path)},
        ).json()
        consumer_project_id = dashboard["project"]["project_id"]

    with TestClient(publisher) as client:
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        create_step(
            client,
            project["project_id"],
            dashboard["repositories"][0]["repository_id"],
            "P1",
            ["P0"],
        )
        client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )

    remote_content = (clone_path / "STATEFORGE_DPT.md").read_text(encoding="utf-8")
    monkeypatch.setattr(
        "stateforge_dpt.service.read_remote_file",
        lambda *_args, **_kwargs: remote_content,
    )
    result = consumer.state.service.refresh_remote_plan_sources(
        consumer_project_id,
        None,
    )

    assert result["status"] == "imported"
    assert result["branch"] == "mvp/implementation"
    assert result["revision"] == 2
    dashboard = consumer.state.service.dashboard(consumer_project_id)
    assert [step["step_id"] for step in dashboard["steps"]] == ["P0", "P1"]
    source_status = consumer.state.service.project_plan_sources(consumer_project_id)
    assert source_status["sources"][0]["source_kind"] == "remote"
    assert source_status["sources"][0]["path"].startswith("origin/mvp/implementation:")


def test_empty_project_does_not_adopt_a_different_remote_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = git_clone(
        tmp_path,
        "source-protocol",
        "stateforge-org/stateforge-protocol",
    )
    second_path = git_clone(
        tmp_path,
        "second-protocol",
        "stateforge-org/stateforge-protocol",
    )
    app = create_app(settings(tmp_path, "machine"))

    with TestClient(app) as client:
        source_project = client.post(
            "/api/projects",
            json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
        ).json()
        source_repository = client.post(
            f"/api/projects/{source_project['project_id']}/repositories",
            json={
                "alias": "protocol",
                "github_full_name": "stateforge-org/stateforge-protocol",
            },
        ).json()
        client.post(
            f"/api/repositories/{source_repository['repository_id']}/attach",
            json={"local_path": str(source_path)},
        )
        create_step(
            client,
            source_project["project_id"],
            source_repository["repository_id"],
            "P0",
        )
        client.post(
            f"/api/projects/{source_project['project_id']}/plan/generate",
            json={"force": False},
        )

        empty_project = client.post(
            "/api/projects",
            json={"name": "Temporary", "slug": "temporary", "release": "Draft"},
        ).json()
        empty_repository = client.post(
            f"/api/projects/{empty_project['project_id']}/repositories",
            json={
                "alias": "source",
                "github_full_name": "stateforge-org/stateforge-protocol",
            },
        ).json()
        client.post(
            f"/api/repositories/{empty_repository['repository_id']}/attach",
            json={"local_path": str(second_path)},
        )

        remote_content = (source_path / "STATEFORGE_DPT.md").read_text(encoding="utf-8")
        monkeypatch.setattr(
            "stateforge_dpt.service.read_remote_file",
            lambda *_args, **_kwargs: remote_content,
        )
        result = app.state.service.refresh_remote_plan_sources(
            empty_project["project_id"],
            None,
        )

        assert result["status"] == "unavailable"
        assert result["errors"] == [
            {
                "repository": "stateforge-org/stateforge-protocol",
                "message": (
                    "The plan on origin/draft/implementation belongs to "
                    "StateForge (stateforge), not Temporary (temporary). Import "
                    "that plan as an existing project instead."
                ),
            }
        ]
        dashboard = client.get(f"/api/projects/{empty_project['project_id']}").json()
        assert dashboard["project"]["slug"] == "temporary"
        assert dashboard["project"]["name"] == "Temporary"
        assert dashboard["project"]["release"] == "Draft"
        assert dashboard["project"]["revision"] == 0
        assert dashboard["steps"] == []


def test_newer_git_plan_does_not_overwrite_local_draft_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_path = git_clone(
        tmp_path,
        "stateforge-protocol",
        "stateforge-org/stateforge-protocol",
    )
    machine_a = settings(tmp_path, "machine-a")
    machine_b = settings(tmp_path, "machine-b")

    with TestClient(create_app(machine_a)) as client:
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
        client.post(
            f"/api/repositories/{repository['repository_id']}/attach",
            json={"local_path": str(protocol_path)},
        )
        create_step(client, project["project_id"], repository["repository_id"], "P0")
        client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )

    with TestClient(create_app(machine_b)) as client:
        dashboard = client.post(
            "/api/projects/import",
            json={"local_path": str(protocol_path)},
        ).json()
        imported_project_id = dashboard["project"]["project_id"]
        imported_repository_id = dashboard["repositories"][0]["repository_id"]
        step = dashboard["steps"][0]
        update = client.put(
            f"/api/projects/{imported_project_id}/steps/P0",
            json={
                "repository_id": imported_repository_id,
                "title": "[TASK][MVP] Local draft title",
                "required_result": step["required_result"],
                "branch_name": step["branch_name"],
                "depends_on": [],
            },
        )
        assert update.status_code == 200

    with TestClient(create_app(machine_a)) as client:
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        create_step(
            client,
            project["project_id"],
            dashboard["repositories"][0]["repository_id"],
            "P1",
            ["P0"],
        )
        client.post(
            f"/api/projects/{project['project_id']}/plan/generate",
            json={"force": False},
        )

    remote_content = (protocol_path / "STATEFORGE_DPT.md").read_text(encoding="utf-8")
    monkeypatch.setattr(
        "stateforge_dpt.service.read_remote_file",
        lambda *_args, **_kwargs: remote_content,
    )
    machine_b_app = create_app(machine_b)
    with TestClient(machine_b_app) as client:
        refresh = machine_b_app.state.service.refresh_remote_plan_sources(
            imported_project_id,
            None,
        )
        assert refresh["status"] == "conflict"
        dashboard = client.get(f"/api/projects/{imported_project_id}").json()
        assert dashboard["steps"][0]["title"] == "[TASK][MVP] Local draft title"
        sources = client.get(f"/api/projects/{imported_project_id}/plan/sources").json()
        assert sources["has_local_changes"] is True
        assert sources["conflict"] is True
        assert sources["sources"][0]["source_kind"] == "remote"
        sync = client.post(
            f"/api/projects/{imported_project_id}/plan/import",
            json={"repository_id": imported_repository_id},
        )
        assert sync.status_code == 409
        assert "unpublished plan edits" in sync.json()["detail"]
