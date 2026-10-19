import io
import json
import logging
import os
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stateforge_dpt import cli, config
from stateforge_dpt.app import create_app
from stateforge_dpt.codex_integration import (
    CodexIntegrationError,
    install_codex_integration,
)
from stateforge_dpt.terminal import print_startup_banner, startup_banner


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for key in os.environ:
        if key.startswith("STATEFORGE_DPT_"):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    package = tmp_path / "installed" / "stateforge_dpt"
    frontend = package / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html>DPT frontend</html>", encoding="utf-8")
    (frontend / "assets" / "app.js").write_text("// bundled asset", encoding="utf-8")
    monkeypatch.setattr(config, "PACKAGE_DIRECTORY", package)
    launch_directory = tmp_path / "unrelated-directory"
    launch_directory.mkdir()
    monkeypatch.chdir(launch_directory)
    return home, package, launch_directory


def test_defaults_are_independent_of_launch_directory(runtime, monkeypatch):
    home, package, launch_directory = runtime
    (launch_directory / ".env").write_text(
        "STATEFORGE_DPT_PORT=9999\nSTATEFORGE_DPT_DATA_DIR=wrong-data\n",
        encoding="utf-8",
    )
    first = config.load_settings()
    monkeypatch.chdir(home)
    second = config.load_settings()

    assert first == second
    assert first.port == 8463
    assert first.data_dir == home / ".stateforge-dpt"
    assert first.working_directory == first.data_dir
    assert first.frontend_dir == package / "frontend"
    assert not (launch_directory / "wrong-data").exists()


def test_user_config_paths_are_relative_to_config_file(runtime):
    home, _, _ = runtime
    config_directory = home / ".stateforge-dpt"
    config_directory.mkdir()
    (config_directory / ".env").write_text(
        "STATEFORGE_DPT_DATA_DIR=data\n"
        "STATEFORGE_DPT_WORKING_DIRECTORY=clones\n"
        "STATEFORGE_DPT_FRONTEND_DIR=ui\n",
        encoding="utf-8",
    )

    resolved = config.load_settings()

    assert resolved.data_dir == config_directory / "data"
    assert resolved.working_directory == config_directory / "clones"
    assert resolved.frontend_dir == config_directory / "ui"


def test_environment_overrides_user_config_without_using_cwd(runtime, monkeypatch):
    home, _, _ = runtime
    config_directory = home / ".stateforge-dpt"
    config_directory.mkdir()
    (config_directory / ".env").write_text(
        "STATEFORGE_DPT_PORT=9999\nSTATEFORGE_DPT_DATA_DIR=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATEFORGE_DPT_PORT", "7777")
    monkeypatch.setenv("STATEFORGE_DPT_DATA_DIR", "custom-data")

    resolved = config.load_settings()

    assert resolved.port == 7777
    assert resolved.data_dir == config_directory / "custom-data"
    assert resolved.working_directory == resolved.data_dir


def test_editable_install_uses_checkout_assets_and_anchored_config(
    runtime, monkeypatch
):
    home, _, _ = runtime
    checkout = home / "checkout"
    package = checkout / "src" / "stateforge_dpt"
    package.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    frontend = checkout / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("editable frontend", encoding="utf-8")
    (checkout / ".env").write_text(
        "STATEFORGE_DPT_DATA_DIR=.stateforge-dpt\nSTATEFORGE_DPT_PORT=7777\n",
        encoding="utf-8",
    )
    user_config = home / ".stateforge-dpt"
    user_config.mkdir()
    (user_config / ".env").write_text("STATEFORGE_DPT_PORT=8888\n", encoding="utf-8")
    monkeypatch.setattr(config, "PACKAGE_DIRECTORY", package)

    resolved = config.load_settings()

    assert resolved.frontend_dir == frontend
    assert resolved.data_dir == checkout / ".stateforge-dpt"
    assert resolved.port == 8888
    bundled = package / "frontend"
    bundled.mkdir()
    (bundled / "index.html").write_text("bundled frontend", encoding="utf-8")
    assert config.load_settings().frontend_dir == bundled


