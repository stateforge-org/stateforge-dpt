import asyncio
import hashlib
import io
import json
import stat
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import stateforge_dpt.security_evidence.osv as osv_module
from stateforge_dpt.app import SESSION_COOKIE, create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.database import Database
from stateforge_dpt.github import GitHubError
from stateforge_dpt.models import (
    PortablePlan,
    PortableProject,
    PortableRepository,
    ProjectCreate,
    RepositoryCreate,
)
from stateforge_dpt.plan_file import (
    DEFAULT_PLAN_FILENAME,
    append_security_cache,
    load_plan,
    plan_fingerprint,
    render_manifest,
)
from stateforge_dpt.security import SecretStore
from stateforge_dpt.security_evidence.models import (
    SecurityState,
    SourceStatus,
    aggregate_state,
)
from stateforge_dpt.security_evidence.osv import OsvClient
from stateforge_dpt.security_evidence.sbom import (
    SbomError,
    UnsupportedRepositoryError,
    dependency_material,
    safe_extract_zip,
    select_sbom_adapter,
    validate_cached_sbom,
)
from stateforge_dpt.security_evidence.service import (
    SecurityEvidenceService,
    import_portable_security_cache,
)
from stateforge_dpt.service import DPTService


def _archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files.items():
            entry = zipfile.ZipInfo(f"repository-root/{name}")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(entry, content)
    return output.getvalue()


def _source(
    state: str,
    *,
    current: bool = True,
    complete: bool = True,
    configured: bool = True,
) -> SourceStatus:
    return SourceStatus(
        state=state,
        current=current,
        complete=complete,
        configured=configured,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        working_directory=tmp_path / "workspace",
    )


@pytest.mark.parametrize(
    ("dependency", "codeql", "findings", "coverage", "expected"),
    [
        (
            _source("current"),
            _source("current"),
            0,
            True,
            SecurityState.NO_KNOWN_FINDINGS,
        ),
        (_source("current"), _source("current"), 1, True, SecurityState.FINDINGS),
        (
            _source("current"),
            _source("pending", current=False, complete=False),
            0,
            True,
            SecurityState.PENDING,
        ),
        (
            _source("stale", current=False),
            _source("current"),
            0,
            True,
            SecurityState.STALE,
        ),
        (
            _source("not_configured", current=False, complete=False, configured=False),
            _source("not_configured", current=False, complete=False, configured=False),
            0,
            False,
            SecurityState.NOT_CONFIGURED,
        ),
        (_source("current"), _source("current"), 0, False, SecurityState.PARTIAL),
    ],
)
def test_aggregate_state_never_reports_green_for_incomplete_evidence(
    dependency: SourceStatus,
    codeql: SourceStatus,
    findings: int,
    coverage: bool,
    expected: SecurityState,
) -> None:
    assert (
        aggregate_state(
            dependency,
            codeql,
            findings,
            coverage_complete=coverage,
        )
        == expected
    )


def test_safe_archive_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("repository-root/../outside.txt", "unsafe")

    with pytest.raises(SbomError, match="unsafe path"):
        safe_extract_zip(archive.getvalue(), tmp_path / "extract")

    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("/absolute.txt", stat.S_IFREG | 0o644),
        ("repository-root/link", stat.S_IFLNK | 0o777),
        ("repository-root/device", stat.S_IFCHR | 0o600),
    ],
)
def test_safe_archive_extraction_rejects_absolute_and_special_entries(
    tmp_path: Path,
    name: str,
    mode: int,
) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        entry = zipfile.ZipInfo(name)
        entry.create_system = 3
        entry.external_attr = mode << 16
        bundle.writestr(entry, "unsafe")

    with pytest.raises(SbomError, match="unsafe"):
        safe_extract_zip(archive.getvalue(), tmp_path / "extract")


