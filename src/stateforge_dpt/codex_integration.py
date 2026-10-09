"""Install StateForge DPT as a personal Codex desktop plugin."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from stateforge_dpt import __version__

PLUGIN_NAME = "stateforge-dpt"
DEFAULT_MARKETPLACE_NAME = "stateforge-personal"
CONFIG_BLOCK_START = "# >>> stateforge-dpt setup-codex >>>"
CONFIG_BLOCK_END = "# <<< stateforge-dpt setup-codex <<<"


class CodexIntegrationError(RuntimeError):
    """Raised when Codex integration cannot be installed safely."""


@dataclass(frozen=True)
class CodexIntegration:
    """Paths and identity written by the Codex setup command."""

    plugin_directory: Path
    marketplace_path: Path
    config_path: Path
    plugin_id: str
    mcp_url: str


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    """Atomically replace a small UTF-8 configuration file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _json_document(value: dict[str, Any]) -> str:
    return f"{json.dumps(value, indent=2, sort_keys=False)}\n"


def _load_marketplace(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": DEFAULT_MARKETPLACE_NAME,
            "interface": {"displayName": "StateForge local tools"},
            "plugins": [],
        }
    if path.is_symlink():
        raise CodexIntegrationError(
            f"Refusing to replace symbolic-link marketplace file: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CodexIntegrationError(
            f"Cannot safely update the existing Codex marketplace at {path}: {error}"
        ) from error
    if not isinstance(value, dict) or not isinstance(value.get("plugins", []), list):
        raise CodexIntegrationError(
            f"The existing Codex marketplace has an unsupported shape: {path}"
        )
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CodexIntegrationError(
            f"The existing Codex marketplace has no valid name: {path}"
        )
    value.setdefault("plugins", [])
    return value


def _strip_managed_config_block(content: str) -> str:
    start = content.find(CONFIG_BLOCK_START)
    end = content.find(CONFIG_BLOCK_END)
    if (start == -1) != (end == -1):
        raise CodexIntegrationError(
            "The StateForge-managed Codex configuration block is incomplete. "
            "Repair or remove it before rerunning setup-codex."
        )
    if start == -1:
        return content
    if end < start:
        raise CodexIntegrationError(
            "The StateForge-managed Codex configuration markers are out of order."
        )
    end += len(CONFIG_BLOCK_END)
    before = content[:start].rstrip()
    after = content[end:].lstrip("\r\n")
    return "\n\n".join(part for part in (before, after) if part)


def _codex_config(
    existing: str,
    *,
    plugin_id: str,
) -> str:
    base = _strip_managed_config_block(existing)
    try:
        parsed = tomllib.loads(base) if base.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise CodexIntegrationError(
            f"Cannot safely update the existing Codex config.toml: {error}"
        ) from error
    plugins = parsed.get("plugins", {})
    if isinstance(plugins, dict) and plugin_id in plugins:
        raise CodexIntegrationError(
            f"Codex already has unmanaged settings for plugin {plugin_id!r}. "
            "Remove that table or configure the plugin manually."
        )
    escaped_plugin_id = plugin_id.replace('"', '\\"')
    block = "\n".join(
        [
            CONFIG_BLOCK_START,
            f'[plugins."{escaped_plugin_id}"]',
            "enabled = true",
            "",
            (f'[plugins."{escaped_plugin_id}".mcp_servers."stateforge-dpt"]'),
            "enabled = true",
            'default_tools_approval_mode = "approve"',
            "",
            (
                f'[plugins."{escaped_plugin_id}".mcp_servers.'
                '"stateforge-dpt".tools.apply_plan_change_proposal]'
            ),
            'approval_mode = "prompt"',
            "",
            (
                f'[plugins."{escaped_plugin_id}".mcp_servers.'
                '"stateforge-dpt".tools.reject_plan_change_proposal]'
            ),
            'approval_mode = "prompt"',
            CONFIG_BLOCK_END,
        ]
    )
    prefix = f"{base.rstrip()}\n\n" if base.strip() else ""
    return f"{prefix}{block}\n"


def _validate_mcp_url(mcp_url: str) -> str:
    value = mcp_url.strip()
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise CodexIntegrationError(
            "Codex setup requires a valid explicit MCP port."
        ) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not port
        or not parsed.path.startswith("/")
    ):
        raise CodexIntegrationError(
            "Codex setup accepts only a loopback HTTP MCP URL with an explicit "
            "port and path, for example http://127.0.0.1:8463/mcp."
        )
    return value


def install_codex_integration(
    *,
    mcp_url: str,
    home: Path | None = None,
) -> CodexIntegration:
    """Install or update the personal Codex plugin and approval policy."""
    root = (home or Path.home()).expanduser().resolve()
    url = _validate_mcp_url(mcp_url)
    plugin_directory = root / ".codex" / "plugins" / PLUGIN_NAME
    marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    config_path = root / ".codex" / "config.toml"
    if plugin_directory.is_symlink():
        raise CodexIntegrationError(
            f"Refusing to install through symbolic-link plugin path: {plugin_directory}"
        )
    plugin_directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    marketplace = _load_marketplace(marketplace_path)
    marketplace_name = str(marketplace["name"])
    plugin_id = f"{PLUGIN_NAME}@{marketplace_name}"
    existing_config = ""
    if config_path.exists():
        if config_path.is_symlink():
            raise CodexIntegrationError(
                f"Refusing to replace symbolic-link Codex config: {config_path}"
            )
        try:
            existing_config = config_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise CodexIntegrationError(
                f"Cannot read the existing Codex config.toml: {error}"
            ) from error
    updated_config = _codex_config(existing_config, plugin_id=plugin_id)

    plugin = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": PLUGIN_NAME,
        "version": __version__,
        "description": (
            "Inspect StateForge DPT projects and submit human-confirmed plan "
            "change proposals through the local DPT runtime."
        ),
    }
    mcp = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "stateforge-dpt": {
                "type": "streamable-http",
                "url": url,
            }
        },
    }
    _atomic_write(plugin_directory / "plugin.json", _json_document(plugin))
    _atomic_write(plugin_directory / "mcp.json", _json_document(mcp))

    entry = {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": f"./.codex/plugins/{PLUGIN_NAME}",
        },
        "policy": {
            "installation": "INSTALLED_BY_DEFAULT",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }
    marketplace["plugins"] = [
        item
        for item in marketplace["plugins"]
        if not isinstance(item, dict) or item.get("name") != PLUGIN_NAME
    ]
    marketplace["plugins"].append(entry)
    _atomic_write(marketplace_path, _json_document(marketplace))
    _atomic_write(
        config_path,
        updated_config,
    )
    return CodexIntegration(
        plugin_directory=plugin_directory,
        marketplace_path=marketplace_path,
        config_path=config_path,
        plugin_id=plugin_id,
        mcp_url=url,
    )
