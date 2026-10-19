import {
  ArrowRight,
  Bug,
  CheckCircle2,
  CircleDot,
  Clock3,
  GitPullRequest,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import {
  blockerBlastRadius,
  criticalPath,
  projectPosition,
} from "./statusDashboard";
import type {
  Dashboard,
  RepositoryStatusMetrics,
  Step,
  StepState,
} from "./types";

const stateLabels: Record<StepState, string> = {
  completed: "Complete",
  in_review: "In review",
  ready: "Ready",
  pending: "Pending",
  blocked: "Blocked",
  planned: "Planned",
  cancelled: "Cancelled",
};

const chartStateOrder: StepState[] = [
  "completed",
  "in_review",
  "ready",
  "pending",
  "blocked",
  "planned",
  "cancelled",
];

function CompletionRing({ completed, total }: { completed: number; total: number }) {
  const percent = total ? Math.round((completed / total) * 100) : 0;
  const radius = 47;
  const circumference = 2 * Math.PI * radius;
  const progress = (percent / 100) * circumference;
  return (
    <div className="status-completion-ring" aria-label={`${percent}% trusted complete`}>
      <svg aria-hidden="true" viewBox="0 0 112 112">
        <circle className="status-ring-track" cx="56" cy="56" r={radius} />
        <circle
          className="status-ring-progress"
          cx="56"
          cy="56"
          r={radius}
          strokeDasharray={`${progress} ${circumference - progress}`}
        />
      </svg>
      <span><strong>{percent}%</strong><small>{completed}/{total} complete</small></span>
    </div>
  );
}

type TooltipPosition = {
  left: number;
  placement: "above" | "below" | "left" | "right";
  top: number;
};

function tooltipPosition(element: HTMLElement, tooltipWidth = 220): TooltipPosition {
  const bounds = element.getBoundingClientRect();
  const gap = 10;
  if (window.innerWidth - bounds.right >= tooltipWidth + gap) {
    return { left: bounds.right + gap, placement: "right", top: bounds.top + bounds.height / 2 };
  }
  if (bounds.left >= tooltipWidth + gap) {
    return { left: bounds.left - gap, placement: "left", top: bounds.top + bounds.height / 2 };
  }
  const left = Math.min(
    window.innerWidth - tooltipWidth / 2 - gap,
    Math.max(tooltipWidth / 2 + gap, bounds.left + bounds.width / 2),
  );
  if (bounds.top >= 72) {
    return { left, placement: "above", top: bounds.top - gap };
  }
  return { left, placement: "below", top: bounds.bottom + gap };
}

function FloatingTooltip({
  children,
  position,
}: {
  children: ReactNode;
  position: TooltipPosition | null;
}) {
  if (!position || typeof document === "undefined") return null;
  return createPortal(
    <span
      className={`status-floating-tooltip status-floating-tooltip-${position.placement}`}
      role="tooltip"
      style={{ left: position.left, top: position.top }}
    >
      {children}
    </span>,
    document.body,
  );
}

function ProgressSegment({
  state,
  count,
  total,
  context,
}: {
  state: StepState;
  count: number;
  total: number;
  context: string;
}) {
  const percent = total ? Math.round((count / total) * 100) : 0;
  const label = `${context}: ${count} ${stateLabels[state].toLowerCase()}, ${percent}%`;
  const [position, setPosition] = useState<TooltipPosition | null>(null);
  const showTooltip = (element: HTMLElement) => setPosition(tooltipPosition(element, 112));
  return (
    <>
      <span
        aria-label={label}
        className={`status-segment status-segment-${state} status-segment-interactive`}
        onBlur={() => setPosition(null)}
        onFocus={(event) => showTooltip(event.currentTarget)}
        onMouseEnter={(event) => showTooltip(event.currentTarget)}
        onMouseLeave={() => setPosition(null)}
        role="img"
        style={{ width: `${(count / total) * 100}%` }}
        tabIndex={0}
      />
      <FloatingTooltip position={position}>
        <strong>{percent}%</strong>
        <small>{count} {stateLabels[state]}</small>
      </FloatingTooltip>
    </>
  );
}

function RepositoryMilestoneCell({
  onOpenStep,
  step,
}: {
  onOpenStep: (step: Step) => void;
  step: Step;
}) {
  const [position, setPosition] = useState<TooltipPosition | null>(null);
  const showTooltip = (element: HTMLElement) => setPosition(tooltipPosition(element));
  return (
    <>
      <button
        aria-label={`${step.step_id}, ${step.title}, ${stateLabels[step.computed_state]}`}
        className={`status-repository-cell status-segment-${step.computed_state}`}
        onBlur={() => setPosition(null)}
        onClick={() => {
          setPosition(null);
          onOpenStep(step);
        }}
        onFocus={(event) => showTooltip(event.currentTarget)}
        onMouseEnter={(event) => showTooltip(event.currentTarget)}
        onMouseLeave={() => setPosition(null)}
        type="button"
      />
      <FloatingTooltip position={position}>
        <strong>{step.step_id} · {stateLabels[step.computed_state]}</strong>
        <small>{step.title}</small>
      </FloatingTooltip>
    </>
  );
}

function RepositoryMilestoneMap({
  dashboard,
  onOpenStep,
}: {
  dashboard: Dashboard;
  onOpenStep: (step: Step) => void;
}) {
  return (
    <div className="status-repository-map" aria-label="Milestones by repository">
      {dashboard.repository_metrics.map((repository) => {
        const milestones = dashboard.steps
          .filter((step) => step.repository_id === repository.repository_id)
          .sort((left, right) => left.sort_order - right.sort_order);
        const percent = repository.total
          ? Math.round((repository.completed / repository.total) * 100)
          : 0;
        return (
          <div className="status-repository-map-row" key={repository.repository_id}>
            <header>
              <strong>{repository.alias}</strong>
              <span>{repository.completed}/{repository.total} · {percent}%</span>
            </header>
            <div className="status-repository-cells">
              {milestones.map((step) => <RepositoryMilestoneCell key={step.step_id} onOpenStep={onOpenStep} step={step} />)}
              {!milestones.length && <span className="status-repository-cells-empty">No milestones</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ReleaseComposition({ dashboard, onOpenStep }: { dashboard: Dashboard; onOpenStep: (step: Step) => void }) {
  const total = dashboard.steps.length;
  return (
    <section className="status-composition" aria-label="Release composition">
      <header><strong>Release composition</strong><span>{total} milestones</span></header>
      <div className="status-composition-bar" role="group" aria-label={chartStateOrder.map((state) => `${dashboard.metrics[state] || 0} ${stateLabels[state]}`).join(", ")}>
        {chartStateOrder.map((state) => {
          const count = dashboard.metrics[state] || 0;
          if (!count || !total) return null;
          return <ProgressSegment context="Release" count={count} key={state} state={state} total={total} />;
        })}
      </div>
      <div className="status-composition-legend">
        {chartStateOrder.map((state) => (
          <span key={state}><i className={`status-dot status-dot-${state}`} />{dashboard.metrics[state] || 0} {stateLabels[state]}</span>
        ))}
      </div>
      <RepositoryMilestoneMap dashboard={dashboard} onOpenStep={onOpenStep} />
    </section>
  );
}

const readinessStates: StepState[] = ["completed", "in_review", "ready", "pending"];

function readinessCount(repository: RepositoryStatusMetrics): number {
  return readinessStates.reduce((total, state) => total + (repository[state] || 0), 0);
}

function readinessPercent(repository: RepositoryStatusMetrics): number {
  return repository.total ? Math.round((readinessCount(repository) / repository.total) * 100) : 0;
}

function RepositoryReadiness({ repositories }: { repositories: RepositoryStatusMetrics[] }) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [pinnedId, setPinnedId] = useState<string | null>(null);
  const width = 920;
  const height = 330;
  const bounds = { left: 68, right: 884, top: 30, bottom: 266 };
  const plotWidth = bounds.right - bounds.left;
  const columnWidth = Math.min(82, Math.max(44, plotWidth / Math.max(1, repositories.length * 2.3)));
  const x = (index: number) => repositories.length <= 1
    ? (bounds.left + bounds.right) / 2
    : bounds.left + ((index + 0.5) / repositories.length) * plotWidth;
  const y = (percent: number) => bounds.bottom - (percent / 100) * (bounds.bottom - bounds.top);
  const activeId = pinnedId ?? focusedId ?? hoveredId;
  const activeIndex = activeId === null
    ? -1
    : repositories.findIndex((repository) => repository.repository_id === activeId);
  const activeRepository = activeIndex >= 0 ? repositories[activeIndex] : null;
  const totalMilestones = repositories.reduce((total, repository) => total + repository.total, 0);
  const readyMilestones = repositories.reduce((total, repository) => total + readinessCount(repository), 0);
  const overallReadiness = totalMilestones ? Math.round((readyMilestones / totalMilestones) * 100) : 0;
  const gridValues = [0, 25, 50, 75, 100];

  return (
    <article className="status-panel status-readiness-panel">
      <header className="status-panel-header status-readiness-header">
        <div><span>DELIVERY READINESS</span><h2>Repository readiness</h2></div>
        <div className="status-readiness-overview">
          <strong>{overallReadiness}%</strong>
          <span>{readyMilestones} of {totalMilestones} milestones ready or complete</span>
        </div>
      </header>
      <div className="status-readiness-legend" aria-label="Milestone states">
        {chartStateOrder.map((state) => (
          <span key={state}><i className={`status-dot status-dot-${state}`} />{stateLabels[state]}</span>
        ))}
      </div>
      <div className={`status-readiness-chart${activeRepository ? " is-exploring" : ""}`}>
        <svg aria-label="Repository readiness percentage by milestone state" role="img" viewBox={`0 0 ${width} ${height}`}>
          <g className="status-readiness-grid">
            {gridValues.map((value) => (
              <g key={value}>
                <line x1={bounds.left} x2={bounds.right} y1={y(value)} y2={y(value)} />
                <text x={bounds.left - 15} y={y(value) + 4} textAnchor="end">{value}%</text>
              </g>
            ))}
          </g>
          <text className="status-readiness-axis-title" textAnchor="middle" transform={`translate(18 ${(bounds.top + bounds.bottom) / 2}) rotate(-90)`}>READINESS</text>
          {repositories.map((repository, index) => {
            const center = x(index);
            const percent = readinessPercent(repository);
            let cumulative = 0;
            return (
              <g
                aria-label={repository.total
                  ? `${repository.alias}: ${percent}% ready, ${readinessCount(repository)} of ${repository.total} milestones ready or complete`
                  : `${repository.alias}: not planned`}
                aria-pressed={pinnedId === repository.repository_id}
                className={`status-readiness-repository${activeId === repository.repository_id ? " selected" : ""}`}
                key={repository.repository_id}
                onBlur={() => setFocusedId(null)}
                onClick={() => setPinnedId((current) => current === repository.repository_id ? null : repository.repository_id)}
                onFocus={() => setFocusedId(repository.repository_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setPinnedId((current) => current === repository.repository_id ? null : repository.repository_id);
                  }
                }}
                onMouseEnter={() => setHoveredId(repository.repository_id)}
                onMouseLeave={() => setHoveredId(null)}
                role="button"
                tabIndex={0}
              >
                <rect className="status-readiness-hit-target" height={height - 8} width={Math.max(columnWidth + 42, 105)} x={center - Math.max(columnWidth + 42, 105) / 2} y="4" />
                <rect className={`status-readiness-column${repository.total ? "" : " empty"}`} height={bounds.bottom - bounds.top} rx="9" width={columnWidth} x={center - columnWidth / 2} y={bounds.top} />
                {repository.total && chartStateOrder.map((state) => {
                  const value = repository[state] || 0;
                  if (!value) return null;
                  const start = (cumulative / repository.total) * 100;
                  cumulative += value;
                  const end = (cumulative / repository.total) * 100;
                  return (
                    <rect
                      className={`status-readiness-segment status-readiness-segment-${state}`}
                      height={Math.max(2, y(start) - y(end))}
                      key={state}
                      width={columnWidth}
                      x={center - columnWidth / 2}
                      y={y(end)}
                    />
                  );
                })}
                {repository.total ? <>
                  <line className="status-readiness-marker" x1={center - columnWidth / 2 - 6} x2={center + columnWidth / 2 + 6} y1={y(percent)} y2={y(percent)} />
                  <circle className="status-readiness-marker-dot" cx={center} cy={y(percent)} r="5" />
                  <text className="status-readiness-value" textAnchor="middle" x={center} y={Math.max(bounds.top + 13, y(percent) - 12)}>{percent}% ready</text>
                </> : (
                  <text className="status-readiness-not-planned" textAnchor="middle" x={center} y={(bounds.top + bounds.bottom) / 2}>Not planned</text>
                )}
                <text className="status-readiness-repository-label" textAnchor="middle" x={center} y={bounds.bottom + 26}>{repository.alias}</text>
              </g>
            );
          })}
        </svg>
        {activeRepository && activeIndex >= 0 && (
          <div
            className={`status-readiness-inspector${pinnedId === activeRepository.repository_id ? " pinned" : ""}${x(activeIndex) > width * .68 ? " align-left" : ""}`}
            style={{ left: `${(x(activeIndex) / width) * 100}%`, top: "12%" }}
          >
            <small>{activeRepository.alias}{pinnedId === activeRepository.repository_id ? " · PINNED" : ""}</small>
            {activeRepository.total ? <>
              <strong>{readinessPercent(activeRepository)}% ready</strong>
              <span>{readinessCount(activeRepository)} of {activeRepository.total} milestones ready or complete</span>
              <div className="status-readiness-inspector-metrics">
                {chartStateOrder.map((state) => (
                  <span key={state}><i className={`status-dot status-dot-${state}`} /><span>{stateLabels[state]}</span><b>{activeRepository[state] || 0}</b></span>
                ))}
              </div>
            </> : <strong>Not planned</strong>}
          </div>
        )}
      </div>
      <footer><span>Readiness includes complete, in-review, ready, and pending milestones.</span><span>Hover or focus to inspect · click to pin</span></footer>
    </article>
  );
}

function stateIcon(state: StepState) {
  if (state === "completed") return <CheckCircle2 />;
  if (state === "in_review") return <GitPullRequest />;
  if (state === "ready") return <Sparkles />;
  if (state === "pending") return <Clock3 />;
  if (state === "blocked") return <Bug />;
  return <CircleDot />;
}

function CriticalPath({ dashboard, onOpenStep }: { dashboard: Dashboard; onOpenStep: (step: Step) => void }) {
  const path = criticalPath(dashboard.steps);
  const visible = path.slice(0, 5);
  const blastRadius = blockerBlastRadius(dashboard);
  return (
    <article className="status-panel status-critical-panel">
      <header className="status-panel-header">
        <div><span>CRITICAL PATH</span><h2>The shortest route through the remaining work</h2></div>
        <small>{path.length} milestone{path.length === 1 ? "" : "s"}</small>
      </header>
      {visible.length ? (
        <div className="status-critical-body">
          <div className="status-critical-path">
            {visible.map((step, index) => (
              <div className="status-critical-item" key={step.step_id}>
                <button className={`status-critical-node status-critical-node-${step.computed_state}`} onClick={() => onOpenStep(step)} type="button">
                  <span>{index === 0 ? "CURRENT" : index === 1 ? "NEXT" : stateLabels[step.computed_state].toUpperCase()}</span>
                  <header><code>{step.step_id}</code>{stateIcon(step.computed_state)}</header>
                  <strong>{step.title}</strong><small>{step.repository_alias}</small>
                </button>
                {index < visible.length - 1 && <ArrowRight className="status-critical-arrow" aria-hidden="true" />}
              </div>
            ))}
            {path.length > visible.length && <span className="status-critical-more">+{path.length - visible.length} downstream</span>}
          </div>
          {blastRadius.total > 0 && (
            <div className="status-blast-radius"><Bug /><p><strong>{dashboard.metrics.active_blockers} active blocker{dashboard.metrics.active_blockers === 1 ? "" : "s"}</strong><span>{blastRadius.direct} directly blocked · {blastRadius.indirect} indirectly delayed</span></p></div>
          )}
        </div>
      ) : (
        <div className="status-path-complete"><ShieldCheck /><div><strong>No remaining critical path</strong><span>Every planned milestone is complete or cancelled.</span></div></div>
      )}
    </article>
  );
}

function FocusStrip({ dashboard, onOpenStep }: { dashboard: Dashboard; onOpenStep: (step: Step) => void }) {
  const path = criticalPath(dashboard.steps);
  const current = path[0];
  const next = path[1];
  const blastRadius = blockerBlastRadius(dashboard);
  return (
    <section className="status-focus-strip">
      <button disabled={!current} onClick={() => current && onOpenStep(current)} type="button"><i className="now" /><span><small>NOW</small><strong>{current ? `${current.step_id} · ${current.title}` : "Release complete"}</strong></span></button>
      <button disabled={!next} onClick={() => next && onOpenStep(next)} type="button"><i className="then" /><span><small>THEN</small><strong>{next ? `${next.step_id} becomes the next focus` : "No downstream milestone"}</strong></span></button>
      <div><i className={blastRadius.total ? "watch" : "clear"} /><span><small>WATCH</small><strong>{blastRadius.total ? `${blastRadius.total} milestones in blocker blast radius` : "No active blocker blast radius"}</strong></span></div>
    </section>
  );
}

export function ProjectStatusDashboard({ dashboard, onOpenStep }: { dashboard: Dashboard; onOpenStep: (step: Step) => void }) {
  const position = useMemo(() => projectPosition(dashboard), [dashboard]);
  const total = dashboard.steps.length;
  const completed = dashboard.metrics.completed || 0;
  return (
    <div className="project-status-dashboard">
      <article className="status-position-panel">
        <div className="status-position-copy"><span>WHERE THE PROJECT IS NOW</span><h2>{position.headline}</h2><p>{position.detail}</p></div>
        <CompletionRing completed={completed} total={total} />
        <div className="status-quick-metrics">
          <span><Sparkles /><small>Ready</small><strong>{dashboard.metrics.actionable}</strong></span>
          <span><GitPullRequest /><small>In review</small><strong>{dashboard.metrics.in_review}</strong></span>
          <span><Bug /><small>Blockers</small><strong>{dashboard.metrics.active_blockers}</strong></span>
          <span><CircleDot /><small>Findings</small><strong>{dashboard.metrics.findings}</strong></span>
        </div>
        <ReleaseComposition dashboard={dashboard} onOpenStep={onOpenStep} />
      </article>
      <section className="status-dashboard-grid">
        <RepositoryReadiness repositories={dashboard.repository_metrics} />
        <CriticalPath dashboard={dashboard} onOpenStep={onOpenStep} />
      </section>
      <FocusStrip dashboard={dashboard} onOpenStep={onOpenStep} />
    </div>
  );
}