def test_dependency_digest_ignores_the_dpt_cache_file(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    first, inputs = dependency_material(tmp_path)

    (tmp_path / DEFAULT_PLAN_FILENAME).write_text("portable cache changed")
    second, changed_inputs = dependency_material(tmp_path)

    assert first == second
    assert inputs == changed_inputs == ("pyproject.toml", "uv.lock")


def test_sbom_adapter_registry_detects_supported_and_unsupported_sources(
    tmp_path: Path,
) -> None:
    with pytest.raises(UnsupportedRepositoryError):
        select_sbom_adapter(tmp_path)

    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")

    adapter = select_sbom_adapter(tmp_path)

    assert adapter.adapter_id == "uv-lock-cyclonedx-v1"
    assert adapter.adapter_version == 1
    assert adapter.tool_name == "uv"
    assert adapter.tool_version == "0.13.5"


def test_changed_dependency_input_invalidates_material_digest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n")
    first, _ = dependency_material(tmp_path)

    lock.write_text("version = 1\nrevision = 2\n")

    second, _ = dependency_material(tmp_path)
    assert first != second


def test_sbom_canonicalization_excludes_uv_transport_identity() -> None:
    def document(serial: str, timestamp: str) -> dict[str, object]:
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": serial,
            "metadata": {
                "timestamp": timestamp,
                "component": {
                    "type": "application",
                    "name": "fixture",
                    "version": "1.0",
                    "bom-ref": "fixture@1.0",
                },
            },
            "components": [
                {
                    "type": "library",
                    "name": "demo",
                    "version": "1.0",
                    "purl": "pkg:pypi/demo@1.0",
                }
            ],
        }

    first = validate_cached_sbom(
        document("urn:uuid:first", "2026-10-22T00:00:00Z"),  # type: ignore[arg-type]
        "digest",
        ("pyproject.toml", "uv.lock"),
    )
    second = validate_cached_sbom(
        document("urn:uuid:second", "2026-10-22T01:00:00Z"),  # type: ignore[arg-type]
        "digest",
        ("pyproject.toml", "uv.lock"),
    )

    assert first.sha256 == second.sha256
    assert "serialNumber" not in first.document
    assert "timestamp" not in first.document["metadata"]


