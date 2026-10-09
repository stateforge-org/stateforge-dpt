# AI-accessible DPT through MCP

## Implementation plan

StateForge DPT will expose its project knowledge and permitted workflows through
an embedded Model Context Protocol (MCP) server. Running `stateforge-dpt run`
will start one local application with three interfaces:

- the existing browser interface;
- the existing REST API; and
- a Streamable HTTP MCP endpoint at `http://127.0.0.1:8463/mcp`.

The MCP interface will let an AI client inspect DPT as a human can, discover
which actions are currently valid, and propose plan changes. The human can
review and approve the exact proposal through the agent client's write-tool
confirmation flow; the AI cannot approve its own proposal. The interface will
not publish the canonical plan, push Git changes, open a pull request, review,
or merge.

This plan is intentionally implementation-specific. It defines the service
boundaries, persistent data, tool contracts, review workflow, security controls,
delivery order, and acceptance criteria required to build the feature without
creating a second source of DPT behavior.

## 1. Product outcome

After this feature ships, an AI coding agent can connect to DPT and answer
questions such as:

- Which work is ready, blocked, in review, or complete?
- What does milestone `R1` require?
- Which GitHub issue and pull request belong to it?
- Which files, declarations, regressions, and verification requirements form
  its approved implementation context?
- What actions are available now, and why are other actions unavailable?
- What exact plan edit would add a dependency, improve a milestone, or add a
  verification requirement?

The agent can submit that last answer as a structured proposal. DPT returns an
exact review package that the agent presents in the conversation. The human can
request edits, choose a subset, reject it, or approve the resulting immutable
proposal. The agent then requests the approval-gated apply tool, and the client
must obtain a separate human confirmation before invoking it. Applying the
proposal updates only DPT's local SQLite working copy. The DPT browser remains
an optional richer review surface, not a requirement. Publishing
`STATEFORGE_DPT.md` remains a separate human-reviewed workflow.

## 2. Non-negotiable boundaries

| Boundary | Required behavior |
| --- | --- |
| One domain implementation | REST, the browser UI, and MCP call the same application actions and `DPTService`; MCP must not query SQLite directly or reproduce plan rules. |
| Human plan authority | MCP can submit an immutable proposal and request its application, but the apply tool must require an explicit per-call confirmation from the human using the agent. The model cannot approve silently or use remembered blanket approval for this tool. |
| Human delivery authority | MCP cannot push, open or merge pull requests, perform reviews, or publish the canonical plan. Local commits remain allowed for an implementation agent outside this MCP planning surface. |
| Git-backed authority | `STATEFORGE_DPT.md` on the shared plan source branch remains the portable project authority. SQLite remains a local working copy. |
| Transactional application | Human-accepted plan operations are revalidated and applied in one SQLite transaction. No partially written plan is observable. |
| Optimistic concurrency | A proposal created against an older working plan becomes stale; it is never applied to a different plan silently. |
| Explicit identity | The server derives agent and human actor identities from their transport/session. Actor identity is never accepted as tool input. |
| Local by default | MCP listens only with the local DPT server. Wider network exposure is disabled unless a future authenticated remote mode is designed explicitly. |
| Closed file access | AI resource access is confined to registered repositories, Git-tracked paths, supported text files, and configured size limits. |
| Auditable behavior | Proposal creation, human decisions, applications, failures, and relevant MCP actions appear in the DPT activity trail. |

## 3. Architecture

```text
stateforge-dpt run
        |
        v
+----------------------- FastAPI host ------------------------+
|                                                              |
|  Browser UI             REST API               MCP /mcp      |
|      |                      |                       |          |
|      +----------- adapter / actor context ----------+          |
|                             |                                  |
|                             v                                  |
|                 Application action registry                    |
|             authorization + availability + schemas             |
|                             |                                  |
|                  +----------+-----------+                      |
|                  |                      |                      |
|                  v                      v                      |
|             DPTService          PlanProposalService            |
|                  |                      |                      |
|                  +-----------+----------+                      |
|                              v                                 |
|                         SQLite / Git / GitHub                   |
+--------------------------------------------------------------+
```

### Why the action layer is necessary

