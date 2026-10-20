import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.git import GitError
from stateforge_dpt.github import GitHubAppPlanPublisher


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


def publisher(tmp_path: Path) -> tuple[GitHubAppPlanPublisher, Path]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "stateforge-dpt.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    clone = tmp_path / "repository"
    clone.mkdir()
    git(clone, "init", "-b", "feature/current-work")
    git(
        clone,
        "remote",
        "add",
        "origin",
        "https://github.com/stateforge-org/stateforge-agent.git",
    )
    settings = Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        github_app_id=12345,
        github_app_private_key_path=key_path,
    )
    return GitHubAppPlanPublisher(settings), clone


def decode_jwt_part(value: str) -> dict[str, object]:
    value += "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value))


def test_app_publication_uses_scoped_installation_token_and_verified_bot_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_publisher, clone = publisher(tmp_path)
    calls: list[dict[str, Any]] = []
    base_commit = "a" * 40
    target_commit = "b" * 40
    expected_credential = "installation-token"

    def request(
        _client: httpx.Client,
        token: str,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(
            {
                "token": token,
                "method": method,
                "path": path,
                "json": kwargs.get("json"),
            }
        )
        if path.endswith("/installation"):
            return {"id": 6789}
        if path.endswith("/access_tokens"):
            return {"token": "installation-token"}
        if "/git/ref/heads/" in path:
            return {"object": {"sha": base_commit}}
        if path.endswith(f"/git/commits/{base_commit}"):
            return {
                "tree": {"sha": "c" * 40},
                "verification": {"verified": True},
            }
        if path.endswith("/git/blobs"):
            return {"sha": "d" * 40}
        if path.endswith("/git/trees"):
            return {"sha": "e" * 40}
        if path.endswith("/git/commits"):
            return {"sha": target_commit}
        if path.endswith(f"/git/commits/{target_commit}"):
            return {"verification": {"verified": True, "reason": "valid"}}
        if "/git/refs/heads/" in path:
            return {"object": {"sha": target_commit}}
        raise AssertionError(path)

    monkeypatch.setattr(app_publisher, "_request", request)
    monkeypatch.setattr(
        "stateforge_dpt.github.fetch_remote_branch",
        lambda _path, _branch, credential: (
            target_commit if credential == expected_credential else ""
        ),
    )

    result = app_publisher.publish(
        str(clone),
        "stateforge-org/stateforge-agent",
        "mvp/implementation",
        "STATEFORGE_DPT.md",
        "# Published plan\n",
        "chore(dpt): publish plan revision 27",
    )

    app_jwt = str(calls[0]["token"])
    assert len(app_jwt.split(".")) == 3
    assert decode_jwt_part(app_jwt.split(".")[1])["iss"] == "12345"
    assert calls[1]["json"] == {
        "repositories": ["stateforge-agent"],
        "permissions": {"contents": "write"},
    }
    assert all(call["token"] == expected_credential for call in calls[2:])
    commit_request = next(
        call
        for call in calls
        if call["method"] == "POST" and call["path"].endswith("/git/commits")
    )
    assert commit_request["json"] == {
        "message": "chore(dpt): publish plan revision 27",
        "tree": "e" * 40,
        "parents": [base_commit],
    }
    assert result["publisher"] == "stateforge-dpt-app"
    assert result["target_commit"] == target_commit
    assert result["signature_verified"] is True


def test_app_publication_refuses_unverified_commit_before_branch_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_publisher, clone = publisher(tmp_path)
    paths: list[str] = []
    base_commit = "a" * 40
    target_commit = "b" * 40

    def request(
        _client: httpx.Client,
        _token: str,
        _method: str,
        path: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        paths.append(path)
        if path.endswith("/installation"):
            return {"id": 6789}
        if path.endswith("/access_tokens"):
            return {"token": "installation-token"}
        if "/git/ref/heads/" in path:
            return {"object": {"sha": base_commit}}
        if path.endswith(f"/git/commits/{base_commit}"):
            return {"tree": {"sha": "c" * 40}}
        if path.endswith("/git/blobs"):
            return {"sha": "d" * 40}
        if path.endswith("/git/trees"):
            return {"sha": "e" * 40}
        if path.endswith("/git/commits"):
            return {"sha": target_commit}
        if path.endswith(f"/git/commits/{target_commit}"):
            return {"verification": {"verified": False, "reason": "unsigned"}}
        raise AssertionError(path)

    monkeypatch.setattr(app_publisher, "_request", request)

    with pytest.raises(GitError, match=r"did not verify.*unsigned"):
        app_publisher.publish(
            str(clone),
            "stateforge-org/stateforge-agent",
            "mvp/implementation",
            "STATEFORGE_DPT.md",
            "# Published plan\n",
            "chore(dpt): publish plan revision 27",
        )

    assert not any("/git/refs/heads/" in path for path in paths)


def test_app_publication_requires_server_side_credentials(tmp_path: Path) -> None:
    clone = tmp_path / "repository"
    clone.mkdir()
    git(clone, "init", "-b", "feature/current-work")
    git(
        clone,
        "remote",
        "add",
        "origin",
        "https://github.com/stateforge-org/stateforge-agent.git",
    )
    app_publisher = GitHubAppPlanPublisher(
        Settings(
            host="127.0.0.1",
            port=8463,
            data_dir=tmp_path / "data",
            frontend_dir=tmp_path / "frontend",
            github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        )
    )

    with pytest.raises(GitError, match="STATEFORGE_DPT_GITHUB_APP_ID"):
        app_publisher.publish(
            str(clone),
            "stateforge-org/stateforge-agent",
            "mvp/implementation",
            "STATEFORGE_DPT.md",
            "# Published plan\n",
            "chore(dpt): publish plan revision 27",
        )


def test_local_tracking_failure_does_not_reclassify_remote_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_fetch(
        _local_path: str,
        _target_branch: str,
        _installation_token: str,
    ) -> str:
        raise GitError("local fetch failed")

    monkeypatch.setattr("stateforge_dpt.github.fetch_remote_branch", fail_fetch)

    result = GitHubAppPlanPublisher._sync_local_tracking_branch(
        "/managed/repository",
        "mvp/implementation",
        "installation-token",
        "a" * 40,
    )

    assert result == {
        "local_tracking_updated": False,
        "local_tracking_error": "local fetch failed",
    }


def test_app_request_reports_transport_failures_as_publication_errors(
    tmp_path: Path,
) -> None:
    app_publisher, _clone = publisher(tmp_path)

    def fail_request(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(fail_request)) as client,
        pytest.raises(GitError, match="GitHub request failed: connection refused"),
    ):
        app_publisher._request(
            client,
            "app-jwt",
            "GET",
            "/app",
        )
