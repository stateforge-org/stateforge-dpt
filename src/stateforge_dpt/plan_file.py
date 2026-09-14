"""Portable, Git-tracked DPT plan manifests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from stateforge_dpt.models import PortablePlan

DEFAULT_PLAN_FILENAME = "STATEFORGE_DPT.md"
MANIFEST_START = "<!-- STATEFORGE_DPT_MANIFEST"
MANIFEST_END = "STATEFORGE_DPT_MANIFEST -->"
MAX_PLAN_FILE_BYTES = 5 * 1024 * 1024
_MANIFEST_PATTERN = re.compile(
    rf"{re.escape(MANIFEST_START)}\s*(.*?)\s*{re.escape(MANIFEST_END)}",
    re.DOTALL,
)


class PlanFileError(RuntimeError):
    """Raised when a Git-tracked DPT plan cannot be trusted."""


@dataclass(frozen=True, slots=True)
class LoadedPlan:
    """Validated portable plan and its source metadata."""

    path: Path
    plan: PortablePlan
    fingerprint: str
    file_digest: str


def _canonical_json(value: Any) -> str:
    """Return stable JSON without applying model defaults or coercion."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_plan_json(plan: PortablePlan) -> str:
    """Return the stable JSON representation used for fingerprints."""
    return _canonical_json(plan.model_dump(mode="json"))


def plan_fingerprint(plan: PortablePlan) -> str:
    """Return a deterministic SHA-256 identity for a portable plan."""
    return hashlib.sha256(canonical_plan_json(plan).encode()).hexdigest()


def render_manifest(plan: PortablePlan) -> str:
    """Render a reviewable, checksummed manifest hidden by Markdown viewers."""
    fingerprint = plan_fingerprint(plan)
    envelope = {
        "format": "stateforge-dpt-plan",
        "format_version": 1,
        "fingerprint": fingerprint,
        "plan": plan.model_dump(mode="json"),
    }
    payload = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
    # Prevent user-authored text from terminating the surrounding HTML comment.
    payload = payload.replace("--", "\\u002d\\u002d")
    return f"{MANIFEST_START}\n{payload}\n{MANIFEST_END}"


def parse_manifest(content: str) -> tuple[PortablePlan, str]:
    """Parse and authenticate a portable manifest from rendered Markdown."""
    match = _MANIFEST_PATTERN.search(content)
    if match is None:
        raise PlanFileError(
            "This file predates portable DPT projects. Publish the plan once "
            "from its original DPT installation, commit it, and try again."
        )
    try:
        envelope: Any = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise PlanFileError("The embedded DPT manifest is not valid JSON") from error
    if not isinstance(envelope, dict):
        raise PlanFileError("The embedded DPT manifest has an invalid shape")
    if envelope.get("format") != "stateforge-dpt-plan":
        raise PlanFileError("The file does not contain a StateForge DPT plan")
    if envelope.get("format_version") != 1:
        raise PlanFileError(
            f"Unsupported DPT plan format: {envelope.get('format_version')}"
        )
    raw_plan = envelope.get("plan")
    raw_fingerprint = hashlib.sha256(_canonical_json(raw_plan).encode()).hexdigest()
    if envelope.get("fingerprint") != raw_fingerprint:
        raise PlanFileError(
            "The DPT manifest checksum does not match its contents. Resolve the "
            "Git conflict or publish a fresh revision before importing it."
        )
    try:
        plan = PortablePlan.model_validate(raw_plan)
    except ValidationError as error:
        problem = error.errors()[0].get("msg", "invalid portable plan")
        raise PlanFileError(f"The embedded DPT plan is invalid: {problem}") from error

    # Format v1 plans published before source-branch synchronization do not carry
    # this field. Verify their original bytes first, then upgrade the in-memory
    # representation so the local SQLite baseline is not immediately considered
    # an unpublished edit.
    if plan.project.plan_source_branch is None:
        project = plan.project.model_copy(
            update={
                "plan_source_branch": f"{plan.project.release.lower()}/implementation"
            }
        )
        plan = plan.model_copy(update={"project": project})
    return plan, plan_fingerprint(plan)


def resolve_plan_path(path_value: str) -> Path:
    """Resolve either a clone root or the plan file itself."""
    path = Path(path_value).expanduser().resolve()
    return path / DEFAULT_PLAN_FILENAME if path.is_dir() else path


def load_plan(path_value: str) -> LoadedPlan:
    """Load a size-limited, validated portable plan from disk."""
    path = resolve_plan_path(path_value)
    if not path.is_file():
        raise PlanFileError(f"DPT plan file not found: {path}")
    if path.stat().st_size > MAX_PLAN_FILE_BYTES:
        raise PlanFileError("The DPT plan file is unexpectedly large")
    content = path.read_text(encoding="utf-8")
    plan, fingerprint = parse_manifest(content)
    return LoadedPlan(
        path=path,
        plan=plan,
        fingerprint=fingerprint,
        file_digest=hashlib.sha256(content.encode()).hexdigest(),
    )
