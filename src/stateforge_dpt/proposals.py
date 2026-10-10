"""Human-approved AI plan change proposals."""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from stateforge_dpt.database import Database
from stateforge_dpt.models import (
    PortableContextResource,
    PortablePlan,
    PortableRepository,
    PortableStep,
    PortableVerificationRequirement,
)
from stateforge_dpt.plan_file import (
    LoadedPlan,
    canonical_plan_json,
    plan_fingerprint,
)
from stateforge_dpt.service import DPTError, DPTService, utc_now

PROPOSAL_SCHEMA_VERSION = 1
MAX_PROPOSAL_OPERATIONS = 250
ProposalStatus = Literal[
    "awaiting_review",
    "applied",
    "rejected",
    "stale",
    "superseded",
    "cancelled",
    "failed",
]


class CreateStepOperation(BaseModel):
    """Create one complete portable milestone."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["create_step"]
    step: PortableStep


class UpdateStepOperation(BaseModel):
    """Update selected fields of an existing milestone."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["update_step"]
    step_id: str
    repository_alias: str | None = None
    release: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    required_result: str | None = Field(default=None, min_length=1, max_length=4000)
    branch_name: str | None = Field(default=None, min_length=1, max_length=240)
    depends_on: list[str] | None = None


class DeleteStepOperation(BaseModel):
    """Delete an unused milestone."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["delete_step"]
    step_id: str


class ReplaceStepContextOperation(BaseModel):
    """Replace one milestone's context and verification contract."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["replace_step_context"]
    step_id: str
    resources: list[PortableContextResource] = Field(
        default_factory=list, max_length=200
    )
    verification: list[PortableVerificationRequirement] = Field(
        default_factory=list, max_length=100
    )


class UpsertRepositoryOperation(BaseModel):
    """Create or replace one portable repository declaration by alias."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["upsert_repository"]
    repository: PortableRepository


class RenameRepositoryOperation(BaseModel):
    """Update one repository's GitHub identity without replacing its history."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["rename_repository"]
    repository_alias: str
    github_full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")


class RemoveRepositoryOperation(BaseModel):
    """Remove an unreferenced repository declaration."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["remove_repository"]
    repository_alias: str


class UpdateProjectOperation(BaseModel):
    """Update portable project metadata without changing its identity."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["update_project"]
    name: str | None = Field(default=None, min_length=1, max_length=100)
    release: str | None = Field(default=None, min_length=1, max_length=40)
    generated_filename: str | None = Field(default=None, max_length=240)
    plan_source_branch: str | None = Field(default=None, min_length=1, max_length=240)


class ReorderStepsOperation(BaseModel):
    """Assign deterministic order to every milestone."""

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["reorder_steps"]
    step_ids: list[str] = Field(min_length=1, max_length=5000)


PlanOperation = Annotated[
    CreateStepOperation
    | UpdateStepOperation
    | DeleteStepOperation
    | ReplaceStepContextOperation
    | UpsertRepositoryOperation
    | RenameRepositoryOperation
    | RemoveRepositoryOperation
    | UpdateProjectOperation
    | ReorderStepsOperation,
    Field(discriminator="kind"),
]
PLAN_OPERATION_ADAPTER = TypeAdapter(list[PlanOperation])


class PlanChangeProposalRequest(BaseModel):
    """A complete agent-authored plan proposal."""

    project_id: str
    base_working_plan_version: int = Field(ge=0)
    rationale: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=8, max_length=200)
    operations: list[PlanOperation] = Field(
        min_length=1, max_length=MAX_PROPOSAL_OPERATIONS
    )
    supersedes_proposal_id: str | None = None
    client_name: str = Field(default="MCP client", min_length=1, max_length=100)
    client_version: str | None = Field(default=None, max_length=100)


