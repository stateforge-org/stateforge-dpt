import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from stateforge_dpt.app import create_app
from stateforge_dpt.config import DEFAULT_GITHUB_CLIENT_ID, Settings
from stateforge_dpt.mcp_server import _artifact_summary


def settings(tmp_path: Path, *, mcp_enabled: bool = False) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8463,
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        github_client_id=DEFAULT_GITHUB_CLIENT_ID,
        mcp_enabled=mcp_enabled,
    )


def create_plan(client: TestClient) -> tuple[dict, dict]:
    project = client.post(
        "/api/projects",
        json={"name": "StateForge", "slug": "stateforge", "release": "MVP"},
    ).json()
    repository = client.post(
        f"/api/projects/{project['project_id']}/repositories",
        json={
            "alias": "protocol",
            "github_full_name": "stateforge-org/stateforge-protocol",
        },
    ).json()
    response = client.post(
        f"/api/projects/{project['project_id']}/steps",
        json={
            "step_id": "P0",
            "repository_id": repository["repository_id"],
            "title": "Define the first contract",
            "required_result": "The contract is explicit.",
            "branch_name": "mvp/first-contract",
            "depends_on": [],
        },
    )
    assert response.status_code == 201
    return project, repository


def create_step(
    client: TestClient,
    project_id: str,
    repository_id: str,
    step_id: str,
) -> None:
    response = client.post(
        f"/api/projects/{project_id}/steps",
        json={
            "step_id": step_id,
            "repository_id": repository_id,
            "title": f"Define {step_id}",
            "required_result": f"{step_id} is explicit.",
            "branch_name": f"mvp/{step_id.lower()}",
            "depends_on": [],
        },
    )
    assert response.status_code == 201


def proposal_payload(project_id: str, working_version: int) -> dict:
    return {
        "project_id": project_id,
        "base_working_plan_version": working_version,
        "rationale": "Clarify the required contract result.",
        "idempotency_key": "test-proposal-0001",
        "client_name": "test-agent",
        "operations": [
            {
                "kind": "update_step",
                "step_id": "P0",
                "required_result": "The versioned contract is explicit and tested.",
            }
        ],
    }


def test_proposal_requires_exact_human_decision_and_only_updates_working_plan(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, _repository = create_plan(client)
        before = client.get(f"/api/projects/{project['project_id']}").json()

        created = client.post(
            f"/api/projects/{project['project_id']}/plan-proposals",
            json=proposal_payload(
                project["project_id"],
                before["project"]["working_plan_version"],
            ),
        )
        assert created.status_code == 201
        proposal = created.json()
        assert proposal["status"] == "awaiting_review"
        assert proposal["diff"]["affected_step_ids"] == ["P0"]

        unchanged = client.get(f"/api/projects/{project['project_id']}").json()
        assert unchanged["steps"][0]["required_result"] == ("The contract is explicit.")

        wrong_digest = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json={
                "decision": "apply",
                "proposal_version": 1,
                "review_digest": "0" * 64,
                "reason": "This must not apply.",
            },
        )
        assert wrong_digest.status_code == 409

        decision = {
            "decision": "apply",
            "proposal_version": proposal["proposal_version"],
            "review_digest": proposal["review_digest"],
            "reason": "Reviewed and confirmed.",
        }
        applied = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json=decision,
        )
        assert applied.status_code == 200
        assert applied.json()["status"] == "applied"

        after = client.get(f"/api/projects/{project['project_id']}").json()
        assert after["steps"][0]["required_result"] == (
            "The versioned contract is explicit and tested."
        )
        assert after["project"]["revision"] == before["project"]["revision"]
        assert (
            after["project"]["plan_fingerprint"]
            == before["project"]["plan_fingerprint"]
        )
        assert after["project"]["working_plan_version"] == (
            before["project"]["working_plan_version"] + 1
        )

        retried = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json=decision,
        )
        assert retried.status_code == 200
        assert retried.json()["status"] == "applied"