Today, FastAPI route handlers call `DPTService` directly. Adding MCP directly
beside those routes would make authorization, input validation, availability
rules, and response contracts drift between interfaces. Before adding many MCP
tools, DPT should introduce a small application layer with typed actions.

An action definition owns:

- a stable action identifier;
- its human title and description;
- its typed input and output models;
- whether it is read-only, local-mutating, open-world, or destructive;
- the actor classes allowed to invoke it;
- precondition and availability evaluation;
- whether human approval is required; and
- the handler that delegates to the domain service.

The UI can use these definitions to render valid controls, REST can use them for
request handling, and MCP can expose the same definitions as discoverable
actions. MCP tools remain explicit and statically declared; the registry does
not dynamically invent tools.

## 4. Proposed module layout

```text
src/stateforge_dpt/
  actions/
    __init__.py
    registry.py              # ActionDefinition and ActionRegistry
    queries.py               # Read-only project/work/repository actions
    proposals.py             # Submit and inspect plan proposals
    actor.py                 # Human, agent, and system actor contexts
  plan_editing/
    models.py                # Discriminated plan-operation models
    draft.py                 # Apply operations to an in-memory plan draft
    diff.py                  # Semantic diff and consequence calculation
    proposals.py             # Persistence, validation, decision, application
  mcp/
    __init__.py
    server.py                # MCPServer construction and tool/resource wiring
    presentation.py          # MCP structured output and resource links
    security.py              # Host/origin and local exposure policy
  app.py                     # Mount MCP and own its lifespan
  service.py                 # Existing domain service; gains transaction helpers
  models.py                  # Existing REST/domain request models
  database.py                # Additive schema migration
```

The exact filenames may change during implementation, but the separation must
remain: protocol presentation, application actions, plan proposal workflow, and
domain persistence are distinct responsibilities.

## 5. Embedded MCP runtime

### Startup and lifecycle

1. `create_app(settings)` constructs one `DPTService`, one action registry, and
   one MCP server.
2. The official Python MCP SDK supplies a Streamable HTTP ASGI application.
3. FastAPI mounts that application at `/mcp` before the frontend catch-all route.
4. The parent FastAPI lifespan enters the MCP session manager for the entire
   server lifetime. A mounted sub-application's lifespan must not be assumed to
   run independently.
5. `stateforge-dpt run` starts the combined application; no second process,
   port, command, or working directory is required.
6. The startup banner prints both endpoints:

   ```text
   Browser     http://127.0.0.1:8463
   AI tools    http://127.0.0.1:8463/mcp
   ```

7. `/api/health` reports whether MCP initialization succeeded and the MCP path,
   without disclosing secrets or client sessions.

### Configuration

Add settings with conservative defaults:

| Setting | Default | Purpose |
| --- | --- | --- |
| `STATEFORGE_DPT_MCP_ENABLED` | `true` | Permit users to disable MCP without disabling the browser. |
| `STATEFORGE_DPT_MCP_PATH` | `/mcp` | Stable endpoint path; initially not exposed as a common UI preference. |
| `STATEFORGE_DPT_MCP_MAX_READ_BYTES` | bounded value | Limit a single tracked text-resource read. |
| `STATEFORGE_DPT_MCP_ALLOWED_ORIGINS` | loopback origins | Prevent cross-site browser access. |

Also add `stateforge-dpt run --no-mcp`. If `--host` is non-loopback, startup
must refuse to expose MCP unless a separately named, explicit remote setting is
enabled. Authenticated remote MCP is out of scope for this release.

### Dependency policy

Use and pin the official MCP Python SDK after a compatibility spike. Do not
hand-roll Streamable HTTP, JSON-RPC session handling, or protocol negotiation.
The MCP dependency version and supported client matrix must be exercised on
Linux, macOS, and Windows before release.

## 6. MCP information model

MCP should use all three applicable primitives deliberately:

- **Tools** perform queries and submit proposals.
- **Resources** provide stable, addressable snapshots such as a milestone,
  implementation package, or approved context resource.
- **Resource links** in tool results allow clients to open the authoritative
  object rather than receiving an oversized response repeatedly.

Every response includes:

```json
{
  "schema_version": 1,
  "entity": {},
  "state": "...",
  "state_reason": "...",
  "available_actions": [],
  "links": [],
  "warnings": []
}
```

Each available action contains:

- `action_id`;
- title and concise consequence;
- `available` and a human-readable reason;
- unmet preconditions;
- `approval`: `none`, `agent_client_confirmation`, `human_plan_review`, or
  another explicit class;
- JSON input schema; and
- whether the action changes local state or an external system.

This makes DPT act like an inspectable tool rather than requiring an agent to
guess what a screen button would do.

## 7. Initial MCP surface

### Read-only tools

| Tool | Result |
| --- | --- |
| `list_projects` | Local projects with release, published revision, working-plan version, repository count, and proposal count. |
| `get_project` | Project summary, plan-source branch, repositories, delivery state, warnings, and available actions. |
| `get_roadmap` | Dependency stages containing milestones and GitHub blockers in their computed positions. |
| `get_work_queue` | Milestones and blockers grouped by actionable state, with the same state reasons used by the UI. |
| `get_work_item` | One milestone or blocker, including Markdown description, dependencies, GitHub evidence, context status, and actions. |
| `list_repositories` | Managed repositories, attachment state, branch, working-tree state, and clone information safe to disclose locally. |
| `list_repository_tree` | Bounded Git-tracked file tree for one attached repository. |
| `read_repository_resource` | Bounded text for a Git-tracked file or supported declaration selector; no arbitrary filesystem path. |
| `get_context_contract` | Declared context, verification, inspection status, human approval, impact analysis, and regression history. |
| `get_implementation_package` | The immutable package, package hash, GitHub issue, context resources, instructions, and verification contract. |
| `list_activities` | Filtered, paginated project activity. |
| `get_available_actions` | Authoritative action availability for a project, work item, repository, or proposal. |
| `get_plan_change_proposal` | Proposal state, original operations, human-edited operations, validation, diff, and decision. |
| `wait_for_plan_change_proposal` | Bounded long-poll that returns when proposal state/version changes or the timeout expires. |
| `review_plan_change_proposal` | A conversation-ready summary, exact operation list, semantic diff, consequences, warnings, and immutable review digest. |

All list tools are paginated and have bounded filters. Large entities return a
summary plus MCP resource links.

### Proposal tool

The only initial plan-writing tool is:

```text
propose_plan_change(
  project_id,
  base_working_plan_version,
  rationale,
  idempotency_key,
  operations[]
)
```

It validates and stores a proposal. It does **not** update `projects`,
`repositories`, `steps`, dependencies, context, verification, or generated
files.

Supported operation variants in the first release:

- create, update, reorder, or delete a milestone;
- replace a milestone's dependency set;
- replace a milestone's context and verification contract;
- add or update a managed repository declaration;
- update the project plan-source branch; and
- update portable project metadata that DPT already permits humans to edit.

GitHub blockers are not part of the portable plan. Creating or linking one is
therefore not smuggled into `propose_plan_change`; it remains a distinct
open-world GitHub workflow and can be designed as a later human-confirmed MCP
action.

The proposal tool is local-mutating because it creates proposal records, but it
is non-destructive and cannot mutate the plan. It accepts an idempotency key so
client retries cannot create duplicate proposals.

### Approval-gated application tool

The agent-native approval path uses a second tool:

```text
apply_plan_change_proposal(
  proposal_id,
  proposal_version,
  review_digest,
  idempotency_key
)
```

The agent may construct this call, but the client must pause and ask the human
to confirm the exact write action before sending it to DPT. The tool is marked
as write/consequential, is never covered by remembered approval, and is not
called as part of proposal submission.

The arguments contain no editable plan operations. They identify the exact
immutable version the human just reviewed. DPT recomputes the digest, checks the
working-plan version, revalidates the complete draft, and applies that version
atomically. If the human asks for any edit or partial acceptance, the agent must
first create a revised proposal version, present its new review package and
digest, and request confirmation for that version.

The apply tool is enabled only for supported agent clients configured for
per-call write confirmation. Setup documentation must require `prompt`/`always`
approval for this tool and explicitly forbid remembered approval. A client that
cannot provide that interaction receives `interactive_approval_required` and a
link to the optional browser review.

