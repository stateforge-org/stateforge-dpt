import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.plan_file import parse_manifest
from stateforge_dpt.proposals import PLAN_OPERATION_ADAPTER
from stateforge_dpt.service import (
    _graph_symbol_location,
    _verify_graph_relationship_evidence,
)
from stateforge_dpt.sfp_baseline import PROTOCOL_MODULES, sfp_baseline_operations

MERGE_SHA = "a" * 40
PROTOCOL_SHA = "b" * 40
PROVIDER_SHA = "c" * 40


def test_graph_symbol_resolver_returns_exact_nested_python_lines() -> None:
    content = "\n".join(
        (
            "from generated import BootstrapServiceStub",
            "",
            "class Client:",
            "    async def perform_handshake(self):",
            "        return BootstrapServiceStub().Handshake()",
        )
    )
    assert _graph_symbol_location("function", "Client.perform_handshake", content) == (
        4,
        5,
    )
    state, detail, evidence = _verify_graph_relationship_evidence(
        "callsite",
        "direct",
        {"symbol": "Client.perform_handshake", "path": "client.py"},
        {"symbol": "stateforge.sfp.bootstrap.v1.BootstrapService.Handshake"},
        content,
    )
    assert state == "verified"
    assert "Handshake" in detail
    assert evidence["matches"]


def test_conceptual_graph_edges_never_masquerade_as_code_evidence() -> None:
    state, detail, evidence = _verify_graph_relationship_evidence(
        "manual",
        "conceptual",
        {"symbol": "Provider"},
        {"symbol": "Protocol"},
        "Protocol",
    )
    assert state == "unverified"
    assert "conceptual" in detail
    assert evidence["directness"] == "conceptual"


def settings(tmp_path: Path, *, mcp_enabled: bool = False) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        mcp_enabled=mcp_enabled,
    )


def create_graph_project(client: TestClient) -> tuple[dict, dict[str, dict]]:
    project = client.post(
        "/api/projects",
        json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
    ).json()
    repositories = {}
    for alias, name in (
        ("protocol", "stateforge-protocol"),
        ("provider", "stateforge-provider-python"),
        ("agent", "stateforge-agent"),
    ):
        repositories[alias] = client.post(
            f"/api/projects/{project['project_id']}/repositories",
            json={
                "alias": alias,
                "github_full_name": f"stateforge-org/{name}",
            },
        ).json()
    response = client.post(
        f"/api/projects/{project['project_id']}/steps",
        json={
            "step_id": "A4",
            "repository_id": repositories["agent"]["repository_id"],
            "title": "Consume trigger publications",
            "required_result": "The Agent consumes and journals trigger events.",
            "branch_name": "mvp/trigger-publication",
            "depends_on": [],
        },
    )
    assert response.status_code == 201
    return project, repositories


def record_merged_pr(app: object, project: dict, repositories: dict[str, dict]) -> None:
    app.state.service.record_github_artifacts(  # type: ignore[attr-defined]
        project["project_id"],
        repositories["agent"]["repository_id"],
        [
            {
                "id": 300,
                "number": 30,
                "title": "Consume trigger publications",
                "body": "Implements A4.",
                "state": "closed",
                "html_url": "https://github.com/stateforge-org/stateforge-agent/pull/30",
                "user": {"login": "reviewer"},
                "labels": [{"name": "dpt:step:A4"}],
                "pull_details": {
                    "merged_at": "2026-11-01T10:00:00Z",
                    "merge_commit_sha": MERGE_SHA,
                    "head": {
                        "ref": "mvp/trigger-publication",
                        "sha": "d" * 40,
                    },
                    "base": {"ref": "mvp/implementation"},
                    "requested_reviewers": [],
                },
                "changed_files": [
                    {"filename": "src/stateforge_agent/triggers/coordinator.py"}
                ],
                "updated_at": "2026-11-01T10:00:00Z",
            }
        ],
    )


