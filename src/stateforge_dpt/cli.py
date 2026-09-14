"""StateForge DPT command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from stateforge_dpt.config import load_settings
from stateforge_dpt.database import Database
from stateforge_dpt.security import SecretStore
from stateforge_dpt.service import DPTService


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
    subcommands.add_parser("init-db", help="initialize the SQLite database")
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
    return command_parser


def main() -> None:
    """Run the requested DPT command."""
    arguments = parser().parse_args()
    settings = load_settings()
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
    uvicorn.run(
        "stateforge_dpt.app:app",
        host=arguments.host or settings.host,
        port=arguments.port or settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