def test_security_cache_is_independent_from_the_plan_fingerprint(
    tmp_path: Path,
) -> None:
    plan = PortablePlan(
        project=PortableProject(
            slug="stateforge",
            name="StateForge",
            release="MVP",
            revision=3,
            plan_source_branch="mvp/implementation",
        ),
        repositories=[
            PortableRepository(
                alias="agent",
                github_full_name="stateforge-org/stateforge-agent",
                responsibility="Agent runtime",
            )
        ],
        steps=[],
    )
    fingerprint = plan_fingerprint(plan)
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {
                "type": "application",
                "name": "stateforge-agent",
                "version": "0.1.0",
                "bom-ref": "stateforge-agent@0.1.0",
            }
        },
        "components": [],
    }
    sbom_sha = hashlib.sha256(
        json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    cache = {
        "plan_fingerprint": fingerprint,
        "generated_at": "2026-10-22T00:00:00+00:00",
        "repositories": [
            {
                "repository": "stateforge-org/stateforge-agent",
                "alias": "agent",
                "plan_source_branch": "mvp/implementation",
                "observed_commit_sha": "a" * 40,
                "generated_at": "2026-10-22T00:00:00+00:00",
                "dependency_material": {},
                "sbom": {
                    "sha256": sbom_sha,
                    "component_count": 0,
                    "document": document,
                },
            }
        ],
    }
    path = tmp_path / DEFAULT_PLAN_FILENAME
    path.write_text(append_security_cache(render_manifest(plan), cache))

    loaded = load_plan(str(tmp_path))

    assert loaded.fingerprint == fingerprint
    assert loaded.security_cache is not None
    assert loaded.security_cache["repositories"][0]["sbom"]["sha256"] == sbom_sha

    path.write_text(path.read_text().replace('"component_count"', '"changed"'))
    damaged = load_plan(str(tmp_path))
    assert damaged.fingerprint == fingerprint
    assert damaged.security_cache is None


def test_osv_querybatch_follows_per_component_pagination() -> None:
    client = OsvClient()
    requests: list[dict[str, object]] = []

    async def request(_client, method, path, **kwargs):
        if method == "POST":
            payload = kwargs["json"]
            requests.append(payload)
            if len(requests) == 1:
                return {
                    "results": [
                        {
                            "vulns": [{"id": "GHSA-one"}],
                            "next_page_token": "next",
                        }
                    ]
                }
            assert payload["queries"][0]["page_token"] == "next"  # noqa: S105
            return {"results": [{"vulns": [{"id": "CVE-two"}]}]}
        identifier = path.rsplit("/", 1)[-1]
        return {
            "id": identifier,
            "aliases": ["CVE-two", "GHSA-one"],
            "summary": "A paginated vulnerability",
            "database_specific": {"severity": "HIGH"},
            "affected": [
                {
                    "package": {"ecosystem": "PyPI", "name": "demo"},
                    "ranges": [{"events": [{"fixed": "1.1"}]}],
                },
                {
                    "package": {"ecosystem": "PyPI", "name": "other"},
                    "ranges": [{"events": [{"fixed": "9.9"}]}],
                },
            ],
        }

    client._request = request  # type: ignore[method-assign]
    findings, coverage = asyncio.run(
        client.assess(
            {
                "components": [
                    {
                        "name": "demo",
                        "version": "1.0",
                        "purl": "pkg:pypi/demo@1.0",
                    }
                ]
            }
        )
    )

    assert len(requests) == 2
    assert coverage == {
        "queryable_components": 1,
        "queried_components": 1,
        "failed_queries": 0,
    }
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["aliases"] == ["CVE-two", "GHSA-one"]
    assert findings[0]["fixed_versions"] == ["1.1"]


def test_osv_withdrawn_record_is_not_reported_as_a_current_vulnerability() -> None:
    client = OsvClient()

    async def request(_client, method, _path, **_kwargs):
        if method == "POST":
            return {"results": [{"vulns": [{"id": "CVE-withdrawn"}]}]}
        return {
            "id": "CVE-withdrawn",
            "summary": "Retracted advisory",
            "withdrawn": "2026-10-22T00:00:00Z",
        }

    client._request = request  # type: ignore[method-assign]
    findings, coverage = asyncio.run(
        client.assess(
            {
                "components": [
                    {
                        "name": "demo",
                        "version": "1.0",
                        "purl": "pkg:pypi/demo@1.0",
                    }
                ]
            }
        )
    )

    assert findings == []
    assert coverage["queried_components"] == 1


def test_osv_splits_large_component_sets_into_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OsvClient()
    batch_sizes: list[int] = []
    monkeypatch.setattr(osv_module, "OSV_BATCH_SIZE", 1)

    async def request(_client, method, _path, **kwargs):
        assert method == "POST"
        queries = kwargs["json"]["queries"]
        batch_sizes.append(len(queries))
        return {"results": [{} for _ in queries]}

    client._request = request  # type: ignore[method-assign]
    findings, coverage = asyncio.run(
        client.assess(
            {
                "components": [
                    {
                        "name": name,
                        "version": "1.0",
                        "purl": f"pkg:pypi/{name}@1.0",
                    }
                    for name in ("first", "second")
                ]
            }
        )
    )

    assert findings == []
    assert batch_sizes == [1, 1]
    assert coverage["queried_components"] == 2


def test_repository_refresh_persists_exact_head_dependency_and_codeql_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "b" * 40
    archive = _archive(
        {
            "pyproject.toml": "[project]\nname='fixture'\nversion='1.0'\n",
            "uv.lock": "version = 1\n",
        }
    )

    class GitHub:
        async def branch_head(self, _token, _full_name, _branch):
            return head

        async def repository_archive(self, _token, _full_name, commit, *, max_bytes):
            assert commit == head
            assert len(archive) < max_bytes
            return archive

        async def codeql_analyses(self, _token, _full_name, _branch):
            return [{"commit_sha": head, "category": "/language:python", "error": ""}]

        async def codeql_alerts(self, _token, _full_name, _branch):
            return [
                {
                    "number": 7,
                    "state": "open",
                    "html_url": "https://github.example/alert/7",
                    "rule": {
                        "id": "py/path-injection",
                        "description": "Path injection",
                        "security_severity_level": "critical",
                    },
                    "most_recent_instance": {
                        "commit_sha": head,
                        "location": {"path": "src/demo.py", "start_line": 4},
                    },
                }
            ]

    class Osv:
        async def assess(self, document):
            assert document["components"][0]["name"] == "demo"
            return (
                [
                    {
                        "kind": "dependency",
                        "id": "CVE-2026-1",
                        "summary": "Fixture dependency vulnerability",
                        "severity": "high",
                        "component": {
                            "name": "demo",
                            "version": "1.0",
                            "purl": "pkg:pypi/demo@1.0",
                            "package_purl": "pkg:pypi/demo",
                            "ecosystem": "pypi",
                        },
                    }
                ],
                {
                    "queryable_components": 1,
                    "queried_components": 1,
                    "failed_queries": 0,
                },
            )

    generated = 0

    def generate(root: Path):
        nonlocal generated
        generated += 1
        digest, inputs = dependency_material(root)
        return validate_cached_sbom(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "metadata": {
                    "component": {
                        "type": "application",
                        "name": "fixture",
                        "version": "1.0",
                        "bom-ref": "fixture@1.0",
                    }
                },
                "components": [
                    {
                        "type": "library",
                        "name": "demo",
                        "version": "1.0",
                        "purl": "pkg:pypi/demo@1.0",
                    }
                ],
            },
            digest,
            inputs,
        )

    class Adapter:
        adapter_id = "uv-lock-cyclonedx-v1"
        adapter_version = 1
        tool_name = "uv"
        tool_version = "0.13.5"

        def matches(self, _root: Path) -> bool:
            return True

        def dependency_material(self, root: Path) -> tuple[str, tuple[str, ...]]:
            return dependency_material(root)

        def generate(self, root: Path):
            return generate(root)

    monkeypatch.setattr(
        "stateforge_dpt.security_evidence.service.select_sbom_adapter",
        lambda _root: Adapter(),
    )
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    dpt = DPTService(database, SecretStore(data_dir))
    project = dpt.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = dpt.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="agent",
            github_full_name="stateforge-org/stateforge-agent",
        ),
    )
    service = SecurityEvidenceService(database, GitHub())  # type: ignore[arg-type]
    service.osv = Osv()  # type: ignore[assignment]

    def fail_unavailable(_repository, _branch, message):
        raise AssertionError(message)

    service._save_unavailable = fail_unavailable  # type: ignore[method-assign]

    first = asyncio.run(
        service.refresh_project(
            "token",
            project,
            [repository],
            force=True,
        )
    )
    asyncio.run(
        service.refresh_project(
            "token",
            project,
            [repository],
            force=True,
        )
    )

    assert generated == 1
    details = first["repositories"][0]
    assert details["security"]["state"] == "findings"
    assert details["security"]["observed_commit_sha"] == head
    assert details["security"]["highest_severity"] == "critical"
    assert details["security"]["dependency_findings"] == 1
    assert details["security"]["codeql_findings"] == 1
    assert details["sbom"]["component_count"] == 1


