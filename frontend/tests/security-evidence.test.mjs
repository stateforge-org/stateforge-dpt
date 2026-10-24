import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

let server;
let RepositorySecurityStrip;
let SbomInventoryView;

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  ({ RepositorySecurityStrip, SbomInventoryView } = await server.ssrLoadModule("/src/SecurityEvidence.tsx"));
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

test("renders the complete SBOM as a readable component inventory", () => {
  const html = renderToStaticMarkup(createElement(SbomInventoryView, {
    sbom: {
      repository_id: "provider",
      alias: "provider",
      github_full_name: "stateforge-org/stateforge-provider-python",
      observed_commit_sha: "0123456789abcdef",
      sha256: "a".repeat(64),
      format: "cyclonedx-json",
      spec_version: "1.5",
      generated_at: "2026-10-24T00:00:00Z",
      document: {
        bomFormat: "CycloneDX",
        specVersion: "1.5",
        metadata: { component: { name: "stateforge-provider", version: "0.1.0" } },
        components: [{
          type: "library",
          name: "grpcio",
          version: "1.84.0",
          purl: "pkg:pypi/grpcio@1.84.0",
          scope: "required",
          licenses: [{ license: { id: "Apache-2.0" } }],
        }],
      },
    },
  }));

  assert.match(html, /stateforge-provider/);
  assert.match(html, /grpcio/);
  assert.match(html, /1\.84\.0/);
  assert.match(html, /Apache-2\.0/);
  assert.match(html, /pkg:pypi\/grpcio@1\.84\.0/);
  assert.match(html, /View raw CycloneDX JSON/);
  assert.match(html, /Download CycloneDX/);
});
