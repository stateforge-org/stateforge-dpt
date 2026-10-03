import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from stateforge_dpt.git import GitError, _git_environment, publish_plan_branches


def git(path: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result = subprocess.run(  # noqa: S603 - fixed executable and test values
        [executable, "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_git_auth_is_scoped_to_github_without_shell_scripts(monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    environment = _git_environment("test-token")
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_COUNT"] == "4"
    assert environment["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert environment["GIT_CONFIG_VALUE_0"] == ""
    header = environment["GIT_CONFIG_VALUE_1"]
    assert (
        base64.b64decode(header.removeprefix("Authorization: Basic "))
        == b"x-access-token:test-token"
    )
    assert "STATEFORGE_DPT_CLONE_TOKEN" not in environment
    assert "STATEFORGE_DPT_FETCH_TOKEN" not in environment
    assert "test-token" not in str(environment)


def test_git_auth_preserves_existing_per_process_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    environment = _git_environment("test-token")
    assert environment["GIT_CONFIG_COUNT"] == "5"
    assert environment["GIT_CONFIG_KEY_0"] == "core.autocrlf"
    assert environment["GIT_CONFIG_VALUE_0"] == "false"
    assert environment["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraHeader"


@pytest.mark.parametrize("count", ["bad-value", "-1"])
def test_invalid_git_config_count_is_reported(monkeypatch, count):
    monkeypatch.setenv("GIT_CONFIG_COUNT", count)
    with pytest.raises(GitError, match="non-negative integer"):
        _git_environment("test-token")


def test_git_honors_auth_scope_and_does_not_persist_it(tmp_path, monkeypatch):
    git = shutil.which("git")
    assert git is not None
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    environment = _git_environment("test-token")
    for url, expected in [
        ("https://github.com/example/repository.git", True),
        ("https://example.com/repository.git", False),
        ("http://github.com/example/repository.git", False),
    ]:
        result = subprocess.run(  # noqa: S603 - fixed executable and test values
            [git, "config", "--get-urlmatch", "http.extraHeader", url],
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert ("Authorization: Basic" in result.stdout) is expected
    assert not (tmp_path / "askpass.sh").exists()


def test_plan_branch_publication_preserves_checkout_and_prefers_update_branch(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    managed = tmp_path / "managed"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "mvp/implementation", str(seed))
    git(seed, "config", "user.name", "DPT Test")
    git(seed, "config", "user.email", "dpt-test@example.invalid")
    (seed / "STATEFORGE_DPT.md").write_text("base plan\n", encoding="utf-8")
    (seed / "code.py").write_text("stable\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Initial state")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "mvp/implementation")

    git(seed, "switch", "-c", "mvp/update-dpt-plan")
    (seed / "STATEFORGE_DPT.md").write_text("update branch plan\n", encoding="utf-8")
    git(seed, "commit", "-am", "Previous plan update")
    git(seed, "push", "-u", "origin", "mvp/update-dpt-plan")
    git(seed, "switch", "mvp/implementation")
    (seed / "STATEFORGE_DPT.md").write_text(
        "implementation branch plan\n", encoding="utf-8"
    )
    git(seed, "commit", "-am", "Concurrent canonical update")
    git(seed, "push", "origin", "mvp/implementation")

    git(
        tmp_path,
        "clone",
        "--branch",
        "mvp/implementation",
        str(remote),
        str(managed),
    )
    git(managed, "switch", "-c", "feature/current-work")
    (managed / "unfinished.py").write_text("do not touch\n", encoding="utf-8")
    branch_before = git(managed, "branch", "--show-current")
    status_before = git(managed, "status", "--porcelain")

    result = publish_plan_branches(
        str(managed),
        "STATEFORGE_DPT.md",
        "canonical revision\n",
        "mvp/update-dpt-plan",
        "mvp/implementation",
        "chore(dpt): publish plan revision 2",
        "DPT Test",
        "dpt-test@example.invalid",
        None,
    )

    assert result["update_branch_created"] is False
    assert git(managed, "branch", "--show-current") == branch_before
    assert git(managed, "status", "--porcelain") == status_before
    assert (managed / "STATEFORGE_DPT.md").read_text(encoding="utf-8") == (
        "implementation branch plan\n"
    )
    for branch in ("mvp/update-dpt-plan", "mvp/implementation"):
        assert (
            git(managed, "show", f"origin/{branch}:STATEFORGE_DPT.md")
            == "canonical revision"
        )
