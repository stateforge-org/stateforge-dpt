import base64
import shutil
import subprocess

import pytest

from stateforge_dpt.git import GitError, _git_environment


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
