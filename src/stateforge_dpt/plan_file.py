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
SECURITY_CACHE_START = "<!-- STATEFORGE_DPT_SECURITY_CACHE"
SECURITY_CACHE_END = "STATEFORGE_DPT_SECURITY_CACHE -->"
MAX_PLAN_FILE_BYTES = 21 * 1024 * 1024
MAX_SECURITY_CACHE_BYTES = 16 * 1024 * 1024
_MANIFEST_PATTERN = re.compile(
    rf"{re.escape(MANIFEST_START)}\s*(.*?)\s*{re.escape(MANIFEST_END)}",
    re.DOTALL,
)
_SECURITY_CACHE_PATTERN = re.compile(
    rf"{re.escape(SECURITY_CACHE_START)}\s*(.*?)\s*"
    rf"{re.escape(SECURITY_CACHE_END)}",
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
    security_cache: dict[str, Any] | None = None


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


def security_cache_fingerprint(cache: dict[str, Any]) -> str:
    """Return the checksum for a cache envelope without its checksum field."""
    material = {key: value for key, value in cache.items() if key != "fingerprint"}
    return hashlib.sha256(_canonical_json(material).encode()).hexdigest()


def render_security_cache(cache: dict[str, Any]) -> str:
    """Render a portable, independently checksummed SBOM cache block."""
    envelope = dict(cache)
    envelope["format"] = "stateforge-dpt-security-cache"
    envelope["format_version"] = 1
    envelope["fingerprint"] = security_cache_fingerprint(envelope)
    payload = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
    if len(payload.encode()) > MAX_SECURITY_CACHE_BYTES:
        raise PlanFileError("The DPT security cache is unexpectedly large")
    payload = payload.replace("--", "\\u002d\\u002d")
    return f"{SECURITY_CACHE_START}\n{payload}\n{SECURITY_CACHE_END}"


def parse_security_cache(content: str) -> dict[str, Any] | None:
    """Parse an optional SBOM cache without changing portable-plan validity."""
    match = _SECURITY_CACHE_PATTERN.search(content)
    if match is None:
        return None
    if len(match.group(1).encode()) > MAX_SECURITY_CACHE_BYTES:
        raise PlanFileError("The embedded DPT security cache is unexpectedly large")
    try:
        cache: Any = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise PlanFileError(
            "The embedded DPT security cache is not valid JSON"
        ) from error
    if not isinstance(cache, dict):
        raise PlanFileError("The embedded DPT security cache has an invalid shape")
    if cache.get("format") != "stateforge-dpt-security-cache":
        raise PlanFileError("The file contains an unknown DPT security cache")
    if cache.get("format_version") != 1:
        raise PlanFileError(
            f"Unsupported DPT security cache format: {cache.get('format_version')}"
        )
    if cache.get("fingerprint") != security_cache_fingerprint(cache):
        raise PlanFileError(
            "The DPT security cache checksum does not match its contents"
        )
    repositories = cache.get("repositories")
    if not isinstance(cache.get("plan_fingerprint"), str) or not isinstance(
        repositories, list
    ):
        raise PlanFileError("The embedded DPT security cache is incomplete")
    for entry in repositories:
        if not isinstance(entry, dict):
            raise PlanFileError("A DPT security cache repository entry is invalid")
        sbom = entry.get("sbom")
        dependency = entry.get("dependency_material")
        if (
            not all(
                isinstance(entry.get(field), str)
                for field in (
                    "repository",
                    "alias",
                    "plan_source_branch",
                    "observed_commit_sha",
                    "generated_at",
                )
            )
            or not isinstance(sbom, dict)
            or not isinstance(dependency, dict)
        ):
            raise PlanFileError("A DPT security cache repository entry is incomplete")
        document = sbom.get("document")
        expected = sbom.get("sha256")
        if not isinstance(document, dict) or not isinstance(expected, str):
            raise PlanFileError("A cached SBOM is incomplete")
        actual = hashlib.sha256(_canonical_json(document).encode()).hexdigest()
        if expected != actual:
            raise PlanFileError("A cached SBOM checksum does not match its contents")
    return cache


def append_security_cache(content: str, cache: dict[str, Any] | None) -> str:
    """Replace the optional cache block without changing the plan manifest."""
    without_cache = _SECURITY_CACHE_PATTERN.sub("", content).rstrip()
    if cache is None or not cache.get("repositories"):
        return without_cache + "\n"
    return f"{without_cache}\n\n{render_security_cache(cache)}\n"


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
    security_cache: dict[str, Any] | None = None
    try:
        candidate = parse_security_cache(content)
        if candidate and candidate.get("plan_fingerprint") == fingerprint:
            security_cache = candidate
    except PlanFileError:
        # Portable security evidence is an optional cache. A damaged cache cannot
        # invalidate or block importing the independently authenticated plan.
        security_cache = None
    return LoadedPlan(
        path=path,
        plan=plan,
        fingerprint=fingerprint,
        file_digest=hashlib.sha256(content.encode()).hexdigest(),
        security_cache=security_cache,
    )
