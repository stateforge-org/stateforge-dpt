import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

let server;
let ProjectStatusDashboard;
let blockerBlastRadius;
let criticalPath;
let projectPosition;

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  ({ blockerBlastRadius, criticalPath, projectPosition } = await server.ssrLoadModule("/src/statusDashboard.ts"));
  ({ ProjectStatusDashboard } = await server.ssrLoadModule("/src/ProjectStatusDashboard.tsx"));
});

after(async () => { await server?.close(); });

function step({ id, state, repository = "provider", dependsOn = [], blockers = [], order }) {
  return {
    step_id: id,
    title: `${id} milestone`,
    repository_id: repository,
    repository_alias: repository,
    computed_state: state,
    depends_on: dependsOn,
    dependencies_complete: state !== "blocked",
    blockers,
    sort_order: order,
  };
}

function dashboardFixture() {
  const activeBlocker = { is_active: true };
  const steps = [
    step({ id: "P1", state: "completed", repository: "protocol", order: 1 }),
    step({ id: "R3", state: "in_review", dependsOn: ["P1"], order: 2 }),
    step({ id: "R4", state: "blocked", dependsOn: ["R3"], blockers: [activeBlocker], order: 3 }),
    step({ id: "R12", state: "blocked", dependsOn: ["R4"], order: 4 }),
    step({ id: "A4", state: "blocked", repository: "agent", dependsOn: ["R12"], order: 5 }),
  ];
  const metrics = {
    completed: 1,
    in_review: 1,
    ready: 0,
    pending: 0,
    blocked: 3,
    planned: 0,
    cancelled: 0,
    actionable: 0,
    findings: 1,
    active_blockers: 1,
    blocked_milestones: 1,
  };
  return {
    project: { revision: 8 },
    steps,
    metrics,
    repository_metrics: [
      { repository_id: "protocol", alias: "protocol", total: 1, completed: 1, in_review: 0, ready: 0, pending: 0, blocked: 0, planned: 0, cancelled: 0 },
      { repository_id: "provider", alias: "provider", total: 3, completed: 0, in_review: 1, ready: 0, pending: 0, blocked: 2, planned: 0, cancelled: 0 },
      { repository_id: "agent", alias: "agent", total: 1, completed: 0, in_review: 0, ready: 0, pending: 0, blocked: 1, planned: 0, cancelled: 0 },
    ],
    history: [
      { snapshot_id: 1, plan_revision: 7, source: "reconciliation", recorded_at: "2026-10-01T10:00:00Z", metrics: { ...metrics, completed: 0 }, repository_metrics: [] },
      { snapshot_id: 2, plan_revision: 8, source: "reconciliation", recorded_at: "2026-10-19T10:00:00Z", metrics, repository_metrics: [] },
    ],
  };
}

test("derives the active critical path and blocker blast radius", () => {
  const dashboard = dashboardFixture();
  assert.deepEqual(criticalPath(dashboard.steps).map((item) => item.step_id), ["R3", "R4", "R12", "A4"]);
  assert.deepEqual(blockerBlastRadius(dashboard), { direct: 1, indirect: 2, total: 3 });
  assert.deepEqual(projectPosition(dashboard), {
    headline: "R3 is in review and leads the current delivery path.",
    detail: "Completing R3 unlocks R4 in provider.",
  });
});

test("renders trusted history, repository maps, progress tooltips, and clickable path nodes", () => {
  const dashboard = dashboardFixture();
  const html = renderToStaticMarkup(createElement(ProjectStatusDashboard, { dashboard, onOpenStep() {} }));
  assert.match(html, /Progress versus release scope/);
  assert.match(html, /Each successful GitHub reconciliation adds a trusted observation/);
  assert.match(html, /COMPLETION/);
  assert.match(html, /20%/);
  assert.match(html, /Progress by repository/);
  assert.equal((html.match(/status-repository-cell /g) || []).length, 5);
  assert.match(html, /Release: 3 blocked, 60%/);
  assert.match(html, /provider: 2 blocked, 67%/);
  assert.match(html, /The shortest route through the remaining work/);
  assert.equal((html.match(/status-critical-node /g) || []).length, 4);
  assert.match(html, /1 directly blocked · 2 indirectly delayed/);
});
