# StateForge DPT

StateForge Development Progress Tracking is a local-first control application
for coordinating work across the StateForge repositories.

DPT edits the implementation plan locally, publishes its complete portable
manifest into an identical `STATEFORGE_DPT.md` in every managed repository, and
derives delivery progress from issues, pull requests, reviews, checks, and merge
evidence retrieved from GitHub.

## Development

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cd frontend && npm install && npm run build && cd ..
stateforge-dpt run
```

Open <http://127.0.0.1:8463>.

Select **Continue with GitHub** and authorize the StateForge DPT GitHub App
with the displayed one-time code. DPT uses GitHub Device Flow: no callback URL,
client secret, or user-managed token is required. Access and refresh tokens are
encrypted locally and never exposed to the browser.

## Core authority model

- Git-tracked `STATEFORGE_DPT.md` files are the portable project authority. An
  embedded versioned manifest preserves every repository, milestone, planned
  branch, dependency, ordering rule, project revision, and content fingerprint.
- SQLite is each computer's local editable working copy and GitHub evidence
  cache. DPT refuses to silently overwrite local edits or choose between
  divergent Git copies.
- Every project has an explicit plan source branch (for example,
  `mvp/implementation`). Reconciliation fetches that branch and reads only its
  `STATEFORGE_DPT.md` through Git; it never checks out, rebases, or modifies the
  developer's current branch or working tree.
- Importing any participating clone recreates the full project. Only that clone
  is attached immediately; the remaining repositories are ready to clone or
  attach on the new computer.
- Publishing writes the next identical revision to every attached clone. Commit
  and push those generated files normally, then merge them into the configured
  plan source branch. Reconciliation on another computer automatically imports
  a strictly newer, conflict-free revision into its local SQLite working copy.
- Local unpublished edits and same-revision divergence are never overwritten;
  DPT surfaces the existing source chooser so the user can resolve them.
- GitHub is the authority for delivery progress, not project definition.
- DPT validates GitHub evidence against the dependency graph and reports real
  workflow violations.

See [docs/architecture.md](docs/architecture.md) for the complete design.
