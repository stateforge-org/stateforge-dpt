import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import c from "react-syntax-highlighter/dist/esm/languages/prism/c";
import cpp from "react-syntax-highlighter/dist/esm/languages/prism/cpp";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import go from "react-syntax-highlighter/dist/esm/languages/prism/go";
import java from "react-syntax-highlighter/dist/esm/languages/prism/java";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup";
import php from "react-syntax-highlighter/dist/esm/languages/prism/php";
import protobuf from "react-syntax-highlighter/dist/esm/languages/prism/protobuf";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import ruby from "react-syntax-highlighter/dist/esm/languages/prism/ruby";
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import toml from "react-syntax-highlighter/dist/esm/languages/prism/toml";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("c", c);
SyntaxHighlighter.registerLanguage("cpp", cpp);
SyntaxHighlighter.registerLanguage("css", css);
SyntaxHighlighter.registerLanguage("go", go);
SyntaxHighlighter.registerLanguage("java", java);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("jsx", jsx);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("markup", markup);
SyntaxHighlighter.registerLanguage("php", php);
SyntaxHighlighter.registerLanguage("protobuf", protobuf);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("ruby", ruby);
SyntaxHighlighter.registerLanguage("rust", rust);
SyntaxHighlighter.registerLanguage("sql", sql);
SyntaxHighlighter.registerLanguage("toml", toml);
SyntaxHighlighter.registerLanguage("tsx", tsx);
SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("yaml", yaml);

const languageByExtension: Record<string, string> = {
  c: "c",
  cc: "cpp",
  cjs: "javascript",
  cpp: "cpp",
  css: "css",
  go: "go",
  h: "c",
  hpp: "cpp",
  htm: "markup",
  html: "markup",
  java: "java",
  js: "javascript",
  json: "json",
  json5: "json",
  jsx: "jsx",
  md: "markdown",
  mdx: "markdown",
  mjs: "javascript",
  php: "php",
  proto: "protobuf",
  py: "python",
  pyw: "python",
  rb: "ruby",
  rs: "rust",
  sh: "bash",
  sql: "sql",
  svg: "markup",
  toml: "toml",
  ts: "typescript",
  tsx: "tsx",
  xml: "markup",
  yaml: "yaml",
  yml: "yaml",
};

const languageByFilename: Record<string, string> = {
  dockerfile: "bash",
  gemfile: "ruby",
  makefile: "bash",
  procfile: "bash",
};

export function languageForPath(path: string): string {
  const filename = path.split("/").at(-1)?.toLowerCase() || "";
  const namedLanguage = languageByFilename[filename];
  if (namedLanguage) return namedLanguage;
  const extension = filename.includes(".") ? filename.split(".").at(-1) || "" : "";
  return languageByExtension[extension] || "text";
}

export function SourceCodePreview({ content, path }: { content: string; path: string }) {
  const language = languageForPath(path);
  return (
    <div className="source-code-preview">
      <div className="source-code-preview-language">{language === "text" ? "Plain text" : language}</div>
      <SyntaxHighlighter
        codeTagProps={{ className: "source-code-preview-code" }}
        customStyle={{ margin: 0, minHeight: "100%", padding: "16px 18px" }}
        language={language}
        lineNumberStyle={{ color: "#596579", minWidth: "3.25em", paddingRight: "1.25em" }}
        showLineNumbers
        style={oneDark}
        wrapLongLines={false}
      >
        {content}
      </SyntaxHighlighter>
    </div>
  );
}