def test_portable_sbom_cache_import_preserves_generator_contract(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    dpt = DPTService(database, SecretStore(data_dir))
    project = dpt.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = dpt.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="agent",
            github_full_name="stateforge-org/stateforge-agent",
        ),
    )
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {
                "type": "application",
                "name": "agent",
                "version": "0.1.0",
                "bom-ref": "agent@0.1.0",
            }
        },
        "components": [],
    }
    digest = "d" * 64
    cache = {
        "repositories": [
            {
                "repository": "stateforge-org/stateforge-agent",
                "alias": "agent",
                "plan_source_branch": "mvp/implementation",
                "observed_commit_sha": "a" * 40,
                "generated_at": "2026-10-22T00:00:00+00:00",
                "dependency_material": {
                    "digest": digest,
                    "inputs": ["pyproject.toml", "uv.lock"],
                    "adapter": "uv-lock-cyclonedx-v1",
                    "adapter_version": 1,
                },
                "sbom": {"document": document},
            }
        ]
    }

    with database.connect() as connection:
        import_portable_security_cache(connection, str(project["project_id"]), cache)
        row = connection.execute(
            "SELECT * FROM repository_security_sboms WHERE repository_id = ?",
            (repository["repository_id"],),
        ).fetchone()

    assert row is not None
    assert row["status"] == "cached"
    assert row["dependency_material_digest"] == digest
    assert row["adapter_id"] == "uv-lock-cyclonedx-v1"
    assert row["adapter_version"] == 1


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (
            (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            "pending",
        ),
        (
            (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "stale",
        ),
    ],
)
def test_codeql_requires_successful_analysis_for_exact_head(
    tmp_path: Path,
    created_at: str,
    expected: str,
) -> None:
    class GitHub:
        async def codeql_analyses(self, _token, _full_name, _branch):
            return [{"commit_sha": "old", "created_at": created_at, "error": ""}]

        async def codeql_alerts(self, _token, _full_name, _branch):
            raise AssertionError(
                "Alerts must not be trusted without exact-head analysis"
            )

    database = Database(tmp_path / "dpt.sqlite3")
    database.initialize()
    service = SecurityEvidenceService(database, GitHub())  # type: ignore[arg-type]

    status, findings, _analyses = asyncio.run(
        service._codeql("token", "stateforge-org/agent", "mvp/implementation", "new")
    )

    assert status["state"] == expected
    assert status["current"] is False
    assert status["complete"] is False
    assert findings == []


