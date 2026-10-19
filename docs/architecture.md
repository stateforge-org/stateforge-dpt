# Architecture

## Process model

StateForge DPT runs on the developer's machine. A FastAPI process owns SQLite,
GitHub communication, Git operations, and controlled filesystem access. The
browser interface communicates only with that local process.

```text
Browser interface                 AI coding agents
       |                                  |
       +------------- REST / MCP ---------+
                          |
Local FastAPI backend (MCP is mounted at /mcp)
       +-- SQLite local working plan and audit cache
       +-- GitHub App authentication and API reconciliation
       +-- Git repository controller
       +-- STATEFORGE_DPT.md renderer
       |
Managed local repository clones <----> GitHub
```

The browser, REST routes, and MCP tools share the same `DPTService` and SQLite
database. MCP is not a sidecar and does not recreate domain rules. Its read
tools expose authoritative project state and bounded Git-tracked resources.
Its only plan mutation path creates immutable typed proposals. Applying the
exact reviewed proposal replaces the local working plan atomically, preserves
GitHub evidence, invalidates affected context approvals and implementation
packages, and leaves the published revision and Git files unchanged.

Every proposal is bound to a monotonic working-plan version, draft fingerprint,
operation list, semantic diff, and review digest. Any intervening human or
agent edit makes it stale. The apply tool requires the same proposal version
and digest the human reviewed, and its MCP annotations mark it as a write so a
supporting client can request per-call confirmation. Browser review is a
fallback; opening a browser is not required for an agent-native approval.

## Sources of authority

The Git-tracked `STATEFORGE_DPT.md` is the portable project authority. Its
invisible, versioned JSON manifest contains the lossless project definition:
project identity and revision, repositories, delivery steps, required results,
dependencies, planned branches, and stable ordering. A SHA-256 fingerprint
detects divergent or modified manifests. The visible Markdown remains a
human-readable plan and is generated identically in every attached repository.

SQLite is the local working copy. It holds edits that have not been published,
machine-specific clone paths, credentials, GitHub evidence, and audit activity.
Publishing creates the next manifest revision in every attached clone; normal
Git commits, pushes, pulls, and reviews distribute that revision exactly like
source code.

On another computer, importing `STATEFORGE_DPT.md` from any participating clone
recreates all repositories and milestones. The source clone is attached and the
remaining repositories are registered for cloning or attachment. When Git has a
newer revision, DPT can import it. If local unpublished edits exist, or two Git
copies claim the same revision with different fingerprints, DPT stops and asks
the user to choose rather than silently overwriting a plan.

Each project also records a portable plan source branch, conventionally the
shared parent branch from which milestone branches are derived (for example,
`mvp/implementation`). Reconciliation runs a narrow Git fetch in each attached
clone and reads `STATEFORGE_DPT.md` directly from the fetched ref. It does not
check out that ref, merge, rebase, or write any worktree file. Valid snapshots
are checksummed and cached in SQLite. A strictly newer snapshot is imported only
when the SQLite plan has no unpublished edits; otherwise it is presented as a
conflict. This allows plan changes merged by another computer to appear locally
while the developer remains on any feature branch.

After GitHub authentication, the server keeps every project reconciled even
when no browser tab is open. Its cadence adapts to project activity: delivery
work is checked more frequently, quiet projects use a slower interval, and
repeated failures back off exponentially. The browser polls the inexpensive
local dashboard projection and offers **Sync now** as an immediate override.
Automatic, manual, and multi-tab triggers share one in-flight backend task per
project. Repository GitHub requests use bounded asynchronous concurrency and
conditional ETags, while blocking Git and context inspection run in bounded
worker threads. This keeps the interface current without multiplying API
traffic or blocking the event loop.

GitHub supplies observed execution evidence. Stable labels bind an issue or
pull request to a DPT step:

```text
dpt:project:stateforge
dpt:release:mvp
dpt:step:P1
```

An open issue is pending work, an open pull request is under review, a merged
pull request is completed work, and a closed unmerged pull request is cancelled.
When no GitHub artifact exists, dependencies determine whether a step is ready
or blocked. Reconciliation reports workflow violations, such as opening a pull
request before its prerequisites are complete. Issues may be prepared in
advance without changing the dependency graph.

Milestones may also carry a portable context and verification contract. Context
roles, authorities, selectors, explanations, and verification requirements are
human-authored plan intent. Resource hashes, approvals, discovery decisions,
implementation packages, command results, pull-request impact, and regression
observations are local execution evidence. See
[AI regression resistance](ai-regression-resistance.md) for the complete
human-controlled workflow and security boundary.

## Generated-file safety

The renderer is deterministic. DPT records the last digest written to each
clone. A branch switch or Git update may legitimately replace that file, so
publishing also recognizes unchanged, committed Git snapshots with a valid
manifest for the same project, release, and repository. Older revisions can be
advanced; a current revision must match the published baseline or working plan.
Both the worktree and index are checked for edits to the generated file;
unrelated code changes do not prevent publication.

Newer plans and same-revision divergence remain protected, including cached
source-branch snapshots discovered during reconciliation. Unknown files with
uncommitted edits or invalid manifests require review instead of being silently
overwritten. The embedded manifest is independently checksummed and parsed
through a strict schema with duplicate, missing-dependency, and cycle checks.
Every target is validated before any file is written. Files are written
atomically. Absolute local paths, credentials, cached GitHub activity, and
tokens never appear in the generated document.

## GitHub authentication

The browser requests a one-time GitHub device code from the local backend. The
backend keeps the associated device credential encrypted, enforces GitHub's
polling interval, and creates a session only after GitHub confirms the user.
Access and refresh tokens are encrypted at rest. The browser receives only a
random session identifier in an HTTP-only SameSite cookie. No GitHub client
secret or callback endpoint is used.

## Repository control

A repository may be attached to an existing clone or cloned by DPT. Before it
is accepted, DPT verifies that the path is a Git worktree and that its `origin`
remote matches the selected GitHub repository. Repository mutations are
performed without a shell and are presented to the user before commit or push.
Remote plan reads use argument-based Git commands with prompting disabled. The
current HEAD, index, untracked files, and worktree are invariant across the
operation.
