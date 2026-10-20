"""Safe local Git operations used by DPT."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class GitError(RuntimeError):
    """Raised when a controlled Git operation fails."""


def _git_environment(access_token: str | None) -> dict[str, str]:
    """Supply GitHub HTTPS credentials using cross-platform Git configuration."""
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if not access_token:
        return environment
    try:
        count = int(environment.get("GIT_CONFIG_COUNT") or "0")
    except ValueError as error:
        raise GitError("GIT_CONFIG_COUNT must be a non-negative integer") from error
    if count < 0:
        raise GitError("GIT_CONFIG_COUNT must be a non-negative integer")
    credentials = base64.b64encode(f"x-access-token:{access_token}".encode()).decode(
        "ascii"
    )
    # Reset inherited headers and scope the token to GitHub HTTPS only. Nothing
    # is written to a shell script, clone URL, repository config, or argv.
    entries = [
        ("http.https://github.com/.extraHeader", ""),
        ("http.https://github.com/.extraHeader", f"Authorization: Basic {credentials}"),
        ("http.https://github.com/.followRedirects", "false"),
        ("credential.https://github.com.helper", ""),
    ]
    for offset, (key, value) in enumerate(entries, start=count):
        environment[f"GIT_CONFIG_KEY_{offset}"] = key
        environment[f"GIT_CONFIG_VALUE_{offset}"] = value
    environment["GIT_CONFIG_COUNT"] = str(count + len(entries))
    return environment


@dataclass(frozen=True, slots=True)
class CloneState:
    """Current state of a managed local clone."""

    path: str
    branch: str
    origin: str
    dirty: bool
    ahead: int
    behind: int


def _run(
    path: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
    timeout: int = 60,
) -> str:
    git = shutil.which("git")
    if git is None:
        raise GitError(
            "Git was not found. Install Git 2.31 or newer and add it to PATH."
        )
    completed = subprocess.run(  # noqa: S603 - fixed executable, no shell
        [git, "-C", str(path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=environment,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise GitError(message or "Git command failed")
    return completed.stdout.strip()


def repository_head(path_value: str) -> str:
    """Return the checked-out commit without changing the managed clone."""
    path = Path(path_value).expanduser().resolve()
    return _run(path, "rev-parse", "HEAD")


def tracked_files(path_value: str) -> list[str]:
    """Return repository-relative tracked paths for read-only discovery."""
    path = Path(path_value).expanduser().resolve()
    output = _run(path, "ls-files", "-z")
    return [value for value in output.split("\0") if value]


def read_remote_file(
    path_value: str,
    branch: str,
    filename: str,
    access_token: str | None = None,
) -> str:
    """Fetch one remote branch and read one file without changing the worktree."""
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise GitError(f"Local path does not exist: {path}")
    _run(path, "check-ref-format", "--branch", branch)

    environment = _git_environment(access_token)
    try:
        _run(
            path,
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            branch,
            environment=environment,
            timeout=180,
        )
        return _run(path, "show", f"FETCH_HEAD:{filename}", timeout=60)
    finally:
        environment.clear()


def fetch_remote_branch(
    path_value: str,
    branch: str,
    access_token: str | None = None,
) -> str:
    """Fetch one remote branch and return its commit without changing checkout."""
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise GitError(f"Local path does not exist: {path}")
    _run(path, "check-ref-format", "--branch", branch)

    environment = _git_environment(access_token)
    try:
        _run(
            path,
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            environment=environment,
            timeout=180,
        )
        return _run(path, "rev-parse", f"refs/remotes/origin/{branch}")
    finally:
        environment.clear()


def is_committed_file(path_value: str, filename: str) -> bool:
    """Compare a file with HEAD and the index without writing Git objects."""
    path = Path(path_value).expanduser().resolve()
    target = path / filename
    if target.is_symlink() or not target.is_file():
        return False
    try:
        head_blob = _run(path, "rev-parse", "--verify", f"HEAD:{filename}")
        index_blob = _run(path, "rev-parse", "--verify", f":0:{filename}")
        worktree_blob = _run(
            path, "hash-object", f"--path={filename}", "--", str(target)
        )
    except (GitError, subprocess.TimeoutExpired):
        return False
    # Hashing the actual worktree bytes avoids assume-unchanged/skip-worktree
    # flags hiding edits. --path applies Git's normal clean/EOL conversion.
    return head_blob == index_blob == worktree_blob


def normalized_remote(value: str) -> str:
    """Normalize common GitHub remote formats to `owner/repository`."""
    remote = value.strip().removesuffix(".git")
    if remote.startswith("git@github.com:"):
        return remote.removeprefix("git@github.com:").lower()
    parsed = urlparse(remote)
    if parsed.hostname == "github.com":
        return parsed.path.strip("/").lower()
    return remote.lower()


def clone_identity(path_value: str) -> tuple[Path, str, str]:
    """Validate a clone and return its path, normalized name, and origin URL."""
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise GitError(f"Local path does not exist: {path}")
    if _run(path, "rev-parse", "--is-inside-work-tree") != "true":
        raise GitError(f"Path is not a Git worktree: {path}")
    origin = _run(path, "remote", "get-url", "origin")
    return path, normalized_remote(origin), origin


def inspect_clone(path_value: str, expected_full_name: str) -> CloneState:
    """Validate a local clone and return its current Git state."""
    path, full_name, origin = clone_identity(path_value)
    if full_name != expected_full_name.lower():
        raise GitError(
            f"Origin points to {full_name}, expected {expected_full_name.lower()}"
        )

    branch = _run(path, "branch", "--show-current") or "detached"
    dirty = bool(_run(path, "status", "--porcelain"))
    ahead = 0
    behind = 0
    try:
        counts = _run(path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        ahead, behind = (int(part) for part in counts.split())
    except (GitError, ValueError):
        pass

    return CloneState(
        path=str(path),
        branch=branch,
        origin=origin,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
    )


def clone_repository(
    full_name: str,
    destination_path: str,
    access_token: str | None,
) -> CloneState:
    """Clone a GitHub repository without placing credentials in arguments."""
    destination = Path(destination_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise GitError(f"Clone destination already exists: {destination}")

    environment = _git_environment(access_token)
    try:
        git = shutil.which("git")
        if git is None:
            raise GitError(
                "Git was not found. Install Git 2.31 or newer and add it to PATH."
            )
        completed = subprocess.run(  # noqa: S603 - fixed executable, no shell
            [
                git,
                "clone",
                f"https://github.com/{full_name}.git",
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=environment,
        )
    finally:
        environment.clear()

    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise GitError(message or "Repository clone failed")
    return inspect_clone(str(destination), full_name)
