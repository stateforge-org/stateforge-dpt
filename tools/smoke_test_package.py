"""Exercise an installed DPT wheel from an unrelated working directory."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from stateforge_dpt.config import PACKAGE_DIRECTORY


def read_url(url: str) -> str:
    """Read one URL from the local smoke-test server."""
    with urlopen(url, timeout=2) as response:  # noqa: S310 - localhost only
        return response.read().decode("utf-8")


def main() -> None:
    """Verify console entry points, bundled assets, API, and the default workspace."""
    if not (PACKAGE_DIRECTORY / "frontend" / "index.html").is_file():
        raise RuntimeError(
            "Smoke test requires an installed wheel, not an editable checkout"
        )
    executable = shutil.which("stateforge-dpt")
    if executable is None:
        raise RuntimeError("Installed stateforge-dpt console command is missing")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="dpt-package-test-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        working_directory = root / "unrelated-working-directory"
        working_directory.mkdir()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STATEFORGE_DPT_") and key != "PYTHONPATH"
        }
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            NO_COLOR="1",
            PYTHONIOENCODING="utf-8",
        )
        process = subprocess.Popen(  # noqa: S603 - installed console command, no shell
            [executable, "run", "--port", str(port)],
            cwd=working_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while True:
                try:
                    health = json.loads(read_url(f"{base}/api/health"))
                    assert health == {"status": "ok"}  # noqa: S101 - smoke-test assertion
                    break
                except (URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(
                            "Installed DPT server did not start"
                        ) from None
                    time.sleep(0.1)
            html = read_url(base)
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
            assert len(assets) >= 3  # noqa: S101 - JS, CSS, and favicon
            for asset in assets:
                assert read_url(f"{base}{asset}")  # noqa: S101 - all bundled assets served
            assert json.loads(read_url(f"{base}/api/projects")) == []  # noqa: S101
            assert (home / ".stateforge-dpt" / "dpt.sqlite3").is_file()  # noqa: S101
            assert not (working_directory / ".stateforge-dpt").exists()  # noqa: S101
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
            print(output)
        assert "Continue with GitHub" in output  # noqa: S101
        assert "INFO:" not in output  # noqa: S101
        print(
            "Installed package OK: console command, frontend, assets, API, "
            "and home workspace."
        )


if __name__ == "__main__":
    main()
