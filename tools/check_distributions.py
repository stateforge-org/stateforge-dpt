"""Reject release archives containing private workspace files or missing UI assets."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def main() -> None:
    """Check the wheel and source archive before either can be published."""
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise RuntimeError(
            "Expected exactly one universal wheel and one source distribution"
        )
    if not wheels[0].name.endswith("-py3-none-any.whl"):
        raise RuntimeError("DPT must produce a platform-independent Python wheel")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        if "stateforge_dpt/frontend/index.html" not in names:
            raise RuntimeError("The wheel is missing its bundled frontend")
        if any(
            not name.startswith(("stateforge_dpt/", "stateforge_dpt-"))
            for name in names
        ):
            raise RuntimeError("Unexpected content in the wheel")
    allowed = {
        "src",
        "tests",
        "tools",
        "docs",
        "frontend",
        "pyproject.toml",
        "README.md",
        ".env.example",
        ".gitignore",
        "PKG-INFO",
    }
    with tarfile.open(sources[0]) as archive:
        names = archive.getnames()
        for name in names:
            parts = PurePosixPath(name).parts
            if len(parts) > 1 and parts[1] not in allowed:
                raise RuntimeError(
                    f"Unexpected private/workspace content in source archive: {name}"
                )
        if not any(name.endswith("/frontend/dist/index.html") for name in names):
            raise RuntimeError("Source distribution is missing its prebuilt frontend")
    print(
        "Release archives OK: universal wheel, bundled frontend, no workspace metadata."
    )


if __name__ == "__main__":
    main()