Generic MCP does not give the server a cryptographic receipt proving that a
particular client displayed its confirmation UI. This mode therefore relies on
the trusted local MCP client's documented approval enforcement, just as other
confirmed MCP write actions do. DPT still enforces everything it can
server-side: immutable digest, authenticated/paird local identity, proposal
state, authorization, current working version, full validation, idempotency,
and atomic application. An authenticated remote mode remains out of scope.

### Deliberately absent tools

There will be no MCP tools named or equivalent to:

- apply arbitrary plan operations without a previously stored immutable
  proposal and a distinct approval-gated tool call;
- accept context or impact review outside its own explicit human-confirmed
  workflow;
- publish plan;
- commit or push;
- create, approve, or merge a pull request;
- execute arbitrary commands; or
- read arbitrary files or raw SQLite data.

MCP elicitation may ask an agent's user for missing structured proposal input,
but an elicitation response alone is not a plan-approval decision. Final plan
application uses the separate consequential tool confirmation.

## 8. Plan proposal model

### Working-plan concurrency

The current `projects.revision` is the published portable-plan revision. It is
not sufficient for concurrency because several unpublished SQLite edits can
exist under the same revision.

Add a monotonic `working_plan_version INTEGER NOT NULL DEFAULT 0` to `projects`.
Every mutation that changes the portable plan increments it in the same
transaction. Reconciliation/import that replaces the local plan also increments
it. Evidence refreshes and other cache-only changes do not.

A proposal records both:

- `base_working_plan_version`; and
- a canonical `base_plan_fingerprint`.

If either differs at decision time, the proposal becomes `stale`. The human may
ask the agent to resubmit or explicitly rebase it through a future review
workflow; DPT must never reinterpret it automatically.

### Tables

Add the following additive schema:

```text
plan_change_proposals
  proposal_id                 primary key
  project_id                  foreign key
  base_working_plan_version
  base_plan_fingerprint
  status                      awaiting_review | applied | rejected |
                              stale | cancelled | failed
  rationale
  proposer_kind               agent
  proposer_id
  client_name
  client_version
  idempotency_key
  validation_json
  diff_json
  consequences_json
  created_at
  updated_at
  decided_at
  decided_by
  decision_reason
  applied_working_plan_version

plan_change_proposal_operations
  proposal_id                 foreign key
  operation_id
  sort_order
  operation_kind
  proposed_payload_json
  review_state                pending | accepted | edited | rejected
  accepted_payload_json
  review_note
  primary key (proposal_id, operation_id)
```

Enforce uniqueness on `(proposer_id, idempotency_key)`. JSON fields use
canonical serialization. The original proposed payload is immutable; human
edits are recorded separately.

### State transitions

```text
                  plan changed
awaiting_review -----------------> stale
      |                              |
      | human rejects                | terminal
      v                              v
   rejected                        stale

awaiting_review -- human accepts/edit+accept --> validate and apply transaction
                                                    |                 |
                                                    v                 v
                                                 applied            failed
```

There is no `approved` state in which an unapplied approval can linger. The
human decision, final validation, and database application occur as one server
operation. This prevents an approved proposal from being applied later against
different state.

### Validation and diff generation

Proposal submission performs a dry run:

1. Read the canonical local portable plan.
2. Verify the supplied base version and fingerprint.
3. Apply the ordered operations to an in-memory `PlanDraft`.
4. Validate the complete result, not only individual fields.
5. Compute a semantic before/after diff and downstream consequences.
6. Persist the proposal, operations, validation, and diff atomically.

Validation includes:

- Pydantic request and portable-plan schemas;
- unique project, repository, milestone, context, and verification identities;
- repository ownership of milestones and resources;
- valid repository-relative paths;
- known dependencies, no self-dependency, and no cycles;
- branch and release conventions already enforced by DPT;
- deletion protection for milestones with dependents or GitHub evidence;
- exact identification of approvals and implementation packages invalidated;
- warnings when completed or in-review work would be materially changed; and
- all existing plan publication invariants.

The semantic diff groups changes by project, repository, milestone,
dependencies, context, and verification. It also reports:

- work items whose actionable state changes;
- downstream milestones whose stage changes;
- context approvals that will be invalidated;
- implementation packages that will become stale;
- existing GitHub evidence attached to affected milestones; and
- whether the local plan will become unpublished.

## 9. Human review and application

### Agent-native workflow

The normal workflow stays in the agent conversation:

1. The agent submits a proposal.
2. The agent calls `review_plan_change_proposal` and presents DPT's structured
   summary, exact operations, semantic diff, consequences, warnings, proposal
   version, and review digest. The model may explain this package but must not
   replace or omit DPT's authoritative fields.
3. The human may reject it, ask questions, request edits, or select a subset in
   natural language.
4. Any change creates a new immutable proposal version. The agent presents the
   new review package and digest.
5. When the human says to apply that version, the agent requests
   `apply_plan_change_proposal` with only its identity and digest.
6. The agent client displays its native consequential-tool confirmation. DPT is
   called only after the human approves that tool call.
7. DPT revalidates and applies the exact proposal version atomically.

This gives the human two distinct opportunities to control the outcome: the
conversation-level decision and the client-owned tool confirmation. A plain
message from the model, an elicitation answer, or the existence of a proposal
is never sufficient to apply it.

The review response should be designed for a developer-facing conversation:

- agent/client identity and submission time;
- rationale;
- base plan version and current plan version;
- a plain-language summary;
- an exact Markdown semantic diff;
- added, changed, reordered, and removed operations;
- dependency-stage changes;
- approval/package invalidations;
- GitHub evidence warnings;
- final proposal version and review digest; and
- an explicit statement that applying updates only the local working plan.

After application, the waiting agent receives `applied`, the final accepted
diff, and the new working-plan version. Publication remains a separate human
action.

### Optional browser workflow

The Plan Editor gains an **AI proposals** inbox and count. Opening a proposal
shows:

- agent/client identity and submission time;
- rationale;
- base plan version and current plan version;
- a plain-language summary;
- an exact, syntax-highlighted semantic diff;
- dependency-stage changes;
- approval/package invalidations;
- GitHub evidence warnings; and
- every operation with **Accept**, **Edit**, or **Reject** controls.

The human may accept all operations, reject the proposal, or construct a
partially accepted proposal by choosing/editing individual operations. Before
the final **Apply to local plan** action, DPT previews the final combined result
and consequences again. This surface is a fallback for clients without safe
per-call write confirmation and an optional richer view for dependency-heavy
changes. It is not required for the standard agent-native flow.

After application:

- the Plan Editor shows the normal unpublished-edits banner;
- publication remains a separate **Review & publish** action;
- the proposal becomes immutable and auditable; and
- the waiting agent can observe `applied` and the new working-plan version.

### Actor and decision boundary

Introduce `ActorContext` with at least `human`, `agent`, and `system` kinds.
Adapters create it; request payloads cannot.

- MCP sessions receive an authenticated/paired local `agent` context and may
  submit proposals.
- The dedicated MCP application action accepts only an immutable proposal ID,
  version, and digest. It cannot carry replacement plan operations.
- The MCP adapter creates a `DecisionAuthorization` for this action only on the
  supported confirmation-gated tool route and records the client, session/tool
  correlation, and `mcp_client_confirmation` decision channel.
- Browser REST decisions require a DPT browser session, CSRF/Origin validation,
  and a `human` decision authorization.
- GitHub-connected sessions use the authenticated GitHub identity.
- Local mode uses a server-issued local-human session identity.
- `PlanProposalService.decide_and_apply()` requires a valid decision
  authorization from either the confirmation-gated MCP action or the browser
  human route, even if called outside the route layer.

The server independently enforces immutable proposal identity, authorization,
current plan version, validation, and atomicity. The MCP host enforces the human
prompt before invocation. DPT must document that the generic MCP protocol does
not carry a cryptographically verifiable human-approval receipt; therefore a
client configured to auto-approve the application tool is unsupported. The
feature is not intended to defend against a malicious local process or a
deliberately weakened client running under the same operating-system account;
those are outside DPT's local threat model.

### Transaction design

Current mutation methods open their own short-lived SQLite connections. Proposal
application requires one transaction, so mutation logic must be split into:

- public convenience methods that open a transaction; and
- internal connection-aware primitives that operate on a supplied connection.

Both current UI actions and proposal application must reuse those primitives.
Do not call a sequence of existing public methods and attempt to compensate on
failure.

The application transaction:

1. locks/reads the proposal and current project version;
2. rejects non-pending or stale proposals;
3. rebuilds and validates the human-accepted draft;
4. writes all accepted changes;
5. invalidates affected context approvals and marks old implementation packages
   superseded or removes them according to the existing retention policy;
6. increments `working_plan_version` exactly once;
7. records the proposal decision and resulting version; and
8. records a detailed activity event.

Any failure rolls the entire transaction back and records a sanitized failure
after rollback.

## 10. Resources and implementation packages

Repository browsing through MCP follows the safety rules already expected by
the context selector:

- resolve paths relative to an attached repository root;
- reject absolute paths and traversal;
- use Git's tracked-file view rather than walking arbitrary files;
- do not follow a path outside the repository through a symbolic link;
- reject binary/unsupported files;
- cap file count, response bytes, line length, and selector work; and
- return commit/blob identity with the content.

An implementation package response includes its GitHub issue and evidence,
approved context snapshot, context observations, relevant regression history,
verification contract, package hash, and explicit implementation boundaries.
If its plan/context inputs are no longer current, DPT reports it as stale rather
than presenting it as authorized implementation context.

## 11. Errors and observability

MCP errors use stable codes and safe details:

| Code | Meaning |
| --- | --- |
| `not_found` | The requested DPT entity does not exist. |
| `invalid_input` | Input failed the published tool schema. |
| `precondition_failed` | The entity exists but the requested action is not currently valid. |
| `human_approval_required` | The agent attempted a human-only action. |
| `interactive_approval_required` | The client cannot establish the required per-call confirmation path; use a supported configuration or browser review. |
| `proposal_stale` | The working plan changed after proposal creation. |
| `conflict` | The idempotency key or entity version conflicts with existing state. |
| `resource_out_of_scope` | A file/path is outside the registered, tracked resource boundary. |
| `resource_too_large` | A bounded response limit was exceeded. |
| `authentication_required` | A future open-world action requires a connected human account. |
| `internal_error` | A sanitized unexpected failure with a correlation ID. |

Do not leak access tokens, cookies, absolute paths unless the local human-facing
workflow already requires them, environment values, or raw exception traces.
Log MCP initialization, tool name, duration, success/failure code, proposal ID,
and actor/client identity. Do not log file contents, proposal secrets, or GitHub
tokens.

## 12. Security controls

The first release is a local trusted-user product with defense against browser
cross-origin abuse and accidental overreach:

- bind to loopback by default;
- validate `Host` and `Origin` for MCP and human decision routes;
- enable the MCP SDK's DNS-rebinding protections/allowed-host configuration;
- use secure random MCP session identifiers;
- issue SameSite browser cookies and require CSRF tokens for decisions;
- keep proposal submission and confirmed application as separate actions;
- mark application as consequential and require supported clients to configure
  per-call prompting without remembered approval;
- derive identities server-side;
- validate every tool input with strict Pydantic models;
- use bounded pagination and response sizes;
- never expose raw SQL, arbitrary shell execution, or arbitrary file reads;
- sanitize GitHub/user-controlled text in the web proposal review; and
- require explicit authenticated design before any non-loopback deployment.

MCP tool annotations and the client's confirmation prompt provide the human
interaction, but do not replace DPT's server-side authorization, immutable
digest binding, current-version check, validation, or transaction boundary.

## 13. Delivery phases

### Phase 0 — Domain seams and plan concurrency

**Work**

- Add `working_plan_version` and its migration.
- Define exactly which mutations increment it.
- Add `ActorContext`, action definitions, and the initial action registry.
- Refactor plan mutations into connection-aware primitives.
- Add canonical working-plan fingerprint generation.

**Exit criteria**

- Existing REST behavior and UI tests remain unchanged.
- One plan mutation increments the working version once.
- GitHub evidence refresh does not increment it.
- Existing publication revision semantics remain unchanged.

### Phase 1 — Embedded read-only MCP

**Work**

- Add and pin the official MCP SDK.
- Mount Streamable HTTP at `/mcp` with parent lifespan ownership.
- Implement local host/origin policy, configuration, health, and banner output.
- Expose project, roadmap, work queue, work item, repository, activity, context,
  and implementation-package read tools/resources.

**Exit criteria**

- `stateforge-dpt run` serves UI, REST, and MCP from one process and port.
- A supported Codex/ChatGPT MCP client can initialize, list tools, and read a
  project without opening the browser.