def test_codeql_exact_head_error_and_missing_configuration_are_not_green(
    tmp_path: Path,
) -> None:
    class Failed:
        async def codeql_analyses(self, _token, _full_name, _branch):
            return [
                {"commit_sha": "head", "error": ""},
                {"commit_sha": "head", "error": "build failed"},
            ]

    class Missing:
        async def codeql_analyses(self, _token, _full_name, _branch):
            raise GitHubError("Code scanning is disabled", status_code=404)

    database = Database(tmp_path / "dpt.sqlite3")
    database.initialize()
    failed = SecurityEvidenceService(database, Failed())  # type: ignore[arg-type]
    missing = SecurityEvidenceService(database, Missing())  # type: ignore[arg-type]

    failed_status, _, _ = asyncio.run(
        failed._codeql("token", "stateforge-org/agent", "branch", "head")
    )
    missing_status, _, _ = asyncio.run(
        missing._codeql("token", "stateforge-org/agent", "branch", "head")
    )

    assert failed_status == {
        "state": "unavailable",
        "current": True,
        "complete": False,
        "configured": True,
        "message": "build failed",
    }
    assert missing_status["state"] == "not_configured"
    assert missing_status["configured"] is False


def test_security_refresh_requires_user_session_and_joins_inflight_task(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    started = threading.Event()

    async def refresh_project(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()
        return {}

    app.state.security_evidence.refresh_project = refresh_project

    with TestClient(app) as client:
        project = client.post(
            "/api/projects",
            json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
        ).json()
        client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": "agent",
                "github_full_name": "stateforge-org/stateforge-agent",
            },
        )
        endpoint = f"/api/projects/{project['project_id']}/security/refresh"

        assert client.post(endpoint).status_code == 401

        token = app.state.service.create_session(
            42,
            "octocat",
            "The Octocat",
            None,
            "user-access-token",
            None,
            None,
            None,
        )
        client.cookies.set(SESSION_COOKIE, token)
        first = client.post(endpoint)
        assert started.wait(timeout=1)
        second = client.post(endpoint)

        assert first.status_code == 202
        assert first.json() == {"state": "started", "joined": False}
        assert second.status_code == 202
        assert second.json() == {"state": "running", "joined": True}
