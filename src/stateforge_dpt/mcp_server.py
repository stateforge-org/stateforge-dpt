"""Embedded MCP interface for AI-assisted DPT workflows."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations

from stateforge_dpt import __version__
from stateforge_dpt.proposals import (
    PLAN_OPERATION_ADAPTER,
    PlanChangeProposalRequest,
    PlanProposalService,
    ProposalDecisionRequest,
)
from stateforge_dpt.service import DPTError, DPTService

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
LOCAL_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
CONSEQUENTIAL_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)
WORK_QUEUE_STATES = (
    "planned",
    "ready",
    "pending",
    "in_review",
    "blocked",
    "completed",
    "cancelled",
)


def _tool_error(error: DPTError) -> ToolError:
    return ToolError(str(error))


def _actions(
    kind: Literal["project", "step", "proposal"],
    entity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe authoritative actions available for one entity."""
    if kind == "project":
        return [
            {
                "action_id": "propose_plan_change",
                "title": "Propose a plan change",
                "available": True,
                "reason": (
                    "The proposal is validated and stored without changing the plan."
                ),
                "approval": "human_confirmation_before_apply",
                "local_state_change": True,
                "external_state_change": False,
            },
            {
                "action_id": "publish_plan",
                "title": "Review and publish the canonical plan",
                "available": False,
                "reason": "Publishing remains a separate human-owned DPT workflow.",
                "approval": "browser_human_review",
                "local_state_change": True,
                "external_state_change": True,
            },
        ]
    if kind == "step":
        readiness = entity.get("context_readiness") or {}
        ready = bool(readiness.get("ready"))
        return [
            {
                "action_id": "get_implementation_package",
                "title": "Read the approved implementation package",
                "available": ready,
                "reason": (
                    "The context contract is ready."
                    if ready
                    else "; ".join(
                        readiness.get("reasons") or ["Context is not ready."]
                    )
                ),
                "approval": "none",
                "local_state_change": False,
                "external_state_change": False,
            },
            {
                "action_id": "propose_plan_change",
                "title": "Propose changes to this milestone",
                "available": True,
                "reason": "Changes remain pending until a human confirms application.",
                "approval": "human_confirmation_before_apply",
                "local_state_change": True,
                "external_state_change": False,
            },
        ]
    status = str(entity.get("status"))
    return [
        {
            "action_id": "apply_plan_change_proposal",
            "title": "Apply this exact proposal to the local plan",
            "available": status == "awaiting_review",
            "reason": (
                "The proposal is current and awaiting human confirmation."
                if status == "awaiting_review"
                else f"The proposal is {status}."
            ),
            "approval": "agent_client_confirmation",
            "local_state_change": True,
            "external_state_change": False,
        },
        {
            "action_id": "reject_plan_change_proposal",
            "title": "Reject this proposal",
            "available": status == "awaiting_review",
            "reason": (
                "The proposal is awaiting a decision."
                if status == "awaiting_review"
                else f"The proposal is {status}."
            ),
            "approval": "agent_client_confirmation",
            "local_state_change": True,
            "external_state_change": False,
        },
    ]


def _excerpt(value: object, *, limit: int = 320) -> str | None:
    """Return a bounded single-line description for discovery responses."""
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _artifact_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    """Keep artifact identity and delivery state without bodies or timelines."""
    keys = (
        "artifact_id",
        "kind",
        "number",
        "title",
        "state",
        "state_reason",
        "html_url",
        "head_branch",
        "base_branch",
        "is_draft",
        "is_merged",
        "checks_state",
        "updated_at",
        "repository_alias",
        "github_full_name",
    )
    return {key: artifact.get(key) for key in keys if artifact.get(key) is not None}


def _blocker_summary(blocker: dict[str, Any]) -> dict[str, Any]:
    """Return one compact GitHub blocker card."""
    return {
        "work_item_kind": "blocker",
        "blocker_id": blocker.get("blocker_id") or blocker.get("artifact_id"),
        "artifact_id": blocker.get("artifact_id"),
        "repository_alias": blocker.get("repository_alias"),
        "github_full_name": blocker.get("github_full_name"),
        "number": blocker.get("number"),
        "title": blocker.get("title"),
        "state": blocker.get("state"),
        "status": blocker.get("status"),
        "is_active": bool(blocker.get("is_active")),
        "state_reason": blocker.get("state_reason"),
        "html_url": blocker.get("html_url"),
        "blocked_step_ids": blocker.get("blocked_step_ids") or [],
        "suggested_branch": blocker.get("suggested_branch"),
        "artifacts": [
            _artifact_summary(artifact) for artifact in blocker.get("artifacts") or []
        ],
    }


