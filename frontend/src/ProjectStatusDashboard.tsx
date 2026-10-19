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
import { useMemo } from "react";

import {
  blockerBlastRadius,
  compactHistory,
  criticalPath,
  deliveryStates,
  projectPosition,
} from "./statusDashboard";
import type {
  Dashboard,
  ProjectStatusSnapshot,
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

function totalMilestones(snapshot: ProjectStatusSnapshot): number {
  return chartStateOrder.reduce((total, state) => total + (snapshot.metrics[state] || 0), 0);
}

function formatObservationDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Current";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

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
  return (
    <span
      aria-label={label}
      className={`status-segment status-segment-${state} status-segment-interactive`}
      role="img"
      style={{ width: `${(count / total) * 100}%` }}
      tabIndex={0}
    >
      <span aria-hidden="true" className="status-progress-tooltip">
        <strong>{percent}%</strong>
        <small>{count} {stateLabels[state]}</small>
      </span>
    </span>
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
              {milestones.map((step) => (
                <button
                  aria-label={`${step.step_id}, ${step.title}, ${stateLabels[step.computed_state]}`}
                  className={`status-repository-cell status-segment-${step.computed_state}`}
                  key={step.step_id}
                  onClick={() => onOpenStep(step)}
                  type="button"
                >
                  <span aria-hidden="true" className="status-cell-tooltip">
                    <strong>{step.step_id} · {stateLabels[step.computed_state]}</strong>
                    <small>{step.title}</small>
                  </span>
                </button>
              ))}
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

function DeliveryTrend({ history }: { history: ProjectStatusSnapshot[] }) {
  const observations = compactHistory(history);
  const width = 760;
  const height = 230;
  const bounds = { left: 48, right: 730, top: 24, bottom: 181 };
  const maxValue = Math.max(5, ...observations.map(totalMilestones));
  const roundedMaximum = Math.ceil(maxValue / 5) * 5;
  const x = (index: number) => observations.length <= 1
    ? (bounds.left + bounds.right) / 2
    : bounds.left + (index / (observations.length - 1)) * (bounds.right - bounds.left);
  const y = (value: number) => bounds.bottom - (value / roundedMaximum) * (bounds.bottom - bounds.top);
  const completedPath = observations.map((snapshot, index) => `${index ? "L" : "M"} ${x(index)} ${y(snapshot.metrics.completed || 0)}`).join(" ");
  const scopePath = observations.map((snapshot, index) => `${index ? "L" : "M"} ${x(index)} ${y(totalMilestones(snapshot))}`).join(" ");
  const areaPath = observations.length
    ? `${completedPath} L ${x(observations.length - 1)} ${bounds.bottom} L ${x(0)} ${bounds.bottom} Z`
    : "";
  const scopeAreaPath = observations.length > 1
    ? `${scopePath} L ${x(observations.length - 1)} ${bounds.bottom} L ${x(0)} ${bounds.bottom} Z`
    : "";
  const labelIndices = new Set([0, Math.floor((observations.length - 1) / 2), observations.length - 1]);
  const gridValues = [0.25, 0.5, 0.75, 1].map((ratio) => Math.round(roundedMaximum * ratio));
  const latest = observations.at(-1);
  const previous = observations.at(-2);
  const latestCompleted = latest?.metrics.completed || 0;
  const latestTotal = latest ? totalMilestones(latest) : 0;
  const latestPercent = latestTotal ? Math.round((latestCompleted / latestTotal) * 100) : 0;
  const completedDelta = previous ? latestCompleted - (previous.metrics.completed || 0) : null;

  return (
    <article className="status-panel status-trend-panel">
      <header className="status-panel-header">
        <div><span>DELIVERY TREND</span><h2>Progress versus release scope</h2></div>
        <div className="status-trend-legend"><span><i className="complete" />Trusted complete</span><span><i className="scope" />Total scope</span></div>
      </header>
      <div className="status-trend-summary">
        <span><small>COMPLETION</small><strong>{latestPercent}%</strong></span>
        <span><small>REMAINING</small><strong>{Math.max(0, latestTotal - latestCompleted)}</strong></span>
        <span><small>CHANGE</small><strong>{completedDelta === null ? "Baseline" : `${completedDelta >= 0 ? "+" : ""}${completedDelta}`}</strong></span>
      </div>
      <div className="status-chart-wrap">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Trusted completed milestones compared with total release scope over time">
          <defs>
            <linearGradient id="status-complete-area" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#377dff" stopOpacity=".28" />
              <stop offset="100%" stopColor="#377dff" stopOpacity=".02" />
            </linearGradient>
            <linearGradient id="status-scope-area" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#7864db" stopOpacity=".11" />
              <stop offset="100%" stopColor="#7864db" stopOpacity=".01" />
            </linearGradient>
            <linearGradient id="status-baseline-range" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#7864db" />
              <stop offset="100%" stopColor="#377dff" />
            </linearGradient>
          </defs>
          <g className="status-chart-grid">
            {gridValues.map((value) => <line key={value} x1={bounds.left} x2={bounds.right} y1={y(value)} y2={y(value)} />)}
          </g>
          <g className="status-chart-y-labels">
            {gridValues.map((value) => <text key={value} x={bounds.left - 11} y={y(value) + 4} textAnchor="end">{value}</text>)}
          </g>
          {scopeAreaPath && <path className="status-chart-scope-area" d={scopeAreaPath} />}
          {areaPath && <path className="status-chart-area" d={areaPath} />}
          {scopePath && <path className="status-chart-line status-chart-scope" d={scopePath} />}
          {completedPath && <path className="status-chart-line status-chart-completed" d={completedPath} />}
          {observations.length === 1 && latest && (
            <g className="status-chart-baseline">
              <line className="status-chart-baseline-guide scope" x1={bounds.left} x2={bounds.right} y1={y(latestTotal)} y2={y(latestTotal)} />
              <line className="status-chart-baseline-guide complete" x1={bounds.left} x2={bounds.right} y1={y(latestCompleted)} y2={y(latestCompleted)} />
              <line className="status-chart-baseline-range" x1={x(0)} x2={x(0)} y1={y(latestTotal)} y2={y(latestCompleted)} />
              <text className="status-chart-value-label scope" x={bounds.right - 5} y={y(latestTotal) - 8} textAnchor="end">{latestTotal} in scope</text>
              <text className="status-chart-value-label complete" x={bounds.right - 5} y={y(latestCompleted) - 8} textAnchor="end">{latestCompleted} complete</text>
            </g>
          )}
          {observations.map((snapshot, index) => (
            <g key={`${snapshot.snapshot_id ?? "current"}-${snapshot.recorded_at}`}>
              <circle className="status-chart-point status-chart-point-scope" cx={x(index)} cy={y(totalMilestones(snapshot))} r="4"><title>{`${formatObservationDate(snapshot.recorded_at)}: ${totalMilestones(snapshot)} milestones in scope`}</title></circle>
              <circle className="status-chart-point status-chart-point-completed" cx={x(index)} cy={y(snapshot.metrics.completed || 0)} r="4"><title>{`${formatObservationDate(snapshot.recorded_at)}: ${snapshot.metrics.completed || 0} trusted complete`}</title></circle>
              {labelIndices.has(index) && <text className="status-chart-x-label" x={x(index)} y={height - 18} textAnchor={index === 0 ? "start" : index === observations.length - 1 ? "end" : "middle"}>{formatObservationDate(snapshot.recorded_at)}</text>}
            </g>
          ))}
          {observations.length === 1 && <text className="status-chart-empty-label" x={width / 2} y={height / 2 + 33} textAnchor="middle">Baseline captured · trend builds with each reconciliation</text>}
        </svg>
      </div>
      <footer>Each successful GitHub reconciliation adds a trusted observation.</footer>
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
                {repository.total ? deliveryStates.map((state) => {
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
        <DeliveryTrend history={dashboard.history} />
        <RepositoryProgress repositories={dashboard.repository_metrics} />
        <CriticalPath dashboard={dashboard} onOpenStep={onOpenStep} />
      </section>
      <FocusStrip dashboard={dashboard} onOpenStep={onOpenStep} />
    </div>
  );
}
