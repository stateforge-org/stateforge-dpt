import type { Artifact, Dashboard, Step, StepState } from "./types";

export type ProgressBlocker = {
  number: number;
  title: string;
  htmlUrl: string;
};

export type ProgressMilestone = {
  step: Step;
  stepId: string;
  title: string;
  repositoryId: string;
  repositoryAlias: string;
  state: StepState;
  sortOrder: number;
  dependencyStage: number;
  dependenciesComplete: boolean;
  activeBlockerCount: number;
  activeBlockers: ProgressBlocker[];
  contextState: Step["context_readiness"]["state"];
  contextReady: boolean;
  contextReasons: string[];
  issueNumber: number | null;
  pullRequestNumber: number | null;
};

export type ProgressGroup = {
  repositoryId: string;
  alias: string;
  completed: number;
  total: number;
  percent: number;
  cellColumns: number;
  milestones: ProgressMilestone[];
};

function artifactNumber(artifacts: Artifact[], kind: Artifact["kind"]): number | null {
  return artifacts.find((artifact) => artifact.kind === kind)?.number ?? null;
}

export function dependencyStages(steps: Step[]): Map<string, number> {
  const byId = new Map(steps.map((step) => [step.step_id, step]));
  const stages = new Map<string, number>();

  function visit(step: Step, visiting: Set<string>): number {
    const known = stages.get(step.step_id);
    if (known !== undefined) return known;
    if (visiting.has(step.step_id)) return 0;
    const nextVisiting = new Set(visiting).add(step.step_id);
    const prerequisites = step.depends_on
      .map((stepId) => byId.get(stepId))
      .filter((candidate): candidate is Step => Boolean(candidate));
    const stage = prerequisites.length
      ? Math.max(...prerequisites.map((candidate) => visit(candidate, nextVisiting))) + 1
      : 0;
    stages.set(step.step_id, stage);
    return stage;
  }

  for (const step of steps) visit(step, new Set());
  return stages;
}

export function progressCellColumns(total: number): number {
  if (total <= 1) return 1;
  return Math.min(10, Math.max(2, Math.ceil(Math.sqrt(total * 1.1))));
}

function projectMilestone(step: Step, stages: Map<string, number>): ProgressMilestone {
  const activeBlockers = step.blockers
    .filter((blocker) => blocker.is_active)
    .map((blocker) => ({
      number: blocker.number,
      title: blocker.title,
      htmlUrl: blocker.html_url,
    }));
  return {
    step,
    stepId: step.step_id,
    title: step.title,
    repositoryId: step.repository_id,
    repositoryAlias: step.repository_alias,
    state: step.computed_state,
    sortOrder: step.sort_order,
    dependencyStage: stages.get(step.step_id) ?? 0,
    dependenciesComplete: step.dependencies_complete,
    activeBlockerCount: activeBlockers.length,
    activeBlockers,
    contextState: step.context_readiness.state,
    contextReady: step.context_readiness.ready,
    contextReasons: step.context_readiness.reasons,
    issueNumber: artifactNumber(step.artifacts, "issue"),
    pullRequestNumber: artifactNumber(step.artifacts, "pull_request"),
  };
}

export function releaseProgressGroups(dashboard: Dashboard): ProgressGroup[] {
  const stages = dependencyStages(dashboard.steps);
  const groups = new Map<string, { alias: string; milestones: ProgressMilestone[] }>();

  for (const repository of dashboard.repositories) {
    groups.set(repository.repository_id, { alias: repository.alias, milestones: [] });
  }
  for (const step of dashboard.steps) {
    const group = groups.get(step.repository_id) ?? {
      alias: step.repository_alias,
      milestones: [],
    };
    group.milestones.push(projectMilestone(step, stages));
    groups.set(step.repository_id, group);
  }

  return [...groups.entries()].map(([repositoryId, group]) => {
    const milestones = [...group.milestones].sort(
      (left, right) => left.sortOrder - right.sortOrder || left.stepId.localeCompare(right.stepId),
    );
    const completed = milestones.filter((milestone) => milestone.state === "completed").length;
    const total = milestones.length;
    return {
      repositoryId,
      alias: group.alias,
      completed,
      total,
      percent: total ? Math.round((completed / total) * 100) : 0,
      cellColumns: progressCellColumns(total),
      milestones,
    };
  });
}