def _blocker_reference(blocker: dict[str, Any]) -> dict[str, Any]:
    """Reference a milestone blocker without repeating its evidence card."""
    return {
        "work_item_kind": "blocker",
        "blocker_id": blocker.get("blocker_id") or blocker.get("artifact_id"),
        "artifact_id": blocker.get("artifact_id"),
        "repository_alias": blocker.get("repository_alias"),
        "number": blocker.get("number"),
        "title": blocker.get("title"),
        "state": blocker.get("state"),
        "is_active": bool(blocker.get("is_active")),
        "html_url": blocker.get("html_url"),
    }


def _step_summary(step: dict[str, Any]) -> dict[str, Any]:
    """Return an agent-friendly milestone card without embedded evidence logs."""
    readiness = step.get("context_readiness") or {}
    risk = step.get("risk_assessment") or {}
    available_action_ids = ["propose_plan_change"]
    if bool(readiness.get("ready")):
        available_action_ids.insert(0, "get_implementation_package")
    item = {
        "work_item_kind": "step",
        "project_id": step.get("project_id"),
        "step_id": step.get("step_id"),
        "repository_id": step.get("repository_id"),
        "repository_alias": step.get("repository_alias"),
        "github_full_name": step.get("github_full_name"),
        "release": step.get("release"),
        "title": step.get("title"),
        "required_result_excerpt": _excerpt(step.get("required_result")),
        "branch_name": step.get("branch_name"),
        "sort_order": step.get("sort_order"),
        "depends_on": step.get("depends_on") or [],
        "dependencies_complete": bool(step.get("dependencies_complete")),
        "blockers_clear": bool(step.get("blockers_clear")),
        "observed_state": step.get("observed_state"),
        "computed_state": step.get("computed_state"),
        "context_readiness": {
            "state": readiness.get("state"),
            "ready": bool(readiness.get("ready")),
            "reasons": readiness.get("reasons") or [],
        },
        "risk_level": risk.get("level"),
        "context_resource_count": len(step.get("context_resources") or []),
        "verification_requirement_count": len(
            step.get("verification_requirements") or []
        ),
        "artifacts": [
            _artifact_summary(artifact) for artifact in step.get("artifacts") or []
        ],
        "blockers": [
            _blocker_reference(blocker) for blocker in step.get("blockers") or []
        ],
        "available_action_ids": available_action_ids,
    }
    return item


