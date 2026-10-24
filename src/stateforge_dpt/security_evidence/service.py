"""Repository security-evidence orchestration and persistence."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

from stateforge_dpt.database import Database
from stateforge_dpt.github import GitHubClient, GitHubError
from stateforge_dpt.security_evidence.models import (
    SourceStatus,
    aggregate_state,
    highest_severity,
    normalize_severity,
    severity_counts,
)
from stateforge_dpt.security_evidence.osv import OsvClient, OsvError
from stateforge_dpt.security_evidence.sbom import (
    MAX_ARCHIVE_BYTES,
    SbomError,
    SbomResult,
    UnsupportedRepositoryError,
    safe_extract_zip,
    sbom_adapter,
    select_sbom_adapter,
    validate_cached_sbom,
)

SecurityReporter = Callable[[dict[str, object]], Awaitable[None]]
ASSESSMENT_TTL = timedelta(hours=6)
CODEQL_PENDING_GRACE = timedelta(minutes=30)


def utc_now() -> str:
    """Return a stable UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _timestamp(value: object) -> datetime | None:
    """Parse one external ISO timestamp without trusting its shape."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _source(value: dict[str, Any]) -> SourceStatus:
    return SourceStatus(
        state=str(value.get("state") or "unavailable"),
        current=bool(value.get("current")),
        complete=bool(value.get("complete")),
        configured=bool(value.get("configured")),
        message=str(value["message"]) if value.get("message") else None,
    )


def _normalize_codeql_alert(alert: dict[str, Any]) -> dict[str, Any]:
    rule_value = alert.get("rule")
    rule: dict[str, Any] = rule_value if isinstance(rule_value, dict) else {}
    instance_value = alert.get("most_recent_instance")
    instance: dict[str, Any] = (
        instance_value if isinstance(instance_value, dict) else {}
    )
    location_value = instance.get("location")
    location: dict[str, Any] = (
        location_value if isinstance(location_value, dict) else {}
    )
    severity = normalize_severity(
        rule.get("security_severity_level") or rule.get("severity")
    )
    return {
        "kind": "codeql",
        "id": str(rule.get("id") or alert.get("number") or "CodeQL alert"),
        "number": int(alert.get("number") or 0),
        "summary": str(rule.get("description") or rule.get("name") or "CodeQL alert"),
        "severity": severity.value,
        "security_severity_level": rule.get("security_severity_level"),
        "rule_severity": rule.get("severity"),
        "state": str(alert.get("state") or "open"),
        "dismissed_reason": alert.get("dismissed_reason"),
        "created_at": alert.get("created_at"),
        "updated_at": alert.get("updated_at"),
        "location": {
            "path": location.get("path"),
            "start_line": location.get("start_line"),
            "end_line": location.get("end_line"),
        },
        "source_url": alert.get("html_url"),
        "introduced_commit_sha": instance.get("commit_sha"),
    }


def portable_security_cache(
    database: Database,
    project_id: str,
    plan_fingerprint: str,
) -> dict[str, Any] | None:
    """Build the optional portable cache from validated local SBOM records."""
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT s.*, r.alias, r.github_full_name
            FROM repository_security_sboms s
            JOIN repositories r ON r.repository_id = s.repository_id
            WHERE r.project_id = ? AND s.status = 'current'
            ORDER BY r.alias, s.validated_at DESC
            """,
            (project_id,),
        ).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(str(row["repository_id"]), row)
    if not latest:
        return None
    return {
        "format": "stateforge-dpt-security-cache",
        "format_version": 1,
        "plan_fingerprint": plan_fingerprint,
        "generated_at": utc_now(),
        "repositories": [
            {
                "repository": str(row["github_full_name"]),
                "alias": str(row["alias"]),
                "plan_source_branch": str(row["plan_source_branch"]),
                "observed_commit_sha": str(row["observed_commit_sha"]),
                "dependency_material": {
                    "algorithm": "sha256",
                    "digest": str(row["dependency_material_digest"]),
                    "inputs": json.loads(str(row["dependency_material_inputs"])),
                    "adapter": str(row["adapter_id"]),
                    "adapter_version": int(row["adapter_version"]),
                    "tool": str(row["tool_name"]),
                    "tool_version": str(row["tool_version"]),
                },
                "sbom": {
                    "format": str(row["sbom_format"]),
                    "spec_version": str(row["sbom_spec_version"]),
                    "sha256": str(row["sbom_sha256"]),
                    "component_count": int(row["component_count"]),
                    "document": json.loads(str(row["sbom_json"])),
                },
                "generated_at": str(row["generated_at"]),
            }
            for row in latest.values()
        ],
    }


