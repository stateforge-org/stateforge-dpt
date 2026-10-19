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

function RepositoryMilestoneGraph({ repositories }: { repositories: RepositoryStatusMetrics[] }) {
  const width = 760;
  const height = 300;
  const bounds = { left: 52, right: 730, top: 24, bottom: 226 };
  const seriesTotals = useMemo(
    () => Object.fromEntries(
      chartStateOrder.map((state) => [
        state,
        repositories.reduce((total, repository) => total + (repository[state] || 0), 0),
      ]),
    ) as Record<StepState, number>,
    [repositories],
  );
  const populatedStates = chartStateOrder.filter((state) => seriesTotals[state] > 0);
  const [visibleStates, setVisibleStates] = useState<Record<StepState, boolean>>(() =>
    Object.fromEntries(chartStateOrder.map((state) => [state, true])) as Record<StepState, boolean>,
  );
  const [hoveredRepository, setHoveredRepository] = useState<number | null>(null);
  const [pinnedRepository, setPinnedRepository] = useState<number | null>(null);
  const activeRepository = pinnedRepository ?? hoveredRepository;
  const maximum = Math.max(
    1,
    ...repositories.flatMap((repository) => chartStateOrder.map((state) => repository[state] || 0)),
  );
  const scaleMaximum = Math.max(4, Math.ceil(maximum / 4) * 4);
  const xFor = (index: number) => repositories.length <= 1
    ? (bounds.left + bounds.right) / 2
    : bounds.left + (index / (repositories.length - 1)) * (bounds.right - bounds.left);
  const yFor = (value: number) => bounds.bottom - (value / scaleMaximum) * (bounds.bottom - bounds.top);
  const gridValues = [0, 1, 2, 3, 4].map((position) => (position / 4) * scaleMaximum);
  const pathFor = (state: StepState) => {
    let path = "";
    let previous: { index: number; x: number; y: number } | null = null;
    repositories.forEach((repository, index) => {
      if (!repository.total) {
        previous = null;
        return;
      }
      const point = { index, x: xFor(index), y: yFor(repository[state] || 0) };
      if (!previous || previous.index !== index - 1) {
        path += `M${point.x},${point.y}`;
      } else {
        const midpoint = (previous.x + point.x) / 2;
        path += ` C${midpoint},${previous.y} ${midpoint},${point.y} ${point.x},${point.y}`;
      }
      previous = point;
    });
    return path;
  };
  const areaPathFor = (state: StepState) => {
    const segments: Array<Array<{ x: number; y: number }>> = [];
    let current: Array<{ x: number; y: number }> = [];
    repositories.forEach((repository, index) => {
      if (!repository.total) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push({ x: xFor(index), y: yFor(repository[state] || 0) });
    });
    if (current.length) segments.push(current);
    return segments.map((points) => {
      const first = points[0];
      let path = `M${first.x},${bounds.bottom} L${first.x},${first.y}`;
      points.slice(1).forEach((point, index) => {
        const previous = points[index];
        const midpoint = (previous.x + point.x) / 2;
        path += ` C${midpoint},${previous.y} ${midpoint},${point.y} ${point.x},${point.y}`;
      });
      const last = points[points.length - 1];
      return `${path} L${last.x},${bounds.bottom} Z`;
    }).join(" ");
  };
  const visiblePopulatedStates = populatedStates.filter((state) => visibleStates[state]);
  const toggleState = (state: StepState) => {
    if (!seriesTotals[state]) return;
    setVisibleStates((current) => {
      const remaining = populatedStates.filter((candidate) => current[candidate]).length;
      if (current[state] && remaining === 1) return current;
      return { ...current, [state]: !current[state] };
    });
  };
  const toggleRepository = (index: number) => {
    setPinnedRepository((current) => current === index ? null : index);
  };
  const activeMetrics = activeRepository === null ? null : repositories[activeRepository];
  const repositoryLabel = (repository: RepositoryStatusMetrics) => {
    if (!repository.total) return `${repository.alias}: no milestones`;
    return `${repository.alias}: ${repository.total} total milestone${repository.total === 1 ? "" : "s"}, ${repository.completed} complete, ${repository.in_review} in review, ${repository.ready} ready, ${repository.pending} pending, ${repository.blocked} blocked, ${repository.planned} planned, ${repository.cancelled} cancelled`;
  };

  return (
    <article className="status-panel status-milestone-graph-panel">
      <header className="status-panel-header status-milestone-graph-header">
        <div><span>MILESTONE DISTRIBUTION</span><h2>Milestones by repository</h2><p>Compare delivery states across every workstream.</p></div>
        <div className="status-milestone-graph-legend" aria-label="Toggle milestone state lines">
          {chartStateOrder.map((state) => (
            <button
              aria-pressed={visibleStates[state] && seriesTotals[state] > 0}
              className={visibleStates[state] && seriesTotals[state] > 0 ? "active" : ""}
              disabled={!seriesTotals[state]}
              key={state}
              onClick={() => toggleState(state)}
              type="button"
            >
              <i className={`status-dot status-dot-${state}`} /><span>{stateLabels[state]}</span><b>{seriesTotals[state]}</b>
            </button>
          ))}
        </div>
      </header>
      <div className={`status-milestone-graph${activeRepository === null ? "" : " is-exploring"}`}>
        <svg aria-label="Milestone state counts by repository" role="img" viewBox={`0 0 ${width} ${height}`}>
          <defs>
            {chartStateOrder.map((state) => (
              <linearGradient id={`status-milestone-area-${state}`} key={state} x1="0" x2="0" y1="0%" y2="100%">
                <stop className={`status-milestone-area-stop status-milestone-area-stop-${state}`} offset="0%" />
                <stop className={`status-milestone-area-end status-milestone-area-end-${state}`} offset="100%" />
              </linearGradient>
            ))}
          </defs>
          <g className="status-milestone-graph-grid">
            {gridValues.map((value) => {
              const y = yFor(value);
              return (
                <g key={value}>
                  <line x1={bounds.left} x2={bounds.right} y1={y} y2={y} />
                  <text x={bounds.left - 13} y={y + 3} textAnchor="end">{value}</text>
                </g>
              );
            })}
            <text className="status-milestone-graph-axis-title" x={bounds.left} y={14}>MILESTONES</text>
            <text className="status-milestone-graph-axis-title" textAnchor="middle" x={(bounds.left + bounds.right) / 2} y={290}>REPOSITORIES</text>
          </g>
          <g className="status-milestone-repository-bands">
            {repositories.map((repository, index) => {
              const x = xFor(index);
              const bandWidth = Math.max(54, (bounds.right - bounds.left) / Math.max(repositories.length, 1) - 12);
              return <rect className={`${repository.total ? "" : "empty"}${activeRepository === index ? " selected" : ""}`} height={bounds.bottom - bounds.top} key={repository.repository_id} rx="10" width={bandWidth} x={x - bandWidth / 2} y={bounds.top} />;
            })}
          </g>
          {visiblePopulatedStates.map((state) => (
            <path className="status-milestone-area" d={areaPathFor(state)} fill={`url(#status-milestone-area-${state})`} key={state} />
          ))}
          {visiblePopulatedStates.map((state) => (
            <path className={`status-milestone-line status-milestone-line-${state}`} d={pathFor(state)} key={state} />
          ))}
          {activeRepository !== null && (
            <line className="status-milestone-crosshair" x1={xFor(activeRepository)} x2={xFor(activeRepository)} y1={bounds.top} y2={bounds.bottom} />
          )}
          {repositories.map((repository, index) => {
            const x = xFor(index);
            const selected = activeRepository === index;
            return (
              <g
                aria-label={repositoryLabel(repository)}
                className={`status-milestone-observation${selected ? " selected" : ""}`}
                key={repository.repository_id}
                onBlur={() => setHoveredRepository(null)}
                onClick={() => toggleRepository(index)}
                onFocus={() => setHoveredRepository(index)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggleRepository(index);
                  }
                }}
                onMouseEnter={() => setHoveredRepository(index)}
                onMouseLeave={() => setHoveredRepository(null)}
                role="button"
                tabIndex={0}
              >
                <rect className="status-milestone-hit-target" height={bounds.bottom - bounds.top + 44} width={Math.max(42, (bounds.right - bounds.left) / Math.max(repositories.length, 1))} x={x - Math.max(42, (bounds.right - bounds.left) / Math.max(repositories.length, 1)) / 2} y={bounds.top} />
                {repository.total && visiblePopulatedStates.map((state) => {
                  const value = repository[state] || 0;
                  return (
                    <g key={state}>
                      <circle className={`status-milestone-point status-milestone-point-${state}`} cx={x} cy={yFor(value)} r={selected ? 5.4 : 4.3} />
                      {value > 0 && <text className={`status-milestone-value status-milestone-value-${state}`} textAnchor="middle" x={x} y={yFor(value) - 11}>{value}</text>}
                    </g>
                  );
                })}
                <text className="status-milestone-repository-label" textAnchor="middle" x={x} y={254}>{repository.alias}</text>
                {!repository.total && <>
                  <rect className="status-milestone-empty-pill" height="15" rx="7.5" width="72" x={x - 36} y="258" />
                  <text className="status-milestone-empty-label" textAnchor="middle" x={x} y={268.5}>No milestones</text>
                </>}
              </g>
            );
          })}
        </svg>
        {activeMetrics && activeRepository !== null && (
          <div
            className={`status-milestone-inspector${xFor(activeRepository) > width * .62 ? " align-left" : ""}${pinnedRepository === activeRepository ? " pinned" : ""}`}
            style={{ left: `${(xFor(activeRepository) / width) * 100}%`, top: "13%" }}
          >
            <small>{pinnedRepository === activeRepository ? "PINNED REPOSITORY" : "REPOSITORY"}</small>
            <strong>{activeMetrics.alias}</strong>
            <span>{activeMetrics.total} milestone{activeMetrics.total === 1 ? "" : "s"} in scope</span>
            <div className="status-milestone-inspector-metrics">
              {chartStateOrder.map((state) => (
                <span key={state}><i className={`status-dot status-dot-${state}`} /><span>{stateLabels[state]}</span><b>{activeMetrics[state] || 0}</b></span>
              ))}
            </div>
          </div>
        )}
      </div>
      <footer><span>Each line shows the current milestone count for one delivery state.</span><span>Hover or focus to inspect · click to pin</span></footer>
    </article>
  );
}

