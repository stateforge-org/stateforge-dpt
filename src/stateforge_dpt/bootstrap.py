"""One-time import of the existing StateForge MVP ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stateforge_dpt.models import (
    ProjectCreate,
    RepositoryCreate,
    StepCreate,
    StepUpdate,
)
from stateforge_dpt.service import DPTService


@dataclass(frozen=True, slots=True)
class LegacyStep:
    """One row parsed from an existing MVP plan."""

    step_id: str
    status: str
    branch: str
    title: str
    required_result: str


REPOSITORIES = (
    (
        "protocol",
        "stateforge-protocol",
        "Versioned StateForge Protocol wire contracts.",
    ),
    (
        "provider",
        "stateforge-provider-implemntation",
        "Provider runtime and author-facing integration surface.",
    ),
    (
        "agent",
        "stateforge-agent",
        "Agent-side integration, staging, and backup tooling.",
    ),
)

DEPENDENCIES: dict[str, list[str]] = {
    "P0": [],
    "P1": ["P0"],
    "P2": ["P1"],
    "P3": ["P1", "P2"],
    "P4": ["P1", "P2"],
    "P5": ["P4"],
    "P6": ["P4", "P5"],
    "P7": ["P6"],
    "P8": ["P3", "P5"],
    "P9": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"],
    "R0": ["P0"],
    "R1": ["P1"],
    "R2": ["R1"],
    "R3": ["P2", "R1"],
    "R4": ["P3", "R3"],
    "R5": ["P4", "R3"],
    "R6": ["P5", "R5"],
    "R7": ["P6", "R6"],
    "R8": ["P7", "R7"],
    "R9": ["P8", "R4", "R6"],
    "R10": ["R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"],
    "A0": ["P0", "R0"],
    "A1": ["P1", "R1"],
    "A2": ["R2", "A1"],
    "A3": ["P2", "R3", "A1"],
    "A4": ["P3", "R4", "A3"],
    "A5": ["P4", "R5", "A3"],
    "A6": ["P5", "R6", "A5"],
    "A7": ["P6", "R7", "A6"],
    "A8": ["A7"],
    "A9": ["A8"],
    "A10": ["P7", "R8", "A9"],
    "A11": ["P8", "R9", "A4", "A6"],
    "A12": [
        "P9",
        "R10",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
        "A7",
        "A8",
        "A9",
        "A10",
        "A11",
    ],
}


def parse_legacy_plan(path: Path) -> list[LegacyStep]:
    """Parse the ordered pull-request queue from one existing ledger."""
    rows: list[LegacyStep] = []
    in_queue = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ID | Status | Branch | Pull-request title |"):
            in_queue = True
            continue
        if not in_queue:
            continue
        if line.startswith("| ---"):
            continue
        if not line.startswith("|"):
            if rows:
                break
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if len(columns) != 5:
            continue
        step_id, status, branch, title, result = columns
        rows.append(
            LegacyStep(
                step_id=step_id,
                status=status.strip("`").lower(),
                branch=branch.strip("`"),
                title=title.strip("`"),
                required_result=result,
            )
        )
    if not rows:
        raise ValueError(f"No ordered PR queue found in {path}")
    return rows


def bootstrap_stateforge(service: DPTService, root: Path) -> str:
    """Import the three existing StateForge ledgers into one DPT project."""
    project = service.create_project(
        ProjectCreate(name="StateForge", slug="stateforge", release="MVP")
    )
    repository_ids: dict[str, str] = {}
    parsed: list[tuple[str, LegacyStep]] = []

    for alias, directory, responsibility in REPOSITORIES:
        repository = service.add_repository(
            project["project_id"],
            RepositoryCreate(
                alias=alias,
                github_full_name=f"stateforge-org/{directory}",
                responsibility=responsibility,
            ),
        )
        repository_ids[alias] = repository["repository_id"]
        service.attach_clone(repository["repository_id"], str(root / directory))
        plan_path = root / directory / ".claude" / "MVP_PLAN.md"
        parsed.extend((alias, step) for step in parse_legacy_plan(plan_path))

    for alias, step in parsed:
        service.create_step(
            project["project_id"],
            StepCreate(
                step_id=step.step_id,
                repository_id=repository_ids[alias],
                release="MVP",
                title=step.title,
                required_result=step.required_result,
                branch_name=step.branch,
            ),
        )

    for _, step in parsed:
        current = service.step(project["project_id"], step.step_id)
        service.update_step(
            project["project_id"],
            step.step_id,
            StepUpdate(
                title=current["title"],
                required_result=current["required_result"],
                branch_name=current["branch_name"],
                depends_on=DEPENDENCIES.get(step.step_id, []),
            ),
        )
    return str(project["project_id"])
