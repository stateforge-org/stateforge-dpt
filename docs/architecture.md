# Architecture

## Process model

StateForge DPT runs on the developer's machine. A FastAPI process owns SQLite,
GitHub communication, Git operations, and controlled filesystem access. The
browser interface communicates only with that local process.

```text
Browser interface
       |
Local FastAPI backend
       +-- SQLite local working plan and audit cache
       +-- GitHub App authentication and API reconciliation
       +-- Git repository controller
       +-- STATEFORGE_DPT.md renderer
       |
Managed local repository clones <----> GitHub
```

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