function RepositoryProgress({ repositories }: { repositories: RepositoryStatusMetrics[] }) {
  return (
    <article className="status-panel status-repositories-panel">
      <header className="status-panel-header"><div><span>WORKSTREAMS</span><h2>Progress by repository</h2></div></header>
      <div className="status-repository-list">
        {repositories.map((repository) => {
          const percent = repository.total ? Math.round((repository.completed / repository.total) * 100) : 0;
          return (
            <div className="status-repository-row" key={repository.repository_id}>
              <header><strong>{repository.alias}</strong><span>{repository.completed}/{repository.total} · {percent}%</span></header>
              <div className="status-repository-bar" role="group" aria-label={`${repository.alias}: ${repository.completed} of ${repository.total} milestones complete`}>
                {repository.total ? chartStateOrder.map((state) => {
                  const count = repository[state] || 0;
                  return count ? <ProgressSegment context={repository.alias} count={count} key={state} state={state} total={repository.total} /> : null;
                }) : <i className="status-repository-empty" />}
              </div>
            </div>
          );
        })}
      </div>
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
        <RepositoryMilestoneGraph repositories={dashboard.repository_metrics} />
        <RepositoryProgress repositories={dashboard.repository_metrics} />
        <CriticalPath dashboard={dashboard} onOpenStep={onOpenStep} />
      </section>
      <FocusStrip dashboard={dashboard} onOpenStep={onOpenStep} />
    </div>
  );
}
