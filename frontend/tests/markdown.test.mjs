import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

let server;
let Markdown;

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  ({ Markdown } = await server.ssrLoadModule("/src/Markdown.tsx"));
});

after(async () => { await server?.close(); });

function render(markdown, compact = false) {
  return renderToStaticMarkup(createElement(Markdown, { compact }, markdown));
}

test("milestone sections, lists, emphasis and inline code render semantically", () => {
  const source = "## Outcome and ownership\n\nDefine **SFP** with `TriggerStream`.\n\n## Required behavior\n\n- Stable publication ID\n- Durable acknowledgement\n\n1. Verify\n2. Review\n";
  const html = render(source);
  assert.match(html, /<h2>Outcome and ownership<\/h2>/);
  assert.match(html, /<h2>Required behavior<\/h2>/);
  assert.match(html, /<strong>SFP<\/strong>/);
  assert.match(html, /<code>TriggerStream<\/code>/);
  assert.match(html, /<ul>[\s\S]*<li>Stable publication ID<\/li>/);
  assert.match(html, /<ol>[\s\S]*<li>Verify<\/li>/);
  assert.ok(!html.includes("## Outcome"));
});

test("GitHub-flavored task lists and tables are supported", () => {
  const html = render("- [x] Tested\n- [ ] Reviewed\n\n| State | Meaning |\n| --- | --- |\n| ACCEPTED | Durable |\n");
  assert.match(html, /type="checkbox"/);
  assert.match(html, /disabled=""/);
  assert.match(html, /checked=""/);
  assert.match(html, /class="markdown-table"><table>/);
  assert.match(html, /<th>State<\/th>/);
  assert.match(html, /<td>Durable<\/td>/);
});

test("code blocks and blockquotes remain separate readable blocks", () => {
  const html = render("```python\nprint('ready')\n```\n\n> Do not redispatch.\n");
  assert.match(html, /<pre><code class="language-python">/);
  assert.match(html, /<blockquote>[\s\S]*Do not redispatch/);
});

test("links open separately with safe opener behavior", () => {
  const html = render("[Protocol](https://github.com/stateforge-org/stateforge-protocol)");
  assert.match(html, /href="https:\/\/github.com\/stateforge-org\/stateforge-protocol"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /target="_blank"/);
});

test("stored content cannot enable raw HTML or unsafe URL protocols", () => {
  const html = render('<script>alert("bad")</script>\n\n[Unsafe](javascript:alert%28%22bad%22%29)\n\n<img src=x onerror="alert(1)">');
  assert.ok(!html.includes("<script"));
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("javascript:"));
  assert.ok(!html.includes("onerror"));
});

test("compact card previews render formatting without nested interactive controls", () => {
  const html = render("## Outcome\n\n**Ready** with `SFP`. [Read more](https://example.com)\n\n- [ ] Review", true);
  assert.match(html, /Outcome/);
  assert.match(html, /<strong>Ready<\/strong>/);
  assert.match(html, /<code>SFP<\/code>/);
  assert.ok(!html.includes("<h2"));
  assert.ok(!html.includes("<a "));
  assert.ok(!html.includes("<input"));
  assert.ok(!html.includes("##"));
});