- The frontend fallback never captures `/mcp` requests.
- Running from an arbitrary directory works in the packaged wheel.

### Phase 2 — Action discovery

**Work**

- Move current state/precondition reasoning into shared action definitions.
- Add `get_available_actions` and include action summaries in entity responses.
- Make applicable browser controls consume the same availability results.
- Add typed, stable MCP errors and resource links.

**Exit criteria**

- UI and MCP report the same action availability and reason for covered actions.
- An unavailable action cannot be invoked by bypassing either adapter.

### Phase 3 — Proposal engine and MCP submission

**Work**

- Add proposal/operation tables and migrations.
- Implement operation schemas, immutable plan drafts, full validation, semantic
  diffs, consequence analysis, and idempotency.
- Add proposal query/wait/review tools and `propose_plan_change`.
- Record proposal activities and expose proposal resource links.

**Exit criteria**

- Submitting a proposal leaves the working plan byte-for-byte unchanged.
- Duplicate retries return the same proposal.
- Invalid and stale proposals cannot reach human application.
- Proposal submission exposes no direct plan-mutation or publication capability.

### Phase 4 — Agent-native approval and atomic application

**Work**

- Add `apply_plan_change_proposal` with exact version/digest binding and
  consequential write annotations.
- Define and test supported-client per-call confirmation configuration.
- Return a complete, developer-readable review package in the conversation.
- Support agent-mediated revision and partial acceptance by creating a new
  immutable proposal version before confirmation.
- Add proposal inbox, count, detail/diff, editing, partial acceptance, rejection,
  and final consequence preview to the Plan Editor as an optional fallback.
- Add browser REST decision endpoints with session, Origin, and CSRF checks.
- Implement atomic revalidation/application and invalidation behavior.
- Add unpublished-plan follow-through after successful application.

**Exit criteria**

- A human can inspect every exact change in the agent conversation before
  applying it.
- The MCP client separately prompts for the exact consequential application
  tool call.
- A model message, proposal submission, or elicitation answer cannot apply a
  proposal.
- Auto-approved application-tool configurations are documented as unsupported
  and fail compatibility validation where the client exposes that information.
- Concurrent plan edits make the proposal stale.
- Partial acceptance produces a new reviewed digest and applies that final set
  atomically.
- Publication remains a separate explicit step.

### Phase 5 — Hardening and release

**Work**

- Cross-platform package smoke tests and supported-client compatibility tests.
- Load/size-limit tests, security tests, accessibility, and documentation.
- Document client configuration for Codex and other supported MCP clients.
- Add release notes and bump the package version.

**Exit criteria**

- Linux, macOS, and Windows CI pass.
- MCP startup, shutdown, and Ctrl+C cleanup are clean.
- Security and concurrency suites pass.
- Installed PyPI artifact contains the frontend and embedded MCP server.

## 14. Verification strategy

### Unit tests

- Operation schema parsing and canonical serialization.
- Draft application for every operation type.
- Duplicate IDs, unknown repositories, invalid paths, self-dependencies, and
  dependency cycles.
- Semantic diff and downstream consequence calculation.
- Published revision versus working-plan version behavior.
- Proposal state transitions and idempotency.
- Stale proposal detection.
- Package/approval invalidation calculation.
- Actor authorization and identity-spoof rejection.

### Service and database tests

- Additive migration from an existing `0.1.x` database.
- All-or-nothing multi-operation application.
- Rollback on the last operation.
- Human-edited and partially rejected operations.
- Deletion protection for dependency/evidence-bearing milestones.
- Activity records contain original proposer and deciding human.
- Existing REST mutations and proposal application share invariants.

### MCP contract tests

- Initialize, negotiate, list tools/resources, call tools, and close session.
- Exact input/output JSON schemas and annotations.
- Pagination, resource links, bounded reads, and typed errors.
- Idempotent proposal retry.
- Proposal wait timeout and wake-up.
- Review output contains the exact immutable digest used by application.
- Application rejects a different proposal version, digest, or working-plan
  version.
- Application is annotated as a consequential write; publish/push/PR/merge
  tools are not registered.
- Catch-all frontend routing does not affect `/mcp`.
- Parent lifespan starts and stops the MCP session manager.

