import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Render stored Markdown without enabling raw HTML or unsafe URL protocols. */
export function Markdown({
  children,
  compact = false,
}: {
  children: string;
  compact?: boolean;
}) {
  return (
    <ReactMarkdown
      allowedElements={compact ? ["p", "strong", "em", "code", "del"] : undefined}
      components={{
        a: ({ children, href }) => (
          <a href={href} rel="noopener noreferrer" target="_blank">
            {children}
          </a>
        ),
        table: ({ children }) => (
          <div className="markdown-table"><table>{children}</table></div>
        ),
      }}
      remarkPlugins={[remarkGfm]}
      skipHtml
      unwrapDisallowed={compact}
    >
      {children}
    </ReactMarkdown>
  );
}