class ProposalDecisionRequest(BaseModel):
    """A human decision about one immutable proposal version."""

    decision: Literal["apply", "reject"]
    proposal_version: int = Field(ge=1)
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(default="", max_length=4000)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _review_digest(
    proposal_id: str,
    proposal_version: int,
    base_working_plan_version: int,
    draft_fingerprint: str,
    operations: list[dict[str, Any]],
    diff: dict[str, Any],
) -> str:
    payload = {
        "proposal_id": proposal_id,
        "proposal_version": proposal_version,
        "base_working_plan_version": base_working_plan_version,
        "draft_plan_fingerprint": draft_fingerprint,
        "operations": operations,
        "diff": diff,
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _by_id(items: list[PortableStep]) -> dict[str, PortableStep]:
    return {item.step_id: item for item in items}


def apply_plan_operations(
    plan: PortablePlan,
    operations: list[PlanOperation],
) -> PortablePlan:
    """Apply typed operations to an immutable portable plan draft."""
    project = plan.project
    repositories = list(plan.repositories)
    steps = list(plan.steps)

    for operation in operations:
        if isinstance(operation, CreateStepOperation):
            if any(step.step_id == operation.step.step_id for step in steps):
                raise DPTError(f"Step already exists: {operation.step.step_id}")
            steps.append(operation.step)
        elif isinstance(operation, UpdateStepOperation):
            index = next(
                (
                    i
                    for i, step in enumerate(steps)
                    if step.step_id == operation.step_id
                ),
                None,
            )
            if index is None:
                raise DPTError(f"Step not found: {operation.step_id}")
            changes = operation.model_dump(
                exclude={"operation_id", "kind", "step_id"}, exclude_none=True
            )
            if not changes:
                raise DPTError(
                    f"Update for {operation.step_id} does not change a field"
                )
            steps[index] = steps[index].model_copy(update=changes)
        elif isinstance(operation, DeleteStepOperation):
            existing = _by_id(steps)
            if operation.step_id not in existing:
                raise DPTError(f"Step not found: {operation.step_id}")
            steps = [step for step in steps if step.step_id != operation.step_id]
        elif isinstance(operation, ReplaceStepContextOperation):
            index = next(
                (
                    i
                    for i, step in enumerate(steps)
                    if step.step_id == operation.step_id
                ),
                None,
            )
            if index is None:
                raise DPTError(f"Step not found: {operation.step_id}")
            resources = [
                resource.model_copy(update={"sort_order": order})
                for order, resource in enumerate(operation.resources)
            ]
            verification = [
                requirement.model_copy(update={"sort_order": order})
                for order, requirement in enumerate(operation.verification)
            ]
            steps[index] = steps[index].model_copy(
                update={
                    "context_resources": resources,
                    "verification_requirements": verification,
                }
            )
        elif isinstance(operation, UpsertRepositoryOperation):
            index = next(
                (
                    i
                    for i, repository in enumerate(repositories)
                    if repository.alias == operation.repository.alias
                ),
                None,
            )
            if index is None:
                repositories.append(operation.repository)
            else:
                repositories[index] = operation.repository
        elif isinstance(operation, RenameRepositoryOperation):
            index = next(
                (
                    i
                    for i, repository in enumerate(repositories)
                    if repository.alias == operation.repository_alias
                ),
                None,
            )
            if index is None:
                raise DPTError(f"Repository not found: {operation.repository_alias}")
            repositories[index] = repositories[index].model_copy(
                update={"github_full_name": operation.github_full_name}
            )
        elif isinstance(operation, RemoveRepositoryOperation):
            if not any(
                repository.alias == operation.repository_alias
                for repository in repositories
            ):
                raise DPTError(f"Repository not found: {operation.repository_alias}")
            repositories = [
                repository
                for repository in repositories
                if repository.alias != operation.repository_alias
            ]
        elif isinstance(operation, UpdateProjectOperation):
            changes = operation.model_dump(
                exclude={"operation_id", "kind"}, exclude_none=True
            )
            if not changes:
                raise DPTError("Project update does not change a field")
            project = project.model_copy(update=changes)
        elif isinstance(operation, ReorderStepsOperation):
            existing_ids = [step.step_id for step in steps]
            if len(operation.step_ids) != len(set(operation.step_ids)):
                raise DPTError("Step order repeats an ID")
            if set(operation.step_ids) != set(existing_ids):
                raise DPTError(
                    "Step order must contain every current step exactly once"
                )
            ordered = _by_id(steps)
            steps = [
                ordered[step_id].model_copy(update={"sort_order": index})
                for index, step_id in enumerate(operation.step_ids)
            ]

    # A proposal must not silently rewrite unrelated milestones. Sparse ordering is
    # intentional: it lets a new milestone occupy an unused position without
    # invalidating approvals and implementation packages for every existing step.
    # Only an explicit ReorderStepsOperation may assign new sort_order values.
    ordered_steps = sorted(steps, key=lambda item: (item.sort_order, item.step_id))
    return PortablePlan.model_validate(
        {
            "schema_version": plan.schema_version,
            "project": project.model_dump(mode="json"),
            "repositories": [item.model_dump(mode="json") for item in repositories],
            "steps": [item.model_dump(mode="json") for item in ordered_steps],
        }
    )


def semantic_plan_diff(before: PortablePlan, after: PortablePlan) -> dict[str, Any]:
    """Return a stable, reviewable semantic plan diff."""
    before_project = before.project.model_dump(mode="json")
    after_project = after.project.model_dump(mode="json")
    project_changes = {
        key: {"before": before_project[key], "after": after_project[key]}
        for key in before_project
        if before_project[key] != after_project[key]
    }

    before_repositories = {item.alias: item for item in before.repositories}
    after_repositories = {item.alias: item for item in after.repositories}
    added_repositories = sorted(after_repositories.keys() - before_repositories.keys())
    removed_repositories = sorted(
        before_repositories.keys() - after_repositories.keys()
    )
    changed_repositories = []
    for alias in sorted(before_repositories.keys() & after_repositories.keys()):
        left = before_repositories[alias].model_dump(mode="json")
        right = after_repositories[alias].model_dump(mode="json")
        if left != right:
            changed_repositories.append(
                {"alias": alias, "before": left, "after": right}
            )

    before_steps = _by_id(before.steps)
    after_steps = _by_id(after.steps)
    added_steps = sorted(after_steps.keys() - before_steps.keys())
    removed_steps = sorted(before_steps.keys() - after_steps.keys())
    changed_steps: list[dict[str, Any]] = []
    for step_id in sorted(before_steps.keys() & after_steps.keys()):
        left = before_steps[step_id].model_dump(mode="json")
        right = after_steps[step_id].model_dump(mode="json")
        if left != right:
            fields = sorted(key for key in left if left[key] != right[key])
            changed_steps.append(
                {"step_id": step_id, "fields": fields, "before": left, "after": right}
            )
    affected_steps = sorted(
        set(added_steps)
        | set(removed_steps)
        | {item["step_id"] for item in changed_steps}
    )
    summaries: list[str] = []
    if project_changes:
        summaries.append(f"Update {len(project_changes)} project field(s)")
    if added_repositories:
        summaries.append(f"Add {len(added_repositories)} repository declaration(s)")
    if removed_repositories:
        summaries.append(
            f"Remove {len(removed_repositories)} repository declaration(s)"
        )
    if changed_repositories:
        summaries.append(
            f"Update {len(changed_repositories)} repository declaration(s)"
        )
    if added_steps:
        summaries.append(f"Create {len(added_steps)} milestone(s)")
    if changed_steps:
        summaries.append(f"Update {len(changed_steps)} milestone(s)")
    if removed_steps:
        summaries.append(f"Delete {len(removed_steps)} milestone(s)")
    return {
        "summary": summaries or ["No semantic plan change"],
        "project": project_changes,
        "repositories": {
            "added": added_repositories,
            "removed": removed_repositories,
            "changed": changed_repositories,
        },
        "steps": {
            "added": added_steps,
            "removed": removed_steps,
            "changed": changed_steps,
        },
        "affected_step_ids": affected_steps,
    }


class PlanProposalService:
    """Validate, persist, review, and atomically apply plan proposals."""

    def __init__(self, database: Database, service: DPTService) -> None:
        """Bind proposal persistence to the shared DPT database and service."""
        self.database = database
        self.service = service

    def create(
        self,
        request: PlanChangeProposalRequest,
        *,
        proposer_id: str = "local-mcp-agent",
    ) -> dict[str, Any]:
        """Validate and store an immutable proposal without changing the plan."""
        project = self.service.project(request.project_id)
        existing = self._by_idempotency(proposer_id, request.idempotency_key)
        if existing is not None:
            return existing
        current_version = int(project["working_plan_version"])
        if request.base_working_plan_version != current_version:
            raise DPTError(
                "proposal_stale: the working plan changed; read the project and "
                "build a new proposal"
            )
        before = self.service.portable_plan(request.project_id)
        base_fingerprint = plan_fingerprint(before)
        self._validate_deletions(request.project_id, request.operations)
        after = apply_plan_operations(before, request.operations)
        if canonical_plan_json(before) == canonical_plan_json(after):
            raise DPTError("Proposal does not change the plan")
        diff = semantic_plan_diff(before, after)
        affected = diff["affected_step_ids"]
        consequences = {
            "affected_step_ids": affected,
            "context_approvals_invalidated": affected,
            "implementation_packages_invalidated": affected,
            "local_plan_becomes_unpublished": True,
            "publication_is_separate": True,
        }

        proposal_id = str(uuid.uuid4())
        proposal_version = 1
        superseded: dict[str, Any] | None = None
        if request.supersedes_proposal_id:
            superseded = self.get(request.supersedes_proposal_id, refresh_stale=False)
            if superseded["project_id"] != request.project_id:
                raise DPTError("Superseded proposal belongs to another project")
            if superseded["status"] != "awaiting_review":
                raise DPTError("Only an awaiting proposal can be superseded")
            proposal_version = int(superseded["proposal_version"]) + 1

        operation_payloads = [
            operation.model_dump(mode="json") for operation in request.operations
        ]
        draft_fingerprint = plan_fingerprint(after)
        digest = _review_digest(
            proposal_id,
            proposal_version,
            current_version,
            draft_fingerprint,
            operation_payloads,
            diff,
        )
        timestamp = utc_now()
        with self.database.connect() as connection:
            if superseded is not None:
                connection.execute(
                    """
                    UPDATE plan_change_proposals
                    SET status = 'superseded', updated_at = ?
                    WHERE proposal_id = ? AND status = 'awaiting_review'
                    """,
                    (timestamp, superseded["proposal_id"]),
                )
            connection.execute(
                """
                INSERT INTO plan_change_proposals(
                    proposal_id, project_id, base_working_plan_version,
                    base_plan_fingerprint, proposal_version, status, rationale,
                    proposer_kind, proposer_id, client_name, client_version,
                    idempotency_key, supersedes_proposal_id, draft_plan_json,
                    draft_plan_fingerprint, validation_json, diff_json,
                    consequences_json, review_digest, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'awaiting_review', ?, 'agent', ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    request.project_id,
                    current_version,
                    base_fingerprint,
                    proposal_version,
                    request.rationale,
                    proposer_id,
                    request.client_name,
                    request.client_version,
                    request.idempotency_key,
                    request.supersedes_proposal_id,
                    canonical_plan_json(after),
                    draft_fingerprint,
                    _canonical(
                        {"valid": True, "schema_version": PROPOSAL_SCHEMA_VERSION}
                    ),
                    _canonical(diff),
                    _canonical(consequences),
                    digest,
                    timestamp,
                    timestamp,
                ),
            )
            connection.executemany(
                """
                INSERT INTO plan_change_proposal_operations(
                    proposal_id, operation_id, sort_order, operation_kind,
                    proposed_payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        proposal_id,
                        payload["operation_id"],
                        index,
                        payload["kind"],
                        _canonical(payload),
                    )
                    for index, payload in enumerate(operation_payloads)
                ],
            )
            self.service._activity(
                connection,
                request.project_id,
                "plan.proposal.created",
                f"AI proposed {len(operation_payloads)} plan change(s)",
                {
                    "proposal_id": proposal_id,
                    "proposal_version": proposal_version,
                    "review_digest": digest,
                    "summary": diff["summary"],
                },
                proposer_id,
            )
        return self.get(proposal_id)

    def list(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """List recent proposals for a project, newest first."""
        self.service.project(project_id)
        with self.database.connect() as connection:
            ids = [
                str(row["proposal_id"])
                for row in connection.execute(
                    """
                    SELECT proposal_id FROM plan_change_proposals
                    WHERE project_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (project_id, min(max(limit, 1), 500)),
                ).fetchall()
            ]
        return [self.get(proposal_id) for proposal_id in ids]

    def get(self, proposal_id: str, *, refresh_stale: bool = True) -> dict[str, Any]:
        """Return a complete review package and refresh its stale status."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plan_change_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise DPTError("Proposal not found")
            if refresh_stale and row["status"] == "awaiting_review":
                current = connection.execute(
                    "SELECT working_plan_version FROM projects WHERE project_id = ?",
                    (row["project_id"],),
                ).fetchone()
                if current is None:
                    raise DPTError("Project not found")
                if int(current["working_plan_version"]) != int(
                    row["base_working_plan_version"]
                ):
                    connection.execute(
                        """
                        UPDATE plan_change_proposals
                        SET status = 'stale', updated_at = ? WHERE proposal_id = ?
                        """,
                        (utc_now(), proposal_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM plan_change_proposals WHERE proposal_id = ?",
                        (proposal_id,),
                    ).fetchone()
            operations = [
                json.loads(operation["proposed_payload_json"])
                for operation in connection.execute(
                    """
                    SELECT proposed_payload_json
                    FROM plan_change_proposal_operations
                    WHERE proposal_id = ? ORDER BY sort_order
                    """,
                    (proposal_id,),
                ).fetchall()
            ]
        if row is None:  # Defensive: the row was verified in the same transaction.
            raise DPTError("Proposal not found")
        result = dict(row)
        for key in (
            "validation_json",
            "diff_json",
            "consequences_json",
            "draft_plan_json",
        ):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        result["operations"] = operations
        result["schema_version"] = PROPOSAL_SCHEMA_VERSION
        result["review_markdown"] = self._review_markdown(result)
        return result

    def reject(
        self,
        proposal_id: str,
        decision: ProposalDecisionRequest,
        *,
        decided_by: str,
        channel: str,
    ) -> dict[str, Any]:
        """Record an exact human rejection without changing the plan."""
        if decision.decision != "reject":
            raise DPTError("Reject endpoint requires a reject decision")
        proposal = self.get(proposal_id)
        if (
            proposal["status"] == "rejected"
            and int(proposal["proposal_version"]) == decision.proposal_version
            and proposal["review_digest"] == decision.review_digest
        ):
            return proposal
        self._assert_exact_decision(proposal, decision)
        timestamp = utc_now()
        with self.database.connect() as connection:
            result = connection.execute(
                """
                UPDATE plan_change_proposals
                SET status = 'rejected', decided_at = ?, decided_by = ?,
                    decision_channel = ?, decision_reason = ?, updated_at = ?
                WHERE proposal_id = ? AND status = 'awaiting_review'
                """,
                (
                    timestamp,
                    decided_by,
                    channel,
                    decision.reason,
                    timestamp,
                    proposal_id,
                ),
            )
            if result.rowcount != 1:
                raise DPTError("Proposal is no longer awaiting review")
            self.service._activity(
                connection,
                str(proposal["project_id"]),
                "plan.proposal.rejected",
                "Rejected an AI plan proposal",
                {"proposal_id": proposal_id, "reason": decision.reason},
                decided_by,
            )
        return self.get(proposal_id)

    def apply(
        self,
        proposal_id: str,
        decision: ProposalDecisionRequest,
        *,
        decided_by: str,
        channel: str,
    ) -> dict[str, Any]:
        """Atomically apply the exact human-confirmed proposal to SQLite."""
        if decision.decision != "apply":
            raise DPTError("Apply endpoint requires an apply decision")
        proposal = self.get(proposal_id)
        if (
            proposal["status"] == "applied"
            and int(proposal["proposal_version"]) == decision.proposal_version
            and proposal["review_digest"] == decision.review_digest
        ):
            return proposal
        self._assert_exact_decision(proposal, decision)
        timestamp = utc_now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plan_change_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            project = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (proposal["project_id"],),
            ).fetchone()
            if row is None or row["status"] != "awaiting_review":
                raise DPTError("Proposal is no longer awaiting review")
            if project is None:
                raise DPTError("Project not found")
            if int(project["working_plan_version"]) != int(
                proposal["base_working_plan_version"]
            ):
                raise DPTError("proposal_stale: the working plan changed")

            current_plan = self.service.portable_plan(str(proposal["project_id"]))
            operations = PLAN_OPERATION_ADAPTER.validate_python(proposal["operations"])
            rebuilt = apply_plan_operations(current_plan, operations)
            rebuilt_fingerprint = plan_fingerprint(rebuilt)
            if rebuilt_fingerprint != proposal["draft_plan_fingerprint"]:
                raise DPTError("Proposal draft no longer matches its review digest")

            unchanged_steps = self._unchanged_step_ids(current_plan, rebuilt)
            preserved = self._snapshot_step_state(
                connection, proposal["project_id"], unchanged_steps
            )
            loaded = LoadedPlan(
                path=Path("AI_PLAN_PROPOSAL"),
                plan=rebuilt,
                fingerprint=rebuilt_fingerprint,
                file_digest="",
            )
            self.service._replace_project_plan(
                connection,
                str(proposal["project_id"]),
                loaded,
                "",
                "",
                source_file_is_worktree=False,
                preserve_github_artifacts=True,
                update_published_baseline=False,
            )
            self._restore_step_state(connection, preserved)
            applied_version = int(project["working_plan_version"]) + 1
            connection.execute(
                """
                UPDATE plan_change_proposals
                SET status = 'applied', decided_at = ?, decided_by = ?,
                    decision_channel = ?, decision_reason = ?,
                    applied_working_plan_version = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    timestamp,
                    decided_by,
                    channel,
                    decision.reason,
                    applied_version,
                    timestamp,
                    proposal_id,
                ),
            )
            self.service._activity(
                connection,
                str(proposal["project_id"]),
                "plan.proposal.applied",
                "Applied a human-confirmed AI plan proposal",
                {
                    "proposal_id": proposal_id,
                    "proposal_version": proposal["proposal_version"],
                    "review_digest": proposal["review_digest"],
                    "working_plan_version": applied_version,
                    "summary": proposal["diff"]["summary"],
                },
                decided_by,
            )
        return self.get(proposal_id)

    def _by_idempotency(
        self, proposer_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT proposal_id FROM plan_change_proposals
                WHERE proposer_id = ? AND idempotency_key = ?
                """,
                (proposer_id, idempotency_key),
            ).fetchone()
        return self.get(str(row["proposal_id"])) if row is not None else None

    def _validate_deletions(
        self, project_id: str, operations: builtins.list[PlanOperation]
    ) -> None:
        dashboard = self.service.dashboard(project_id)
        by_id = {step["step_id"]: step for step in dashboard["steps"]}
        for operation in operations:
            if isinstance(operation, DeleteStepOperation):
                step = by_id.get(operation.step_id)
                if step is not None and step.get("artifacts"):
                    raise DPTError(
                        f"Step {operation.step_id} has GitHub delivery evidence and "
                        "cannot be deleted"
                    )
            if isinstance(operation, RemoveRepositoryOperation):
                with self.database.connect() as connection:
                    repository = connection.execute(
                        """
                        SELECT repository_id FROM repositories
                        WHERE project_id = ? AND alias = ?
                        """,
                        (project_id, operation.repository_alias),
                    ).fetchone()
                    if repository is None:
                        continue
                    artifact = connection.execute(
                        """
                        SELECT 1 FROM github_artifacts
                        WHERE repository_id = ? LIMIT 1
                        """,
                        (repository["repository_id"],),
                    ).fetchone()
                if artifact is not None:
                    raise DPTError(
                        f"Repository {operation.repository_alias} has GitHub delivery "
                        "evidence and cannot be removed"
                    )
            if isinstance(operation, UpsertRepositoryOperation):
                with self.database.connect() as connection:
                    repository = connection.execute(
                        """
                        SELECT repository_id, github_full_name FROM repositories
                        WHERE project_id = ? AND alias = ?
                        """,
                        (project_id, operation.repository.alias),
                    ).fetchone()
                    if (
                        repository is None
                        or str(repository["github_full_name"]).casefold()
                        == operation.repository.github_full_name.casefold()
                    ):
                        continue
                    artifact = connection.execute(
                        """
                        SELECT 1 FROM github_artifacts
                        WHERE repository_id = ? LIMIT 1
                        """,
                        (repository["repository_id"],),
                    ).fetchone()
                if artifact is not None:
                    raise DPTError(
                        f"Repository {operation.repository.alias} has GitHub delivery "
                        "evidence; its GitHub identity cannot be replaced"
                    )

    @staticmethod
    def _assert_exact_decision(
        proposal: dict[str, Any], decision: ProposalDecisionRequest
    ) -> None:
        if proposal["status"] != "awaiting_review":
            raise DPTError(f"Proposal is {proposal['status']}, not awaiting review")
        if int(proposal["proposal_version"]) != decision.proposal_version:
            raise DPTError("Proposal version changed; review it again")
        if proposal["review_digest"] != decision.review_digest:
            raise DPTError("Proposal review digest does not match")

    @staticmethod
    def _unchanged_step_ids(before: PortablePlan, after: PortablePlan) -> set[str]:
        left = _by_id(before.steps)
        right = _by_id(after.steps)
        return {
            step_id
            for step_id in left.keys() & right.keys()
            if left[step_id].model_dump(mode="json")
            == right[step_id].model_dump(mode="json")
        }

    @staticmethod
    def _snapshot_step_state(
        connection: sqlite3.Connection, project_id: str, step_ids: set[str]
    ) -> dict[str, builtins.list[dict[str, Any]]]:
        if not step_ids:
            return {}
        placeholders = ",".join("?" for _ in step_ids)
        parameters = (project_id, *sorted(step_ids))
        tables = (
            "context_observations",
            "verification_observations",
            "context_approvals",
            "implementation_packages",
        )
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE project_id = ? "  # noqa: S608
                    f"AND step_id IN ({placeholders})",
                    parameters,
                ).fetchall()
            ]
            for table in tables
        }

    @staticmethod
    def _restore_step_state(
        connection: sqlite3.Connection,
        preserved: dict[str, builtins.list[dict[str, Any]]],
    ) -> None:
        for table, rows in preserved.items():
            for row in rows:
                columns = list(row)
                placeholders = ",".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO {table} ({','.join(columns)}) "  # noqa: S608
                    f"VALUES ({placeholders})",
                    tuple(row[column] for column in columns),
                )

    @staticmethod
    def _review_markdown(proposal: dict[str, Any]) -> str:
        diff = proposal["diff"]
        lines = [
            f"# Plan proposal {proposal['proposal_id']}",
            "",
            f"**Status:** `{proposal['status']}`  ",
            f"**Version:** `{proposal['proposal_version']}`  ",
            f"**Review digest:** `{proposal['review_digest']}`  ",
            f"**Base working plan:** `{proposal['base_working_plan_version']}`",
            "",
            "## Rationale",
            "",
            str(proposal["rationale"]),
            "",
            "## Summary",
            "",
        ]
        lines.extend(f"- {item}" for item in diff["summary"])
        lines.extend(["", "## Exact operations", ""])
        for index, operation in enumerate(proposal["operations"], start=1):
            lines.extend(
                [
                    f"### {index}. `{operation['kind']}`",
                    "",
                    "```json",
                    json.dumps(operation, ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            )
        lines.extend(
            [
                "## Consequences",
                "",
                "- Updates only the local SQLite working plan.",
                "- Does not publish, commit, push, open, review, or merge anything.",
                "- Publishing `STATEFORGE_DPT.md` remains a separate human action.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"