### Security tests

- Rejected Host and Origin values.
- Non-loopback MCP startup refusal without explicit future opt-in.
- Absolute path, traversal, untracked file, symlink escape, binary file, and
  oversized resource rejection.
- Proposal operations cannot be supplied to or changed by the application tool.
- Unsupported clients without per-call confirmation receive
  `interactive_approval_required` and cannot use agent-native application.
- Browser decision requires human session and CSRF token.
- Payload actor fields cannot spoof `approved_by`/`decided_by`.
- Sanitized errors and logs contain no credentials or file contents.

### Frontend tests

- Proposal inbox and unread count.
- Exact diff, warnings, and dependency impact.
- Accept all, edit, partial accept, reject, stale, and failed states.
- Keyboard navigation, focus trapping, scroll containment, and accessible status
  announcements in the review modal.
- Successful application returns to the unpublished plan workflow.

### Packaging tests

- `pip` and `pipx` installation.
- `stateforge-dpt run` from an unrelated working directory.
- Browser and MCP endpoints from the packaged frontend wheel.
- UTF-8-safe startup banner on Windows consoles.
- Clean server shutdown on all supported platforms.

## 15. End-to-end acceptance scenario

1. A user runs `stateforge-dpt run` and sees browser and MCP URLs.
2. Codex connects to `/mcp` and lists the user's StateForge project.
3. Codex reads the work queue, opens milestone `R1`, and obtains its issue,
   approved context, implementation package, blockers, and available actions.
4. Codex proposes adding a missing verification and dependency using the current
   working-plan version and an idempotency key.
5. DPT validates the change, stores the diff, returns a proposal URL/ID, and does
   not alter the plan.
6. Codex presents DPT's review package in the conversation. The human asks it to
   edit the verification command, keep the dependency, and remove an unrelated
   wording change.
7. Codex creates and presents the revised immutable proposal and its new digest.
8. The human tells Codex to apply it. Codex requests the consequential
   `apply_plan_change_proposal` tool and the client asks the human to confirm.
9. After confirmation, DPT revalidates and applies that exact version in one
   transaction, invalidates affected context approval/package state, and
   increments the working version.
10. Codex receives `applied` with the final accepted diff. The Plan Editor shows
    unpublished changes. Nothing has been committed, pushed, or published.
11. The human separately chooses **Review & publish** when ready.

## 16. Out of scope

- Public or team-hosted DPT MCP service.
- OAuth for remote MCP clients.
- Application without a distinct per-call human confirmation.
- Autonomous Git publication or GitHub delivery actions.
- Arbitrary SQL, shell, or filesystem access.
- Synchronizing proposal records through `STATEFORGE_DPT.md`.
- Treating AI proposals as canonical project history.
- Treating an agent's prose response as evidence of human approval.

## 17. Definition of done

The feature is complete only when:

- one `stateforge-dpt run` process reliably serves UI, REST, and `/mcp`;
- the MCP interface exposes the same current project semantics as the UI;
- entities explain state, reasons, available actions, preconditions, and approval
  requirements;
- AI clients can obtain complete implementation packages and bounded context;
- AI clients can submit validated, idempotent plan proposals;
- proposals do not mutate the plan before a human decision;
- the agent presents DPT's exact review package and immutable digest;
- the client obtains a distinct per-call human confirmation before invoking the
  application tool;
- DPT atomically applies only the exact reviewed proposal version and digest;
- stale proposals cannot overwrite newer work;
- applying a proposal never publishes, commits, pushes, reviews, or merges;
- every relevant action and decision is auditable;
- the installed package works on Linux, macOS, and Windows; and
- the user documentation explains how to connect an AI client and how the human
  approval boundary works.

## 18. External protocol references

- [OpenAI: Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
  describes tool design, server-side authorization, and MCP elicitation.
- [OpenAI: Build a custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server)
  describes tool annotations and confirmation expectations for write actions.
- [OpenAI: Connectors and remote MCP servers](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
  documents approval behavior when MCP tools are used by OpenAI clients.
- [Official MCP Python SDK: mounting in an existing ASGI server](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md)
  documents Streamable HTTP mounting, parent lifespan ownership, and local
  Host/Origin protection.
