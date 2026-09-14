"""Safe local Git operations used by DPT."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class GitError(RuntimeError):
    """Raised when a controlled Git operation fails."""


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
        raise GitError("Git executable was not found")
    completed = subprocess.run(  # noqa: S603 - fixed executable, no shell
        [git, "-C", str(path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise GitError(message or "Git command failed")
    return completed.stdout.strip()


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

    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if access_token:
        temporary_directory = tempfile.TemporaryDirectory(prefix="stateforge-dpt-")
        askpass = Path(temporary_directory.name) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *) printf "%s\\n" "$STATEFORGE_DPT_FETCH_TOKEN" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        environment["GIT_ASKPASS"] = str(askpass)
        environment["STATEFORGE_DPT_FETCH_TOKEN"] = access_token

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
        environment.pop("STATEFORGE_DPT_FETCH_TOKEN", None)
        if temporary_directory:
            temporary_directory.cleanup()


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

    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if access_token:
        temporary_directory = tempfile.TemporaryDirectory(prefix="stateforge-dpt-")
        askpass = Path(temporary_directory.name) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *) printf "%s\\n" "$STATEFORGE_DPT_CLONE_TOKEN" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        environment["GIT_ASKPASS"] = str(askpass)
        environment["STATEFORGE_DPT_CLONE_TOKEN"] = access_token

    try:
        git = shutil.which("git")
        if git is None:
            raise GitError("Git executable was not found")
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
            timeout=600,
            env=environment,
        )
    finally:
        environment.pop("STATEFORGE_DPT_CLONE_TOKEN", None)
        if temporary_directory:
            temporary_directory.cleanup()

    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise GitError(message or "Repository clone failed")
    return inspect_clone(str(destination), full_name)
