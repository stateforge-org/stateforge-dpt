"""Public API models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProjectCreate(BaseModel):
    """Project creation request."""

    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    release: str = Field(default="MVP", min_length=1, max_length=40)
    plan_source_branch: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )


class PlanSourceBranchUpdate(BaseModel):
    """Parent branch from which reconciliation reads the portable plan."""

    plan_source_branch: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )


class RepositoryCreate(BaseModel):
    """Repository registration request."""

    alias: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    github_full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    responsibility: str = Field(default="", max_length=300)
    github_default_branch: str | None = None
    clone_url: str | None = None


class AttachCloneRequest(BaseModel):
    """Existing-clone attachment request."""

    local_path: str = Field(min_length=1)


class CloneRepositoryRequest(BaseModel):
    """Repository clone request."""

    destination_path: str | None = Field(default=None, min_length=1)
    destination_root: str | None = Field(default=None, min_length=1)


class StepCreate(BaseModel):
    """Implementation-step creation request."""

    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    repository_id: str
    release: str = Field(default="MVP", min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=240)
    required_result: str = Field(min_length=1, max_length=4000)
    branch_name: str = Field(min_length=1, max_length=240)
    depends_on: list[str] = Field(default_factory=list)


class StepUpdate(BaseModel):
    """Editable implementation-step fields."""

    repository_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    required_result: str = Field(min_length=1, max_length=4000)
    branch_name: str = Field(min_length=1, max_length=240)
    depends_on: list[str] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    """Generated-file write options."""

    force: bool = False


class PullRequestCreate(BaseModel):
    """Branches selected for a milestone pull request."""

    base_branch: str = Field(min_length=1, max_length=240)
    head_branch: str = Field(min_length=1, max_length=240)


class BranchCreate(BaseModel):
    """Base branch selected for the planned milestone branch."""

    base_branch: str = Field(min_length=1, max_length=240)


class BlockerCreate(BaseModel):
    """Create a GitHub bug or link an existing issue as a milestone blocker."""

    issue_url: str | None = Field(default=None, max_length=500)
    repository_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    body: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def validate_source(self) -> BlockerCreate:
        """Require exactly one existing-issue or new-issue source."""
        linking_existing = self.issue_url is not None
        creating_new = self.repository_id is not None or self.title is not None
        if linking_existing == creating_new:
            raise ValueError(
                "Provide either an existing GitHub Issue URL or a repository and title"
            )
        if creating_new and (self.repository_id is None or self.title is None):
            raise ValueError("A repository and title are required for a new blocker")
        return self


class ProjectImportRequest(BaseModel):
    """Local Git clone containing a portable DPT project."""

    local_path: str = Field(min_length=1)


class PlanImportRequest(BaseModel):
    """Git-tracked plan selected as an existing project's source."""

    repository_id: str
    force: bool = False


class PortableProject(BaseModel):
    """Project identity stored in the Git-tracked DPT manifest."""

    slug: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=100)
    release: str = Field(min_length=1, max_length=40)
    generated_filename: str = Field(
        default="STATEFORGE_DPT.md",
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    revision: int = Field(ge=0)
    plan_source_branch: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )


class PortableRepository(BaseModel):
    """Repository definition stored in the portable plan."""

    alias: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    github_full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    github_default_branch: str | None = Field(default=None, max_length=240)
    responsibility: str = Field(default="", max_length=300)


class PortableStep(BaseModel):
    """Lossless implementation milestone stored in the portable plan."""

    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    repository_alias: str
    release: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=240)
    required_result: str = Field(min_length=1, max_length=4000)
    branch_name: str = Field(min_length=1, max_length=240)
    depends_on: list[str] = Field(default_factory=list)
    sort_order: int = Field(ge=0)


class PortablePlan(BaseModel):
    """Validated, deterministic DPT project representation."""

    schema_version: Literal[1] = 1
    project: PortableProject
    repositories: list[PortableRepository] = Field(max_length=100)
    steps: list[PortableStep] = Field(max_length=5000)

    @model_validator(mode="after")
    def validate_references(self) -> PortablePlan:
        """Reject ambiguous identifiers, missing references, and cycles."""
        aliases = [repository.alias for repository in self.repositories]
        full_names = [
            repository.github_full_name.casefold() for repository in self.repositories
        ]
        step_ids = [step.step_id for step in self.steps]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Repository aliases must be unique")
        if len(set(full_names)) != len(full_names):
            raise ValueError("GitHub repositories must be unique")
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("Step IDs must be unique")

        alias_set = set(aliases)
        step_set = set(step_ids)
        dependencies: dict[str, list[str]] = {}
        for step in self.steps:
            if step.repository_alias not in alias_set:
                raise ValueError(
                    f"Step {step.step_id} references unknown repository "
                    f"{step.repository_alias}"
                )
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError(f"Step {step.step_id} repeats a dependency")
            missing = sorted(set(step.depends_on) - step_set)
            if missing:
                raise ValueError(
                    f"Step {step.step_id} references unknown dependencies: "
                    f"{', '.join(missing)}"
                )
            if step.step_id in step.depends_on:
                raise ValueError(f"Step {step.step_id} cannot depend on itself")
            dependencies[step.step_id] = step.depends_on

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError(f"Dependency cycle detected at {step_id}")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies.get(step_id, []):
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        return self
