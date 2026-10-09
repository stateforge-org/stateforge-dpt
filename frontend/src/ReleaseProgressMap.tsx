import {
  Ban,
  Bug,
  Check,
  CircleDot,
  Clock3,
  GitPullRequest,
  LockKeyhole,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";

import { releaseProgressGroups, type ProgressMilestone } from "./progressMap";
import type { Dashboard, Step, StepState } from "./types";

const stateOrder: StepState[] = [
  "completed",
  "in_review",
  "pending",
  "ready",
  "blocked",
  "cancelled",
  "planned",
];

const stateLabels: Record<StepState, string> = {
  completed: "Completed",
  in_review: "In review",
  pending: "Pending",
  ready: "Ready",
  blocked: "Blocked by prerequisites",
  cancelled: "Cancelled",
  planned: "Planned",
};

function stateIcon(state: StepState): ReactNode {
  switch (state) {
    case "completed": return <Check />;
    case "in_review": return <GitPullRequest />;
    case "pending": return <Clock3 />;
    case "ready": return <Sparkles />;
    case "blocked": return <LockKeyhole />;
    case "cancelled": return <Ban />;
    default: return <CircleDot />;
  }
}

function evidenceLabel(milestone: ProgressMilestone): string {
  const evidence = [];
  if (milestone.issueNumber !== null) evidence.push(`issue #${milestone.issueNumber}`);
  if (milestone.pullRequestNumber !== null) evidence.push(`pull request #${milestone.pullRequestNumber}`);
  return evidence.length ? evidence.join(" and ") : "No linked GitHub evidence";
}

function contextLabel(milestone: ProgressMilestone): string {
  if (milestone.contextReady) return "implementation context ready";
  return `implementation context ${milestone.contextState.replaceAll("_", " ")}`;
}

function MilestoneProgressCell({
  milestone,
  muted,
  onOpen,
}: {
  milestone: ProgressMilestone;
  muted: boolean;
  onOpen: (step: Step) => void;
}) {
  const tooltipId = useId();
  const contextCritical = milestone.contextState === "missing_required_context";
  const accessibleLabel = [
    milestone.stepId,
    milestone.title,
    `${milestone.repositoryAlias} repository`,
    stateLabels[milestone.state],
    contextLabel(milestone),
    milestone.activeBlockerCount
      ? `${milestone.activeBlockerCount} active GitHub blocker${milestone.activeBlockerCount === 1 ? "" : "s"}`
      : "no active GitHub blockers",
  ].join(", ");

  return (
    <div className={`release-map-cell-wrap${muted ? " is-muted" : ""}`}>
      <button
        aria-describedby={tooltipId}
        aria-label={accessibleLabel}
        className={`release-map-cell release-map-cell-${milestone.state}${milestone.activeBlockerCount ? " has-blocker" : ""}`}
        onClick={() => onOpen(milestone.step)}
        type="button"
      >
        <span aria-hidden="true" className="release-map-state-mark">{stateIcon(milestone.state)}</span>
        <strong>{milestone.stepId}</strong>
        {milestone.activeBlockerCount > 0 && <span aria-hidden="true" className="release-map-blocker-mark"><Bug /></span>}
        {!milestone.contextReady && (
          <span
            aria-hidden="true"
            className={`release-map-context-mark ${contextCritical ? "is-critical" : "is-warning"}`}
          >
            <ShieldAlert />
          </span>
        )}
      </button>
      <div className="release-map-tooltip" id={tooltipId} role="tooltip">
        <header><code>{milestone.stepId}</code><span>{stateLabels[milestone.state]}</span></header>
        <strong>{milestone.title}</strong>
        <dl>
          <div><dt>Repository</dt><dd>{milestone.repositoryAlias}</dd></div>
          <div><dt>Dependency stage</dt><dd>{milestone.dependencyStage + 1}</dd></div>
          <div><dt>Prerequisites</dt><dd>{milestone.dependenciesComplete ? "Complete" : "Incomplete"}</dd></div>
          <div><dt>GitHub evidence</dt><dd>{evidenceLabel(milestone)}</dd></div>
          <div><dt>Context</dt><dd>{milestone.contextReady ? "Ready" : milestone.contextState.replaceAll("_", " ")}</dd></div>
        </dl>
        {milestone.activeBlockers.length > 0 && (
          <div className="release-map-tooltip-blockers">
            <span>Active blockers</span>
            {milestone.activeBlockers.map((blocker) => <p key={`${blocker.number}-${blocker.htmlUrl}`}>#{blocker.number} {blocker.title}</p>)}
          </div>
        )}
        {!milestone.contextReady && milestone.contextReasons.length > 0 && <p className="release-map-tooltip-reason">{milestone.contextReasons.join(" · ")}</p>}
      </div>
    </div>
  );
}

export function ReleaseProgressMap({
  dashboard,
  onOpenStep,
}: {
  dashboard: Dashboard;
  onOpenStep: (step: Step) => void;
}) {
  const groups = useMemo(() => releaseProgressGroups(dashboard), [dashboard]);
  const [stateFilter, setStateFilter] = useState<StepState | null>(null);
  const total = dashboard.steps.length;
  const completed = dashboard.metrics.completed || 0;

  function toggleFilter(state: StepState) {
    setStateFilter((current) => current === state ? null : state);
  }

  return (
    <article className="panel release-map-panel">
      <header className="release-map-header">
        <div>
          <span className="release-map-eyebrow">Release map</span>
          <h2>Every milestone, one trusted state</h2>
          <p>Grouped by repository and backed by reconciled GitHub evidence.</p>
        </div>
        <div aria-label={`${completed} of ${total} milestones completed`} className="release-map-total">
          <strong>{completed}<span>/{total}</span></strong>
          <small>trusted complete</small>
        </div>
      </header>

      <div aria-label="Filter milestones by delivery state" className="release-map-legend" role="group">
        {stateOrder.map((state) => (
          <button
            aria-pressed={stateFilter === state}
            className={`release-map-legend-item legend-${state}`}
            key={state}
            onClick={() => toggleFilter(state)}
            type="button"
          >
            <i aria-hidden="true">{stateIcon(state)}</i>
            <span>{stateLabels[state]}</span>
            <strong>{dashboard.metrics[state] || 0}</strong>
          </button>
        ))}
        <span className="release-map-indicator-key"><i className="is-blocker"><Bug /></i>Active GitHub blocker</span>
        <span className="release-map-indicator-key"><i className="is-context"><ShieldAlert /></i>Context needs attention</span>
      </div>

      {groups.length ? (
        <div className="release-map-regions">
          {groups.map((group) => (
            <section
              aria-label={`${group.alias} repository: ${group.completed} of ${group.total} milestones completed, ${group.percent} percent`}
              className="release-map-region"
              key={group.repositoryId}
              style={{ flexGrow: Math.max(group.total, 1) }}
            >
              <header>
                <div><strong>{group.alias}</strong><span>{group.total ? `${group.percent}% complete` : "No milestones"}</span></div>
                <span><b>{group.completed}</b> / {group.total}</span>
              </header>
              {group.milestones.length ? (
                <div
                  className="release-map-cells"
                  style={{ gridTemplateColumns: `repeat(${group.cellColumns}, minmax(44px, 1fr))` }}
                >
                  {group.milestones.map((milestone) => (
                    <MilestoneProgressCell
                      key={milestone.stepId}
                      milestone={milestone}
                      muted={stateFilter !== null && milestone.state !== stateFilter}
                      onOpen={onOpenStep}
                    />
                  ))}
                </div>
              ) : <p className="release-map-empty">No milestones assigned.</p>}
            </section>
          ))}
        </div>
      ) : <p className="release-map-empty release-map-empty-project">Add a repository and declare milestones to build this release map.</p>}
    </article>
  );
}