def graph_operations() -> list[dict]:
    return [
        {
            "kind": "initialize_graph_tracking",
            "tracking_started_at": "2026-11-01T09:00:00Z",
        },
        {
            "kind": "set_graph_repository_snapshot",
            "snapshot": {
                "repository_alias": "protocol",
                "commit_sha": PROTOCOL_SHA,
            },
        },
        {
            "kind": "set_graph_repository_snapshot",
            "snapshot": {
                "repository_alias": "provider",
                "commit_sha": PROVIDER_SHA,
            },
        },
        {
            "kind": "set_graph_repository_snapshot",
            "snapshot": {
                "repository_alias": "agent",
                "commit_sha": MERGE_SHA,
            },
        },
        {
            "kind": "upsert_graph_node",
            "node": {
                "node_id": "sfp.trigger.publish",
                "repository_alias": "protocol",
                "kind": "rpc",
                "label": "TriggerPublicationService.Publish",
                "path": "proto/stateforge/sfp/trigger/v1/trigger.proto",
                "symbol": "stateforge.sfp.trigger.v1.TriggerPublicationService.Publish",
            },
        },
        {
            "kind": "upsert_graph_node",
            "node": {
                "node_id": "provider.trigger.publish",
                "repository_alias": "provider",
                "kind": "function",
                "label": "Publish trigger",
                "path": "src/stateforge_provider/triggers/server.py",
                "symbol": "TriggerPublicationServicer.Publish",
            },
        },
        {
            "kind": "upsert_graph_node",
            "node": {
                "node_id": "agent.trigger.consume",
                "repository_alias": "agent",
                "kind": "function",
                "label": "Consume trigger",
                "path": "src/stateforge_agent/triggers/coordinator.py",
                "symbol": "TriggerCoordinator.consume",
            },
        },
        {
            "kind": "upsert_graph_relationship",
            "relationship": {
                "relationship_id": "provider-trigger-implements-sfp",
                "source_node_id": "provider.trigger.publish",
                "target_node_id": "sfp.trigger.publish",
                "relationship_type": "implements",
                "introduced_by_change_set_id": "agent#30",
                "verification": {
                    "state": "verified",
                    "method": "inheritance",
                    "details": "Provider implements the generated servicer.",
                },
            },
        },
        {
            "kind": "upsert_graph_relationship",
            "relationship": {
                "relationship_id": "agent-trigger-consumes-sfp",
                "source_node_id": "agent.trigger.consume",
                "target_node_id": "sfp.trigger.publish",
                "relationship_type": "consumes",
                "introduced_by_change_set_id": "agent#30",
                "verification": {
                    "state": "verified",
                    "method": "callsite",
                    "details": "Agent consumes the generated stream stub.",
                },
            },
        },
        {
            "kind": "review_merged_pull_request",
            "repository_alias": "agent",
            "pull_request_number": 30,
            "milestone_step_id": "A4",
            "merge_commit_sha": MERGE_SHA,
            "disposition": "graph_updated",
            "reason": "Registers the Agent trigger consumer and SFP counterparts.",
            "reviewed_at": "2026-11-01T11:00:00Z",
            "reviewed_by": "test-reviewer",
        },
    ]


