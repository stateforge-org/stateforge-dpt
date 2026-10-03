import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

let server;
let SourceCodePreview;
let languageForPath;

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  ({ SourceCodePreview, languageForPath } = await server.ssrLoadModule("/src/SourceCodePreview.tsx"));
});

after(async () => { await server?.close(); });

test("detects common repository languages from paths", () => {
  assert.equal(languageForPath("src/client.tsx"), "tsx");
  assert.equal(languageForPath("proto/sfp/manifest.proto"), "protobuf");
  assert.equal(languageForPath("scripts/reconcile.py"), "python");
  assert.equal(languageForPath("Dockerfile"), "bash");
  assert.equal(languageForPath("LICENSE"), "text");
});

test("renders highlighted tokens and line numbers without changing source text", () => {
  const source = "def reconcile(project_id: str):\n    return project_id\n";
  const html = renderToStaticMarkup(createElement(SourceCodePreview, {
    content: source,
    path: "src/reconcile.py",
  }));

  assert.match(html, /source-code-preview-language[^>]*>python</);
  assert.match(html, /<span class="token" style="color:[^"]+">def<\/span>/);
  assert.match(html, /react-syntax-highlighter-line-number/);
  assert.match(html, /reconcile/);
  assert.match(html, /project_id/);
});
