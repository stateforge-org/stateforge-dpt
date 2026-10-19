<img src="https://avatars.githubusercontent.com/u/309498710?v=4&amp;s=160" width="80" height="80" alt="StateForge logo">

# StateForge DPT

**Plan visually. Track delivery. Share your plan through Git.**

[![PyPI version](https://img.shields.io/pypi/v/stateforge-dpt?color=3978ff)](https://pypi.org/project/stateforge-dpt/)
[![Python versions](https://img.shields.io/pypi/pyversions/stateforge-dpt?color=3978ff)](https://pypi.org/project/stateforge-dpt/)
![Platforms: Linux, macOS, Windows](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-3978ff)

StateForge DPT (Development Progress Tracking) is a local web app for planning
and tracking work across GitHub repositories. Create steps, connect dependencies,
and see what is ready to build next—all in a visual workspace.

Your plan lives alongside your code in Git. Issues, pull requests, and merges
provide the delivery evidence. Each computer keeps its own local working copy.

## Why DPT?

- **Visual planning.** Describe a result, choose a repository, and select
  prerequisite cards. DPT handles step IDs, ordering, and dependency validation.
- **A connected roadmap.** Explore dependency stages or a graph, with search
  and repository filters.
- **An actionable work queue.** See ready, pending, in-review, blocked, and
  completed work, with assigned contributors and their profile photos.
- **GitHub context in one place.** Read formatted descriptions, comments,
  reviews, and activity. Open issues, create planned branches and PRs, or jump
  to GitHub to start a review. The dashboard also shows each repository's
  latest successful GitHub Actions run on the plan source branch.
- **Git-backed collaboration.** Share the complete plan in
  `STATEFORGE_DPT.md` and import it on another computer from a participating clone.
- **Continuous safe synchronization.** DPT keeps the shared plan, delivery
  evidence, and CI status current in the background after GitHub is connected,
  without switching branches. Refreshes are adaptive, concurrent, and
  coalesced across the server and every open tab; conflicts and unpublished
  local edits are surfaced, not overwritten. Routine refreshes compare a
  lightweight GitHub index and fully reload only changed evidence, with periodic
  full audits as a correctness backstop.
- **Regression-resistant implementation.** Define cross-repository context,
  protected behavior, and verification before work begins; review the exact
  change surface and retain relevant bug history afterward.
- **A first-class AI interface.** The same local process exposes DPT through
  MCP, so coding agents can inspect roadmaps, blockers, repository context, and
  implementation packages without scraping the browser. Agents may propose
  plan edits, but an exact digest-bound proposal needs human confirmation.

## Installation

Requires **Python 3.11+** and **Git 2.31+** on your PATH. Runs on Linux, macOS,
and Windows. The browser interface is included; Node.js is not needed.

```sh
python -m pip install stateforge-dpt
stateforge-dpt run
```

Open [http://127.0.0.1:8463](http://127.0.0.1:8463) in your browser. Start DPT
from any directory, keep the terminal open, and press `Ctrl+C` to stop it.

On macOS/Linux, use `python3` if needed. On Windows, use
`py -m pip install stateforge-dpt` and `py -m stateforge_dpt run` if the
console command is not on your PATH.

Use a virtual environment for an OS-managed Python. If you already use
[pipx](https://pipx.pypa.io/), `pipx install stateforge-dpt` is another option.

## Your first project

1. **Connect GitHub—or start locally.** Choose **Continue with GitHub** and
   approve the one-time code. Local mode supports planning without signing in;
   refreshing GitHub delivery evidence requires a connected account.
2. **Add repositories.** Create a project, then attach existing clones or choose
   **Clone for me**.
3. **Build the plan.** In **Plan editor**, create a step, describe its expected
   result, and choose its repository. Select prerequisite cards to connect work
   across repositories—no dependency IDs to memorize.
4. **Follow delivery.** Explore **Roadmap** and **Work queue**. DPT refreshes
   every project's shared plan and GitHub evidence automatically while the DPT
   server is running; **Sync now** remains available when you want an immediate
   refresh.

Joining an existing project? Choose **Import from repository** on the projects
page and select a local clone containing a published `STATEFORGE_DPT.md`.
DPT previews the complete plan before importing it.

## Use DPT from an AI coding agent

`stateforge-dpt run` also serves a local Streamable HTTP MCP endpoint:

```text
http://127.0.0.1:8463/mcp
```

For the Codex desktop app, install DPT's personal local plugin once:

```sh
stateforge-dpt setup-codex
```

Then keep DPT running, restart the Codex app, and start a new chat. The setup
command preserves other personal marketplace entries, registers the loopback
MCP endpoint, and configures proposal application and rejection to prompt for
confirmation in Codex. Rerun it after changing DPT's configured port, or pass
the matching endpoint with `--url`.

Connect an MCP-capable coding client to that URL while DPT is running. The
agent can read projects, dependency stages, the work queue, GitHub blockers,
managed repository trees, approved context contracts, and implementation
packages. File access is limited to Git-tracked text resources in attached
repositories and bounded by a configurable read limit.

Discovery tools intentionally return compact milestone and blocker cards.
Agents call `get_work_item` only for the selected item's complete GitHub,
context, and review evidence, keeping routine planning queries small.

Plan editing uses a proposal boundary. The agent submits typed operations; DPT
validates the complete resulting plan and returns an immutable review package
with a semantic diff, consequences, version, and SHA-256 review digest. After
you review it in the conversation, the agent may call the consequential apply
tool. Keep per-call tool confirmation enabled in your MCP client—never
auto-approve `apply_plan_change_proposal`. The same proposals can be inspected,
applied, or rejected in **Plan editor → AI proposals**.

Applying a proposal changes only the local SQLite working plan. It never writes
`STATEFORGE_DPT.md`, publishes a revision, commits, pushes, opens a pull request,
reviews, or merges. Those remain separate human-owned workflows. Run with
`--no-mcp` to disable the agent interface. See the
[AI agent interface guide](https://github.com/stateforge-org/stateforge-dpt/blob/main/docs/ai-agent-interface.md)
for the complete tool and safety model.

## Share a plan across computers

Collaborate through your repositories, not a separate planning server:

1. Choose **Review & publish** in **Plan editor** to preview and write the next
   plan revision into your attached clones.
2. Commit and push the generated files with Git, then merge them into the
   project's shared **plan source branch**, configured in **Settings**.
3. On another computer, install DPT and import the project from any participating
   clone. Attach or clone the remaining repositories; the steps and dependencies
   are already there.
4. Connect GitHub. DPT automatically fetches newer plan revisions and delivery
   evidence—even while you remain on a feature branch.

The source branch can be your default branch or another shared parent, such as
`mvp/implementation`. Publishing writes local files; it does **not** commit,
push, or merge them. Reconciliation does **not** rebase or change your working
tree. Conflicting revisions or unpublished local edits require your resolution.

## Progress backed by GitHub

DPT uses project, release, and step labels to match GitHub activity to the plan:

| Matched GitHub evidence | DPT state |
| --- | --- |
| Open issue | Pending |
| Open pull request | In review |
| Merged pull request | Completed |
| Closed, unmerged pull request | Cancelled |

Without matched evidence, prerequisites determine whether work is ready or
blocked. Reconciliation flags workflow findings, such as a PR opened before its
dependencies are complete. Perform reviews on GitHub using the shortcut to the
PR's **Files changed** tab.

## Local by design

Working copies, cached GitHub activity, and encrypted credentials live under
`~/.stateforge-dpt/`. New clones default to:

```text
~/.stateforge-dpt/projects/<PROJECT_NAME>/<REPOSITORY_NAME>
```

The shared plan excludes credentials, cached conversations, and local clone
paths. Each collaborator uses their own account and clones. Remote cloning,
shared-plan fetching, and GitHub updates require network access.

Update DPT with:

```sh
python -m pip install --upgrade stateforge-dpt
```

DPT is an early-stage release. Keep generated plan changes in Git for review
and recovery through your normal development workflow.

## Documentation

- [Configuration and troubleshooting](https://github.com/stateforge-org/stateforge-dpt/blob/main/docs/configuration.md)
- [Architecture and plan format](https://github.com/stateforge-org/stateforge-dpt/blob/main/docs/architecture.md)
- [AI regression-resistance workflow](https://github.com/stateforge-org/stateforge-dpt/blob/main/docs/ai-regression-resistance.md)
- [AI agent interface](https://github.com/stateforge-org/stateforge-dpt/blob/main/docs/ai-agent-interface.md)
- [Development and release guide](https://github.com/stateforge-org/stateforge-dpt/blob/main/docs/development.md)
- [Issue tracker](https://github.com/stateforge-org/stateforge-dpt/issues)

GitHub documentation and the issue tracker require access to the project
repository.
