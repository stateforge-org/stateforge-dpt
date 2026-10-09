"""StateForge DPT command-line interface."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import uvicorn

from stateforge_dpt.codex_integration import (
    CodexIntegrationError,
    install_codex_integration,
)
from stateforge_dpt.config import load_settings
from stateforge_dpt.database import Database
from stateforge_dpt.security import SecretStore
from stateforge_dpt.service import DPTService
from stateforge_dpt.terminal import print_startup_banner


def parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    command_parser = argparse.ArgumentParser(
        prog="stateforge-dpt",
        description="StateForge Development Progress Tracking",
    )
    subcommands = command_parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="run the local DPT application")
    run.add_argument("--host", help="listening host")
    run.add_argument("--port", type=int, help="listening port")
    run.add_argument(
        "--data-dir", type=Path, help="override the per-user DPT data directory"
    )
    run.add_argument(
        "--working-directory", type=Path, help="workspace for repository clones"
    )
    run.add_argument(
        "--no-mcp",
        action="store_true",
        help="disable the local MCP endpoint",
    )
    setup_codex = subcommands.add_parser(
        "setup-codex",
        help="install the local DPT tools in the Codex desktop app",
    )
    setup_codex.add_argument(
        "--url",
        help="override the loopback MCP URL registered with Codex",
    )
    init_db = subcommands.add_parser("init-db", help="initialize the SQLite database")
    init_db.add_argument(
        "--data-dir", type=Path, help="override the DPT data directory"
    )
    bootstrap = subcommands.add_parser(
        "bootstrap-stateforge",
        help="import the existing three StateForge MVP ledgers",
    )
    bootstrap.add_argument(
        "--root",
        type=Path,
        required=True,
        help="directory containing the StateForge repository clones",
    )
    bootstrap.add_argument(
        "--data-dir", type=Path, help="override the DPT data directory"
    )
    return command_parser


def main() -> None:
    """Run the requested DPT command."""
    command_parser = parser()
    arguments = command_parser.parse_args()
    settings = load_settings()
    data_dir_argument = getattr(arguments, "data_dir", None)
    if data_dir_argument is not None:
        data_dir = data_dir_argument.expanduser().resolve()
        settings = replace(
            settings,
            data_dir=data_dir,
            working_directory=(
                data_dir
                if settings.working_directory == settings.data_dir
                else settings.working_directory
            ),
        )
    if arguments.command == "init-db":
        Database(settings.database_path).initialize()
        print(f"Initialized {settings.database_path}")
        return
    if arguments.command == "bootstrap-stateforge":
        from stateforge_dpt.bootstrap import bootstrap_stateforge

        database = Database(settings.database_path)
        database.initialize()
        service = DPTService(database, SecretStore(settings.data_dir))
        project_id = bootstrap_stateforge(service, arguments.root.resolve())
        print(f"Imported StateForge project {project_id}")
        return
    if arguments.command == "setup-codex":
        browser_host = (
            "127.0.0.1"
            if settings.host in {"0.0.0.0", "::", "localhost"}  # noqa: S104 - convert bind addresses into loopback client addresses
            else settings.host
        )
        if ":" in browser_host:
            browser_host = f"[{browser_host}]"
        default_url = f"http://{browser_host}:{settings.port}{settings.mcp_path}"
        try:
            integration = install_codex_integration(
                mcp_url=arguments.url or default_url,
            )
        except CodexIntegrationError as error:
            command_parser.error(str(error))
        print("StateForge DPT is configured for the Codex desktop app.")
        print(f"Plugin:      {integration.plugin_directory}")
        print(f"Marketplace: {integration.marketplace_path}")
        print(f"MCP:         {integration.mcp_url}")
        print()
        print("Restart the Codex app and start a new chat after DPT is running.")
        print("Plan application and proposal rejection require confirmation in Codex.")
        return
    if not (settings.frontend_dir / "index.html").is_file():
        command_parser.error(
            f"The DPT frontend build is missing at {settings.frontend_dir}. "
            "Install a release containing the frontend, or run 'npm ci' and "
            "'npm run build' in the source checkout's frontend directory. "
            "STATEFORGE_DPT_FRONTEND_DIR can point to an existing build."
        )
    settings = replace(
        settings,
        host=arguments.host or settings.host,
        port=arguments.port if arguments.port is not None else settings.port,
        working_directory=(
            arguments.working_directory.expanduser().resolve()
            if arguments.working_directory is not None
            else settings.working_directory
        ),
        mcp_enabled=settings.mcp_enabled and not arguments.no_mcp,
    )
    if (
        settings.mcp_enabled
        and settings.host not in {"127.0.0.1", "localhost", "::1"}
        and not settings.mcp_allow_remote
    ):
        # The web application may be intentionally exposed on a LAN. Do not
        # expose the unauthenticated agent control surface with it by accident.
        settings = replace(settings, mcp_enabled=False)
    from stateforge_dpt.app import create_app

    application = create_app(settings)
    print_startup_banner(settings)
    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
