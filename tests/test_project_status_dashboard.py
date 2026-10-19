from pathlib import Path

from stateforge_dpt.database import Database
from stateforge_dpt.models import ProjectCreate, RepositoryCreate, StepCreate
from stateforge_dpt.security import SecretStore
from stateforge_dpt.service import DPTService


def _service(tmp_path: Path) -> DPTService:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "dpt.sqlite3")
    database.initialize()
    return DPTService(database, SecretStore(data_dir))


def test_dashboard_exposes_current_repository_progress_and_baseline(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = service.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="protocol",
            github_full_name="stateforge-org/stateforge-protocol",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="P1",
            repository_id=repository["repository_id"],
            title="Define protocol",
            required_result="The protocol is defined.",
            branch_name="mvp/protocol",
        ),
    )

    dashboard = service.dashboard(project["project_id"])

    assert dashboard["repository_metrics"] == [
        {
            "repository_id": repository["repository_id"],
            "alias": "protocol",
            "total": 1,
            "planned": 0,
            "blocked": 0,
            "ready": 1,
            "pending": 0,
            "in_review": 0,
            "completed": 0,
            "cancelled": 0,
        }
    ]
    assert len(dashboard["history"]) == 1
    assert dashboard["history"][0]["source"] == "current"
    assert dashboard["history"][0]["snapshot_id"] is None
    assert dashboard["history"][0]["metrics"] == dashboard["metrics"]


def test_reconciliation_snapshot_is_durable_and_current_state_is_not_duplicated(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository = service.add_repository(
        project["project_id"],
        RepositoryCreate(
            alias="agent",
            github_full_name="stateforge-org/stateforge-agent",
        ),
    )
    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="A1",
            repository_id=repository["repository_id"],
            title="Build Agent",
            required_result="The Agent is built.",
            branch_name="mvp/agent",
        ),
    )
    dashboard = service.dashboard(project["project_id"])

    saved = service.record_project_status_snapshot(project["project_id"], dashboard)
    refreshed = service.dashboard(project["project_id"])

    assert saved["source"] == "reconciliation"
    assert saved["snapshot_id"] is not None
    assert refreshed["history"] == [saved]

    service.create_step(
        project["project_id"],
        StepCreate(
            step_id="A2",
            repository_id=repository["repository_id"],
            title="Integrate Agent",
            required_result="The Agent is integrated.",
            branch_name="mvp/agent-integration",
            depends_on=["A1"],
        ),
    )
    changed = service.dashboard(project["project_id"])

    assert len(changed["history"]) == 2
    assert changed["history"][0] == saved
    assert changed["history"][1]["source"] == "current"
    assert changed["history"][1]["snapshot_id"] is None
    assert changed["history"][1]["repository_metrics"][0]["total"] == 2
