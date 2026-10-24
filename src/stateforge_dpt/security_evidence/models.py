"""Typed security evidence and aggregate-state rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SecurityState(StrEnum):
    """Complete repository assessment states exposed by the API."""

    NO_KNOWN_FINDINGS = "no_known_findings"
    FINDINGS = "findings"
    PENDING = "pending"
    STALE = "stale"
    PARTIAL = "partial"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"


class SecuritySeverity(StrEnum):
    """Normalized finding severities in descending precedence."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"
    NONE = "none"


SEVERITY_ORDER = {
    SecuritySeverity.NONE: 0,
    SecuritySeverity.UNKNOWN: 1,
    SecuritySeverity.LOW: 2,
    SecuritySeverity.MEDIUM: 3,
    SecuritySeverity.HIGH: 4,
    SecuritySeverity.CRITICAL: 5,
}


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """Current state of one independent evidence source."""

    state: str
    current: bool
    complete: bool
    configured: bool
    message: str | None = None


def normalize_severity(value: object) -> SecuritySeverity:
    """Normalize an untrusted severity label without understating unknown data."""
    label = str(value or "").strip().casefold()
    aliases = {
        "critical": SecuritySeverity.CRITICAL,
        "high": SecuritySeverity.HIGH,
        "error": SecuritySeverity.HIGH,
        "medium": SecuritySeverity.MEDIUM,
        "moderate": SecuritySeverity.MEDIUM,
        "warning": SecuritySeverity.MEDIUM,
        "low": SecuritySeverity.LOW,
        "note": SecuritySeverity.LOW,
        "none": SecuritySeverity.NONE,
    }
    return aliases.get(label, SecuritySeverity.UNKNOWN)


def highest_severity(findings: list[dict[str, Any]]) -> SecuritySeverity:
    """Return the strongest normalized severity across all findings."""
    return max(
        (normalize_severity(item.get("severity")) for item in findings),
        key=SEVERITY_ORDER.__getitem__,
        default=SecuritySeverity.NONE,
    )


def severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count normalized severities with stable keys for API consumers."""
    counts = {
        severity.value: 0
        for severity in SecuritySeverity
        if severity != SecuritySeverity.NONE
    }
    for finding in findings:
        severity = normalize_severity(finding.get("severity"))
        if severity != SecuritySeverity.NONE:
            counts[severity.value] += 1
    return counts


def aggregate_state(
    dependency: SourceStatus,
    codeql: SourceStatus,
    finding_count: int,
    *,
    coverage_complete: bool,
) -> SecurityState:
    """Combine independent evidence without turning absence into a green result."""
    if finding_count:
        return SecurityState.FINDINGS
    if dependency.state == "pending" or codeql.state == "pending":
        return SecurityState.PENDING
    if dependency.state == "stale" or codeql.state == "stale":
        return SecurityState.STALE
    if not dependency.configured and not codeql.configured:
        return SecurityState.NOT_CONFIGURED
    if dependency.state == "unavailable" and codeql.state == "unavailable":
        return SecurityState.UNAVAILABLE
    if not (
        dependency.current
        and dependency.complete
        and codeql.current
        and codeql.complete
        and coverage_complete
    ):
        return SecurityState.PARTIAL
    return SecurityState.NO_KNOWN_FINDINGS
