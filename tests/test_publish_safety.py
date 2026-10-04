import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.git import is_committed_file
from stateforge_dpt.plan_file import (
    MANIFEST_END,
    MANIFEST_START,
    load_plan,
    render_manifest,
)


def git(path: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result = subprocess.run(  # noqa: S603 - fixed executable, no shell
        [executable, "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def commit_plan(path: Path) -> None:
    git(path, "add", "--", "STATEFORGE_DPT.md")
    git(
        path,
        "-c",
        "user.name=DPT Test",
        "-c",
        "user.email=dpt-test@example.invalid",
        "commit",
        "-m",
        "Publish DPT snapshot",
    )


@dataclass
class Workspace:
    app: FastAPI
    client: TestClient
    clone: Path
    project_id: str
    repository_id: str

    @property
    def plan_path(self) -> Path:
        return self.clone / "STATEFORGE_DPT.md"

    def publish(self, *, force: bool = False) -> httpx.Response:
        return self.client.post(
            f"/api/projects/{self.project_id}/plan/generate",
            json={"force": force},
        )

    def add_step(self, step_id: str) -> None:
        response = self.client.post(
            f"/api/projects/{self.project_id}/steps",
            json={
                "step_id": step_id,
                "repository_id": self.repository_id,
                "title": f"[TASK][MVP] Build {step_id}",
                "required_result": f"The result for {step_id} exists.",
                "branch_name": f"mvp/build-{step_id.lower()}",
                "depends_on": [] if step_id == "P0" else ["P0"],
            },
        )
        assert response.status_code == 201

    def edit_title(self, title: str) -> None:
        response = self.client.put(
            f"/api/projects/{self.project_id}/steps/P0",
            json={
                "repository_id": self.repository_id,
                "title": title,
                "required_result": "The result for P0 exists.",
                "branch_name": "mvp/build-p0",
                "depends_on": [],
            },
        )
        assert response.status_code == 200

    def checkout_older_snapshot(self) -> None:
        git(self.clone, "branch", "feature/older-plan")
        self.add_step("P1")
        assert self.publish().json()["revision"] == 2
        commit_plan(self.clone)
        git(self.clone, "checkout", "feature/older-plan")


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    clone = tmp_path / "protocol"
    clone.mkdir()
    git(clone, "init", "-b", "mvp/implementation")
    git(clone, "config", "core.autocrlf", "false")
    git(
        clone,
        "remote",
        "add",
        "origin",
        "https://github.com/stateforge-org/stateforge-protocol.git",
    )
    app = create_app(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path / "data",
            frontend_dir=tmp_path / "frontend",
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
            working_directory=tmp_path,
        )
    )
    with TestClient(app) as client:
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
        attached = client.post(
            f"/api/repositories/{repository['repository_id']}/attach",
            json={"local_path": str(clone)},
        )
        assert attached.status_code == 200
        result = Workspace(
            app, client, clone, project["project_id"], repository["repository_id"]
        )
        result.add_step("P0")
        assert result.publish().json()["revision"] == 1
        commit_plan(clone)
        yield result


def rewrite_manifest(workspace: Workspace, *, revision: int, title: str) -> str:
    old_content = workspace.plan_path.read_text(encoding="utf-8")
    plan = load_plan(str(workspace.plan_path)).plan
    plan = plan.model_copy(
        update={
            "project": plan.project.model_copy(update={"revision": revision}),
            "steps": [plan.steps[0].model_copy(update={"title": title})],
        }
    )
    return render_manifest(plan) + old_content.split(MANIFEST_END, 1)[1]


def assert_blocked(workspace: Workspace, message: str) -> None:
    before = workspace.plan_path.read_bytes()
    project = workspace.app.state.service.project(workspace.project_id)
    repositories = workspace.app.state.service.repositories(workspace.project_id)
    response = workspace.publish()
    assert response.status_code == 409
    assert message in response.json()["detail"]
    assert workspace.plan_path.read_bytes() == before
    assert workspace.app.state.service.project(workspace.project_id) == project
    assert (
        workspace.app.state.service.repositories(workspace.project_id) == repositories
    )


def test_publish_advances_an_older_feature_branch_without_losing_local_edits(
    workspace: Workspace,
) -> None:
    workspace.checkout_older_snapshot()
    workspace.edit_title("[TASK][MVP] Local draft result")
    (workspace.clone / "code.py").write_text("unfinished code\n", encoding="utf-8")
    branch = git(workspace.clone, "rev-parse", "HEAD")
    index_blob = git(workspace.clone, "rev-parse", ":STATEFORGE_DPT.md")

    published = workspace.publish()

    assert published.status_code == 200
    assert published.json()["revision"] == 3
    plan = load_plan(str(workspace.plan_path)).plan
    assert plan.project.revision == 3
    assert plan.steps[0].title == "[TASK][MVP] Local draft result"
    assert [step.step_id for step in plan.steps] == ["P0", "P1"]
    assert git(workspace.clone, "rev-parse", "HEAD") == branch
    assert git(workspace.clone, "rev-parse", ":STATEFORGE_DPT.md") == index_blob
    assert (workspace.clone / "code.py").read_text(
        encoding="utf-8"
    ) == "unfinished code\n"


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_publish_accepts_a_committed_legacy_snapshot_after_a_branch_switch(
    workspace: Workspace, line_ending: str
) -> None:
    workspace.checkout_older_snapshot()
    content = workspace.plan_path.read_text(encoding="utf-8")
    raw_envelope = content.split(MANIFEST_START, 1)[1].split(MANIFEST_END, 1)[0]
    envelope = json.loads(raw_envelope)
    del envelope["plan"]["project"]["plan_source_branch"]
    canonical = json.dumps(
        envelope["plan"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    envelope["fingerprint"] = hashlib.sha256(canonical.encode()).hexdigest()
    content = (
        f"{MANIFEST_START}\n{json.dumps(envelope, indent=2, sort_keys=True)}\n"
        f"{MANIFEST_END}{content.split(MANIFEST_END, 1)[1]}"
    )
    workspace.plan_path.write_bytes(content.replace("\n", line_ending).encode())
    commit_plan(workspace.clone)

    published = workspace.publish()

    assert published.status_code == 200
    assert published.json()["revision"] == 3
    assert load_plan(str(workspace.plan_path)).plan.project.plan_source_branch == (
        "mvp/implementation"
    )


def test_publish_after_remote_import_accepts_the_updated_git_file(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_digest = workspace.app.state.service.repository(workspace.repository_id)[
        "last_generated_digest"
    ]
    peer = create_app(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path / "peer-data",
            frontend_dir=tmp_path / "frontend",
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
            working_directory=tmp_path,
        )
    )
    with TestClient(peer) as client:
        imported = client.post(
            "/api/projects/import", json={"local_path": str(workspace.clone)}
        ).json()
        publisher = Workspace(
            peer,
            client,
            workspace.clone,
            imported["project"]["project_id"],
            imported["repositories"][0]["repository_id"],
        )
        publisher.add_step("P1")
        assert publisher.publish().json()["revision"] == 2
    commit_plan(workspace.clone)
    remote_content = workspace.plan_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "stateforge_dpt.service.read_remote_file",
        lambda *_args, **_kwargs: remote_content,
    )
    refreshed = workspace.app.state.service.refresh_remote_plan_sources(
        workspace.project_id, None
    )
    assert refreshed["status"] == "imported"
    assert refreshed["revision"] == 2
    assert (
        workspace.app.state.service.repository(workspace.repository_id)[
            "last_generated_digest"
        ]
        == original_digest
    )
    workspace.edit_title("[TASK][MVP] Draft after shared update")

    published = workspace.publish()

    assert published.status_code == 200
    assert published.json()["revision"] == 3
    plan = load_plan(str(workspace.plan_path)).plan
    assert plan.steps[0].title == "[TASK][MVP] Draft after shared update"
    assert [step.step_id for step in plan.steps] == ["P0", "P1"]


@pytest.mark.parametrize("edit", ["unstaged", "staged", "staged_then_restored"])
def test_publish_protects_manual_edits_to_an_older_snapshot(
    workspace: Workspace, edit: str
) -> None:
    workspace.checkout_older_snapshot()
    original = workspace.plan_path.read_bytes()
    workspace.plan_path.write_bytes(
        original + b"\nHuman notes that must not disappear.\n"
    )
    if edit != "unstaged":
        git(workspace.clone, "add", "--", "STATEFORGE_DPT.md")
    if edit == "staged_then_restored":
        workspace.plan_path.write_bytes(original)

    assert_blocked(workspace, "uncommitted or staged edits")


@pytest.mark.parametrize("damage", ["checksum", "no_manifest", "invalid_utf8"])
def test_publish_protects_invalid_committed_files(
    workspace: Workspace, damage: str
) -> None:
    workspace.checkout_older_snapshot()
    if damage == "checksum":
        content = workspace.plan_path.read_text(encoding="utf-8")
        workspace.plan_path.write_text(
            content.replace("mvp/build-p0", "mvp/tampered-p0", 1), encoding="utf-8"
        )
    elif damage == "no_manifest":
        workspace.plan_path.write_text(
            "# My manually maintained plan\n", encoding="utf-8"
        )
    else:
        workspace.plan_path.write_bytes(b"\xff\xfe invalid UTF-8")
    commit_plan(workspace.clone)

    assert_blocked(workspace, "cannot be validated")


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_git_index_flags_cannot_hide_manual_plan_edits(
    workspace: Workspace, flag: str
) -> None:
    workspace.checkout_older_snapshot()
    git(workspace.clone, "update-index", flag, "--", "STATEFORGE_DPT.md")
    workspace.plan_path.write_bytes(
        workspace.plan_path.read_bytes() + b"\nHidden edits must be preserved.\n"
    )
    assert not git(
        workspace.clone, "diff", "--name-only", "HEAD", "--", "STATEFORGE_DPT.md"
    )

    assert_blocked(workspace, "uncommitted or staged edits")


def test_publish_does_not_treat_an_untracked_plan_as_a_branch_snapshot(
    workspace: Workspace,
) -> None:
    workspace.checkout_older_snapshot()
    git(workspace.clone, "rm", "--cached", "--", "STATEFORGE_DPT.md")

    assert_blocked(workspace, "uncommitted or staged edits")


@pytest.mark.parametrize("kind", ["newer", "same_revision_conflict"])
def test_publish_protects_newer_or_divergent_committed_plans(
    workspace: Workspace, kind: str
) -> None:
    content = rewrite_manifest(
        workspace,
        revision=4 if kind == "newer" else 1,
        title="[TASK][MVP] Another contributor's plan",
    )
    workspace.plan_path.write_text(content, encoding="utf-8")
    commit_plan(workspace.clone)

    assert_blocked(
        workspace,
        "newer Git plan" if kind == "newer" else "different plans at revision 1",
    )


@pytest.mark.parametrize("identity", ["project", "release", "filename", "repository"])
def test_publish_protects_committed_foreign_manifests(
    workspace: Workspace, identity: str
) -> None:
    plan = load_plan(str(workspace.plan_path)).plan
    if identity == "repository":
        plan = plan.model_copy(
            update={
                "repositories": [
                    plan.repositories[0].model_copy(
                        update={"github_full_name": "other-org/other-protocol"}
                    )
                ]
            }
        )
    else:
        key, value = {
            "project": ("slug", "other-project"),
            "release": ("release", "Other release"),
            "filename": ("generated_filename", "OTHER_DPT.md"),
        }[identity]
        plan = plan.model_copy(
            update={"project": plan.project.model_copy(update={key: value})}
        )
    workspace.plan_path.write_text(render_manifest(plan), encoding="utf-8")
    commit_plan(workspace.clone)

    assert_blocked(
        workspace,
        "does not include"
        if identity == "repository"
        else "different project or release",
    )


@pytest.mark.parametrize("revision", [1, 4], ids=["same-revision-conflict", "newer"])
def test_publish_protects_remote_conflicts_even_when_the_branch_file_is_unchanged(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, revision: int
) -> None:
    remote_content = rewrite_manifest(
        workspace,
        revision=revision,
        title="[TASK][MVP] A conflicting shared plan",
    )
    workspace.edit_title("[TASK][MVP] My local draft")
    monkeypatch.setattr(
        "stateforge_dpt.service.read_remote_file",
        lambda *_args, **_kwargs: remote_content,
    )
    refreshed = workspace.app.state.service.refresh_remote_plan_sources(
        workspace.project_id, None
    )
    assert refreshed["status"] == "conflict"
    assert is_committed_file(str(workspace.clone), "STATEFORGE_DPT.md")

    assert_blocked(
        workspace,
        "newer Git plan" if revision > 1 else "different plans at revision 1",
    )


def test_failed_publish_does_not_write_to_other_repositories(
    workspace: Workspace, tmp_path: Path
) -> None:
    second = tmp_path / "provider"
    second.mkdir()
    git(second, "init", "-b", "mvp/implementation")
    git(second, "config", "core.autocrlf", "false")
    git(
        second,
        "remote",
        "add",
        "origin",
        "https://github.com/stateforge-org/stateforge-provider.git",
    )
    repository = workspace.client.post(
        f"/api/projects/{workspace.project_id}/repositories",
        json={
            "alias": "provider",
            "github_full_name": "stateforge-org/stateforge-provider",
        },
    ).json()
    response = workspace.client.post(
        f"/api/repositories/{repository['repository_id']}/attach",
        json={"local_path": str(second)},
    )
    assert response.status_code == 200
    assert workspace.publish().json()["revision"] == 2
    commit_plan(workspace.clone)
    commit_plan(second)
    first_bytes = workspace.plan_path.read_bytes()
    second_file = second / "STATEFORGE_DPT.md"
    plan = load_plan(str(second_file)).plan
    plan = plan.model_copy(
        update={"project": plan.project.model_copy(update={"revision": 4})}
    )
    second_file.write_text(render_manifest(plan), encoding="utf-8")
    commit_plan(second)

    assert_blocked(workspace, "newer Git plan")
    assert workspace.plan_path.read_bytes() == first_bytes
    assert load_plan(str(second_file)).plan.project.revision == 4


def test_explicit_force_still_requires_an_opt_in(
    workspace: Workspace,
) -> None:
    workspace.plan_path.write_text(
        "# Deliberately replace this file\n", encoding="utf-8"
    )
    assert_blocked(workspace, "cannot be validated")

    response = workspace.publish(force=True)

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert load_plan(str(workspace.plan_path)).plan.project.revision == 2


def test_git_publication_records_remote_revision_without_touching_active_checkout(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.edit_title("[TASK][MVP] Publish through Git")
    before = workspace.plan_path.read_bytes()
    previous_digest = workspace.app.state.service.repository(workspace.repository_id)[
        "last_generated_digest"
    ]
    calls: list[dict[str, object]] = []

    def publish_stub(
        path_value: str,
        filename: str,
        content: str,
        update_branch: str,
        target_branch: str,
        commit_message: str,
        author_name: str,
        author_email: str,
        access_token: str | None,
    ) -> dict[str, str | bool]:
        calls.append(
            {
                "path": path_value,
                "filename": filename,
                "content": content,
                "update_branch": update_branch,
                "target_branch": target_branch,
                "commit_message": commit_message,
                "author_name": author_name,
                "author_email": author_email,
                "access_token": access_token,
            }
        )
        return {
            "update_branch": update_branch,
            "target_branch": target_branch,
            "update_branch_created": True,
            "update_commit": "a" * 40,
            "target_commit": "b" * 40,
        }

    monkeypatch.setattr("stateforge_dpt.service.publish_plan_branches", publish_stub)
    result = workspace.app.state.service.publish_project_plan_to_git(
        workspace.project_id,
        "token",
        123,
        "maintainer",
        "Maintainer",
    )

    assert result["revision"] == 2
    assert result["update_branch"] == "mvp/update-dpt-plan"
    assert result["target_branch"] == "mvp/implementation"
    assert len(calls) == 1
    assert "Publish through Git" in str(calls[0]["content"])
    assert calls[0]["author_email"] == "123+maintainer@users.noreply.github.com"
    assert workspace.plan_path.read_bytes() == before
    assert (
        workspace.app.state.service.repository(workspace.repository_id)[
            "last_generated_digest"
        ]
        == previous_digest
    )
    status = workspace.app.state.service.project_plan_sources(workspace.project_id)
    assert status["sources"][0]["source_kind"] == "remote"
    assert status["sources"][0]["revision"] == 2
    assert status["sources"][0]["status"] == "current"


def test_git_publication_preserves_uncommitted_dpt_generated_snapshot(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.edit_title("[TASK][MVP] Generated local draft")
    generated = workspace.publish()
    assert generated.status_code == 200
    assert generated.json()["revision"] == 2
    local_content = workspace.plan_path.read_bytes()
    local_digest = workspace.app.state.service.repository(workspace.repository_id)[
        "last_generated_digest"
    ]

    def publish_stub(
        _path_value: str,
        _filename: str,
        _content: str,
        update_branch: str,
        target_branch: str,
        _commit_message: str,
        _author_name: str,
        _author_email: str,
        _access_token: str | None,
    ) -> dict[str, str | bool]:
        return {
            "update_branch": update_branch,
            "target_branch": target_branch,
            "update_branch_created": True,
            "update_commit": "a" * 40,
            "target_commit": "b" * 40,
        }

    monkeypatch.setattr("stateforge_dpt.service.publish_plan_branches", publish_stub)
    workspace.edit_title("[TASK][MVP] Published Git draft")
    published = workspace.app.state.service.publish_project_plan_to_git(
        workspace.project_id,
        "token",
        123,
        "maintainer",
        "Maintainer",
    )

    assert published["revision"] == 3
    assert workspace.plan_path.read_bytes() == local_content
    assert (
        workspace.app.state.service.repository(workspace.repository_id)[
            "last_generated_digest"
        ]
        == local_digest
    )

    workspace.edit_title("[TASK][MVP] Next local draft")
    next_publish = workspace.publish()

    assert next_publish.status_code == 200
    assert next_publish.json()["revision"] == 4
    assert load_plan(str(workspace.plan_path)).plan.steps[0].title == (
        "[TASK][MVP] Next local draft"
    )
