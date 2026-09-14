"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_GITHUB_CLIENT_ID = "Iv23lidYTyPNl3LWbjTI"


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved DPT runtime settings."""

    host: str
    port: int
    data_dir: Path
    frontend_dir: Path
    github_client_id: str
    working_directory: Path = field(default_factory=lambda: Path.cwd().resolve())

    @property
    def database_path(self) -> Path:
        """Return the SQLite database path."""
        return self.data_dir / "dpt.sqlite3"


def load_settings(root: Path | None = None) -> Settings:
    """Load settings from the environment and an optional local `.env`."""
    project_root = (root or Path.cwd()).resolve()
    load_dotenv(project_root / ".env")

    data_dir = Path(
        os.getenv("STATEFORGE_DPT_DATA_DIR", project_root / ".stateforge-dpt")
    ).expanduser()
    frontend_dir = Path(
        os.getenv("STATEFORGE_DPT_FRONTEND_DIR", project_root / "frontend/dist")
    ).expanduser()

    return Settings(
        host=os.getenv("STATEFORGE_DPT_HOST", "127.0.0.1"),
        port=int(os.getenv("STATEFORGE_DPT_PORT", "8463")),
        data_dir=data_dir.resolve(),
        frontend_dir=frontend_dir.resolve(),
        github_client_id=(
            os.getenv("STATEFORGE_DPT_GITHUB_CLIENT_ID") or DEFAULT_GITHUB_CLIENT_ID
        ),
        working_directory=project_root,
    )