def import_portable_security_cache(
    connection: sqlite3.Connection,
    project_id: str,
    cache: dict[str, Any] | None,
) -> None:
    """Import validated portable SBOMs as reusable, not-yet-assessed evidence."""
    if not cache:
        return
    repositories = {
        str(row["github_full_name"]).casefold(): row
        for row in connection.execute(
            "SELECT * FROM repositories WHERE project_id = ?", (project_id,)
        ).fetchall()
    }
    for entry in cache.get("repositories", []):
        if not isinstance(entry, dict):
            continue
        repository = repositories.get(str(entry.get("repository") or "").casefold())
        dependency = entry.get("dependency_material")
        sbom = entry.get("sbom")
        if (
            repository is None
            or not isinstance(dependency, dict)
            or not isinstance(sbom, dict)
        ):
            continue
        adapter_id = str(dependency.get("adapter") or "")
        try:
            adapter_version = int(str(dependency.get("adapter_version")))
        except (TypeError, ValueError):
            continue
        adapter = sbom_adapter(adapter_id, adapter_version)
        if adapter is None:
            continue
        inputs = tuple(str(value) for value in dependency.get("inputs", []))
        document = sbom.get("document")
        if not isinstance(document, dict):
            continue
        try:
            result = validate_cached_sbom(
                document,
                str(dependency.get("digest") or ""),
                inputs,
                adapter_id=adapter.adapter_id,
                adapter_version=adapter.adapter_version,
                tool_name=adapter.tool_name,
                tool_version=adapter.tool_version,
            )
        except SbomError:
            continue
        timestamp = str(entry.get("generated_at") or utc_now())
        _save_sbom(
            connection,
            str(repository["repository_id"]),
            str(entry.get("plan_source_branch") or "mvp/implementation"),
            str(entry.get("observed_commit_sha") or ""),
            result,
            timestamp,
            status="cached",
        )


def _save_sbom(
    connection: sqlite3.Connection,
    repository_id: str,
    branch: str,
    commit_sha: str,
    result: SbomResult,
    timestamp: str,
    *,
    status: str = "current",
) -> None:
    connection.execute(
        """
        INSERT INTO repository_security_sboms(
            repository_id, plan_source_branch, observed_commit_sha,
            dependency_material_digest, dependency_material_inputs, adapter_id,
            adapter_version, tool_name, tool_version, sbom_format,
            sbom_spec_version, sbom_sha256, sbom_json, component_count,
            third_party_component_count, queryable_component_count, coverage_json,
            generator_diagnostics_json, generated_at, validated_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository_id, dependency_material_digest, adapter_id,
                    adapter_version)
        DO UPDATE SET plan_source_branch = excluded.plan_source_branch,
            observed_commit_sha = excluded.observed_commit_sha,
            sbom_sha256 = excluded.sbom_sha256, sbom_json = excluded.sbom_json,
            component_count = excluded.component_count,
            third_party_component_count = excluded.third_party_component_count,
            queryable_component_count = excluded.queryable_component_count,
            coverage_json = excluded.coverage_json,
            generator_diagnostics_json = excluded.generator_diagnostics_json,
            validated_at = excluded.validated_at, status = excluded.status,
            error_code = NULL, error_message = NULL
        """,
        (
            repository_id,
            branch,
            commit_sha,
            result.dependency_digest,
            _json(list(result.dependency_inputs)),
            result.adapter_id,
            result.adapter_version,
            result.tool_name,
            result.tool_version,
            "cyclonedx-json",
            "1.5",
            result.sha256,
            result.canonical_json,
            result.component_count,
            result.third_party_component_count,
            result.queryable_component_count,
            _json(result.coverage),
            _json(result.generator_diagnostics),
            timestamp,
            timestamp,
            status,
        ),
    )


