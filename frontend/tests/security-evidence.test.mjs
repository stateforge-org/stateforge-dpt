import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

let server;
let RepositorySecurityStrip;

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  ({ RepositorySecurityStrip } = await server.ssrLoadModule("/src/SecurityEvidence.tsx"));
});

after(async () => { await server?.close(); });

function repository(alias, state, severity, dependencyFindings = 0, codeqlFindings = 0) {
  return {
    repository_id: alias,
    alias,
    security: {
      branch: "mvp/implementation",
      state,
      highest_severity: severity,
      dependency_findings: dependencyFindings,
      codeql_findings: codeqlFindings,
      observed_commit_sha: "0123456789abcdef",
      refreshed_at: "2026-10-23T00:00:00Z",
      coverage: {},
      dependency_status: { state: "current", current: true, complete: true, configured: true },
      codeql_status: { state: "current", current: true, complete: true, configured: true },
    },
  };
}

test("renders honest repository security states without claiming repositories are secure", () => {
  const repositories = [
    repository("agent", "no_known_findings", "none"),
    repository("protocol", "findings", "critical", 2, 1),
    repository("provider", "partial", "none"),
  ];
  const html = renderToStaticMarkup(createElement(RepositorySecurityStrip, {
    repositories,
    onOpen() {},
  }));

  assert.match(html, /Security Status/);
  assert.match(html, /mvp\/implementation/);
  assert.match(html, /No known findings/);
  assert.match(html, /3 open/);
  assert.match(html, /2 dependency · 1 CodeQL/);
  assert.match(html, /Partial/);
  assert.doesNotMatch(html, />Secure</);
  assert.match(html, /repository-security-card-critical/);
});
