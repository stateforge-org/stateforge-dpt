#!/usr/bin/env python3
"""Emit the initial SFP relationship-graph proposal operations as JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from stateforge_dpt.sfp_baseline import (
    sfp_baseline_operations,
    unmapped_protocol_paths,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> None:
    """Validate source coverage and print proposal-ready operations."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--provider", type=Path, required=True)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--source-ref", default="origin/mvp/implementation")
    arguments = parser.parse_args()
    protocol_paths = _git(
        arguments.protocol,
        "ls-tree",
        "-r",
        "--name-only",
        arguments.source_ref,
    ).splitlines()
    unmapped = unmapped_protocol_paths(protocol_paths)
    if unmapped:
        parser.error("unmapped protocol modules: " + ", ".join(unmapped))
    operations = sfp_baseline_operations(
        {
            "protocol": _git(arguments.protocol, "rev-parse", arguments.source_ref),
            "provider": _git(arguments.provider, "rev-parse", arguments.source_ref),
            "agent": _git(arguments.agent, "rev-parse", arguments.source_ref),
        },
        tracking_started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    print(
        json.dumps(
            {
                "rationale": (
                    "Establish the initial SFP cross-repository implementation "
                    "relationship baseline."
                ),
                "operations": operations,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
