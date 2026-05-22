import { useLayoutEffect, useState } from "react";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

export default function DocsPage() {
  const { setEnd } = usePageHeader();
  const [active, setActive] = useState<"guide" | "faq" | "cli">("guide");
  const [markdown, setMarkdown] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useLayoutEffect(() => {
    setEnd(null);
    return () => setEnd(null);
  }, [setEnd]);

  useLayoutEffect(() => {
    setLoading(true);
    fetch(`/docs/vbit-agent-guide.md?_=${Date.now()}`)
      .then(r => r.text())
      .then(setMarkdown)
      .catch(() => setMarkdown("# 加载失败\n请检查 /docs/vbit-agent-guide.md 是否存在。"))
      .finally(() => setLoading(false));
  }, []);

  // Simple markdown-to-HTML renderer (no deps needed)
  const renderMarkdown = (md: string): string => {
    let html = md
      // Code blocks (```lang ... ```)
      .replace(/```(\w*)\n([\s\S]*?)```/g, (_: string, lang: string, code: string) => {
        const escaped = code
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
        return `<pre class="doc-codeblock"><code class="language-${lang}">${escaped}</code></pre>`;
      })
      // Inline code
      .replace(/`([^`]+)`/g, '<code class="doc-inline-code">$1</code>')
      // Headers
      .replace(/^##### (.+)$/gm, "<h5>$1</h5>")
      .replace(/^#### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^### (.+)$/gm, "<h3>$1</h3>")
      .replace(/^## (.+)$/gm, "<h2>$1</h2>")
      .replace(/^# (.+)$/gm, "<h1>$1</h1>")
      // Bold
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      // Italic
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // Horizontal rule
      .replace(/^---$/gm, "<hr>")
      // Tables
      .replace(/^\|(.+)\|$/gm, (match: string) => {
        const cells = match.split("|").filter((c: string) => c.trim());
        const isHeader = match.includes("---");
        if (isHeader) return "";
        const tag = match.includes("|:") ? "th" : "td";
        const cellsHtml = cells.map((c: string) => `<${tag}>${c.trim()}</${tag}>`).join("");
        return `<tr>${cellsHtml}</tr>`;
      })
      // Unordered list items
      .replace(/^[\s]*[-*+] (.+)$/gm, "<li>$1</li>")
      // Paragraphs (double newline)
      .replace(/\n\n/g, "</p><p>")
      ;
    return `<div class="doc-content"><p>${html}</p></div>`;
  };

  return (
    <div className={cn(
      "flex min-h-0 w-full min-w-0 flex-1 flex-col",
      "pt-1 sm:pt-2",
    )}>
      <PluginSlot name="docs:top" />

      {/* Tabs */}
      <div className="flex gap-1 px-4 mb-2 border-b border-gray-700">
        {([
          ["guide", "使用指南"],
          ["cli", "CLI 命令"],
          ["faq", "故障排查"],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setActive(key)}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 transition-colors",
              active === key
                ? "border-[#00d47e] text-[#00d47e]"
                : "border-transparent text-gray-400 hover:text-gray-200",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 pb-8">
        {loading ? (
          <div className="flex items-center justify-center h-64 text-gray-500">
            正在加载文档…
          </div>
        ) : (
          <div
            className="doc-render max-w-4xl mx-auto prose prose-invert"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(markdown) }}
          />
        )}
      </div>

      <PluginSlot name="docs:bottom" />

      {/* Styles */}
      <style>{`
        .doc-render h1 { font-size: 1.875rem; font-weight: 700; color: #00d47e; margin-top: 2rem; margin-bottom: 1rem; border-bottom: 1px solid #374151; padding-bottom: 0.5rem; }
        .doc-render h2 { font-size: 1.5rem; font-weight: 600; color: #e5e7eb; margin-top: 1.5rem; margin-bottom: 0.75rem; }
        .doc-render h3 { font-size: 1.25rem; font-weight: 600; color: #d1d5db; margin-top: 1.25rem; margin-bottom: 0.5rem; }
        .doc-render h4 { font-size: 1.125rem; font-weight: 600; color: #9ca3af; margin-top: 1rem; margin-bottom: 0.5rem; }
        .doc-render h5 { font-size: 1rem; font-weight: 600; color: #6b7280; margin-top: 0.75rem; margin-bottom: 0.25rem; }
        .doc-render p { color: #d1d5db; line-height: 1.75; margin-bottom: 0.75rem; }
        .doc-render strong { color: #f3f4f6; }
        .doc-render a { color: #00d47e; text-decoration: underline; }
        .doc-render hr { border-color: #374151; margin: 2rem 0; }
        .doc-render table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
        .doc-render th, .doc-render td { border: 1px solid #374151; padding: 0.5rem 0.75rem; text-align: left; font-size: 0.875rem; }
        .doc-render th { background: #1f2937; color: #00d47e; font-weight: 600; }
        .doc-render td { color: #d1d5db; }
        .doc-render tr:nth-child(even) td { background: #111827; }
        .doc-render .doc-codeblock { background: #0d1117; border: 1px solid #30363d; border-radius: 0.5rem; padding: 1rem; overflow-x: auto; margin: 1rem 0; font-size: 0.875rem; line-height: 1.7; }
        .doc-render .doc-codeblock code { color: #e6edf3; }
        .doc-render .doc-inline-code { background: #1f2937; color: #00d47e; padding: 0.15rem 0.35rem; border-radius: 0.25rem; font-size: 0.875em; }
        .doc-render ul, .doc-render ol { padding-left: 1.5rem; margin: 0.5rem 0; }
        .doc-render li { color: #d1d5db; margin-bottom: 0.35rem; line-height: 1.6; }
        .doc-render blockquote { border-left: 4px solid #00d47e; padding-left: 1rem; color: #9ca3af; font-style: italic; margin: 1rem 0; }
        .doc-render .prose-invert { color: #d1d5db; }
      `}</style>
    </div>
  );
}
