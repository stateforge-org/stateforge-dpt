"""Exact-source CycloneDX generation with bounded archive handling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_FILES = 50_000
MAX_ARCHIVE_PATH_BYTES = 4_096
MAX_SBOM_BYTES = 2 * 1024 * 1024
GENERATOR_TIMEOUT_SECONDS = 120
ADAPTER_ID = "uv-lock-cyclonedx-v1"
ADAPTER_VERSION = 1
UV_VERSION = "0.13.5"


class SbomError(RuntimeError):
    """Raised when dependency material cannot produce trusted evidence."""


class UnsupportedRepositoryError(SbomError):
    """Raised when no reviewed generator adapter matches the repository."""


@dataclass(frozen=True, slots=True)
class SbomResult:
    """Validated and canonicalized CycloneDX evidence."""

    dependency_digest: str
    dependency_inputs: tuple[str, ...]
    document: dict[str, Any]
    canonical_json: str
    sha256: str
    component_count: int
    third_party_component_count: int
    queryable_component_count: int
    coverage: dict[str, Any]
    adapter_id: str = ADAPTER_ID
    adapter_version: int = ADAPTER_VERSION
    tool_name: str = "uv"
    tool_version: str = UV_VERSION
    generator_diagnostics: dict[str, Any] = field(default_factory=dict)


class SbomAdapter(Protocol):
    """One reviewed, deterministic repository-to-SBOM generator contract."""

    @property
    def adapter_id(self) -> str:
        """Return the stable generator contract identifier."""

    @property
    def adapter_version(self) -> int:
        """Return the reviewed generator contract version."""

    @property
    def tool_name(self) -> str:
        """Return the pinned generator executable name."""

    @property
    def tool_version(self) -> str:
        """Return the pinned generator executable version."""

    def matches(self, root: Path) -> bool:
        """Return whether this adapter owns the repository dependency inputs."""

    def dependency_material(self, root: Path) -> tuple[str, tuple[str, ...]]:
        """Return the adapter-bound digest and the input paths that produced it."""

    def generate(self, root: Path) -> SbomResult:
        """Generate validated, deterministic CycloneDX evidence."""


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for hashes and persistence."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def safe_extract_zip(archive: bytes, destination: Path) -> Path:
    """Extract a GitHub zipball without trusting entry paths or attributes."""
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise SbomError("Repository archive exceeds the download limit")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(archive)) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_ARCHIVE_FILES:
            raise SbomError("Repository archive contains too many files")
        total_size = sum(entry.file_size for entry in entries)
        if total_size > MAX_EXTRACTED_BYTES:
            raise SbomError("Repository archive expands beyond the safety limit")
        roots = {
            PurePosixPath(entry.filename).parts[0]
            for entry in entries
            if PurePosixPath(entry.filename).parts
        }
        if len(roots) != 1:
            raise SbomError("Repository archive does not have one source root")
        root = next(iter(roots))
        file_count = 0
        extracted_paths: set[PurePosixPath] = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute():
                raise SbomError("Repository archive contains an unsafe path")
            if not path.parts or path.parts[0] != root:
                raise SbomError("Repository archive contains an invalid root")
            relative = PurePosixPath(*path.parts[1:])
            if not relative.parts:
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise SbomError("Repository archive contains an unsafe path")
            if len(str(relative).encode()) > MAX_ARCHIVE_PATH_BYTES:
                raise SbomError("Repository archive contains an excessive path")
            if relative in extracted_paths:
                raise SbomError("Repository archive contains a duplicate path")
            extracted_paths.add(relative)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (
                mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
            ):
                raise SbomError("Repository archive contains an unsafe entry type")
            target = destination.joinpath(*relative.parts)
            resolved = target.resolve()
            if destination.resolve() not in (resolved, *resolved.parents):
                raise SbomError("Repository archive escapes its extraction root")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            file_count += 1
            if file_count > MAX_ARCHIVE_FILES:
                raise SbomError("Repository archive contains too many files")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    return destination


def dependency_material(root: Path) -> tuple[str, tuple[str, ...]]:
    """Identify uv inputs and return their adapter-bound digest."""
    required = root / "uv.lock"
    project = root / "pyproject.toml"
    if not required.is_file() or not project.is_file():
        raise UnsupportedRepositoryError("No supported committed uv.lock project found")
    candidates = [project, required, root / "uv.toml"]
    inputs = tuple(path.name for path in candidates if path.is_file())
    digest = hashlib.sha256()
    digest.update(f"{ADAPTER_ID}:{ADAPTER_VERSION}:uv:{UV_VERSION}\0".encode())
    for name in sorted(inputs):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), inputs


def _validate_sbom(document: Any) -> SbomResult:
    if not isinstance(document, dict):
        raise SbomError("CycloneDX output is not a JSON object")
    if (
        document.get("bomFormat") != "CycloneDX"
        or str(document.get("specVersion")) != "1.5"
    ):
        raise SbomError("Generator did not produce CycloneDX JSON 1.5")
    # uv emits a new timestamp and UUID URN for equivalent exports. Those
    # fields are transport metadata, not dependency identity.
    document.pop("serialNumber", None)
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise SbomError("CycloneDX output has no project metadata")
    metadata.pop("timestamp", None)
    root_component = metadata.get("component")
    if not isinstance(root_component, dict) or not all(
        isinstance(root_component.get(field), str) and root_component.get(field)
        for field in ("type", "name", "version", "bom-ref")
    ):
        raise SbomError("CycloneDX output does not identify its root project")
    components = document.get("components")
    if not isinstance(components, list):
        raise SbomError("CycloneDX output has no component inventory")
    references: set[str] = set()
    missing_purl = 0
    missing_version = 0
    queryable = 0
    for component in components:
        if not isinstance(component, dict):
            raise SbomError("CycloneDX contains an invalid component")
        if not isinstance(component.get("name"), str) or not component.get("name"):
            raise SbomError("CycloneDX contains an unnamed component")
        reference = component.get("bom-ref")
        if isinstance(reference, str):
            if reference in references:
                raise SbomError("CycloneDX contains duplicate bom-ref values")
            references.add(reference)
        purl = component.get("purl")
        version = component.get("version")
        if not isinstance(purl, str) or not purl.startswith("pkg:"):
            missing_purl += 1
        if not isinstance(version, str) or not version:
            missing_version += 1
        if (
            isinstance(purl, str)
            and purl.startswith("pkg:")
            and isinstance(version, str)
            and version
        ):
            queryable += 1
    canonical = canonical_json(document)
    if len(canonical.encode()) > MAX_SBOM_BYTES:
        raise SbomError("Generated CycloneDX document exceeds the cache limit")
    coverage = {
        "total_components": len(components),
        "third_party_components": len(components),
        "queryable_components": queryable,
        "missing_purl": missing_purl,
        "missing_version": missing_version,
        "unsupported_ecosystem": 0,
        "failed_queries": 0,
    }
    return SbomResult(
        dependency_digest="",
        dependency_inputs=(),
        document=document,
        canonical_json=canonical,
        sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        component_count=len(components),
        third_party_component_count=len(components),
        queryable_component_count=queryable,
        coverage=coverage,
    )


def validate_cached_sbom(
    document: dict[str, Any],
    dependency_digest: str,
    dependency_inputs: tuple[str, ...],
    *,
    adapter_id: str = ADAPTER_ID,
    adapter_version: int = ADAPTER_VERSION,
    tool_name: str = "uv",
    tool_version: str = UV_VERSION,
    generator_diagnostics: dict[str, Any] | None = None,
) -> SbomResult:
    """Validate a portable cached document against current dependency inputs."""
    result = _validate_sbom(document)
    return SbomResult(
        dependency_digest=dependency_digest,
        dependency_inputs=dependency_inputs,
        document=result.document,
        canonical_json=result.canonical_json,
        sha256=result.sha256,
        component_count=result.component_count,
        third_party_component_count=result.third_party_component_count,
        queryable_component_count=result.queryable_component_count,
        coverage=result.coverage,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        tool_name=tool_name,
        tool_version=tool_version,
        generator_diagnostics=generator_diagnostics or {},
    )


def generate_uv_sbom(root: Path) -> SbomResult:
    """Export a frozen uv lock to deterministic CycloneDX JSON."""
    digest, inputs = dependency_material(root)
    adjacent = Path(sys.executable).with_name("uv")
    executable = shutil.which("uv") or (str(adjacent) if adjacent.is_file() else None)
    if executable is None:
        raise SbomError("uv 0.13.5 is required to generate repository SBOMs")
    version = subprocess.run(  # noqa: S603 - resolved executable, fixed arguments
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    version_parts = version.stdout.split()
    if (
        version.returncode != 0
        or len(version_parts) < 2
        or version_parts[:2] != ["uv", UV_VERSION]
    ):
        raise SbomError(f"Expected uv {UV_VERSION}, found {version.stdout.strip()}")
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP")
        if key in os.environ
    }
    environment.update(
        {
            "UV_NO_PROGRESS": "1",
            "UV_NO_CACHE": "1",
            "UV_OFFLINE": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
    )
    command = [
        executable,
        "export",
        "--format",
        "cyclonedx1.5",
        "--locked",
        "--offline",
        "--all-groups",
        "--all-extras",
        "--no-editable",
    ]
    started = time.monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="stateforge-dpt-sbom-home-") as home,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        environment["HOME"] = home
        try:
            process = subprocess.run(  # noqa: S603 - fixed adapter command
                command,
                cwd=root,
                env=environment,
                check=False,
                stdout=stdout,
                stderr=stderr,
                timeout=GENERATOR_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise SbomError(
                f"uv CycloneDX export exceeded {GENERATOR_TIMEOUT_SECONDS} seconds"
            ) from error
        duration_ms = round((time.monotonic() - started) * 1000)
        stdout_size = stdout.seek(0, 2)
        if stdout_size > MAX_SBOM_BYTES:
            raise SbomError("Generated CycloneDX document exceeds the cache limit")
        stdout.seek(0)
        output = stdout.read(MAX_SBOM_BYTES + 1)
        stderr.seek(0)
        error_output = stderr.read(4_001).decode(errors="replace")
    if process.returncode != 0:
        detail = error_output[:4_000].strip()
        raise SbomError(f"uv CycloneDX export failed after {duration_ms} ms: {detail}")
    try:
        document: Any = json.loads(output)
    except json.JSONDecodeError as error:
        raise SbomError("uv returned invalid CycloneDX JSON") from error
    return validate_cached_sbom(
        document,
        digest,
        inputs,
        generator_diagnostics={
            "command": ["uv", *command[1:]],
            "duration_ms": duration_ms,
            "exit_code": process.returncode,
            "stderr": error_output[:4_000],
        },
    )


@dataclass(frozen=True, slots=True)
class UvLockCycloneDxAdapter:
    """Generate a complete Python SBOM from a committed frozen uv lock."""

    adapter_id: str = ADAPTER_ID
    adapter_version: int = ADAPTER_VERSION
    tool_name: str = "uv"
    tool_version: str = UV_VERSION

    def matches(self, root: Path) -> bool:
        """Match only Python projects with both metadata and a committed uv lock."""
        return (root / "pyproject.toml").is_file() and (root / "uv.lock").is_file()

    def dependency_material(self, root: Path) -> tuple[str, tuple[str, ...]]:
        """Hash every file that affects the frozen uv export."""
        return dependency_material(root)

    def generate(self, root: Path) -> SbomResult:
        """Run the pinned frozen uv CycloneDX export."""
        return generate_uv_sbom(root)


SBOM_ADAPTERS: tuple[SbomAdapter, ...] = (UvLockCycloneDxAdapter(),)


def select_sbom_adapter(root: Path) -> SbomAdapter:
    """Select the first reviewed adapter whose detection contract matches."""
    for adapter in SBOM_ADAPTERS:
        if adapter.matches(root):
            return adapter
    raise UnsupportedRepositoryError(
        "No supported committed dependency lock was found for SBOM generation"
    )


def sbom_adapter(adapter_id: str, adapter_version: int) -> SbomAdapter | None:
    """Resolve a portable cache's generator contract from the registry."""
    return next(
        (
            adapter
            for adapter in SBOM_ADAPTERS
            if adapter.adapter_id == adapter_id
            and adapter.adapter_version == adapter_version
        ),
        None,
    )
