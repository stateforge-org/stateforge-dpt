# AI regression resistance

StateForge DPT makes the implementation boundary for a milestone explicit before
an AI agent or a human changes code. It does not attempt to predict a defect
probability and it does not replace review. It gives the people planning,
implementing, and reviewing work the same versioned context and a traceable
record of what was known at each decision.

## The control loop

```text
Human authors milestone and context contract
                    |
                    v
DPT resolves resources and records their Git provenance
                    |
                    v
Human reviews and approves the exact context snapshot
                    |
                    v
DPT creates a deterministic implementation package
                    |
                    v
Agent or developer implements only the requested milestone
                    |
                    v
Human pushes, opens the pull request, reviews, and merges
                    |
                    v
DPT compares the PR with the declared impact and verification contract
                    |
                    v
Human accepts unexpected impact and verification evidence
```

DPT never commits, pushes, reviews, or merges on behalf of the human. Local
commits made by an implementation agent remain subject to the normal GitHub
review workflow.

## Context contracts

Every milestone can identify resources from any managed repository:

- **Must understand** — contracts, consumers, existing implementations, and
  decisions that must be read first.
- **Expected to change** — the planned pull-request change surface. Additional
  changes are allowed, but become visible during impact review.
- **Must verify** — tests, fixtures, schemas, and generated output that supply
  evidence.
- **Must preserve** — behavior and contracts that the implementation must not
  regress.

Resources have an authority label: canonical, reference, generated,
compatibility-only, deprecated, or historical. A resource can select a whole
file, directory, glob, symbol, Protobuf declaration, JSON Schema definition,
Markdown heading, or test case.

The repository browser exposes Git-tracked files, directory selection, text
previews, and conservatively detected declarations. Context links are ordered,
repository-relative, and portable. Absolute paths never enter
`STATEFORGE_DPT.md`.

## Integrity and approval

Inspection resolves each required resource without following symbolic links or
repository escapes. DPT records the repository commit, a content digest,
selector presence, and whether the resource is current, changed, missing, or
unavailable. Reads and generated packages are bounded.

Approval is an explicit human action. It records the approving identity, plan
revision, time, repository commits, and resource hashes. A required resource
that changes, disappears, or no longer contains its selector invalidates the
approved snapshot and prevents a new implementation package from being issued.

## Read-only discovery

Impact discovery searches tracked files using milestone terms. Its results are
suggestions, never plan changes. A human may:

- add a suggestion to the portable plan;
- include it only in the current implementation package; or
- reject it with a reason.

Execution-only and rejected decisions stay local. This keeps machine-derived
evidence out of the portable human intent while preventing the same rejected
candidate from being presented as an undecided discovery.

## Implementation packages

**Start implementation** produces deterministic JSON and Markdown containing:

- the milestone result, branch, dependencies, and active blockers;
- approved context, roles, authorities, contents, commits, and hashes;
- execution-only discoveries approved for this run;
- preserved invariants and the expected change surface;
- verification requirements;
- previous related regressions and review findings; and
- an explainable risk classification.

The package hash and exact JSON are stored locally. This is provenance: if a
later defect appears, reviewers can determine which context was available when
the implementation began. The format is vendor-neutral and can be supplied to
any agent.

## Verification and pull-request impact

Verification requirements are portable intent. Results are local or GitHub
evidence. A human can record passed, failed, skipped, or unavailable evidence.
For an executable check, DPT shows the exact command and requires confirmation
before running it in the selected managed repository. The command is parsed
into arguments and launched without a shell, with a timeout and bounded output.
The result records the human identity, repository commit, exit code, time, and
output excerpt.

After reconciliation, DPT compares a pull request with the approved contract:

- expected and unexpected changed files;
- expected areas left untouched;
- verification resources changed;
- preserved resources changed;
- GitHub check state; and
- the latest recorded verification evidence.

Unexpected files require an explicit human impact decision. The decision is
bound to a hash of the reviewed change surface and becomes stale when that
surface changes.

## Regression memory

GitHub remains authoritative for blockers and corrective work. DPT connects a
resolved blocker with its original milestone, related corrective pull request,
changed files, review findings, and the implementation package that was used.
When a later milestone declares one of those corrective files as context, the
previous regression is surfaced automatically and included in its next
implementation package.

This is evidence retrieval, not a claim that the new work will fail.

## Readiness and risk

Context readiness is independent from delivery state. A milestone is ready to
start only when dependencies are complete, active GitHub blockers are clear,
required context resolves, the current snapshot is human-approved, required
verification is defined, and any reconciled unexpected change surface has been
reviewed.

Review pressure is classified as green, amber, or red from visible structural
factors such as cross-repository scope, public contracts, security or
persistence concerns, prior regressions, verification gaps, and unexpected
files. Every factor is shown. The classification is not a defect forecast.

## Portable intent and local evidence

The following travel through Git in `STATEFORGE_DPT.md`:

- context resources, roles, explanations, selectors, required status, and
  authority;
- verification requirements; and
- milestone dependencies and delivery intent.

The following remain local or are re-derived from Git and GitHub:

- current commits and resource hashes;
- approvals and implementation packages;
- discovery decisions;
- verification results and command output;
- pull-request changes and impact reviews; and
- regression observations.

This separation lets the same plan resolve on computers with different clone
locations while keeping changing evidence honest and machine-specific data out
of the shared contract.
