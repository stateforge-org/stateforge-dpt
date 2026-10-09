"""Readable, terminal-aware startup output."""

from __future__ import annotations

import os
import sys

from stateforge_dpt.config import Settings


def startup_banner(
    settings: Settings, *, color: bool = False, unicode_symbols: bool = True
) -> str:
    """Format the DPT welcome screen without requiring a terminal library."""

    def style(text: str, codes: str) -> str:
        return f"\033[{codes}m{text}\033[0m" if color else text

    browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host  # noqa: S104 - format bind addresses, not a listening socket
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    url = f"http://{browser_host}:{settings.port}"
    mcp_url = f"{url}{settings.mcp_path}"
    mark, rule, arrow = ("◆", "─", "→") if unicode_symbols else ("*", "-", ">")

    return "\n".join(
        [
            "",
            f"  {style(mark, '94')}  {style('StateForge DPT', '1;94')}",
            f"     {style('Your local planning workspace', '2')}",
            "",
            f"  {style(rule * 48, '2;94')}",
            "",
            f"  {style('OPEN IN YOUR BROWSER', '1')}",
            f"  {style(arrow, '1;32')}  {style(url, '1;4;96')}",
            *(
                [
                    "",
                    f"  {style('AI TOOLS (MCP)', '1')}",
                    f"  {style(arrow, '1;35')}  {style(mcp_url, '1;4;95')}",
                    "  "
                    f"{style('Codex app setup ', '2')}"
                    f"{style('stateforge-dpt setup-codex', '1;95')}",
                ]
                if settings.mcp_enabled
                else []
            ),
            "",
            f"  Sign in with {style('Continue with GitHub', '1;95')}",
            f"  {style('or continue in local mode.', '2')}",
            "",
            f"  {style('WORKSPACE', '1')}",
            f"  {style('Local data       ', '2')}{settings.data_dir}",
            f"  {style('Repositories     ', '2')}"
            f"{settings.working_directory / 'projects'}",
            "",
            f"  {style('Keep this terminal open.', '2')} "
            f"Press {style('Ctrl+C', '1;33')} {style('to stop the server.', '2')}",
            "",
        ]
    )


def print_startup_banner(settings: Settings) -> None:
    """Use colors on interactive terminals, respecting NO_COLOR and TERM=dumb."""
    color = (
        sys.stdout.isatty()
        and "NO_COLOR" not in os.environ
        and os.getenv("TERM") != "dumb"
    )
    try:
        "◆─→".encode(sys.stdout.encoding or "utf-8")
        unicode_symbols = True
    except UnicodeEncodeError:
        unicode_symbols = False
    print(
        startup_banner(settings, color=color, unicode_symbols=unicode_symbols),
        flush=True,
    )