def test_merged_milestone_requires_graph_redeployment_and_round_trips(
    tmp_path: Path,
) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        project, repositories = create_graph_project(client)
        record_merged_pr(app, project, repositories)

        pending = client.get(
            f"/api/projects/{project['project_id']}/relationship-graph"
        ).json()
        assert pending["deployment_status"] == "update_required"
        assert pending["pending_redeployments"][0]["change_set_id"] == "agent#30"
        assert pending["pending_redeployments"][0]["merge_commit_sha"] == MERGE_SHA

        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        response = client.post(
            f"/api/projects/{project['project_id']}/plan-proposals",
            json={
                "project_id": project["project_id"],
                "base_working_plan_version": dashboard["project"][
                    "working_plan_version"
                ],
                "rationale": "Register A4 cross-repository relationships.",
                "idempotency_key": "graph-agent-30",
                "client_name": "test",
                "operations": graph_operations(),
            },
        )
        assert response.status_code == 201, response.text
        proposal = response.json()
        assert proposal["diff"]["cross_repository_graph"]["reviewed_pull_requests"] == [
            "agent#30"
        ]

        applied = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json={
                "decision": "apply",
                "proposal_version": proposal["proposal_version"],
                "review_digest": proposal["review_digest"],
                "reason": "Verified.",
            },
        )
        assert applied.status_code == 200, applied.text

        graph = client.get(
            f"/api/projects/{project['project_id']}/relationship-graph"
        ).json()
        assert graph["deployment_status"] == "reconciling"
        assert graph["pending_redeployments"] == []
        assert graph["graph_revision"] == 1
        assert len(graph["nodes"]) == 3
        assert len(graph["relationships"]) == 2

        verified = client.post(
            f"/api/projects/{project['project_id']}/relationship-graph/verify"
        ).json()
        assert verified["verification_status"] == "partial"
        assert verified["failures"] == []
        persisted = client.get(
            f"/api/projects/{project['project_id']}/relationship-graph"
        ).json()
        assert persisted["metrics"]["evidence_coverage"] == 100
        assert {item["observation"]["state"] for item in persisted["nodes"]} == {
            "unavailable"
        }

        registry = client.get(
            f"/api/projects/{project['project_id']}/relationship-graph/registry",
            params={"repository_alias": "agent", "page_size": 1},
        ).json()
        assert registry["total"] == 1
        assert registry["nodes"][0]["node_id"] == "agent.trigger.consume"

        neighborhood = client.get(
            f"/api/projects/{project['project_id']}/relationship-graph/symbols",
            params={"node_id": "sfp.trigger.publish"},
        ).json()
        assert {item["node_id"] for item in neighborhood["nodes"]} == {
            "sfp.trigger.publish",
            "provider.trigger.publish",
            "agent.trigger.consume",
        }

        rendered = app.state.service.render_project(project["project_id"])
        portable, _fingerprint = parse_manifest(rendered)
        assert portable.cross_repository_graph.revision == 1
        assert portable.cross_repository_graph.change_sets[0].change_set_id == (
            "agent#30"
        )
        assert "## Cross-repository implementation graph" in rendered


def test_graph_proposal_rejects_the_wrong_pr_merge_commit(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        project, repositories = create_graph_project(client)
        record_merged_pr(app, project, repositories)
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        operations = graph_operations()
        operations[-1]["merge_commit_sha"] = "f" * 40
        response = client.post(
            f"/api/projects/{project['project_id']}/plan-proposals",
            json={
                "project_id": project["project_id"],
                "base_working_plan_version": dashboard["project"][
                    "working_plan_version"
                ],
                "rationale": "Attempt to register the wrong merge.",
                "idempotency_key": "graph-wrong-sha",
                "client_name": "test",
                "operations": operations,
            },
        )
        assert response.status_code == 409
        assert "does not match" in response.json()["detail"]


def test_mcp_exposes_graph_status_and_bounded_symbol_queries(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path, mcp_enabled=True))
    tools = asyncio.run(app.state.mcp_server.list_tools())
    names = {tool.name for tool in tools}
    assert "get_cross_repository_graph_status" in names
    assert "get_symbol_relationships" in names
    assert "propose_relationship_graph_change" in names

    with TestClient(app) as client:
        project, repositories = create_graph_project(client)
        record_merged_pr(app, project, repositories)
        result = asyncio.run(
            app.state.mcp_server.call_tool(
                "get_cross_repository_graph_status",
                {"project_id": project["project_id"]},
            )
        )
    payload = result.structured_content
    assert payload is not None
    assert payload["deployment_status"] == "update_required"
    assert payload["pending_redeployments"][0]["change_set_id"] == "agent#30"