def _roadmap(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute dependency stages using the same declared graph as the UI."""
    steps = {str(item["step_id"]): item for item in dashboard["steps"]}
    levels: dict[str, int] = {}

    def level(step_id: str, visiting: set[str] | None = None) -> int:
        if step_id in levels:
            return levels[step_id]
        active = set() if visiting is None else set(visiting)
        if step_id in active:
            return 0
        active.add(step_id)
        dependencies = steps[step_id].get("depends_on") or []
        value = (
            0
            if not dependencies
            else 1 + max(level(str(dependency), active) for dependency in dependencies)
        )
        levels[step_id] = value
        return value

    for step_id in steps:
        level(step_id)
    stages: dict[int, list[dict[str, Any]]] = {}
    for step_id, step in steps.items():
        item = _step_summary(step)
        stages.setdefault(levels[step_id], []).append(item)
    return [
        {
            "level": stage,
            "title": "Starts here" if stage == 0 else f"Stage {stage + 1}",
            "work": sorted(
                items, key=lambda item: (item["sort_order"], item["step_id"])
            ),
        }
        for stage, items in sorted(stages.items())
    ]


def create_mcp_server(
    service: DPTService,
    proposals: PlanProposalService,
    *,
    max_read_bytes: int = 128_000,
) -> MCPServer:
    """Create the MCP server bound to the application's shared services."""
    server = MCPServer(
        name="stateforge-dpt",
        title="StateForge DPT",
        version=__version__,
        description="Inspect DPT projects and propose human-confirmed plan changes.",
        instructions=(
            "Read DPT before acting. Plan edits must be submitted with "
            "propose_plan_change. Present review_plan_change_proposal verbatim, "
            "including its digest and consequences, then ask the human. Call "
            "apply_plan_change_proposal only after the human explicitly agrees; "
            "the client must show its separate write-tool confirmation. Applying "
            "never publishes, commits, pushes, reviews, or merges."
        ),
    )

    @server.tool(
        title="List DPT projects",
        description=(
            "Use this to discover local DPT projects before requesting "
            "project-specific state."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_projects(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        projects = service.projects()
        bounded_limit = min(max(limit, 1), 200)
        bounded_offset = max(offset, 0)
        return {
            "schema_version": 1,
            "projects": projects[bounded_offset : bounded_offset + bounded_limit],
            "total": len(projects),
            "next_offset": (
                bounded_offset + bounded_limit
                if bounded_offset + bounded_limit < len(projects)
                else None
            ),
        }

    @server.tool(
        title="Get a DPT project",
        description=(
            "Use this to inspect project identity, repositories, metrics, findings, "
            "and current available actions."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_project(project_id: str) -> dict[str, Any]:
        try:
            dashboard = service.dashboard(project_id)
        except DPTError as error:
            raise _tool_error(error) from error
        project = dashboard["project"]
        return {
            "schema_version": 1,
            "entity": project,
            "state": "available",
            "state_reason": "The local DPT working copy is available.",
            "metrics": dashboard["metrics"],
            "repositories": dashboard["repositories"],
            "findings": dashboard["findings"],
            "pending_plan_proposals": sum(
                item["status"] == "awaiting_review"
                for item in proposals.list(project_id)
            ),
            "available_actions": _actions("project", project),
        }

    @server.tool(
        title="Get the project roadmap",
        description=(
            "Use this to inspect milestone dependency stages and GitHub blockers "
            "across repositories."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_roadmap(project_id: str) -> dict[str, Any]:
        try:
            dashboard = service.dashboard(project_id)
        except DPTError as error:
            raise _tool_error(error) from error
        return {
            "schema_version": 1,
            "project": dashboard["project"],
            "stages": _roadmap(dashboard),
            "blockers": [
                _blocker_summary(blocker) for blocker in dashboard["blockers"]
            ],
            "findings": dashboard["findings"],
            "detail_tool": (
                "Call get_work_item for one milestone or blocker when full GitHub "
                "evidence, context, or review history is needed."
            ),
        }

    @server.tool(
        title="Get the DPT work queue",
        description=(
            "Use this to inspect milestones and blockers grouped by their current "
            "actionable state."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_work_queue(project_id: str) -> dict[str, Any]:
        try:
            dashboard = service.dashboard(project_id)
        except DPTError as error:
            raise _tool_error(error) from error
        states: dict[str, list[dict[str, Any]]] = {
            state: [] for state in WORK_QUEUE_STATES
        }
        for step in dashboard["steps"]:
            states.setdefault(str(step["computed_state"]), []).append(
                _step_summary(step)
            )
        for blocker in dashboard["blockers"]:
            blocker_state = "blocked" if blocker["is_active"] else "completed"
            states.setdefault(blocker_state, []).append(_blocker_summary(blocker))
        return {
            "schema_version": 1,
            "project": dashboard["project"],
            "states": states,
            "metrics": dashboard["metrics"],
            "detail_tool": (
                "Call get_work_item for one milestone or blocker when full GitHub "
                "evidence, context, or review history is needed."
            ),
        }

    @server.tool(
        title="Get a DPT work item",
        description=(
            "Use this to inspect one milestone or GitHub blocker with evidence, "
            "context status, and actions."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_work_item(
        project_id: str,
        work_item_kind: Literal["step", "blocker"],
        work_item_id: str,
    ) -> dict[str, Any]:
        try:
            dashboard = service.dashboard(project_id)
            collection = (
                dashboard["steps"]
                if work_item_kind == "step"
                else dashboard["blockers"]
            )
            key = "step_id" if work_item_kind == "step" else "artifact_id"
            entity = next(
                (item for item in collection if str(item[key]) == work_item_id),
                None,
            )
            if entity is None:
                raise DPTError("Work item not found")
        except DPTError as error:
            raise _tool_error(error) from error
        return {
            "schema_version": 1,
            "entity": entity,
            "state": entity.get("computed_state")
            or ("blocked" if entity.get("is_active") else "completed"),
            "state_reason": entity.get("state_reason")
            or "Derived from GitHub evidence.",
            "available_actions": (
                _actions("step", entity) if work_item_kind == "step" else []
            ),
        }

    @server.tool(
        title="List managed repositories",
        description=(
            "Use this to inspect repository attachment and local Git state for a "
            "DPT project."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_repositories(project_id: str) -> dict[str, Any]:
        try:
            repositories = service.repositories(project_id)
        except DPTError as error:
            raise _tool_error(error) from error
        return {"schema_version": 1, "repositories": repositories}

    @server.tool(
        title="List a repository's tracked tree",
        description=(
            "Use this to browse bounded Git-tracked files and directories in an "
            "attached repository."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_repository_tree(
        repository_id: str, query: str = "", limit: int = 250
    ) -> dict[str, Any]:
        try:
            return {
                "schema_version": 1,
                **service.repository_tree(repository_id, query, limit),
            }
        except DPTError as error:
            raise _tool_error(error) from error

    @server.tool(
        title="Read a tracked repository resource",
        description=(
            "Use this to read one bounded Git-tracked text file and its detected "
            "declarations. Arbitrary paths are rejected."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def read_repository_resource(repository_id: str, path: str) -> dict[str, Any]:
        try:
            result = service.repository_resource_preview(repository_id, path)
        except DPTError as error:
            raise _tool_error(error) from error
        content = result.get("content")
        if isinstance(content, str) and len(content.encode("utf-8")) > max_read_bytes:
            raise ToolError("resource_too_large: resource exceeds the MCP read limit")
        return {"schema_version": 1, **result}

    @server.tool(
        title="Get a milestone context contract",
        description=(
            "Use this to inspect required context, verification, readiness, impact, "
            "and regression evidence for a milestone."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_context_contract(project_id: str, step_id: str) -> dict[str, Any]:
        try:
            step = service.step(project_id, step_id)
        except DPTError as error:
            raise _tool_error(error) from error
        return {
            "schema_version": 1,
            "step_id": step_id,
            "context_resources": step["context_resources"],
            "verification_requirements": step["verification_requirements"],
            "context_readiness": step["context_readiness"],
            "impact_analysis": step["impact_analysis"],
            "risk_assessment": step["risk_assessment"],
            "regression_history": step["regression_history"],
        }

    @server.tool(
        title="Get an implementation package",
        description=(
            "Use this to retrieve the latest human-approved implementation package, "
            "including its GitHub issue and verification contract."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_implementation_package(project_id: str, step_id: str) -> dict[str, Any]:
        try:
            return {
                "schema_version": 1,
                **service.latest_implementation_package(project_id, step_id),
            }
        except DPTError as error:
            raise _tool_error(error) from error

    @server.tool(
        title="List DPT activity",
        description="Use this to inspect recent auditable project activity.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_activities(project_id: str, limit: int = 100) -> dict[str, Any]:
        try:
            service.project(project_id)
            return {
                "schema_version": 1,
                "activities": service.activities(project_id, min(max(limit, 1), 500)),
            }
        except DPTError as error:
            raise _tool_error(error) from error

    @server.tool(
        title="Get available DPT actions",
        description=(
            "Use this before acting to learn which operations are currently valid "
            "and which human approval they require."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def get_available_actions(
        entity_kind: Literal["project", "step", "proposal"],
        project_id: str,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            if entity_kind == "project":
                entity = service.project(project_id)
            elif entity_kind == "step":
                if not entity_id:
                    raise DPTError("entity_id is required for a step")
                entity = service.step(project_id, entity_id)
            else:
                if not entity_id:
                    raise DPTError("entity_id is required for a proposal")
                entity = proposals.get(entity_id)
                if entity["project_id"] != project_id:
                    raise DPTError("Proposal belongs to another project")
        except DPTError as error:
            raise _tool_error(error) from error
        return {
            "schema_version": 1,
            "entity_kind": entity_kind,
            "entity_id": entity_id or project_id,
            "available_actions": _actions(entity_kind, entity),
        }

    @server.tool(
        title="Propose a DPT plan change",
        description=(
            "Use this to submit typed plan operations for human review. It stores "
            "a proposal only and never changes or publishes the working plan."
        ),
        annotations=LOCAL_WRITE,
        structured_output=True,
    )
    def propose_plan_change(
        project_id: str,
        base_working_plan_version: int,
        rationale: str,
        idempotency_key: str,
        operations: list[dict[str, Any]],
        supersedes_proposal_id: str | None = None,
        client_name: str = "MCP client",
        client_version: str | None = None,
    ) -> dict[str, Any]:
        try:
            request = PlanChangeProposalRequest(
                project_id=project_id,
                base_working_plan_version=base_working_plan_version,
                rationale=rationale,
                idempotency_key=idempotency_key,
                operations=PLAN_OPERATION_ADAPTER.validate_python(operations),
                supersedes_proposal_id=supersedes_proposal_id,
                client_name=client_name,
                client_version=client_version,
            )
            proposal = proposals.create(request)
        except (DPTError, ValueError) as error:
            raise ToolError(str(error)) from error
        return {
            "schema_version": 1,
            "proposal": proposal,
            "available_actions": _actions("proposal", proposal),
            "human_instruction": (
                "Present review_markdown and the review digest to the human. "
                "Do not apply until the human explicitly agrees and the client "
                "confirms the separate write tool call."
            ),
        }

    @server.tool(
        title="Review a DPT plan proposal",
        description=(
            "Use this to retrieve the authoritative exact operations, semantic diff, "
            "consequences, and immutable digest before asking the human."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def review_plan_change_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            proposal = proposals.get(proposal_id)
        except DPTError as error:
            raise _tool_error(error) from error
        return {
            "schema_version": 1,
            "proposal": proposal,
            "available_actions": _actions("proposal", proposal),
        }

    @server.tool(
        title="List DPT plan proposals",
        description=(
            "Use this to find pending and historical AI plan proposals for a project."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_plan_change_proposals(project_id: str, limit: int = 100) -> dict[str, Any]:
        try:
            items = proposals.list(project_id, limit)
        except DPTError as error:
            raise _tool_error(error) from error
        return {"schema_version": 1, "proposals": items}

    @server.tool(
        title="Apply an exact reviewed DPT plan proposal",
        description=(
            "CONSEQUENTIAL WRITE. Use only after presenting this exact proposal "
            "version and digest and receiving explicit human agreement. The MCP "
            "client must ask the human to confirm this call. Applies only to the "
            "local SQLite working plan; it never publishes or changes Git."
        ),
        annotations=CONSEQUENTIAL_WRITE,
        structured_output=True,
    )
    def apply_plan_change_proposal(
        proposal_id: str,
        proposal_version: int,
        review_digest: str,
        idempotency_key: str,
        reason: str = "Confirmed through the MCP client",
    ) -> dict[str, Any]:
        del idempotency_key  # proposal identity and terminal state make retries safe
        try:
            decision = ProposalDecisionRequest(
                decision="apply",
                proposal_version=proposal_version,
                review_digest=review_digest,
                reason=reason,
            )
            result = proposals.apply(
                proposal_id,
                decision,
                decided_by="human-via-mcp-client",
                channel="mcp_client_confirmation",
            )
        except (DPTError, ValueError) as error:
            raise ToolError(str(error)) from error
        return {
            "schema_version": 1,
            "proposal": result,
            "result": (
                "The local working plan was updated. Nothing was published or pushed."
            ),
        }

    @server.tool(
        title="Reject a DPT plan proposal",
        description=(
            "Use this after the human explicitly rejects the reviewed proposal. "
            "This changes proposal status but not the plan."
        ),
        annotations=CONSEQUENTIAL_WRITE,
        structured_output=True,
    )
    def reject_plan_change_proposal(
        proposal_id: str,
        proposal_version: int,
        review_digest: str,
        reason: str = "Rejected through the MCP client",
    ) -> dict[str, Any]:
        try:
            decision = ProposalDecisionRequest(
                decision="reject",
                proposal_version=proposal_version,
                review_digest=review_digest,
                reason=reason,
            )
            result = proposals.reject(
                proposal_id,
                decision,
                decided_by="human-via-mcp-client",
                channel="mcp_client_confirmation",
            )
        except (DPTError, ValueError) as error:
            raise ToolError(str(error)) from error
        return {"schema_version": 1, "proposal": result}

    @server.resource(
        "dpt://projects/{project_id}",
        name="DPT project",
        description="Current local DPT project summary.",
        mime_type="application/json",
    )
    def project_resource(project_id: str) -> str:
        try:
            return json.dumps(service.dashboard(project_id), ensure_ascii=False)
        except DPTError as error:
            raise ResourceError(str(error)) from error

    @server.resource(
        "dpt://projects/{project_id}/steps/{step_id}",
        name="DPT milestone",
        description="One milestone with dependencies, evidence, and context state.",
        mime_type="application/json",
    )
    def step_resource(project_id: str, step_id: str) -> str:
        try:
            return json.dumps(service.step(project_id, step_id), ensure_ascii=False)
        except DPTError as error:
            raise ResourceError(str(error)) from error

    @server.resource(
        "dpt://plan-proposals/{proposal_id}/review",
        name="DPT plan proposal review",
        description="Human-readable immutable plan proposal review.",
        mime_type="text/markdown",
    )
    def proposal_resource(proposal_id: str) -> str:
        try:
            return str(proposals.get(proposal_id)["review_markdown"])
        except DPTError as error:
            raise ResourceError(str(error)) from error

    return server
