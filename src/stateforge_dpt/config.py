"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_GITHUB_CLIENT_ID = "Iv23lidYTyPNl3LWbjTI"
PACKAGE_DIRECTORY = Path(__file__).resolve().parent


def default_data_dir() -> Path:
    """Return the per-user workspace, independent of the launch directory."""
    return Path.home() / ".stateforge-dpt"


def _source_root() -> Path | None:
    """Locate an editable installation without searching the current directory."""
    if PACKAGE_DIRECTORY.parent.name == "src":
        candidate = PACKAGE_DIRECTORY.parent.parent
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def default_frontend_dir() -> Path:
    """Prefer bundled UI assets, falling back to an editable checkout's build."""
    bundled = PACKAGE_DIRECTORY / "frontend"
    if (bundled / "index.html").is_file():
        return bundled
    source = _source_root()
    if source is not None:
        return source / "frontend" / "dist"
    return bundled


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved DPT runtime settings."""

    host: str
    port: int
    data_dir: Path
    frontend_dir: Path
    github_client_id: str
    working_directory: Path = field(default_factory=default_data_dir)
    # Programmatic Settings instances opt in explicitly. load_settings() enables
    # MCP for the normal local CLI runtime.
    mcp_enabled: bool = False
    mcp_path: str = "/mcp"
    mcp_max_read_bytes: int = 128_000
    mcp_allow_remote: bool = False
    # Programmatic Settings instances keep background work disabled unless a
    # caller explicitly opts in. load_settings() enables it for the local app.
    auto_reconcile_enabled: bool = False
    auto_reconcile_active_seconds: float = 45.0
    auto_reconcile_idle_seconds: float = 120.0
    auto_reconcile_full_seconds: float = 1_800.0

    @property
    def database_path(self) -> Path:
        """Return the SQLite database path."""
        return self.data_dir / "dpt.sqlite3"


def load_settings(root: Path | None = None) -> Settings:
    """Load user configuration and environment overrides, never from the cwd."""
    config_root = (root or default_data_dir()).resolve()
    values: dict[str, tuple[str, Path]] = {}
    source = _source_root()
    config_roots = [source, config_root] if root is None else [config_root]
    for config_directory in config_roots:
        if config_directory is not None:
            for key, configured_value in dotenv_values(
                config_directory / ".env"
            ).items():
                if configured_value is not None:
                    values[key] = (configured_value, config_directory)
    for key, environment_value in os.environ.items():
        if key.startswith("STATEFORGE_DPT_"):
            values[key] = (environment_value, config_root)

    def value(key: str, default: str) -> str:
        return values.get(key, (default, config_root))[0] or default

    def directory(key: str, default: Path) -> Path:
        configured, base = values.get(key, (str(default), config_root))
        selected = Path(configured).expanduser() if configured else default
        return (selected if selected.is_absolute() else base / selected).resolve()

    def boolean(key: str, default: bool) -> bool:
        configured = value(key, "true" if default else "false").strip().casefold()
        if configured in {"1", "true", "yes", "on"}:
            return True
        if configured in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{key} must be true or false")

    def positive_float(key: str, default: float) -> float:
        configured = float(value(key, str(default)))
        if configured <= 0:
            raise ValueError(f"{key} must be positive")
        return configured

    data_dir = directory("STATEFORGE_DPT_DATA_DIR", default_data_dir())
    frontend_dir = directory("STATEFORGE_DPT_FRONTEND_DIR", default_frontend_dir())

    mcp_path = value("STATEFORGE_DPT_MCP_PATH", "/mcp").strip()
    if not mcp_path.startswith("/") or mcp_path == "/":
        raise ValueError("STATEFORGE_DPT_MCP_PATH must be a non-root absolute path")
    mcp_path = mcp_path.rstrip("/")
    mcp_max_read_bytes = int(value("STATEFORGE_DPT_MCP_MAX_READ_BYTES", "128000"))
    if mcp_max_read_bytes <= 0:
        raise ValueError("STATEFORGE_DPT_MCP_MAX_READ_BYTES must be positive")

    return Settings(
        host=value("STATEFORGE_DPT_HOST", "127.0.0.1"),
        port=int(value("STATEFORGE_DPT_PORT", "8463")),
        data_dir=data_dir,
        frontend_dir=frontend_dir,
        github_client_id=value(
            "STATEFORGE_DPT_GITHUB_CLIENT_ID", DEFAULT_GITHUB_CLIENT_ID
        ),
        working_directory=directory("STATEFORGE_DPT_WORKING_DIRECTORY", data_dir),
        mcp_enabled=boolean("STATEFORGE_DPT_MCP_ENABLED", True),
        mcp_path=mcp_path,
        mcp_max_read_bytes=mcp_max_read_bytes,
        mcp_allow_remote=boolean("STATEFORGE_DPT_MCP_ALLOW_REMOTE", False),
        auto_reconcile_enabled=boolean(
            "STATEFORGE_DPT_AUTO_RECONCILE_ENABLED",
            True,
        ),
        auto_reconcile_active_seconds=positive_float(
            "STATEFORGE_DPT_AUTO_RECONCILE_ACTIVE_SECONDS",
            45.0,
        ),
        auto_reconcile_idle_seconds=positive_float(
            "STATEFORGE_DPT_AUTO_RECONCILE_IDLE_SECONDS",
            120.0,
        ),
        auto_reconcile_full_seconds=positive_float(
            "STATEFORGE_DPT_AUTO_RECONCILE_FULL_SECONDS",
            1_800.0,
        ),
    )