def test_initial_sfp_baseline_covers_every_catalogued_protocol_module() -> None:
    operations = sfp_baseline_operations(
        {
            "protocol": PROTOCOL_SHA,
            "provider": PROVIDER_SHA,
            "agent": MERGE_SHA,
        },
        tracking_started_at="2026-11-01T12:00:00Z",
    )
    parsed = PLAN_OPERATION_ADAPTER.validate_python(operations)
    protocol_ids = {str(node["node_id"]) for node in PROTOCOL_MODULES}
    relationship_targets = {
        operation.relationship.target_node_id
        for operation in parsed
        if operation.kind == "upsert_graph_relationship"
    }
    assert protocol_ids <= relationship_targets
    graph_nodes = {
        operation.node.node_id: operation.node
        for operation in parsed
        if operation.kind == "upsert_graph_node"
    }
    assert {
        "sfp.bootstrap.rpc.Handshake",
        "sfp.manifest.message.GetManifestResponse",
        "provider.trigger_publication.rpc.StreamTriggers",
        "agent.trigger_publication.rpc.consume",
    } <= graph_nodes.keys()
    assert (
        graph_nodes[
            "provider.operation_admission.rpc.StartOperation"
        ].implementation_state
        == "expected"
    )
    graph_relationships = [
        operation.relationship
        for operation in parsed
        if operation.kind == "upsert_graph_relationship"
    ]
    assert any(item.directness == "direct" for item in graph_relationships)
    assert any(item.directness == "conceptual" for item in graph_relationships)


def test_mcp_allows_one_reviewed_initial_graph_baseline(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path, mcp_enabled=True))
    with TestClient(app) as client:
        project, repositories = create_graph_project(client)
        record_merged_pr(app, project, repositories)
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        operations = [
            {
                "kind": "initialize_graph_tracking",
                "tracking_started_at": "2026-11-01T12:00:00Z",
            },
            {
                "kind": "set_graph_repository_snapshot",
                "snapshot": {
                    "repository_alias": "protocol",
                    "commit_sha": PROTOCOL_SHA,
                },
            },
            {
                "kind": "upsert_graph_node",
                "node": {
                    "node_id": "sfp.bootstrap.service",
                    "repository_alias": "protocol",
                    "kind": "service",
                    "label": "BootstrapService",
                    "path": "proto/stateforge/bootstrap.proto",
                    "symbol": "stateforge.sfp.BootstrapService",
                },
            },
        ]
        result = asyncio.run(
            app.state.mcp_server.call_tool(
                "propose_relationship_graph_change",
                {
                    "project_id": project["project_id"],
                    "base_working_plan_version": dashboard["project"][
                        "working_plan_version"
                    ],
                    "rationale": "Bootstrap the reviewed SFP baseline.",
                    "idempotency_key": "sfp-initial-baseline",
                    "operations": operations,
                },
            )
        )
        payload = result.structured_content
        assert payload is not None
        assert payload["proposal"]["status"] == "awaiting_review"
        proposal = payload["proposal"]
        applied = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json={
                "decision": "apply",
                "proposal_version": proposal["proposal_version"],
                "review_digest": proposal["review_digest"],
                "reason": "Reviewed initial baseline.",
            },
        )
        assert applied.status_code == 200, applied.text
        graph = client.get(
            f"/api/projects/{project['project_id']}/relationship-graph"
        ).json()
        assert graph["pending_redeployments"] == []
        assert graph["tracking_started_at"] == "2026-11-01T12:00:00+00:00"
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        bypass = client.post(
            f"/api/projects/{project['project_id']}/plan-proposals",
            json={
                "project_id": project["project_id"],
                "base_working_plan_version": dashboard["project"][
                    "working_plan_version"
                ],
                "rationale": "Attempt a graph update without a merged PR.",
                "idempotency_key": "graph-review-bypass",
                "operations": [
                    {
                        "kind": "upsert_graph_node",
                        "node": {
                            "node_id": "sfp.manifest.service",
                            "repository_alias": "protocol",
                            "kind": "service",
                            "label": "ManifestService",
                            "path": "proto/stateforge/manifest.proto",
                            "symbol": "stateforge.sfp.ManifestService",
                        },
                    }
                ],
            },
        )
        assert bypass.status_code == 409
        assert "must review a merged pull request" in bypass.json()["detail"]