def test_proposal_becomes_stale_when_the_working_plan_changes(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_plan(client)
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        proposal = client.post(
            f"/api/projects/{project['project_id']}/plan-proposals",
            json=proposal_payload(
                project["project_id"],
                dashboard["project"]["working_plan_version"],
            ),
        ).json()

        changed = client.put(
            f"/api/projects/{project['project_id']}/steps/P0",
            json={
                "repository_id": repository["repository_id"],
                "title": "Human changed the milestone",
                "required_result": "The contract is explicit.",
                "branch_name": "mvp/first-contract",
                "depends_on": [],
            },
        )
        assert changed.status_code == 200

        refreshed = client.get(f"/api/plan-proposals/{proposal['proposal_id']}").json()
        assert refreshed["status"] == "stale"
        rejected_apply = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json={
                "decision": "apply",
                "proposal_version": proposal["proposal_version"],
                "review_digest": proposal["review_digest"],
                "reason": "Apply stale proposal.",
            },
        )
        assert rejected_apply.status_code == 409


def test_create_step_preserves_sparse_order_and_unrelated_review_state(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_plan(client)
        project_id = project["project_id"]
        for step_id in ("P8", "P9", "R2", "A1", "A2"):
            create_step(client, project_id, repository["repository_id"], step_id)

        expected_order = {
            "P0": 1,
            "P8": 9,
            "P9": 10,
            "R2": 13,
            "A1": 23,
            "A2": 24,
        }
        with client.app.state.service.database.connect() as connection:
            for step_id, sort_order in expected_order.items():
                connection.execute(
                    "UPDATE steps SET sort_order = ? "
                    "WHERE project_id = ? AND step_id = ?",
                    (sort_order, project_id, step_id),
                )
            connection.execute(
                """
                INSERT INTO context_approvals(
                    approval_id, project_id, step_id, plan_revision,
                    approved_by, snapshot, approved_at
                ) VALUES ('approval-a1', ?, 'A1', 0, 'maintainer', '{}', 'now')
                """,
                (project_id,),
            )
            connection.execute(
                """
                INSERT INTO implementation_packages(
                    package_id, project_id, step_id, package_hash,
                    package_json, created_at
                ) VALUES ('package-a1', ?, 'A1', 'hash-a1', '{}', 'now')
                """,
                (project_id,),
            )

        dashboard = client.get(f"/api/projects/{project_id}").json()
        operations = [
            {
                "kind": "create_step",
                "step": {
                    "step_id": "P6",
                    "repository_alias": "protocol",
                    "release": "MVP",
                    "title": (
                        "Define Provider Health and Operational Eligibility Contract"
                    ),
                    "required_result": "Provider health is explicit and verifiable.",
                    "branch_name": "mvp/provider-health",
                    "depends_on": [],
                    "sort_order": 7,
                    "context_resources": [],
                    "verification_requirements": [],
                },
            },
            *(
                {
                    "kind": "update_step",
                    "step_id": step_id,
                    "depends_on": ["P6"],
                }
                for step_id in ("P8", "P9", "R2", "A2")
            ),
        ]
        response = client.post(
            f"/api/projects/{project_id}/plan-proposals",
            json={
                "project_id": project_id,
                "base_working_plan_version": dashboard["project"][
                    "working_plan_version"
                ],
                "rationale": "Add the missing provider health contract.",
                "idempotency_key": "sparse-order-regression",
                "client_name": "test-agent",
                "operations": operations,
            },
        )
        assert response.status_code == 201
        proposal = response.json()
        affected = ["A2", "P6", "P8", "P9", "R2"]
        assert proposal["diff"]["affected_step_ids"] == affected
        assert proposal["consequences"]["affected_step_ids"] == affected
        assert "A1" not in proposal["consequences"]["context_approvals_invalidated"]
        assert {
            item["step_id"]: item["fields"]
            for item in proposal["diff"]["steps"]["changed"]
        } == {
            "A2": ["depends_on"],
            "P8": ["depends_on"],
            "P9": ["depends_on"],
            "R2": ["depends_on"],
        }

        applied = client.post(
            f"/api/plan-proposals/{proposal['proposal_id']}/decision",
            json={
                "decision": "apply",
                "proposal_version": proposal["proposal_version"],
                "review_digest": proposal["review_digest"],
                "reason": "The focused change is correct.",
            },
        )
        assert applied.status_code == 200

        after = client.get(f"/api/projects/{project_id}").json()
        actual_order = {step["step_id"]: step["sort_order"] for step in after["steps"]}
        assert actual_order == {**expected_order, "P6": 7}
        with client.app.state.service.database.connect() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM context_approvals "
                    "WHERE project_id = ? AND step_id = 'A1'",
                    (project_id,),
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM implementation_packages "
                    "WHERE project_id = ? AND step_id = 'A1'",
                    (project_id,),
                ).fetchone()[0]
                == 1
            )


