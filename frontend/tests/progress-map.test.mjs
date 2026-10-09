import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

let server;
let ReleaseProgressMap;
let dependencyStages;
let progressCellColumns;
let releaseProgressGroups;

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  ({ dependencyStages, progressCellColumns, releaseProgressGroups } = await server.ssrLoadModule("/src/progressMap.ts"));
  ({ ReleaseProgressMap } = await server.ssrLoadModule("/src/ReleaseProgressMap.tsx"));
});

after(async () => { await server?.close(); });

function artifact(kind, number) {
  return { kind, number };
}

function step({
  id,
  repositoryId,
  repositoryAlias,
  sortOrder,
  state,
  dependsOn = [],
  blockerActive = false,
  contextState = "ready",
  artifacts = [],
}) {
  return {
    step_id: id,
    repository_id: repositoryId,
    repository_alias: repositoryAlias,
    title: `${id} milestone`,
    computed_state: state,
    sort_order: sortOrder,
    depends_on: dependsOn,
    dependencies_complete: dependsOn.length === 0,
    artifacts,
    blockers: blockerActive ? [{
      is_active: true,
      number: 91,
      title: "Active compatibility defect",
      html_url: "https://example.test/issues/91",
    }] : [{
      is_active: false,
      number: 72,
      title: "Resolved defect",
      html_url: "https://example.test/issues/72",
    }],
    context_readiness: {
      state: contextState,
      ready: contextState === "ready",
      reasons: contextState === "ready" ? [] : ["Required context is unavailable"],
    },
  };
}

function dashboardFixture() {
  const steps = [
    step({ id: "A2", repositoryId: "agent", repositoryAlias: "agent", sortOrder: 6, state: "planned", dependsOn: ["P1"] }),
    step({ id: "P1", repositoryId: "protocol", repositoryAlias: "protocol", sortOrder: 2, state: "completed", dependsOn: ["P0"], artifacts: [artifact("issue", 5), artifact("pull_request", 8)] }),
    step({ id: "P0", repositoryId: "protocol", repositoryAlias: "protocol", sortOrder: 1, state: "completed" }),
    step({ id: "A1", repositoryId: "agent", repositoryAlias: "agent", sortOrder: 5, state: "cancelled" }),
    step({ id: "A0", repositoryId: "agent", repositoryAlias: "agent", sortOrder: 3, state: "ready", contextState: "missing_required_context" }),
    step({ id: "A3", repositoryId: "agent", repositoryAlias: "agent", sortOrder: 7, state: "blocked", blockerActive: true }),
    step({ id: "A4", repositoryId: "agent", repositoryAlias: "agent", sortOrder: 8, state: "in_review" }),
    step({ id: "A5", repositoryId: "agent", repositoryAlias: "agent", sortOrder: 9, state: "pending" }),
  ];
  return {
    project: { revision: 7 },
    repositories: [
      { repository_id: "protocol", alias: "protocol" },
      { repository_id: "agent", alias: "agent" },
      { repository_id: "provider", alias: "provider" },
    ],
    steps,
    metrics: {
      completed: 2,
      in_review: 1,
      pending: 1,
      ready: 1,
      blocked: 1,
      cancelled: 1,
      planned: 1,
    },
  };
}

test("projects milestones into stable repository regions and authoritative states", () => {
  const groups = releaseProgressGroups(dashboardFixture());
  assert.deepEqual(groups.map((group) => group.alias), ["protocol", "agent", "provider"]);
  assert.deepEqual(groups[0].milestones.map((milestone) => milestone.stepId), ["P0", "P1"]);
  assert.deepEqual(groups[1].milestones.map((milestone) => milestone.stepId), ["A0", "A1", "A2", "A3", "A4", "A5"]);
  assert.deepEqual({ completed: groups[0].completed, total: groups[0].total, percent: groups[0].percent }, { completed: 2, total: 2, percent: 100 });
  assert.deepEqual({ completed: groups[1].completed, total: groups[1].total, percent: groups[1].percent }, { completed: 0, total: 6, percent: 0 });
  assert.equal(groups[2].total, 0);
  assert.equal(groups.flatMap((group) => group.milestones).length, dashboardFixture().steps.length);
});

test("derives dependency stage, evidence, blockers, and context independently", () => {
  const groups = releaseProgressGroups(dashboardFixture());
  const milestones = new Map(groups.flatMap((group) => group.milestones).map((milestone) => [milestone.stepId, milestone]));
  assert.equal(milestones.get("P0").dependencyStage, 0);
  assert.equal(milestones.get("P1").dependencyStage, 1);
  assert.equal(milestones.get("A2").dependencyStage, 2);
  assert.equal(milestones.get("P1").issueNumber, 5);
  assert.equal(milestones.get("P1").pullRequestNumber, 8);
  assert.equal(milestones.get("A3").activeBlockerCount, 1);
  assert.equal(milestones.get("P0").activeBlockerCount, 0);
  assert.equal(milestones.get("A0").contextState, "missing_required_context");
});

test("layout remains deterministic for empty, small, and large groups", () => {
  assert.equal(progressCellColumns(0), 1);
  assert.equal(progressCellColumns(1), 1);
  assert.equal(progressCellColumns(3), 2);
  assert.equal(progressCellColumns(12), 4);
  assert.equal(progressCellColumns(1000), 10);
  const stages = dependencyStages(dashboardFixture().steps);
  assert.deepEqual([...stages.entries()].sort(), [...dependencyStages(dashboardFixture().steps).entries()].sort());
});

test("renders one accessible button per milestone with state and risk indicators", () => {
  const dashboard = dashboardFixture();
  const html = renderToStaticMarkup(createElement(ReleaseProgressMap, { dashboard, onOpenStep() {} }));
  assert.equal((html.match(/class="release-map-cell release-map-cell-/g) || []).length, dashboard.steps.length);
  assert.match(html, /aria-label="A3, A3 milestone, agent repository, Blocked by prerequisites,/);
  assert.match(html, /1 active GitHub blocker/);
  assert.match(html, /Required context is unavailable/);
  assert.match(html, /protocol repository: 2 of 2 milestones completed, 100 percent/);
  assert.match(html, /provider repository: 0 of 0 milestones completed, 0 percent/);
});
