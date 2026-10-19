import type { Dashboard, ProjectStatusSnapshot, Step, StepState } from "./types";

export const deliveryStates: StepState[] = [
  "completed",
  "in_review",
  "ready",
  "pending",
  "blocked",
  "planned",
  "cancelled",
];

const frontierPriority: Record<StepState, number> = {
  in_review: 0,
  ready: 1,
  pending: 2,
  blocked: 3,
  planned: 4,
  cancelled: 5,
  completed: 6,
};

function downstreamByStep(steps: Step[]): Map<string, Step[]> {
  const result = new Map<string, Step[]>();
  for (const step of steps) {
    for (const dependency of step.depends_on) {
      const values = result.get(dependency) || [];
      values.push(step);
      result.set(dependency, values);
    }
  }
  return result;
}

export function criticalPath(steps: Step[]): Step[] {
  const incomplete = steps.filter(
    (step) => !["completed", "cancelled"].includes(step.computed_state),
  );
  if (!incomplete.length) return [];
  const incompleteIds = new Set(incomplete.map((step) => step.step_id));
  const downstream = downstreamByStep(incomplete);
  const memo = new Map<string, Step[]>();

  function longestFrom(step: Step, visiting = new Set<string>()): Step[] {
    const known = memo.get(step.step_id);
    if (known) return known;
    if (visiting.has(step.step_id)) return [step];
    const nextVisiting = new Set(visiting).add(step.step_id);
    const children = (downstream.get(step.step_id) || []).filter((candidate) =>
      incompleteIds.has(candidate.step_id),
    );
    const continuation = children
      .map((candidate) => longestFrom(candidate, nextVisiting))
      .sort((left, right) => right.length - left.length || left[0].sort_order - right[0].sort_order)[0] || [];
    const path = [step, ...continuation];
    memo.set(step.step_id, path);
    return path;
  }

  const frontier = incomplete.filter(
    (step) => step.dependencies_complete || ["in_review", "ready", "pending"].includes(step.computed_state),
  );
  const candidates = frontier.length ? frontier : incomplete;
  return candidates
    .map((step) => longestFrom(step))
    .sort((left, right) => {
      const stateDifference = frontierPriority[left[0].computed_state] - frontierPriority[right[0].computed_state];
      return stateDifference || right.length - left.length || left[0].sort_order - right[0].sort_order;
    })[0];
}

export function blockerBlastRadius(dashboard: Dashboard): {
  direct: number;
  indirect: number;
  total: number;
} {
  const directIds = new Set(
    dashboard.steps
      .filter((step) => step.blockers.some((blocker) => blocker.is_active))
      .map((step) => step.step_id),
  );
  const downstream = downstreamByStep(dashboard.steps);
  const affected = new Set(directIds);
  const queue = [...directIds];
  while (queue.length) {
    const current = queue.shift()!;
    for (const step of downstream.get(current) || []) {
      if (["completed", "cancelled"].includes(step.computed_state) || affected.has(step.step_id)) continue;
      affected.add(step.step_id);
      queue.push(step.step_id);
    }
  }
  return {
    direct: directIds.size,
    indirect: Math.max(0, affected.size - directIds.size),
    total: affected.size,
  };
}

export function projectPosition(dashboard: Dashboard): {
  headline: string;
  detail: string;
} {
  const path = criticalPath(dashboard.steps);
  const current = path[0];
  const next = path[1];
  const blockers = dashboard.metrics.active_blockers || 0;
  if (!dashboard.steps.length) {
    return {
      headline: "The release plan is empty.",
      detail: "Add repositories and milestones to establish the delivery path.",
    };
  }
  if (!current) {
    return {
      headline: "Every milestone in this release is complete.",
      detail: "The reconciled GitHub evidence confirms the planned result.",
    };
  }
  const repository = current.repository_alias;
  if (current.computed_state === "in_review") {
    return {
      headline: `${current.step_id} is in review and leads the current delivery path.`,
      detail: next
        ? `Completing ${current.step_id} unlocks ${next.step_id} in ${next.repository_alias}.`
        : `Completing ${current.step_id} finishes the remaining ${repository} path.`,
    };
  }
  if (blockers) {
    const blastRadius = blockerBlastRadius(dashboard);
    return {
      headline: `${blockers} active blocker${blockers === 1 ? "" : "s"} control the current delivery path.`,
      detail: `${blastRadius.total} milestone${blastRadius.total === 1 ? " is" : "s are"} directly or indirectly affected.`,
    };
  }
  if (dashboard.metrics.actionable) {
    return {
      headline: `${current.step_id} is ready to move the release forward.`,
      detail: next
        ? `It is the first step on a ${path.length}-milestone path toward ${next.step_id} and its dependents.`
        : `${repository} has no remaining downstream dependency on this path.`,
    };
  }
  return {
    headline: `${current.step_id} is the next milestone on the critical path.`,
    detail: `Its remaining prerequisites currently determine when downstream work can begin.`,
  };
}

export function compactHistory(history: ProjectStatusSnapshot[], maximum = 12): ProjectStatusSnapshot[] {
  if (history.length <= maximum) return history;
  const result: ProjectStatusSnapshot[] = [];
  for (let index = 0; index < maximum; index += 1) {
    const sourceIndex = Math.round((index * (history.length - 1)) / (maximum - 1));
    const value = history[sourceIndex];
    if (result.at(-1) !== value) result.push(value);
  }
  return result;
}
