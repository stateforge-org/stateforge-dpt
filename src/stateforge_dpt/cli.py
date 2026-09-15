"""StateForge DPT command-line interface."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import uvicorn

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
    if arguments.data_dir is not None:
        data_dir = arguments.data_dir.expanduser().resolve()
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
    )
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