def test_explicit_reorder_is_the_only_operation_that_renumbers_steps(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, repository = create_plan(client)
        project_id = project["project_id"]
        create_step(client, project_id, repository["repository_id"], "P1")
        with client.app.state.service.database.connect() as connection:
            connection.execute(
                "UPDATE steps SET sort_order = 4 "
                "WHERE project_id = ? AND step_id = 'P0'",
                (project_id,),
            )
            connection.execute(
                "UPDATE steps SET sort_order = 12 "
                "WHERE project_id = ? AND step_id = 'P1'",
                (project_id,),
            )
        dashboard = client.get(f"/api/projects/{project_id}").json()

        proposal = client.post(
            f"/api/projects/{project_id}/plan-proposals",
            json={
                "project_id": project_id,
                "base_working_plan_version": dashboard["project"][
                    "working_plan_version"
                ],
                "rationale": "Intentionally reorder every milestone.",
                "idempotency_key": "explicit-reorder-regression",
                "client_name": "test-agent",
                "operations": [{"kind": "reorder_steps", "step_ids": ["P1", "P0"]}],
            },
        ).json()

        assert proposal["diff"]["affected_step_ids"] == ["P0", "P1"]
        assert {
            item["step_id"]: item["after"]["sort_order"]
            for item in proposal["diff"]["steps"]["changed"]
        } == {"P0": 1, "P1": 0}


def test_mcp_is_served_on_the_same_runtime_at_exact_path(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path, mcp_enabled=True))
    tools = asyncio.run(app.state.mcp_server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert by_name["propose_plan_change"].annotations.read_only_hint is False
    assert by_name["propose_plan_change"].annotations.destructive_hint is False
    assert by_name["apply_plan_change_proposal"].annotations.read_only_hint is False
    assert by_name["apply_plan_change_proposal"].annotations.destructive_hint is True
    with TestClient(app, base_url="http://127.0.0.1:8463") as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "DPT test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["result"]["serverInfo"]["name"] == "stateforge-dpt"
        assert client.get("/").status_code == 404


def test_mcp_work_queue_is_compact_and_routes_details_to_one_item(
    tmp_path: Path,
) -> None:
    app = create_app(settings(tmp_path, mcp_enabled=True))
    with TestClient(app) as client:
        project, _repository = create_plan(client)
        result = asyncio.run(
            app.state.mcp_server.call_tool(
                "get_work_queue",
                {"project_id": project["project_id"]},
            )
        )

    payload = result.structured_content
    assert payload is not None
    assert set(payload["states"]) == {
        "planned",
        "ready",
        "pending",
        "in_review",
        "blocked",
        "completed",
        "cancelled",
    }
    step = next(
        item
        for items in payload["states"].values()
        for item in items
        if item.get("work_item_kind") == "step"
    )
    assert step["required_result_excerpt"] == "The contract is explicit."
    assert "required_result" not in step
    assert "context_resources" not in step
    assert "available_actions" not in step
    assert step["available_action_ids"] == ["propose_plan_change"]
    assert "get_work_item" in payload["detail_tool"]


def test_mcp_artifact_summary_omits_bodies_timelines_and_changed_files() -> None:
    summary = _artifact_summary(
        {
            "artifact_id": "artifact-1",
            "kind": "pull_request",
            "number": 10,
            "title": "Implement the contract",
            "state": "open",
            "html_url": "https://github.example/pull/10",
            "body": "large body",
            "timeline": [{"event": "reviewed", "body": "large review"}],
            "changed_files": [{"path": "large.py", "patch": "large patch"}],
        }
    )

    assert summary == {
        "artifact_id": "artifact-1",
        "kind": "pull_request",
        "number": 10,
        "title": "Implement the contract",
        "state": "open",
        "html_url": "https://github.example/pull/10",
    }


def test_browser_proposal_decisions_require_same_origin(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        project, _repository = create_plan(client)
        dashboard = client.get(f"/api/projects/{project['project_id']}").json()
        response = client.post(
            f"/api/projects/{project['project_id']}/plan-proposals",
            headers={"Origin": "https://attacker.example"},
            json=proposal_payload(
                project["project_id"],
                dashboard["project"]["working_plan_version"],
            ),
        )
        assert response.status_code == 403
