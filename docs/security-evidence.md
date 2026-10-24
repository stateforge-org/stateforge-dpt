# Vulnerability evidence

StateForge DPT assesses every managed repository at the exact head of the
project's plan-source branch. The dashboard summary and **Vulnerabilities** page
combine two independent sources:

- A commit-specific CycloneDX 1.5 dependency inventory assessed through OSV.
- GitHub CodeQL analyses and open alerts for the same branch and exact head.

DPT says **No known findings**, never “secure.” That state is available only
when both sources are current and complete and every third-party SBOM component
has a usable versioned Package URL. An empty response without complete evidence
cannot produce a green status.

## Exact-source assessment

One refresh resolves the configured branch to an immutable Git SHA, downloads
that exact source archive, extracts it into an isolated temporary directory,
and selects a reviewed SBOM adapter. Attached worktrees, checked-out branches,
and lock files are not changed.

The first adapter supports repositories with committed `pyproject.toml` and
`uv.lock` files. It uses pinned `uv 0.13.5` in locked, offline mode and includes
all dependency groups and extras. DPT removes uv's non-semantic export timestamp
and serial number before canonicalizing and hashing the document. Local
provenance records the adapter contract, pinned tool version, exact semantic
command, exit status, duration, and bounded diagnostic output. The generator
runs with a minimal environment and isolated home directory so repository input
cannot inherit unrelated user configuration or credentials.

The dependency-material digest covers `pyproject.toml`, `uv.lock`, optional
`uv.toml`, and the adapter/tool contract. It excludes `STATEFORGE_DPT.md`. A
plan-only commit can therefore reuse the same validated SBOM, while CodeQL must
still have a successful analysis for the new exact head.

## Result states

| State | Meaning |
| --- | --- |
| `no_known_findings` | Current, complete OSV and exact-head CodeQL evidence contain no findings. |
| `findings` | At least one dependency vulnerability or open CodeQL alert exists. |
| `pending` | The head is known, but required evidence is still being produced. |
| `stale` | Available evidence is expired or belongs to an older head. |
| `partial` | One source or material component coverage is incomplete. |
| `not_configured` | Neither a supported dependency source nor CodeQL evidence exists. |
| `unavailable` | Authentication, transport, generation, validation, or persistence failed. |

Severity is normalized as `critical`, `high`, `medium`, `low`, or `unknown`.
Unknown severity is preserved and is never silently downgraded to low.

## Refresh and persistence

Normal dashboard reads use only persisted SQLite summaries and never start a
scan. GitHub reconciliation refreshes evidence with bounded concurrency and
reports progress through the existing reconciliation stream. The dedicated
refresh button starts or joins one authenticated project refresh and bypasses
the six-hour OSV freshness window.

One repository failure does not cancel the remaining repositories. The detail
page binds findings and provenance to the displayed commit so evidence from an
older head is not accidentally presented as current.

## Portable SBOM cache

SQLite is the authority for current observations. Review & Publish may also
embed the latest validated SBOMs in a second hidden block in
`STATEFORGE_DPT.md`. This cache:

- is checksummed independently from the portable plan;
- does not change the plan fingerprint or revision;
- contains SBOM material, not OSV findings or CodeQL results;
- is reused only after the current source produces the same dependency digest
  under the same adapter contract; and
- may be ignored if malformed, oversized, stale, or tampered without preventing
  the plan itself from being imported.

Security reconciliation never writes or commits the file. Only the existing
human-controlled **Review & Publish** operation can publish the cache through
the StateForge DPT GitHub App. All security reads use the connected user's
GitHub identity.

## Initial safety limits

- Repository archive download: 100 MiB
- Expanded archive: 500 MiB and 50,000 entries
- Individual archive path: 4,096 bytes
- Canonical SBOM: 2 MiB per repository
- Embedded cache: 16 MiB per DPT file
- Generator execution: 120 seconds

Unsupported ecosystems remain visible as incomplete evidence. Future reviewed
adapters can add other lockfile ecosystems without weakening the status rules.