def test_homepage_and_bundled_assets_work_from_an_unrelated_directory(runtime):
    home, _, launch_directory = runtime
    with TestClient(create_app(config.load_settings())) as client:
        assert client.get("/").text == "<html>DPT frontend</html>"
        assert client.get("/projects/example").text == "<html>DPT frontend</html>"
        assert client.get("/assets/app.js").text == "// bundled asset"
        assert client.get("/api/health").status_code == 200

    assert (home / ".stateforge-dpt" / "dpt.sqlite3").is_file()
    assert not (launch_directory / ".stateforge-dpt").exists()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "127.0.0.1"])  # noqa: S104 - test bind-address formatting without starting a server
def test_cli_prints_login_instructions_and_passes_resolved_settings(
    runtime, monkeypatch, capsys, host
):
    home, _, _ = runtime
    calls = []

    def run_server(*args, **kwargs):
        calls.append((args, kwargs, logging.root.manager.disable))

    monkeypatch.setattr(cli.uvicorn, "run", run_server)
    monkeypatch.setattr(
        sys, "argv", ["stateforge-dpt", "run", "--host", host, "--port", "9000"]
    )

    cli.main()

    output = capsys.readouterr().out
    assert "http://127.0.0.1:9000" in output
    assert "Continue with GitHub" in output
    assert "local mode" in output
    assert str(home / ".stateforge-dpt") in output
    assert "Ctrl+C" in output
    application = calls[0][0][0]
    assert application.state.settings.host == host
    assert application.state.settings.port == 9000
    assert calls[0][1] == {
        "host": host,
        "port": 9000,
        "reload": False,
        "log_level": "error",
        "access_log": False,
    }
    assert calls[0][2] == logging.WARNING


def test_cli_data_directory_override_keeps_existing_workspaces_accessible(
    runtime, monkeypatch
):
    home, _, launch_directory = runtime
    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *args, **kwargs: calls.append(args[0])
    )
    monkeypatch.setattr(
        sys, "argv", ["stateforge-dpt", "run", "--data-dir", "old-data"]
    )

    cli.main()

    resolved = calls[0].state.settings
    assert resolved.data_dir == launch_directory / "old-data"
    assert resolved.working_directory == resolved.data_dir
    assert not (home / ".stateforge-dpt").exists()


def test_cli_can_choose_a_separate_clone_workspace(runtime, monkeypatch):
    home, _, launch_directory = runtime
    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *args, **kwargs: calls.append(args[0])
    )
    monkeypatch.setattr(
        sys, "argv", ["stateforge-dpt", "run", "--working-directory", "workspace"]
    )

    cli.main()

    resolved = calls[0].state.settings
    assert resolved.working_directory == launch_directory / "workspace"
    assert resolved.data_dir == home / ".stateforge-dpt"