class SecurityEvidenceService:
    """Generate, retrieve, and summarize repository security evidence."""

    def __init__(self, database: Database, github: GitHubClient) -> None:
        """Bind persistence and GitHub clients used by assessments."""
        self.database = database
        self.github = github
        self.osv = OsvClient()

    def repository_summaries(
        self, project_id: str, branch: str
    ) -> dict[str, dict[str, Any]]:
        """Return compact status cards without starting external work."""
        with self.database.connect() as connection:
            repositories = connection.execute(
                "SELECT repository_id FROM repositories WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            rows = connection.execute(
                """
                SELECT status.* FROM repository_security_status status
                JOIN repositories r ON r.repository_id = status.repository_id
                WHERE r.project_id = ?
                """,
                (project_id,),
            ).fetchall()
        by_id = {str(row["repository_id"]): row for row in rows}
        result: dict[str, dict[str, Any]] = {}
        for repository in repositories:
            repository_id = str(repository["repository_id"])
            row = by_id.get(repository_id)
            if row is None:
                result[repository_id] = {
                    "branch": branch,
                    "state": "pending",
                    "highest_severity": "none",
                    "dependency_findings": 0,
                    "codeql_findings": 0,
                    "observed_commit_sha": None,
                    "refreshed_at": None,
                    "coverage": {},
                    "message": "Security evidence has not been assessed yet",
                    "dependency_status": {
                        "state": "pending",
                        "current": False,
                        "complete": False,
                        "configured": True,
                    },
                    "codeql_status": {
                        "state": "pending",
                        "current": False,
                        "complete": False,
                        "configured": True,
                    },
                }
                continue
            dependency = json.loads(str(row["dependency_status_json"]))
            codeql = json.loads(str(row["codeql_status_json"]))
            state = str(row["state"])
            refreshed_at = str(row["refreshed_at"])
            if (
                state == "no_known_findings"
                and datetime.now(UTC) - datetime.fromisoformat(refreshed_at)
                >= ASSESSMENT_TTL
            ):
                state = "stale"
            result[repository_id] = {
                "branch": str(row["plan_source_branch"]),
                "state": state,
                "highest_severity": str(row["highest_severity"]),
                "dependency_findings": int(row["dependency_findings"]),
                "codeql_findings": int(row["codeql_findings"]),
                "observed_commit_sha": row["observed_commit_sha"],
                "refreshed_at": refreshed_at,
                "coverage": json.loads(str(row["coverage_json"])),
                "message": dependency.get("message") or codeql.get("message"),
                "dependency_status": dependency,
                "codeql_status": codeql,
            }
        return result

    def details(self, project_id: str, branch: str) -> dict[str, Any]:
        """Return persisted findings, provenance, and project totals."""
        summaries = self.repository_summaries(project_id, branch)
        with self.database.connect() as connection:
            repositories = connection.execute(
                """
                SELECT * FROM repositories
                WHERE project_id = ? ORDER BY created_at, alias
                """,
                (project_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            all_findings: list[dict[str, Any]] = []
            for repository in repositories:
                repository_id = str(repository["repository_id"])
                head = summaries[repository_id]["observed_commit_sha"]
                sbom = None
                assessment = None
                codeql = None
                if head:
                    sbom = connection.execute(
                        """
                        SELECT * FROM repository_security_sboms
                        WHERE repository_id = ? AND observed_commit_sha = ?
                        ORDER BY validated_at DESC LIMIT 1
                        """,
                        (repository_id, head),
                    ).fetchone()
                    codeql = connection.execute(
                        """
                        SELECT * FROM repository_codeql_snapshots
                        WHERE repository_id = ? AND observed_commit_sha = ?
                        ORDER BY observed_at DESC LIMIT 1
                        """,
                        (repository_id, head),
                    ).fetchone()
                if (
                    sbom
                    and summaries[repository_id]["dependency_status"]["state"]
                    == "current"
                ):
                    assessment = connection.execute(
                        """
                        SELECT * FROM repository_vulnerability_assessments
                        WHERE repository_id = ? AND sbom_sha256 = ?
                        ORDER BY assessed_at DESC LIMIT 1
                        """,
                        (repository_id, sbom["sbom_sha256"]),
                    ).fetchone()
                dependency_findings = (
                    json.loads(str(assessment["findings_json"])) if assessment else []
                )
                codeql_findings = (
                    json.loads(str(codeql["alerts_json"])) if codeql else []
                )
                all_findings.extend([*dependency_findings, *codeql_findings])
                result.append(
                    {
                        **dict(repository),
                        "security": summaries[repository_id],
                        "dependency_findings": dependency_findings,
                        "codeql_findings": codeql_findings,
                        "sbom": (
                            {
                                "sha256": str(sbom["sbom_sha256"]),
                                "format": str(sbom["sbom_format"]),
                                "spec_version": str(sbom["sbom_spec_version"]),
                                "component_count": int(sbom["component_count"]),
                                "queryable_component_count": int(
                                    sbom["queryable_component_count"]
                                ),
                                "dependency_material_digest": str(
                                    sbom["dependency_material_digest"]
                                ),
                                "adapter": str(sbom["adapter_id"]),
                                "adapter_version": int(sbom["adapter_version"]),
                                "tool": str(sbom["tool_name"]),
                                "tool_version": str(sbom["tool_version"]),
                                "generator": json.loads(
                                    str(sbom["generator_diagnostics_json"])
                                ),
                            }
                            if sbom
                            else None
                        ),
                    }
                )
        counts = severity_counts(all_findings)
        return {
            "summary": {
                "repositories": len(result),
                "fully_assessed": sum(
                    item["security"]["state"] in {"no_known_findings", "findings"}
                    for item in result
                ),
                "with_findings": sum(
                    item["security"]["state"] == "findings" for item in result
                ),
                "severity_counts": counts,
            },
            "repositories": result,
        }

    async def refresh_project(
        self,
        access_token: str,
        project: dict[str, Any],
        repositories: list[dict[str, Any]],
        report: SecurityReporter | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Refresh every repository with bounded concurrency and isolated failures."""
        branch = str(project.get("plan_source_branch") or "mvp/implementation")
        limit = asyncio.Semaphore(3)

        async def refresh(index: int, repository: dict[str, Any]) -> None:
            async with limit:
                if report:
                    await report(
                        {
                            "phase": "security",
                            "title": f"Assessing {repository['alias']}",
                            "detail": (
                                "Resolving exact source, dependencies, OSV, and CodeQL."
                            ),
                            "repository": repository["alias"],
                            "repository_index": index,
                            "repository_count": len(repositories),
                        }
                    )
                try:
                    await self._refresh_repository(
                        access_token, repository, branch, force=force
                    )
                except Exception as error:
                    self._save_unavailable(repository, branch, str(error))

        await asyncio.gather(
            *(
                refresh(index, repository)
                for index, repository in enumerate(repositories, 1)
            )
        )
        return self.details(str(project["project_id"]), branch)

    def _fresh_status(self, repository_id: str, branch: str, commit_sha: str) -> bool:
        """Avoid repeating a complete automatic assessment inside its TTL."""
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT state, refreshed_at FROM repository_security_status
                WHERE repository_id = ? AND plan_source_branch = ?
                  AND observed_commit_sha = ?
                """,
                (repository_id, branch, commit_sha),
            ).fetchone()
        if row is None or str(row["state"]) in {"pending", "stale", "unavailable"}:
            return False
        refreshed = datetime.fromisoformat(str(row["refreshed_at"]))
        return datetime.now(UTC) - refreshed < ASSESSMENT_TTL

    async def _refresh_repository(
        self,
        access_token: str,
        repository: dict[str, Any],
        branch: str,
        *,
        force: bool,
    ) -> None:
        repository_id = str(repository["repository_id"])
        full_name = str(repository["github_full_name"])
        timestamp = utc_now()
        head = await self.github.branch_head(access_token, full_name, branch)
        if not force and self._fresh_status(repository_id, branch, head):
            return
        dependency_status: dict[str, Any]
        dependency_findings: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {}

        try:
            archive = await self.github.repository_archive(
                access_token, full_name, head, max_bytes=MAX_ARCHIVE_BYTES
            )
            with tempfile.TemporaryDirectory(
                prefix="stateforge-dpt-security-"
            ) as value:
                root = safe_extract_zip(archive, Path(value))
                adapter = select_sbom_adapter(root)
                digest, inputs = await asyncio.to_thread(
                    adapter.dependency_material, root
                )
                cached = self._cached_sbom(
                    repository_id,
                    digest,
                    adapter.adapter_id,
                    adapter.adapter_version,
                )
                if cached is None:
                    sbom = await asyncio.to_thread(adapter.generate, root)
                    if (
                        sbom.dependency_digest != digest
                        or sbom.dependency_inputs != inputs
                        or sbom.adapter_id != adapter.adapter_id
                        or sbom.adapter_version != adapter.adapter_version
                        or sbom.tool_name != adapter.tool_name
                        or sbom.tool_version != adapter.tool_version
                    ):
                        raise SbomError(
                            "SBOM generator output does not match its selected "
                            "adapter contract"
                        )
                    with self.database.connect() as connection:
                        _save_sbom(
                            connection,
                            repository_id,
                            branch,
                            head,
                            sbom,
                            timestamp,
                        )
                else:
                    sbom = validate_cached_sbom(
                        json.loads(str(cached["sbom_json"])),
                        digest,
                        inputs,
                        adapter_id=adapter.adapter_id,
                        adapter_version=adapter.adapter_version,
                        tool_name=adapter.tool_name,
                        tool_version=adapter.tool_version,
                        generator_diagnostics=json.loads(
                            str(cached["generator_diagnostics_json"])
                        ),
                    )
                    with self.database.connect() as connection:
                        _save_sbom(
                            connection,
                            repository_id,
                            branch,
                            head,
                            sbom,
                            timestamp,
                        )
            dependency_findings, osv_coverage = await self.osv.assess(sbom.document)
            coverage = {**sbom.coverage, **osv_coverage}
            complete = (
                sbom.queryable_component_count == sbom.third_party_component_count
                and int(osv_coverage.get("queried_components", 0))
                == sbom.queryable_component_count
                and int(osv_coverage.get("failed_queries", 0)) == 0
            )
            dependency_status = {
                "state": "current",
                "current": True,
                "complete": complete,
                "configured": True,
                "message": None
                if complete
                else "Some SBOM components could not be assessed",
            }
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO repository_vulnerability_assessments(
                        repository_id, sbom_sha256, source, source_version,
                        findings_json, severity_counts_json, coverage_json,
                        assessed_at, expires_at, status
                    ) VALUES (?, ?, 'osv', 'v1', ?, ?, ?, ?, ?, 'current')
                    ON CONFLICT(repository_id, sbom_sha256, source)
                    DO UPDATE SET findings_json = excluded.findings_json,
                        severity_counts_json = excluded.severity_counts_json,
                        coverage_json = excluded.coverage_json,
                        assessed_at = excluded.assessed_at,
                        expires_at = excluded.expires_at, status = 'current',
                        error_code = NULL, error_message = NULL
                    """,
                    (
                        repository_id,
                        sbom.sha256,
                        _json(dependency_findings),
                        _json(severity_counts(dependency_findings)),
                        _json(coverage),
                        timestamp,
                        (datetime.now(UTC) + ASSESSMENT_TTL)
                        .replace(microsecond=0)
                        .isoformat(),
                    ),
                )
        except UnsupportedRepositoryError as error:
            dependency_status = {
                "state": "not_configured",
                "current": False,
                "complete": False,
                "configured": False,
                "message": str(error),
            }
        except (SbomError, OsvError, httpx.HTTPError) as error:
            dependency_status = {
                "state": "unavailable",
                "current": False,
                "complete": False,
                "configured": True,
                "message": str(error),
            }

        codeql_status, codeql_findings, analyses = await self._codeql(
            access_token, full_name, branch, head
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO repository_codeql_snapshots(
                    repository_id, plan_source_branch, observed_commit_sha,
                    analyses_json, alerts_json, severity_counts_json, observed_at,
                    status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id, plan_source_branch, observed_commit_sha)
                DO UPDATE SET analyses_json = excluded.analyses_json,
                    alerts_json = excluded.alerts_json,
                    severity_counts_json = excluded.severity_counts_json,
                    observed_at = excluded.observed_at, status = excluded.status,
                    error_message = excluded.error_message
                """,
                (
                    repository_id,
                    branch,
                    head,
                    _json(analyses),
                    _json(codeql_findings),
                    _json(severity_counts(codeql_findings)),
                    timestamp,
                    codeql_status["state"],
                    codeql_status.get("message"),
                ),
            )
        all_findings = [*dependency_findings, *codeql_findings]
        state = aggregate_state(
            _source(dependency_status),
            _source(codeql_status),
            len(all_findings),
            coverage_complete=bool(dependency_status.get("complete")),
        )
        self._save_status(
            repository_id,
            branch,
            head,
            state.value,
            highest_severity(all_findings).value,
            dependency_status,
            codeql_status,
            all_findings,
            coverage,
            len(dependency_findings),
            len(codeql_findings),
            timestamp,
        )

    def _cached_sbom(
        self,
        repository_id: str,
        digest: str,
        adapter_id: str,
        adapter_version: int,
    ) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return cast(
                sqlite3.Row | None,
                connection.execute(
                    """
                    SELECT * FROM repository_security_sboms
                    WHERE repository_id = ? AND dependency_material_digest = ?
                      AND adapter_id = ? AND adapter_version = ?
                    ORDER BY validated_at DESC LIMIT 1
                    """,
                    (repository_id, digest, adapter_id, adapter_version),
                ).fetchone(),
            )

    async def _codeql(
        self,
        access_token: str,
        full_name: str,
        branch: str,
        head: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            analyses = await self.github.codeql_analyses(
                access_token, full_name, branch
            )
        except GitHubError as error:
            if error.status_code == 404:
                return (
                    {
                        "state": "not_configured",
                        "current": False,
                        "complete": False,
                        "configured": False,
                        "message": "CodeQL has no analysis for this repository",
                    },
                    [],
                    [],
                )
            return (
                {
                    "state": "unavailable",
                    "current": False,
                    "complete": False,
                    "configured": True,
                    "message": str(error),
                },
                [],
                [],
            )
        exact_head = [
            item for item in analyses if str(item.get("commit_sha") or "") == head
        ]
        exact_errors = [
            str(item.get("error") or "CodeQL analysis failed")
            for item in exact_head
            if str(item.get("error") or "").strip()
        ]
        if exact_errors:
            return (
                {
                    "state": "unavailable",
                    "current": True,
                    "complete": False,
                    "configured": True,
                    "message": "; ".join(exact_errors),
                },
                [],
                analyses,
            )
        exact = exact_head
        if not exact:
            if analyses:
                observed = [
                    timestamp
                    for item in analyses
                    if (timestamp := _timestamp(item.get("created_at"))) is not None
                ]
                state = (
                    "pending"
                    if observed
                    and datetime.now(UTC) - max(observed) < CODEQL_PENDING_GRACE
                    else "stale"
                )
                return (
                    {
                        "state": state,
                        "current": False,
                        "complete": False,
                        "configured": True,
                        "message": (
                            "CodeQL has not completed for the current branch head"
                            if state == "pending"
                            else "CodeQL evidence exists only for an older branch head"
                        ),
                    },
                    [],
                    analyses,
                )
            return (
                {
                    "state": "not_configured",
                    "current": False,
                    "complete": False,
                    "configured": False,
                    "message": "CodeQL has no analysis for this repository",
                },
                [],
                analyses,
            )
        try:
            alerts = await self.github.codeql_alerts(access_token, full_name, branch)
        except GitHubError as error:
            return (
                {
                    "state": "unavailable",
                    "current": True,
                    "complete": False,
                    "configured": True,
                    "message": str(error),
                },
                [],
                analyses,
            )
        findings = [_normalize_codeql_alert(alert) for alert in alerts]
        return (
            {
                "state": "current",
                "current": True,
                "complete": True,
                "configured": True,
                "message": None,
            },
            findings,
            analyses,
        )

    def _save_unavailable(
        self, repository: dict[str, Any], branch: str, message: str
    ) -> None:
        status = {
            "state": "unavailable",
            "current": False,
            "complete": False,
            "configured": True,
            "message": message,
        }
        self._save_status(
            str(repository["repository_id"]),
            branch,
            None,
            "unavailable",
            "none",
            status,
            status,
            [],
            {},
            0,
            0,
            utc_now(),
        )

    def _save_status(
        self,
        repository_id: str,
        branch: str,
        commit_sha: str | None,
        state: str,
        severity: str,
        dependency_status: dict[str, Any],
        codeql_status: dict[str, Any],
        findings: list[dict[str, Any]],
        coverage: dict[str, Any],
        dependency_count: int,
        codeql_count: int,
        timestamp: str,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO repository_security_status(
                    repository_id, plan_source_branch, observed_commit_sha, state,
                    highest_severity, dependency_status_json, codeql_status_json,
                    severity_counts_json, coverage_json, dependency_findings,
                    codeql_findings, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id) DO UPDATE SET
                    plan_source_branch = excluded.plan_source_branch,
                    observed_commit_sha = excluded.observed_commit_sha,
                    state = excluded.state,
                    highest_severity = excluded.highest_severity,
                    dependency_status_json = excluded.dependency_status_json,
                    codeql_status_json = excluded.codeql_status_json,
                    severity_counts_json = excluded.severity_counts_json,
                    coverage_json = excluded.coverage_json,
                    dependency_findings = excluded.dependency_findings,
                    codeql_findings = excluded.codeql_findings,
                    refreshed_at = excluded.refreshed_at
                """,
                (
                    repository_id,
                    branch,
                    commit_sha,
                    state,
                    severity,
                    _json(dependency_status),
                    _json(codeql_status),
                    _json(severity_counts(findings)),
                    _json(coverage),
                    dependency_count,
                    codeql_count,
                    timestamp,
                ),
            )
