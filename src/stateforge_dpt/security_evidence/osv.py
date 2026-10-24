"""OSV dependency-vulnerability client and finding normalization."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import httpx

from stateforge_dpt.security_evidence.models import normalize_severity

OSV_API_URL = "https://api.osv.dev"
MAX_OSV_QUERIES = 10_000
MAX_OSV_PAGES = 100
OSV_BATCH_SIZE = 1_000


class OsvError(RuntimeError):
    """Raised when OSV evidence cannot be obtained reliably."""


def _package_purl(purl: str) -> str:
    """Remove the component version while preserving package qualifiers."""
    fragmentless = purl.split("#", 1)[0]
    base, separator, qualifiers = fragmentless.partition("?")
    package = base.split("@", 1)[0]
    return f"{package}{separator}{qualifiers}" if separator else package


def _components(document: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    values = document.get("components")
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        purl = value.get("purl")
        version = value.get("version")
        if not isinstance(purl, str) or not isinstance(version, str) or not version:
            continue
        result.append(
            {
                "purl": purl,
                "package_purl": _package_purl(purl),
                "name": str(value.get("name") or purl),
                "version": version,
                "ecosystem": purl[4:].split("/", 1)[0].split(":", 1)[0],
            }
        )
    return result[:MAX_OSV_QUERIES]


def _affected_component(affected: dict[str, Any], component: dict[str, str]) -> bool:
    """Match an OSV affected package to the exact component being reported."""
    package = affected.get("package")
    if not isinstance(package, dict):
        return False
    purl = package.get("purl")
    if isinstance(purl, str) and purl:
        return _package_purl(purl).casefold() == component["package_purl"].casefold()
    ecosystem = str(package.get("ecosystem") or "").casefold()
    name = str(package.get("name") or "").casefold()
    return (
        bool(ecosystem)
        and bool(name)
        and ecosystem == component["ecosystem"].casefold()
        and name == component["name"].casefold()
    )


def _fixed_versions(record: dict[str, Any], component: dict[str, str]) -> list[str]:
    """Return fixes only from ranges belonging to the displayed component."""
    fixed: set[str] = set()
    affected = record.get("affected")
    if not isinstance(affected, list):
        return []
    for item in affected:
        if not isinstance(item, dict):
            continue
        if not _affected_component(item, component):
            continue
        ranges = item.get("ranges")
        if not isinstance(ranges, list):
            continue
        for value_range in ranges:
            if not isinstance(value_range, dict):
                continue
            events = value_range.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if isinstance(event, dict) and isinstance(event.get("fixed"), str):
                    fixed.add(str(event["fixed"]))
    return sorted(fixed)


def _record_severity(record: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    values = record.get("severity")
    original = (
        [
            {
                "type": str(value.get("type") or ""),
                "score": str(value.get("score") or ""),
            }
            for value in values
            if isinstance(value, dict)
        ]
        if isinstance(values, list)
        else []
    )
    candidates: list[object] = []
    for key in ("database_specific", "ecosystem_specific"):
        details = record.get(key)
        if isinstance(details, dict):
            candidates.extend((details.get("severity"), details.get("cvss_severity")))
    normalized = [normalize_severity(value) for value in candidates if value]
    strongest = max(
        normalized,
        key=lambda item: ["none", "unknown", "low", "medium", "high", "critical"].index(
            item.value
        ),
        default=normalize_severity(None),
    )
    return strongest.value, original


class OsvClient:
    """Query OSV with bounded retries and normalize advisory records."""

    def __init__(self, api_url: str = OSV_API_URL) -> None:
        """Configure the OSV API origin."""
        self.api_url = api_url.rstrip("/")

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        for attempt in range(3):
            try:
                response = await client.request(
                    method, f"{self.api_url}{path}", **kwargs
                )
                if (
                    response.status_code == 429 or response.status_code >= 500
                ) and attempt < 2:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as error:
                    raise OsvError("OSV returned invalid JSON") from error
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == 2:
                    raise OsvError(f"OSV request failed: {error}") from error
                await asyncio.sleep(0.25 * (2**attempt))
            except httpx.HTTPStatusError as error:
                raise OsvError(
                    f"OSV returned HTTP {error.response.status_code}"
                ) from error
        raise OsvError("OSV request failed after retries")

    async def assess(
        self,
        document: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Return normalized, alias-deduplicated findings and query coverage."""
        components = _components(document)
        coverage = {
            "queryable_components": len(components),
            "queried_components": 0,
            "failed_queries": 0,
            "withdrawn_advisories": 0,
        }
        if not components:
            return [], coverage
        queries = [
            {
                "package": {"purl": item["package_purl"]},
                "version": item["version"],
            }
            for item in components
        ]
        async with httpx.AsyncClient(timeout=20) as client:
            results: list[object | None] = [None] * len(queries)
            failed_components: set[int] = set()
            successful_components: set[int] = set()
            for offset in range(0, len(queries), OSV_BATCH_SIZE):
                query_batch = queries[offset : offset + OSV_BATCH_SIZE]
                try:
                    payload = await self._request(
                        client,
                        "POST",
                        "/v1/querybatch",
                        json={"queries": query_batch},
                    )
                except OsvError:
                    failed_components.update(range(offset, offset + len(query_batch)))
                    continue
                values = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(values, list) or len(values) != len(query_batch):
                    failed_components.update(range(offset, offset + len(query_batch)))
                    continue
                for relative_index, value in enumerate(values):
                    index = offset + relative_index
                    if not isinstance(value, dict):
                        failed_components.add(index)
                        continue
                    results[index] = value
                    successful_components.add(index)
            component_ids: list[set[str]] = [set() for _ in components]
            pending: list[tuple[int, dict[str, Any], str]] = []

            def collect(
                index: int,
                query: dict[str, Any],
                result: object,
            ) -> None:
                vulns = result.get("vulns") if isinstance(result, dict) else None
                component_ids[index].update(
                    str(value["id"])
                    for value in (vulns if isinstance(vulns, list) else [])
                    if isinstance(value, dict) and value.get("id")
                )
                token = (
                    result.get("next_page_token") if isinstance(result, dict) else None
                )
                if isinstance(token, str) and token:
                    pending.append((index, query, token))

            for index, (query, result) in enumerate(zip(queries, results, strict=True)):
                if result is not None:
                    collect(index, query, result)

            page = 0
            while pending:
                page += 1
                if page > MAX_OSV_PAGES:
                    raise OsvError("OSV pagination exceeded the safety limit")
                current, pending = pending, []
                for offset in range(0, len(current), OSV_BATCH_SIZE):
                    pending_batch = current[offset : offset + OSV_BATCH_SIZE]
                    page_queries = [
                        {**query, "page_token": token}
                        for _, query, token in pending_batch
                    ]
                    try:
                        page_payload = await self._request(
                            client,
                            "POST",
                            "/v1/querybatch",
                            json={"queries": page_queries},
                        )
                    except OsvError:
                        failed_components.update(
                            index for index, _query, _token in pending_batch
                        )
                        continue
                    page_results = (
                        page_payload.get("results")
                        if isinstance(page_payload, dict)
                        else None
                    )
                    if not isinstance(page_results, list) or len(page_results) != len(
                        pending_batch
                    ):
                        failed_components.update(
                            index for index, _query, _token in pending_batch
                        )
                        continue
                    for (index, query, _), result in zip(
                        pending_batch, page_results, strict=True
                    ):
                        collect(index, query, result)

            coverage["queried_components"] = len(
                successful_components - failed_components
            )
            coverage["failed_queries"] = len(failed_components)
            ids = set().union(*component_ids)
            semaphore = asyncio.Semaphore(8)

            async def hydrate(identifier: str) -> tuple[str, dict[str, Any]]:
                async with semaphore:
                    record = await self._request(
                        client,
                        "GET",
                        f"/v1/vulns/{quote(identifier, safe='')}",
                    )
                    if not isinstance(record, dict):
                        raise OsvError(
                            f"OSV returned an invalid record for {identifier}"
                        )
                    return identifier, record

            hydrated_results = await asyncio.gather(
                *(hydrate(value) for value in ids), return_exceptions=True
            )
            hydrated: dict[str, dict[str, Any]] = {}
            failed_identifiers: set[str] = set()
            for identifier, result in zip(ids, hydrated_results, strict=True):
                if isinstance(result, BaseException):
                    failed_identifiers.add(identifier)
                else:
                    hydrated[result[0]] = result[1]
            if failed_identifiers:
                for index, identifiers in enumerate(component_ids):
                    if identifiers & failed_identifiers:
                        failed_components.add(index)
                coverage["queried_components"] = len(
                    successful_components - failed_components
                )
                coverage["failed_queries"] = len(failed_components)

        parent: dict[str, str] = {}

        def find(value: str) -> str:
            parent.setdefault(value, value)
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        for identifier, record in hydrated.items():
            aliases = [identifier, *[str(value) for value in record.get("aliases", [])]]
            for alias in aliases:
                union(identifier, alias)
        records_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
        aliases_by_root: dict[str, set[str]] = defaultdict(set)
        for identifier, record in hydrated.items():
            root = find(identifier)
            records_by_root[root].append(record)
            aliases_by_root[root].update(
                [identifier, *[str(value) for value in record.get("aliases", [])]]
            )

        findings: list[dict[str, Any]] = []
        emitted: set[tuple[str, str]] = set()
        withdrawn_roots: set[str] = set()
        for component, identifiers in zip(components, component_ids, strict=True):
            for identifier in identifiers:
                if identifier not in hydrated:
                    continue
                root = find(identifier)
                key = (root, component["purl"])
                if key in emitted:
                    continue
                emitted.add(key)
                records = records_by_root[root]
                active_records = [item for item in records if not item.get("withdrawn")]
                if not active_records:
                    withdrawn_roots.add(root)
                    continue
                record = max(
                    active_records,
                    key=lambda item: str(item.get("modified") or ""),
                )
                severity, original = _record_severity(record)
                aliases = sorted(aliases_by_root[root])
                display_id = next(
                    (value for value in aliases if value.startswith("CVE-")),
                    next(
                        (value for value in aliases if value.startswith("GHSA-")),
                        str(record.get("id") or root),
                    ),
                )
                references = record.get("references")
                findings.append(
                    {
                        "kind": "dependency",
                        "id": display_id,
                        "aliases": aliases,
                        "summary": str(record.get("summary") or display_id),
                        "details": str(record.get("details") or "")[:20_000],
                        "severity": severity,
                        "source_severity": original,
                        "component": component,
                        "fixed_versions": _fixed_versions(record, component),
                        "published": record.get("published"),
                        "modified": record.get("modified"),
                        "withdrawn": record.get("withdrawn"),
                        "references": [
                            dict(value)
                            for value in (
                                references if isinstance(references, list) else []
                            )
                            if isinstance(value, dict)
                        ][:50],
                        "source_url": f"https://osv.dev/vulnerability/{record.get('id')}",
                    }
                )
        findings.sort(
            key=lambda item: (
                ["critical", "high", "medium", "low", "unknown"].index(
                    str(item["severity"])
                ),
                str(item["id"]),
                str(item["component"]["purl"]),
            )
        )
        coverage["withdrawn_advisories"] = len(withdrawn_roots)
        return findings, coverage
