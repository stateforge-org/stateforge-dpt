# AI agent interface

StateForge DPT exposes its local project model to MCP-capable coding agents.
Start the normal application:

```sh
stateforge-dpt run
```

Then configure the client with the Streamable HTTP endpoint printed by DPT:

```text
http://127.0.0.1:8463/mcp
```

### Codex desktop app

Install the personal local plugin once:

```sh
stateforge-dpt setup-codex
```

Keep `stateforge-dpt run` open, restart the Codex app, and begin a new chat.
The command adds DPT to the personal plugin marketplace without removing other
entries and enables it by default. It also sets Codex to approve read and
proposal-creation tools normally while requiring an explicit prompt for
`apply_plan_change_proposal` and `reject_plan_change_proposal`.

The registered endpoint follows DPT's configured port. For a deliberate custom
runtime address, use a loopback URL explicitly:

```sh
stateforge-dpt setup-codex --url http://127.0.0.1:9000/mcp
```

The endpoint runs in the same process and on the same port as the browser. It
uses the same service layer, validation, SQLite transaction boundary, and audit
trail. No browser needs to be open.

## What an agent can read

The MCP server exposes explicit tools for:

- discovering projects, repositories, roadmap stages, work-queue states, and
  GitHub blockers;
- reading one milestone or blocker with its current evidence and valid actions;
- browsing Git-tracked paths in attached repositories and reading bounded text
  resources;
- reading context and verification contracts, impact and regression evidence,
  and the latest approved implementation package;
- inspecting recent DPT activity and plan-change proposals.

`get_roadmap` and `get_work_queue` are compact discovery calls. They contain
identity, dependency state, concise artifact status, readiness, and valid
actions, but omit issue bodies, timelines, patches, and complete context. After
selecting one item, call `get_work_item` for its detailed evidence. This keeps a
large GitHub history from consuming the agent's context window before it has
chosen relevant work.

Repository reads use DPT's existing tracked-file boundary. An arbitrary local
path, untracked file, binary resource, or oversized file is not exposed.

## Human-controlled plan editing

An agent cannot directly call ordinary plan CRUD operations. It follows this
protocol:

1. Read the current project and its `working_plan_version`.
2. Submit typed operations through `propose_plan_change` with an idempotency
   key and rationale.
3. DPT applies those operations to an in-memory draft and validates the entire
   portable plan, including repository references, dependencies, duplicates,
   and cycles.
4. Read `review_plan_change_proposal`. Present its exact operations, semantic
   diff, consequences, proposal version, and review digest to the human.
5. If the human requests a change, submit a new proposal that supersedes the
   old one. Do not silently reinterpret the stored proposal.
6. Only after explicit agreement, call `apply_plan_change_proposal` using the
   reviewed proposal version and digest. The MCP client must independently ask
   the human to confirm this write-tool call.
7. If the human declines, call `reject_plan_change_proposal`.

The proposal becomes stale when any other plan edit changes the working-plan
version. DPT then requires a new proposal based on fresh state. Successful apply
and reject calls are idempotent for safe client retries.

## Application is not publication

Applying an AI proposal changes the local SQLite working plan only. It does not:

- write or publish `STATEFORGE_DPT.md`;
- increase the published plan revision;
- commit, push, create or review a pull request, or merge;
- reconcile GitHub evidence; or
- bypass context or verification readiness.

The plan remains visibly unpublished until a human uses DPT's normal **Review &
publish** workflow. The optional **AI proposals** inbox in Plan editor shows the
same immutable review package and can apply or reject it without going through
the agent conversation.

## Client approval requirement

`apply_plan_change_proposal` and `reject_plan_change_proposal` are declared as
write tools. A compatible client should show a confirmation prompt before each
call. Do not add them to blanket or remembered auto-approval rules. MCP itself
does not define a portable cryptographic proof that a particular human approved
a call, so DPT combines client confirmation with exact version/digest matching,
stale-state rejection, local-only scope, and audit records.

## Network boundary

MCP is intended for loopback use. Use `stateforge-dpt run --no-mcp` to disable
it. When the web server binds to a non-loopback address, DPT disables MCP unless
`STATEFORGE_DPT_MCP_ALLOW_REMOTE=true` is explicitly set. That override does not
provide authentication or TLS; operators are responsible for placing an
authenticated, encrypted boundary in front of any remote deployment.