def test_cli_missing_frontend_fails_with_build_instructions(
    runtime, monkeypatch, capsys
):
    home, package, _ = runtime
    (package / "frontend" / "index.html").unlink()
    monkeypatch.setattr(sys, "argv", ["stateforge-dpt", "run"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    assert "frontend build is missing" in capsys.readouterr().err
    assert not (home / ".stateforge-dpt").exists()


def test_setup_codex_installs_personal_plugin_and_confirmation_policy(
    runtime, monkeypatch, capsys
):
    home, _, _ = runtime
    monkeypatch.setattr(sys, "argv", ["stateforge-dpt", "setup-codex"])

    cli.main()

    plugin_directory = home / ".codex" / "plugins" / "stateforge-dpt"
    plugin = json.loads((plugin_directory / "plugin.json").read_text())
    mcp = json.loads((plugin_directory / "mcp.json").read_text())
    marketplace = json.loads(
        (home / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    codex_config = tomllib.loads((home / ".codex" / "config.toml").read_text())

    assert plugin["name"] == "stateforge-dpt"
    assert mcp["mcpServers"]["stateforge-dpt"] == {
        "type": "streamable-http",
        "url": "http://127.0.0.1:8463/mcp",
    }
    assert marketplace["plugins"][0]["policy"]["installation"] == (
        "INSTALLED_BY_DEFAULT"
    )
    plugin_config = codex_config["plugins"]["stateforge-dpt@stateforge-personal"]
    server_config = plugin_config["mcp_servers"]["stateforge-dpt"]
    assert server_config["default_tools_approval_mode"] == "approve"
    assert server_config["tools"]["apply_plan_change_proposal"] == {
        "approval_mode": "prompt"
    }
    assert server_config["tools"]["reject_plan_change_proposal"] == {
        "approval_mode": "prompt"
    }
    output = capsys.readouterr().out
    assert "Restart the Codex app" in output
    assert "http://127.0.0.1:8463/mcp" in output


def test_setup_codex_is_idempotent_and_preserves_personal_configuration(runtime):
    home, _, _ = runtime
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_text(
        json.dumps(
            {
                "name": "personal-tools",
                "interface": {"displayName": "My tools"},
                "plugins": [
                    {
                        "name": "existing-tool",
                        "source": "./existing-tool",
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_INSTALL",
                        },
                        "category": "Productivity",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config_path = home / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('model = "gpt-6.1-sol"\n', encoding="utf-8")

    for _ in range(2):
        result = install_codex_integration(
            home=home,
            mcp_url="http://127.0.0.1:9000/mcp",
        )

    marketplace = json.loads(marketplace_path.read_text())
    names = [item["name"] for item in marketplace["plugins"]]
    assert names == ["existing-tool", "stateforge-dpt"]
    assert result.plugin_id == "stateforge-dpt@personal-tools"
    config_text = config_path.read_text()
    assert config_text.startswith('model = "gpt-6.1-sol"')
    assert config_text.count("# >>> stateforge-dpt setup-codex >>>") == 1
    assert tomllib.loads(config_text)["plugins"][result.plugin_id]["enabled"] is True


def test_setup_codex_closes_atomic_temp_files_without_posix_fchmod(
    runtime, monkeypatch
):
    home, _, _ = runtime
    monkeypatch.delattr(os, "fchmod", raising=False)

    install_codex_integration(
        home=home,
        mcp_url="http://127.0.0.1:9000/mcp",
    )

    plugin_directory = home / ".codex" / "plugins" / "stateforge-dpt"
    assert json.loads((plugin_directory / "plugin.json").read_text())["name"] == (
        "stateforge-dpt"
    )
    assert list(plugin_directory.glob(".*.tmp")) == []


def test_setup_codex_rejects_non_loopback_mcp_urls(runtime):
    home, _, _ = runtime

    with pytest.raises(CodexIntegrationError, match="loopback HTTP MCP URL"):
        install_codex_integration(
            home=home,
            mcp_url="https://dpt.example/mcp",
        )


def test_startup_banner_has_color_and_readable_plain_text(runtime):
    resolved = config.load_settings()
    colored = startup_banner(resolved, color=True)
    plain = startup_banner(resolved)

    assert "\033[1;94mStateForge DPT\033[0m" in colored
    assert "\033[1;4;96mhttp://127.0.0.1:8463\033[0m" in colored
    assert "\033[1;95mContinue with GitHub\033[0m" in colored
    assert "\033[" not in plain
    for banner in [colored, plain]:
        assert "WORKSPACE" in banner
        assert str(resolved.data_dir) in banner
        assert str(resolved.working_directory / "projects") in banner
        assert "Ctrl+C" in banner


@pytest.mark.parametrize(
    ("interactive", "no_color", "term", "expected_color"),
    [
        (True, False, "xterm-256color", True),
        (False, False, "xterm-256color", False),
        (True, True, "xterm-256color", False),
        (True, False, "dumb", False),
    ],
)
def test_banner_respects_terminal_and_color_preferences(
    runtime, monkeypatch, capsys, interactive, no_color, term, expected_color
):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: interactive)
    monkeypatch.setenv("TERM", term)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)

    print_startup_banner(config.load_settings())

    assert ("\033[" in capsys.readouterr().out) is expected_color


def test_banner_works_with_legacy_windows_console_encoding(runtime, monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    print_startup_banner(config.load_settings())

    output = buffer.getvalue().decode("cp1252")
    assert "*  StateForge DPT" in output
    assert ">  http://127.0.0.1:8463" in output
    assert "Continue with GitHub" in output
